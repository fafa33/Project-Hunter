"""ADR 0035 protected worker for TRANSIENT_NOT_RETAINED validation.

The worker is started with multiprocessing ``spawn`` so it begins in a
fresh Python interpreter and inherits no live application threads,
locks, authority-store snapshots, validator objects, or repository
connections from the caller process. Exact provider-response bytes are
created, screened, held, and semantically consumed only in that spawned
and OS-hardened process. The caller receives non-content transport and
validation metadata only.
"""

from __future__ import annotations

import ctypes
import faulthandler
import json
import logging
import multiprocessing
import resource
import socket
import sys
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any

_MAX_CONTROL_MESSAGE = 1_048_576
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4
_PT_DENY_ATTACH = 31


class ProtectedTransportRaised(RuntimeError):
    """A transport raised inside the isolated worker after handoff consumption."""

    def __init__(self, exception_type: str) -> None:
        super().__init__(exception_type)
        self.exception_type = exception_type


def _access_error(message: str) -> Exception:
    from hunter.evidence_intelligence.model_adapter import TransientResponseAccessError

    return TransientResponseAccessError(message)


def _pack(value: Any) -> Any:
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, Enum):
        return _pack(value.value)
    if isinstance(value, dict):
        return ["dict", [[str(key), _pack(item)] for key, item in value.items()]]
    if isinstance(value, (list, tuple)):
        return ["list", [_pack(item) for item in value]]
    raise TypeError(f"unsupported protected-worker control value: {type(value).__name__}")


def _unpack(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        raise RuntimeError("protected-worker control value is malformed")
    tag = value[0]
    if tag == "none":
        return None
    if tag in {"bool", "str"}:
        return value[1]
    if tag == "int":
        return int(value[1])
    if tag == "decimal":
        return Decimal(value[1])
    if tag == "float":
        return float(value[1])
    if tag == "datetime":
        return value[1]
    if tag == "dict":
        return {str(key): _unpack(item) for key, item in value[1]}
    if tag == "list":
        return [_unpack(item) for item in value[1]]
    raise RuntimeError("protected-worker control value tag is unknown")


def _send_message(endpoint: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(_pack(payload), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > _MAX_CONTROL_MESSAGE:
        raise RuntimeError("protected-worker control message exceeds bound")
    endpoint.sendall(len(encoded).to_bytes(4, "big") + encoded)


def _recv_exact(endpoint: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = endpoint.recv(remaining)
        if not chunk:
            raise EOFError("protected-worker control channel closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_message(endpoint: socket.socket) -> dict[str, Any]:
    size = int.from_bytes(_recv_exact(endpoint, 4), "big")
    if size <= 0 or size > _MAX_CONTROL_MESSAGE:
        raise RuntimeError("protected-worker control frame is malformed")
    decoded = _unpack(json.loads(_recv_exact(endpoint, size).decode("utf-8")))
    if not isinstance(decoded, dict):
        raise RuntimeError("protected-worker control payload must be an object")
    return decoded


def _harden_worker() -> str:
    logging.disable(logging.CRITICAL)
    try:
        faulthandler.disable()
    except RuntimeError:
        pass
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_DUMPABLE failed")
        # Read the flag back rather than trusting the setter's return code alone:
        # a caller that only checks the prctl() exit status cannot distinguish a
        # genuinely applied PR_SET_DUMPABLE(0) from a silent no-op, which would
        # let a regression here go undetected by anything relying on this
        # function's return value as evidence that hardening took effect.
        readback = libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0)
        if readback != 0:
            raise OSError(0, f"PR_SET_DUMPABLE did not take effect (PR_GET_DUMPABLE={readback})")
        return "linux-prctl-nondumpable"
    if sys.platform == "darwin":
        if libc.ptrace(_PT_DENY_ATTACH, 0, None, 0) != 0:
            raise OSError(ctypes.get_errno(), "PT_DENY_ATTACH failed")
        return "darwin-pt-deny-attach"
    raise OSError("platform has no accepted protected-worker hardening primitive")


@dataclass(frozen=True)
class _TransientResponseEnvelope:
    response_capture_identity: str
    attempt_id: str
    handoff_id: str
    outcome_id: str
    execution_profile_identity: str
    response_protocol_identity: str
    response_protocol_version: str
    transport_identity: str
    transport_version: str

    def as_control(self) -> dict[str, str]:
        return {
            "response_capture_identity": self.response_capture_identity,
            "attempt_id": self.attempt_id,
            "handoff_id": self.handoff_id,
            "outcome_id": self.outcome_id,
            "execution_profile_identity": self.execution_profile_identity,
            "response_protocol_identity": self.response_protocol_identity,
            "response_protocol_version": self.response_protocol_version,
            "transport_identity": self.transport_identity,
            "transport_version": self.transport_version,
        }

    def matches(self, coordinates: dict[str, str]) -> bool:
        return all(getattr(self, name) == value for name, value in coordinates.items())


@dataclass
class _ProtectedWorkerSession:
    process: Any = field(repr=False, compare=False)
    endpoint: socket.socket = field(repr=False, compare=False)
    envelope: _TransientResponseEnvelope
    hardening: str
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    closed: bool = False


def _evaluate_body(response_text: str, plan: dict[str, Any]) -> dict[str, Any]:
    from hunter.evidence_intelligence.response_validator import (
        DeterministicJsonValidationRuntime,
        ResponseValidationFinding,
        ResponseValidationRuleUnavailable,
        ValidationState,
        highest_precedence_validation_state,
    )

    runtime_values = plan.get("runtime")
    coordinates_values = plan.get("coordinates")
    if not isinstance(runtime_values, dict) or not isinstance(coordinates_values, dict):
        raise RuntimeError("protected semantic plan is incomplete")
    runtime = DeterministicJsonValidationRuntime(**runtime_values)
    coordinates = SimpleNamespace(**coordinates_values)
    try:
        findings = tuple(
            runtime.evaluate(
                response_text=response_text,
                coordinates=coordinates,
                output_contract=plan["output_contract"],
                evidence_inputs=tuple(tuple(item) for item in plan["evidence_inputs"]),
                provider_status_metadata=tuple(tuple(item) for item in plan["provider_status_metadata"]),
                required_dimensions=tuple(plan["required_dimensions"]),
            )
        )
    except ResponseValidationRuleUnavailable:
        findings = (
            ResponseValidationFinding(
                "RULE_AVAILABILITY",
                ValidationState.RULE_UNAVAILABLE,
                "EXECUTABLE_VALIDATION_RULE_UNAVAILABLE",
            ),
        )
    except Exception:
        findings = (
            ResponseValidationFinding(
                "VALIDATOR_EXECUTION",
                ValidationState.VALIDATOR_ERROR,
                "VALIDATOR_EXECUTION_FAILED",
            ),
        )
    state = highest_precedence_validation_state(item.state for item in findings)
    return {
        "kind": "EXECUTION_RESULT",
        "authorization_id": str(plan["authorization_id"]),
        "state": state.value,
        "findings": [
            {"dimension": item.dimension, "state": item.state.value, "reason_code": item.reason_code}
            for item in findings
        ],
    }


def _spawn_transport_worker(
    endpoint: socket.socket,
    transport: Any,
    request: Any,
    authorization_fields: dict[str, str],
    credential_secret: str,
    credential_slot_identity: str,
) -> None:
    body: str | None = None
    envelope: _TransientResponseEnvelope | None = None
    try:
        hardening = _harden_worker()
        _send_message(endpoint, {"kind": "HARDENED", "mechanism": hardening})
        from hunter.evidence_intelligence.model_adapter import response_content_credential_risk
        from hunter.evidence_intelligence.model_adapter_transport import (
            _DISPATCH_MINT,
            DispatchAuthorization,
            TransportCredential,
            TransportResult,
        )

        authorization = DispatchAuthorization(_DISPATCH_MINT, **authorization_fields)
        credential = TransportCredential(credential_secret, slot_identity=credential_slot_identity)
        try:
            result = transport.send(request, authorization=authorization, credential=credential)
        except Exception as error:
            _send_message(endpoint, {"kind": "TRANSPORT_RAISED", "exception_type": type(error).__name__})
            return
        if not isinstance(result, TransportResult):
            _send_message(endpoint, {"kind": "TRANSPORT_NON_CANONICAL"})
            return
        body = result.response_text
        credential_safe = body is None or response_content_credential_risk(body) is None
        _send_message(
            endpoint,
            {
                "kind": "TRANSPORT_RESULT",
                "result_class": result.result_class,
                "delivery_certainty": result.delivery_certainty,
                "execution_evidence": result.execution_evidence,
                "transport_identity": result.transport_identity,
                "transport_version": result.transport_version,
                "response_protocol_identity": result.response_protocol_identity,
                "response_protocol_version": result.response_protocol_version,
                "response_present": body is not None,
                "credential_safe": credential_safe,
                "provider_status_metadata": result.provider_status_metadata,
                "correlation_identity": result.correlation_identity,
                "idempotency_key": result.idempotency_key,
                "reason_code": result.reason_code,
            },
        )
        bind = _recv_message(endpoint)
        if bind.get("op") != "BIND" or not bind.get("keep_body"):
            body = None
            _send_message(endpoint, {"kind": "DISCARDED"})
            return
        raw_envelope = bind.get("envelope")
        if body is None or not credential_safe or not isinstance(raw_envelope, dict):
            body = None
            _send_message(endpoint, {"kind": "DISCARDED"})
            return
        envelope = _TransientResponseEnvelope(**raw_envelope)
        _send_message(endpoint, {"kind": "BOUND"})
        command = _recv_message(endpoint)
        if command.get("op") == "DISCARD":
            body = None
            _send_message(endpoint, {"kind": "DISCARDED"})
            return
        if command.get("op") != "EXECUTE" or body is None or envelope is None:
            _send_message(
                endpoint,
                {
                    "kind": "REFUSAL",
                    "state": "INPUT_UNAVAILABLE",
                    "reason_code": "PROTECTED_WORKER_COMMAND_REJECTED",
                },
            )
            return
        plan = command.get("plan")
        if not isinstance(plan, dict):
            _send_message(
                endpoint,
                {
                    "kind": "REFUSAL",
                    "state": "INPUT_UNAVAILABLE",
                    "reason_code": "VALIDATION_PLAN_UNAVAILABLE",
                },
            )
            return
        _send_message(endpoint, _evaluate_body(body, plan))
        body = None
    except BaseException:
        try:
            _send_message(endpoint, {"kind": "WORKER_ERROR", "reason_code": "PROTECTED_WORKER_EXECUTION_FAILED"})
        except BaseException:
            pass
    finally:
        body = None
        try:
            endpoint.close()
        except OSError:
            pass


class TransientResponseHandoffVault:
    """Spawn-based coordinator containing no caller-readable response body state."""

    def __init__(self) -> None:
        self.__model_adapter_bound = False
        self.__model_adapter_capability: object | None = None
        self.__response_validator_bound = False
        self.__response_validator_ref: weakref.ReferenceType[Any] | None = None
        self.__sessions: dict[str, _ProtectedWorkerSession] = {}
        self.__lock = threading.Lock()

    @staticmethod
    def protected_worker_supported() -> bool:
        return sys.platform.startswith("linux") or sys.platform == "darwin"

    def _bind_model_adapter(self, owner: object, installer: Any) -> None:
        from hunter.evidence_intelligence.model_adapter import ModelAdapterService

        expected = vars(ModelAdapterService)["_ModelAdapterService__install_transient_handoff_capability"]
        if (
            type(owner) is not ModelAdapterService
            or getattr(owner, "_transient_response_vault", None) is not self
            or getattr(installer, "__self__", None) is not owner
            or getattr(installer, "__func__", None) is not expected
            or self.__model_adapter_bound
        ):
            raise _access_error("protected transient worker producer owner is not canonical")
        capability = object()
        self.__model_adapter_bound = True
        self.__model_adapter_capability = capability
        installer(capability)

    def _bind_response_validator(self, owner: object, installer: Any) -> None:
        from hunter.evidence_intelligence.response_validator import ResponseValidator

        expected = vars(ResponseValidator)["_ResponseValidator__install_transient_response_boundary"]
        if (
            type(owner) is not ResponseValidator
            or getattr(owner, "_transient_response_vault", None) is not self
            or getattr(installer, "__self__", None) is not owner
            or getattr(installer, "__func__", None) is not expected
            or self.__response_validator_bound
        ):
            raise _access_error("protected transient worker validator owner is not canonical")
        installer(self)
        self.__response_validator_ref = weakref.ref(owner)
        self.__response_validator_bound = True

    def _deposit_authorized(self, capability: object | None, *, entry: Any) -> None:
        raise _access_error("direct transient-body deposit is forbidden by the spawn worker topology")

    def _dispatch_authorized(
        self,
        capability: object | None,
        *,
        transport: Any,
        request: Any,
        authorization: Any,
        credential: Any,
        operation: Any,
    ) -> Any:
        if capability is None or capability is not self.__model_adapter_capability:
            raise _access_error("protected dispatch requires Model Adapter authority")
        if not self.__response_validator_bound or self.__response_validator_ref is None:
            raise _access_error("protected dispatch requires the canonical ResponseValidator owner")
        if not self.protected_worker_supported():
            raise _access_error("no accepted OS-protected worker is available on this platform")

        parent_endpoint, worker_endpoint = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        authorization_fields = {
            "handoff_id": authorization.handoff_id,
            "attempt_id": authorization.attempt_id,
            "execution_profile_identity": authorization.execution_profile_identity,
            "consumed_at": authorization.consumed_at,
        }
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_spawn_transport_worker,
            args=(
                worker_endpoint,
                transport,
                request,
                authorization_fields,
                credential.reveal(),
                credential.slot_identity,
            ),
            daemon=False,
        )
        try:
            process.start()
        except BaseException as error:
            parent_endpoint.close()
            worker_endpoint.close()
            raise ProtectedTransportRaised(f"SPAWN_{type(error).__name__}") from error
        worker_endpoint.close()
        try:
            hardened = _recv_message(parent_endpoint)
            if hardened.get("kind") != "HARDENED":
                raise _access_error("protected worker did not establish its isolation boundary")
            transport_message = _recv_message(parent_endpoint)
            kind = transport_message.get("kind")
            if kind == "TRANSPORT_RAISED":
                raise ProtectedTransportRaised(str(transport_message.get("exception_type") or "UNKNOWN"))
            if kind == "TRANSPORT_NON_CANONICAL":
                from hunter.evidence_intelligence.model_adapter_transport import TransportResult

                sanitized = object()
            elif kind == "TRANSPORT_RESULT":
                from hunter.evidence_intelligence.model_adapter_transport import TransportResult

                sanitized = TransportResult(
                    result_class=transport_message["result_class"],
                    delivery_certainty=transport_message["delivery_certainty"],
                    execution_evidence=transport_message["execution_evidence"],
                    transport_identity=transport_message["transport_identity"],
                    transport_version=transport_message["transport_version"],
                    response_protocol_identity=transport_message["response_protocol_identity"],
                    response_protocol_version=transport_message["response_protocol_version"],
                    response_text="" if transport_message.get("response_present") else None,
                    provider_status_metadata=tuple(
                        tuple(item) for item in transport_message.get("provider_status_metadata", [])
                    ),
                    correlation_identity=transport_message.get("correlation_identity"),
                    idempotency_key=transport_message.get("idempotency_key"),
                    reason_code=str(transport_message.get("reason_code") or ""),
                )
            else:
                raise _access_error("protected worker failed before governed transport observation")

            result = operation(sanitized)
            response_artifact = getattr(result, "response_artifact", None)
            outcome = getattr(result, "outcome", None)
            keep_body = bool(
                kind == "TRANSPORT_RESULT"
                and transport_message.get("response_present")
                and transport_message.get("credential_safe")
                and response_artifact is not None
                and outcome is not None
                and getattr(outcome, "outcome", None) == "SUCCEEDED_TRANSPORT"
            )
            if not keep_body:
                _send_message(parent_endpoint, {"op": "BIND", "keep_body": False})
                try:
                    _recv_message(parent_endpoint)
                except BaseException:
                    pass
                parent_endpoint.close()
                process.join(timeout=5)
                return result

            capture_identity = response_artifact.response_artifact_identity
            envelope = self._envelope_from_persistence_coordinates(
                capture_identity,
                {
                    "capture_cutoff": outcome.recorded_at.isoformat(),
                    "outcome_id": outcome.outcome_id,
                },
            )
            _send_message(
                parent_endpoint,
                {"op": "BIND", "keep_body": True, "envelope": envelope.as_control()},
            )
            bound = _recv_message(parent_endpoint)
            if bound.get("kind") != "BOUND":
                raise _access_error("protected worker refused canonical capture binding")
            session = _ProtectedWorkerSession(
                process=process,
                endpoint=parent_endpoint,
                envelope=envelope,
                hardening=str(hardened.get("mechanism") or ""),
            )
            with self.__lock:
                if capture_identity in self.__sessions:
                    raise _access_error("protected worker session already exists for capture")
                self.__sessions[capture_identity] = session
            return result
        except BaseException:
            try:
                parent_endpoint.close()
            except OSError:
                pass
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            raise

    def _envelope_from_persistence_coordinates(
        self,
        capture_identity: str,
        result: dict[str, Any],
    ) -> _TransientResponseEnvelope:
        validator = self.__response_validator_ref() if self.__response_validator_ref is not None else None
        if validator is None:
            raise _access_error("canonical ResponseValidator owner disappeared")
        repository = validator._model_adapter_repository  # noqa: SLF001 - same protected boundary
        capture = repository.strict_known_response_capture(
            capture_identity, datetime.fromisoformat(str(result["capture_cutoff"]))
        )
        if capture is None:
            raise _access_error("protected capture is not durably knowable")
        artifact, outcome = capture
        if outcome.outcome_id != result.get("outcome_id"):
            raise _access_error("protected dispatch outcome identity mismatch")
        return _TransientResponseEnvelope(
            response_capture_identity=artifact.response_artifact_identity,
            attempt_id=str(outcome.attempt_id or ""),
            handoff_id=str(outcome.handoff_id or ""),
            outcome_id=outcome.outcome_id,
            execution_profile_identity=outcome.execution_profile_identity,
            response_protocol_identity=artifact.response_protocol_identity,
            response_protocol_version=artifact.response_protocol_version,
            transport_identity=artifact.transport_identity,
            transport_version=artifact.transport_version,
        )

    def available_for_validation(self, **coordinates: str) -> bool:
        capture_identity = coordinates.get("response_capture_identity")
        if not capture_identity:
            return False
        with self.__lock:
            session = self.__sessions.get(capture_identity)
        return bool(
            session is not None
            and not session.closed
            and session.process.is_alive()
            and session.envelope.matches(coordinates)
        )

    def consume_authorized(self, **coordinates: str) -> str:
        raise _access_error("caller-facing process has no transient-body consume capability")

    def execute_canonical_event(
        self,
        *,
        response_capture_identity: str,
        validation_event_id: str,
        execution_plan: dict[str, Any],
    ) -> dict[str, Any]:
        with self.__lock:
            session = self.__sessions.get(response_capture_identity)
        if session is None or session.closed:
            raise _access_error("protected transient worker is unavailable")
        if str(execution_plan.get("coordinates", {}).get("validation_event_id")) != validation_event_id:
            raise _access_error("protected semantic plan event identity mismatch")
        with session.lock:
            try:
                _send_message(session.endpoint, {"op": "EXECUTE", "plan": execution_plan})
                reply = _recv_message(session.endpoint)
            except (OSError, EOFError, RuntimeError, ValueError) as error:
                self._close_session(response_capture_identity, terminate=True)
                raise _access_error("protected transient worker disappeared before semantics") from error
            self._close_session(response_capture_identity, terminate=False)
        if reply.get("kind") not in {"EXECUTION_RESULT", "REFUSAL"}:
            raise _access_error("protected worker returned an invalid semantic message")
        return reply

    def discard_authorized(self, **coordinates: str) -> None:
        capture_identity = coordinates.get("response_capture_identity")
        if not capture_identity:
            return
        with self.__lock:
            session = self.__sessions.get(capture_identity)
        if session is None or session.closed or not session.envelope.matches(coordinates):
            return
        with session.lock:
            try:
                _send_message(session.endpoint, {"op": "DISCARD"})
                _recv_message(session.endpoint)
            except BaseException:
                pass
            self._close_session(capture_identity, terminate=False)

    def _in_protected_worker(self) -> bool:
        return False

    def _close_session(self, capture_identity: str, *, terminate: bool) -> None:
        with self.__lock:
            session = self.__sessions.pop(capture_identity, None)
        if session is None or session.closed:
            return
        session.closed = True
        try:
            session.endpoint.close()
        except OSError:
            pass
        if terminate and session.process.is_alive():
            session.process.terminate()
        session.process.join(timeout=5)

    def close(self) -> None:
        with self.__lock:
            identities = tuple(self.__sessions)
        for identity in identities:
            self._close_session(identity, terminate=True)

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass
