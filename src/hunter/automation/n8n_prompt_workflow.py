"""Concrete n8n adapter for the governed Smart Prompt Machine automation seam.

The Smart Prompt Machine owns task routing, prompt compilation, Source Handling,
and envelope provenance. This module owns only the operational edge that delivers
an already-authorized non-content payload to n8n and validates the narrow
acknowledgement returned by that workflow.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hunter.evidence_intelligence.pre_model_persistence import EvidencePreModelReconstruction
from hunter.evidence_intelligence.smart_prompt_routing import (
    PromptTaskCompilationResult,
    PromptTaskRequest,
    SmartPromptMachine,
)
from hunter.evidence_intelligence.smart_prompt_transport import (
    PROMPT_AUTOMATION_ACK_SCHEMA_VERSION,
    PromptAutomationAcknowledgement,
    PromptAutomationDestination,
    PromptAutomationDestinationRegistry,
    PromptAutomationDispatcher,
    PromptAutomationDispatchRequest,
    PromptAutomationDispatchResult,
    PromptAutomationPayload,
    PromptAutomationTransportError,
)

N8N_WEBHOOK_URL_ENV = "HUNTER_N8N_WEBHOOK_URL"
N8N_BEARER_TOKEN_ENV = "HUNTER_N8N_BEARER_TOKEN"
N8N_WIRE_REQUEST_SCHEMA_VERSION = "hunter-smart-prompt-n8n-request-v1"
N8N_DESTINATION_ID = "hunter-n8n"
N8N_DESTINATION_VERSION = "1"
N8N_DESTINATION_KEY = "automation.n8n"
N8N_TRANSPORT_NAME = "n8n-webhook-v1"
N8N_ACK_MAX_BYTES = 16_384
N8N_DEFAULT_TIMEOUT_SECONDS = 15.0

_N8N_RUNTIME_MINT = object()

HttpOpener = Callable[[urllib.request.Request, float], Any]


class N8nPromptAutomationError(PromptAutomationTransportError):
    """Raised when the operational n8n boundary cannot prove safe delivery."""


class _N8nBearerCredential:
    """Runtime-only bearer credential that refuses display or serialization."""

    __slots__ = ("_secret",)

    def __init__(self, secret: str) -> None:
        if not isinstance(secret, str) or not secret or any(character.isspace() for character in secret):
            raise N8nPromptAutomationError("n8n bearer credential is malformed")
        object.__setattr__(self, "_secret", secret)

    def reveal(self) -> str:
        """Reveal the credential only while constructing the outbound HTTP request."""
        return str(object.__getattribute__(self, "_secret"))

    def __setattr__(self, name: str, value: object) -> None:
        raise N8nPromptAutomationError("n8n bearer credential is immutable")

    def __repr__(self) -> str:
        return "_N8nBearerCredential(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise N8nPromptAutomationError("n8n bearer credential may not be serialized")

    def __getstate__(self) -> Any:
        raise N8nPromptAutomationError("n8n bearer credential may not be serialized")


def _validate_webhook_url(value: object) -> str:
    """Return one runtime webhook URL or fail closed without echoing it."""
    if not isinstance(value, str) or not value.strip() or any(character.isspace() for character in value):
        raise N8nPromptAutomationError("n8n webhook URL is missing or malformed")
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise N8nPromptAutomationError("n8n webhook URL is malformed") from error
    if parsed.scheme != "https" or not parsed.hostname:
        raise N8nPromptAutomationError("n8n webhook URL must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise N8nPromptAutomationError("n8n webhook URL must not contain userinfo credentials")
    if parsed.fragment:
        raise N8nPromptAutomationError("n8n webhook URL must not contain a fragment")
    return value


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    """Perform the real network call; tests inject an opener and never touch the network."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - HTTPS is validated before construction


class N8nWebhookTransport:
    """Runtime-configured HTTPS adapter implementing the Phase C transport seam."""

    __slots__ = ("_bearer", "_opener", "_timeout_seconds", "_webhook_url")

    def __init__(
        self,
        mint: object,
        *,
        webhook_url: str,
        bearer: _N8nBearerCredential | None,
        timeout_seconds: float,
        opener: HttpOpener,
    ) -> None:
        if mint is not _N8N_RUNTIME_MINT:
            raise N8nPromptAutomationError("n8n transport must be constructed from runtime configuration")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise N8nPromptAutomationError("n8n timeout must be a positive number")
        self._webhook_url = _validate_webhook_url(webhook_url)
        self._bearer = bearer
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = N8N_DEFAULT_TIMEOUT_SECONDS,
        opener: HttpOpener | None = None,
    ) -> N8nWebhookTransport:
        """Load endpoint and optional bearer material from runtime-only environment slots."""
        runtime = os.environ if environment is None else environment
        webhook_url = _validate_webhook_url(runtime.get(N8N_WEBHOOK_URL_ENV, ""))
        token = runtime.get(N8N_BEARER_TOKEN_ENV, "")
        bearer = _N8nBearerCredential(token) if token else None
        return cls(
            _N8N_RUNTIME_MINT,
            webhook_url=webhook_url,
            bearer=bearer,
            timeout_seconds=timeout_seconds,
            opener=_default_opener if opener is None else opener,
        )

    def __repr__(self) -> str:
        bearer_state = "configured" if self._bearer is not None else "none"
        return f"N8nWebhookTransport(webhook=<redacted>, bearer={bearer_state})"

    def __reduce__(self) -> Any:
        raise N8nPromptAutomationError("n8n transport runtime configuration may not be serialized")

    def __getstate__(self) -> Any:
        raise N8nPromptAutomationError("n8n transport runtime configuration may not be serialized")

    def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
        """POST one canonical non-content payload and validate the exact n8n acknowledgement."""
        canonical = self._canonical_payload(payload)
        body = self._wire_body(canonical)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": canonical.dispatch_id,
            "X-Hunter-Dispatch-ID": canonical.dispatch_id,
        }
        if self._bearer is not None:
            headers["Authorization"] = f"Bearer {self._bearer.reveal()}"
        request = urllib.request.Request(
            self._webhook_url,
            data=body,
            headers=headers,
            method="POST",
        )
        raw = self._read_response(request)
        return self._parse_acknowledgement(raw, canonical)

    @staticmethod
    def _canonical_payload(payload: Mapping[str, str]) -> PromptAutomationPayload:
        if not isinstance(payload, Mapping):
            raise N8nPromptAutomationError("n8n transport requires a canonical payload mapping")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()):
            raise N8nPromptAutomationError("n8n transport payload must contain strings only")
        try:
            return PromptAutomationPayload(**dict(payload))
        except (TypeError, ValueError, PromptAutomationTransportError) as error:
            raise N8nPromptAutomationError("n8n transport received a non-canonical automation payload") from error

    @staticmethod
    def _wire_body(payload: PromptAutomationPayload) -> bytes:
        document = {
            "payload": dict(payload.as_mapping()),
            "payload_id": payload.payload_id,
            "schema_version": N8N_WIRE_REQUEST_SCHEMA_VERSION,
        }
        return json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def _read_response(self, request: urllib.request.Request) -> bytes:
        response: Any = None
        try:
            response = self._opener(request, self._timeout_seconds)
            status = getattr(response, "status", None)
            if not isinstance(status, int):
                status = response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                raise N8nPromptAutomationError("n8n webhook returned a non-success status")
            raw = response.read(N8N_ACK_MAX_BYTES + 1)
            if not isinstance(raw, bytes):
                raise N8nPromptAutomationError("n8n webhook returned a non-byte response")
            if len(raw) > N8N_ACK_MAX_BYTES:
                raise N8nPromptAutomationError("n8n acknowledgement exceeded the maximum size")
            return raw
        except N8nPromptAutomationError:
            raise
        except (TimeoutError, OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
            raise N8nPromptAutomationError("n8n webhook delivery failed") from error
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

    @staticmethod
    def _parse_acknowledgement(
        raw: bytes,
        payload: PromptAutomationPayload,
    ) -> PromptAutomationAcknowledgement:
        try:
            decoded = raw.decode("utf-8")
            document = json.loads(decoded)
        except (UnicodeDecodeError, ValueError) as error:
            raise N8nPromptAutomationError("n8n acknowledgement must be valid UTF-8 JSON") from error
        expected_keys = {
            "accepted",
            "dispatch_id",
            "payload_id",
            "receipt_id",
            "schema_version",
        }
        if not isinstance(document, dict) or set(document) != expected_keys:
            raise N8nPromptAutomationError("n8n acknowledgement has a non-canonical shape")
        if document.get("schema_version") != PROMPT_AUTOMATION_ACK_SCHEMA_VERSION:
            raise N8nPromptAutomationError("n8n acknowledgement schema version mismatch")
        if document.get("dispatch_id") != payload.dispatch_id:
            raise N8nPromptAutomationError("n8n acknowledgement dispatch identity mismatch")
        if document.get("payload_id") != payload.payload_id:
            raise N8nPromptAutomationError("n8n acknowledgement payload identity mismatch")
        try:
            acknowledgement = PromptAutomationAcknowledgement(
                dispatch_id=document["dispatch_id"],
                payload_id=document["payload_id"],
                receipt_id=document["receipt_id"],
                accepted=document["accepted"],
                schema_version=document["schema_version"],
            )
        except (TypeError, ValueError, PromptAutomationTransportError) as error:
            raise N8nPromptAutomationError("n8n acknowledgement is malformed") from error
        if not acknowledgement.accepted:
            raise N8nPromptAutomationError("n8n workflow rejected the canonical payload")
        return acknowledgement


def n8n_destination_registry() -> PromptAutomationDestinationRegistry:
    """Return the single governed destination registry for the first operational n8n workflow."""
    return PromptAutomationDestinationRegistry(
        (
            PromptAutomationDestination(
                destination_id=N8N_DESTINATION_ID,
                version=N8N_DESTINATION_VERSION,
                destination_key=N8N_DESTINATION_KEY,
                transport_name=N8N_TRANSPORT_NAME,
            ),
        )
    )


def build_n8n_dispatcher(
    *,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = N8N_DEFAULT_TIMEOUT_SECONDS,
    opener: HttpOpener | None = None,
) -> PromptAutomationDispatcher:
    """Compose the existing Phase C dispatcher with the concrete runtime n8n adapter."""
    transport = N8nWebhookTransport.from_environment(
        environment=environment,
        timeout_seconds=timeout_seconds,
        opener=opener,
    )
    return PromptAutomationDispatcher(
        destinations=n8n_destination_registry(),
        transport=transport,
    )


@dataclass(frozen=True)
class N8nPromptAutomationWorkflowResult:
    """One full governed compilation plus its accepted operational n8n dispatch."""

    compilation: PromptTaskCompilationResult
    dispatch: PromptAutomationDispatchResult


class N8nPromptAutomationWorkflow:
    """First operational task-to-n8n workflow over the existing governed boundaries."""

    def __init__(
        self,
        *,
        machine: SmartPromptMachine,
        dispatcher: PromptAutomationDispatcher,
    ) -> None:
        if type(machine) is not SmartPromptMachine:
            raise TypeError("n8n workflow requires the exact canonical SmartPromptMachine")
        if type(dispatcher) is not PromptAutomationDispatcher:
            raise TypeError("n8n workflow requires the exact canonical PromptAutomationDispatcher")
        self._machine = machine
        self._dispatcher = dispatcher

    def run(self, request: PromptTaskRequest) -> N8nPromptAutomationWorkflowResult:
        """Compile under Hunter authority before delivering the signed lineage envelope to n8n."""
        if type(request) is not PromptTaskRequest:
            raise TypeError("n8n workflow requires the exact canonical PromptTaskRequest")
        compilation = self._machine.compile_task(request)
        dispatch = self._dispatcher.dispatch(
            PromptAutomationDispatchRequest(
                destination_key=N8N_DESTINATION_KEY,
                envelope=compilation.envelope,
            )
        )
        return N8nPromptAutomationWorkflowResult(
            compilation=compilation,
            dispatch=dispatch,
        )

    def strict_known_reconstruction(
        self,
        build_record_id: str,
        cutoff: datetime,
    ) -> EvidencePreModelReconstruction:
        """Delegate reconstruction to the existing Smart Prompt Machine authority path."""
        return self._machine.strict_known_reconstruction(build_record_id, cutoff)


def build_n8n_prompt_automation_workflow(
    *,
    machine: SmartPromptMachine,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = N8N_DEFAULT_TIMEOUT_SECONDS,
    opener: HttpOpener | None = None,
) -> N8nPromptAutomationWorkflow:
    """Compose one operational Smart Prompt Machine to n8n workflow from runtime configuration."""
    return N8nPromptAutomationWorkflow(
        machine=machine,
        dispatcher=build_n8n_dispatcher(
            environment=environment,
            timeout_seconds=timeout_seconds,
            opener=opener,
        ),
    )
