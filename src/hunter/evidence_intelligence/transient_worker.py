"""ADR 0035 protected worker for TRANSIENT_NOT_RETAINED validation.

Exact provider-response bytes are created, screened, and semantically
consumed only in the hardened child process.  The caller-facing process
owns only non-content metadata and a closed control channel whose protocol
can request one canonical validation event and can never return body bytes.
"""

from __future__ import annotations

import ctypes
import faulthandler
import json
import logging
import os
import resource
import socket
import sys
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_MAX_CONTROL_MESSAGE = 1_048_576
_PR_SET_DUMPABLE = 4
_PT_DENY_ATTACH = 31


def _access_error(message: str) -> Exception:
    from hunter.evidence_intelligence.model_adapter import TransientResponseAccessError

    return TransientResponseAccessError(message)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _send_message(endpoint: socket.socket, payload: dict[str, Any]) -> None:
    encoded = _canonical_json(payload)
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
    decoded = json.loads(_recv_exact(endpoint, size).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("protected-worker control payload must be an object")
    return decoded


def _harden_worker() -> str:
    """Establish the falsifiable OS inspection boundary before provider I/O."""
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

    @classmethod
    def from_entry(cls, entry: Any) -> _TransientResponseEnvelope:
        return cls(
            response_capture_identity=entry.response_capture_identity,
            attempt_id=entry.attempt_id,
            handoff_id=entry.handoff_id,
            outcome_id=entry.outcome_id,
            execution_profile_identity=entry.execution_profile_identity,
            response_protocol_identity=entry.response_protocol_identity,
            response_protocol_version=entry.response_protocol_version,
            transport_identity=entry.transport_identity,
            transport_version=entry.transport_version,
        )

    def matches(self, coordinates: dict[str, str]) -> bool:
        return all(getattr(self, name) == value for name, value in coordinates.items())


@dataclass
class _ProtectedWorkerSession:
    pid: int
    endpoint: socket.socket = field(repr=False, compare=False)
    envelope: _TransientResponseEnvelope
    hardening: str
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    closed: bool = False


class TransientResponseHandoffVault:
    """Split protected-worker coordinator containing no caller-readable body state."""

    def __init__(self) -> None:
        self.__model_adapter_bound = False
        self.__model_adapter_capability: object | None = None
        self.__response_validator_bound = False
        self.__response_validator_ref: weakref.ReferenceType[Any] | None = None
        self.__sessions: dict[str, _ProtectedWorkerSession] = {}
        self.__worker_mode = False
        self.__worker_entry: Any | None = None
        self.__lock = threading.Lock()

    @staticmethod
    def protected_worker_supported() -> bool:
        return hasattr(os, "fork") and (sys.platform.startswith("linux") or sys.platform == "darwin")

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
        if capability is None or capability is not self.__model_adapter_capability:
            raise _access_error("transient response deposit requires Model Adapter authority")
        if not self.__worker_mode:
            raise _access_error("exact transient bytes may exist only inside the protected worker")
        if self.__worker_entry is not None:
            raise _access_error("protected worker already owns a pending transient capture")
        self.__worker_entry = entry

    def _dispatch_authorized(
        self,
        capability: object | None,
        *,
        operation: Callable[[], Any],
    ) -> dict[str, Any]:
        if capability is None or capability is not self.__model_adapter_capability:
            raise _access_error("protected dispatch requires Model Adapter authority")
        if not self.__response_validator_bound or self.__response_validator_ref is None:
            raise _access_error("protected dispatch requires the canonical ResponseValidator owner")
        if not self.protected_worker_supported():
            raise _access_error("no accepted OS-protected worker is available on this platform")

        validator = self.__response_validator_ref()
        if validator is None:
            raise _access_error("canonical ResponseValidator owner disappeared")

        parent_endpoint, worker_endpoint = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        parent_endpoint.set_inheritable(False)
        worker_endpoint.set_inheritable(False)

        # Fork only from a quiescent validator boundary. A concurrent authorization
        # or execution may otherwise leave _state_lock locked in the child with no
        # surviving owning thread. The child installs a fresh lock before any
        # validator operation; the parent releases the original immediately after
        # the fork.
        validator_state_lock = validator._state_lock  # noqa: SLF001 - same protected boundary
        validator_state_lock.acquire()
        try:
            pid = os.fork()
        except BaseException:
            validator_state_lock.release()
            parent_endpoint.close()
            worker_endpoint.close()
            raise
        if pid == 0:  # pragma: no cover - assertions observe the parent side
            try:
                validator._state_lock = threading.Lock()  # noqa: SLF001 - post-fork child reset
                parent_endpoint.close()
                self.__worker_mode = True
                self.__sessions = {}
                hardening = _harden_worker()
                _send_message(worker_endpoint, {"kind": "HARDENED", "mechanism": hardening})
                result = operation()
                response_artifact = getattr(result, "response_artifact", None)
                capture_identity = (
                    getattr(response_artifact, "response_artifact_identity", None)
                    if response_artifact is not None
                    else None
                )
                ready = self.__worker_entry is not None
                _send_message(
                    worker_endpoint,
                    {
                        "kind": "DISPATCH_RESULT",
                        "outcome_id": result.outcome.outcome_id,
                        "response_capture_identity": capture_identity,
                        "capture_cutoff": result.outcome.recorded_at.isoformat(),
                        "validation_ready": ready,
                    },
                )
                if ready:
                    self.__serve_one_command(worker_endpoint)
            except BaseException:  # noqa: BLE001 - never serialize body/exception detail
                try:
                    _send_message(
                        worker_endpoint,
                        {"kind": "WORKER_ERROR", "reason_code": "PROTECTED_WORKER_EXECUTION_FAILED"},
                    )
                except BaseException:
                    pass
            finally:
                self.__worker_entry = None
                try:
                    worker_endpoint.close()
                except OSError:
                    pass
                os._exit(0)

        validator_state_lock.release()
        worker_endpoint.close()
        try:
            hardened = _recv_message(parent_endpoint)
            if hardened.get("kind") != "HARDENED":
                raise _access_error("protected worker did not establish its isolation boundary")
            result = _recv_message(parent_endpoint)
            if result.get("kind") != "DISPATCH_RESULT":
                raise _access_error("protected worker failed before governed response capture")
            capture_identity = result.get("response_capture_identity")
            if result.get("validation_ready"):
                if not isinstance(capture_identity, str) or not capture_identity:
                    raise _access_error("protected worker omitted canonical capture identity")
                entry_envelope = self._envelope_from_persistence_coordinates(capture_identity, result)
                session = _ProtectedWorkerSession(
                    pid=pid,
                    endpoint=parent_endpoint,
                    envelope=entry_envelope,
                    hardening=str(hardened.get("mechanism") or ""),
                )
                with self.__lock:
                    if capture_identity in self.__sessions:
                        raise _access_error("protected worker session already exists for capture")
                    self.__sessions[capture_identity] = session
            else:
                parent_endpoint.close()
                os.waitpid(pid, 0)
            return result
        except BaseException:
            try:
                parent_endpoint.close()
            except OSError:
                pass
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
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
        )  # noqa: SLF001
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
        if self.__worker_mode:
            entry = self.__worker_entry
            return entry is not None and _TransientResponseEnvelope.from_entry(entry).matches(coordinates)
        capture_identity = coordinates.get("response_capture_identity")
        if not capture_identity:
            return False
        with self.__lock:
            session = self.__sessions.get(capture_identity)
        if session is None or session.closed or not session.envelope.matches(coordinates):
            return False
        try:
            os.kill(session.pid, 0)
        except OSError:
            return False
        return True

    def consume_authorized(self, **coordinates: str) -> str:
        if not self.__worker_mode:
            raise _access_error("caller-facing process has no transient-body consume capability")
        entry = self.__worker_entry
        if entry is None or not _TransientResponseEnvelope.from_entry(entry).matches(coordinates):
            raise _access_error("transient response lineage does not match authorization")
        self.__worker_entry = None
        return entry.content

    def execute_canonical_event(self, *, response_capture_identity: str, validation_event_id: str) -> dict[str, Any]:
        if self.__worker_mode:
            raise _access_error("worker cannot recursively invoke its caller control channel")
        with self.__lock:
            session = self.__sessions.get(response_capture_identity)
        if session is None or session.closed:
            raise _access_error("protected transient worker is unavailable")
        with session.lock:
            try:
                _send_message(
                    session.endpoint,
                    {"op": "EXECUTE", "validation_event_id": validation_event_id},
                )
                reply = _recv_message(session.endpoint)
            except (OSError, EOFError, RuntimeError, ValueError) as error:
                self._close_session(response_capture_identity, terminate=True)
                raise _access_error("protected transient worker disappeared before semantics") from error
            self._close_session(response_capture_identity, terminate=False)
        if reply.get("kind") not in {"EXECUTION_RESULT", "REFUSAL"}:
            raise _access_error("protected worker returned an invalid semantic message")
        return reply

    def discard_authorized(self, **coordinates: str) -> None:
        if self.__worker_mode:
            entry = self.__worker_entry
            if entry is not None and _TransientResponseEnvelope.from_entry(entry).matches(coordinates):
                self.__worker_entry = None
            return
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
        return self.__worker_mode

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
        if terminate:
            try:
                os.kill(session.pid, 9)
            except OSError:
                pass
        try:
            os.waitpid(session.pid, 0)
        except OSError:
            pass

    def __serve_one_command(self, endpoint: socket.socket) -> None:
        command = _recv_message(endpoint)
        op = command.get("op")
        if op == "DISCARD":
            self.__worker_entry = None
            _send_message(endpoint, {"kind": "DISCARDED"})
            return
        if op != "EXECUTE":
            _send_message(
                endpoint,
                {"kind": "REFUSAL", "state": "INPUT_UNAVAILABLE", "reason_code": "PROTECTED_WORKER_COMMAND_REJECTED"},
            )
            return
        validation_event_id = command.get("validation_event_id")
        if not isinstance(validation_event_id, str) or not validation_event_id:
            _send_message(
                endpoint,
                {"kind": "REFUSAL", "state": "INPUT_UNAVAILABLE", "reason_code": "VALIDATION_EVENT_UNAVAILABLE"},
            )
            return
        validator = self.__response_validator_ref() if self.__response_validator_ref is not None else None
        if validator is None:
            _send_message(
                endpoint,
                {"kind": "REFUSAL", "state": "INPUT_UNAVAILABLE", "reason_code": "VALIDATOR_OWNER_UNAVAILABLE"},
            )
            return
        allocation = validator._foundation._repository.validation_event(validation_event_id)  # noqa: SLF001
        if allocation is None:
            _send_message(
                endpoint,
                {"kind": "REFUSAL", "state": "INPUT_UNAVAILABLE", "reason_code": "VALIDATION_EVENT_UNAVAILABLE"},
            )
            return
        authorization_result = validator.authorize_event(allocation)
        if authorization_result.refusal is not None:
            refusal = authorization_result.refusal.refusal
            _send_message(
                endpoint, {"kind": "REFUSAL", "state": refusal.state.value, "reason_code": refusal.reason_code}
            )
            return
        authorization = authorization_result.authorization
        if authorization is None:
            _send_message(
                endpoint,
                {
                    "kind": "REFUSAL",
                    "state": "INPUT_UNAVAILABLE",
                    "reason_code": "VALIDATION_AUTHORIZATION_UNAVAILABLE",
                },
            )
            return
        execution = validator.execute(authorization)
        if not hasattr(execution, "outcome"):
            refusal = execution.refusal
            _send_message(
                endpoint, {"kind": "REFUSAL", "state": refusal.state.value, "reason_code": refusal.reason_code}
            )
            return
        outcome = execution.outcome
        _send_message(
            endpoint,
            {
                "kind": "EXECUTION_RESULT",
                "authorization_id": outcome.authorization_id,
                "state": outcome.state.value,
                "findings": [
                    {"dimension": item.dimension, "state": item.state.value, "reason_code": item.reason_code}
                    for item in outcome.findings
                ],
            },
        )

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
