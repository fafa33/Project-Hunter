from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Protocol

from hunter.evidence_intelligence.smart_prompt_machine import SmartPromptMachineError
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationEnvelope, PromptAutomationVerifier

PROMPT_AUTOMATION_DESTINATION_SCHEMA_VERSION = "smart-prompt-automation-destination-v1"
PROMPT_AUTOMATION_DISPATCH_SCHEMA_VERSION = "smart-prompt-automation-dispatch-v1"
PROMPT_AUTOMATION_PAYLOAD_SCHEMA_VERSION = "smart-prompt-automation-payload-v1"
PROMPT_AUTOMATION_ACK_SCHEMA_VERSION = "smart-prompt-automation-ack-v1"


class PromptAutomationTransportError(SmartPromptMachineError):
    """Raised when governed automation transport lineage cannot be proven."""


def _required_text(name: str, value: object) -> str:
    """Return one required text coordinate or fail closed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _exact_key(name: str, value: object) -> str:
    """Validate one exact key with no wildcard routing semantics."""
    key = _required_text(name, value)
    if "*" in key or "?" in key:
        raise PromptAutomationTransportError(f"{name} must be exact; wildcards are forbidden")
    return key


def _identity(kind: str, value: object) -> str:
    """Produce one stable SHA-256 identity over canonical JSON."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"{kind}:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class PromptAutomationDestination:
    """Governed non-secret destination metadata for an external automation transport."""

    destination_id: str
    version: str
    destination_key: str
    transport_name: str
    schema_version: str = PROMPT_AUTOMATION_DESTINATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate exact destination coordinates without accepting endpoint secrets."""
        _required_text("destination_id", self.destination_id)
        _required_text("version", self.version)
        _exact_key("destination_key", self.destination_key)
        _required_text("transport_name", self.transport_name)
        if self.schema_version != PROMPT_AUTOMATION_DESTINATION_SCHEMA_VERSION:
            raise PromptAutomationTransportError("unknown automation destination schema version")

    @property
    def destination_identity(self) -> str:
        """Return the stable governed destination identity."""
        return _identity("smart-prompt-automation-destination", asdict(self))


class PromptAutomationDestinationRegistry:
    """Immutable exact destination registry with unique identity/version coordinates."""

    def __init__(self, destinations: Iterable[PromptAutomationDestination]) -> None:
        """Reject duplicate keys and reused destination identity/version pairs."""
        entries: dict[str, PromptAutomationDestination] = {}
        coordinates: dict[tuple[str, str], PromptAutomationDestination] = {}
        for destination in destinations:
            if not isinstance(destination, PromptAutomationDestination):
                raise TypeError("destination registry accepts PromptAutomationDestination entries only")
            coordinate = (destination.destination_id, destination.version)
            if coordinate in coordinates:
                existing = coordinates[coordinate]
                if existing.destination_identity != destination.destination_identity:
                    raise PromptAutomationTransportError("conflicting automation destination identity/version payload")
                raise PromptAutomationTransportError("duplicate automation destination identity/version")
            if destination.destination_key in entries:
                existing = entries[destination.destination_key]
                if existing.destination_identity != destination.destination_identity:
                    raise PromptAutomationTransportError("conflicting automation destination key")
                raise PromptAutomationTransportError("duplicate automation destination key")
            coordinates[coordinate] = destination
            entries[destination.destination_key] = destination
        self._destinations = dict(sorted(entries.items()))
        self._registry_identity = _identity(
            "smart-prompt-automation-destination-registry",
            [
                {
                    "destination_key": destination.destination_key,
                    "destination_identity": destination.destination_identity,
                }
                for destination in self._destinations.values()
            ],
        )

    @property
    def registry_identity(self) -> str:
        """Return the insertion-order-independent registry identity."""
        return self._registry_identity

    def resolve(self, destination_key: str) -> PromptAutomationDestination:
        """Resolve one exact destination key or fail closed."""
        key = _exact_key("destination_key", destination_key)
        try:
            return self._destinations[key]
        except KeyError as error:
            raise PromptAutomationTransportError("unknown governed automation destination") from error


@dataclass(frozen=True)
class PromptAutomationDispatchRequest:
    """Narrow delivery request over a machine-issued non-content envelope."""

    destination_key: str
    envelope: PromptAutomationEnvelope
    schema_version: str = PROMPT_AUTOMATION_DISPATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate that no prompt or transport authority is caller-supplied."""
        _exact_key("destination_key", self.destination_key)
        if not isinstance(self.envelope, PromptAutomationEnvelope):
            raise TypeError("dispatch requires the canonical PromptAutomationEnvelope")
        if self.schema_version != PROMPT_AUTOMATION_DISPATCH_SCHEMA_VERSION:
            raise PromptAutomationTransportError("unknown automation dispatch schema version")


@dataclass(frozen=True)
class PromptAutomationPayload:
    """Canonical non-content payload suitable for n8n or another automation transport."""

    dispatch_id: str
    destination_registry_identity: str
    destination_identity: str
    destination_key: str
    transport_name: str
    envelope_id: str
    task_request_id: str
    route_registry_identity: str
    profile_registry_identity: str
    route_identity: str
    profile_identity: str
    build_manifest_id: str
    build_record_id: str
    schema_version: str = PROMPT_AUTOMATION_PAYLOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate every non-content lineage coordinate and exact schema version."""
        for name in (
            "dispatch_id",
            "destination_registry_identity",
            "destination_identity",
            "destination_key",
            "transport_name",
            "envelope_id",
            "task_request_id",
            "route_registry_identity",
            "profile_registry_identity",
            "route_identity",
            "profile_identity",
            "build_manifest_id",
            "build_record_id",
        ):
            _required_text(name, getattr(self, name))
        if self.schema_version != PROMPT_AUTOMATION_PAYLOAD_SCHEMA_VERSION:
            raise PromptAutomationTransportError("unknown automation payload schema version")

    @property
    def payload_id(self) -> str:
        """Return the stable identity of this canonical transport payload."""
        return _identity("smart-prompt-automation-payload", asdict(self))

    def as_mapping(self) -> Mapping[str, str]:
        """Return an immutable string-only mapping for transport delivery."""
        return MappingProxyType({key: str(value) for key, value in asdict(self).items()})


@dataclass(frozen=True)
class PromptAutomationAcknowledgement:
    """Transport acknowledgement bound to one exact dispatch and payload identity."""

    dispatch_id: str
    payload_id: str
    receipt_id: str
    accepted: bool
    schema_version: str = PROMPT_AUTOMATION_ACK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate acknowledgement coordinates before dispatcher acceptance."""
        _required_text("dispatch_id", self.dispatch_id)
        _required_text("payload_id", self.payload_id)
        _required_text("receipt_id", self.receipt_id)
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a bool")
        if self.schema_version != PROMPT_AUTOMATION_ACK_SCHEMA_VERSION:
            raise PromptAutomationTransportError("unknown automation acknowledgement schema version")


class PromptAutomationTransport(Protocol):
    """External delivery seam; endpoint URLs and credentials stay outside Hunter core."""

    def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
        """Deliver one already-validated non-content payload."""
        raise NotImplementedError


@dataclass(frozen=True)
class PromptAutomationDispatchResult:
    """Accepted transport result with canonical payload and validated acknowledgement."""

    payload: PromptAutomationPayload
    acknowledgement: PromptAutomationAcknowledgement


def _make_dispatch_scope() -> tuple[
    Callable[[Any, PromptAutomationDispatchRequest], PromptAutomationDispatchResult],
    Callable[[object, Mapping[str, str], PromptAutomationVerifier], PromptAutomationEnvelope],
]:
    """Create a private dispatch scope whose mutable context is never module-visible."""
    active_context: ContextVar[
        tuple[object, Mapping[str, str], PromptAutomationEnvelope, PromptAutomationVerifier] | None
    ] = ContextVar("hunter_prompt_automation_dispatch_context", default=None)

    def require_active_dispatch(
        transport: object,
        payload: Mapping[str, str],
        verifier: PromptAutomationVerifier,
    ) -> PromptAutomationEnvelope:
        """Require the exact mapping and verifier bound by the active dispatcher call."""
        context = active_context.get()
        if context is None or context[0] is not transport or context[1] is not payload or context[3] != verifier:
            raise PromptAutomationTransportError("n8n transport delivery requires dispatcher authorization")
        return context[2]

    def dispatch(dispatcher: Any, request: PromptAutomationDispatchRequest) -> PromptAutomationDispatchResult:
        """Deliver only through the private context scope and validate its acknowledgement."""
        if type(dispatcher) is not PromptAutomationDispatcher:
            raise PromptAutomationTransportError("dispatcher authorization scope is not canonical")
        payload = dispatcher.build_payload(request)
        delivery_mapping = payload.as_mapping()
        context_token = active_context.set(
            (dispatcher._transport, delivery_mapping, request.envelope, dispatcher._verifier)
        )
        try:
            acknowledgement = dispatcher._transport.deliver(delivery_mapping)
        finally:
            active_context.reset(context_token)
        if not isinstance(acknowledgement, PromptAutomationAcknowledgement):
            raise PromptAutomationTransportError("transport returned non-canonical acknowledgement")
        if acknowledgement.dispatch_id != payload.dispatch_id:
            raise PromptAutomationTransportError("transport acknowledgement dispatch identity mismatch")
        if acknowledgement.payload_id != payload.payload_id:
            raise PromptAutomationTransportError("transport acknowledgement payload identity mismatch")
        if not acknowledgement.accepted:
            raise PromptAutomationTransportError("automation transport rejected the canonical payload")
        return PromptAutomationDispatchResult(payload=payload, acknowledgement=acknowledgement)

    return dispatch, require_active_dispatch


_dispatch_with_scope, _require_active_dispatch = _make_dispatch_scope()


class PromptAutomationDispatcher:
    """Build and deliver canonical non-content automation payloads fail-closed."""

    __slots__ = ("_destinations", "_transport", "_verifier", "_seen_dispatches")

    def __init__(
        self,
        *,
        destinations: PromptAutomationDestinationRegistry,
        transport: PromptAutomationTransport,
        verifier: PromptAutomationVerifier | None = None,
    ) -> None:
        """Bind governed destinations, transport, and one process-bound verifier snapshot."""
        if not isinstance(destinations, PromptAutomationDestinationRegistry):
            raise TypeError("dispatcher requires the canonical destination registry")
        if verifier is not None and type(verifier) is not PromptAutomationVerifier:
            raise TypeError("dispatcher requires the canonical process-bound issuer verifier")
        self._destinations = destinations
        self._transport = transport
        self._verifier = verifier if verifier is not None else PromptAutomationVerifier.from_environment()
        self._seen_dispatches: dict[str, str] = {}

    def build_payload(self, request: PromptAutomationDispatchRequest) -> PromptAutomationPayload:
        """Derive one deterministic payload from a governed envelope and destination."""
        if not isinstance(request, PromptAutomationDispatchRequest):
            raise TypeError("build_payload requires PromptAutomationDispatchRequest")
        envelope = request.envelope
        if type(envelope) is not PromptAutomationEnvelope:
            raise PromptAutomationTransportError("dispatcher requires an exact PromptAutomationEnvelope")
        try:
            PromptAutomationEnvelope.verify_issuer_signature(envelope, self._verifier)
        except SmartPromptMachineError as error:
            raise PromptAutomationTransportError(
                "automation envelope issuer signature could not be verified"
            ) from error
        destination = self._destinations.resolve(request.destination_key)
        dispatch_id = _identity(
            "smart-prompt-automation-dispatch",
            {
                "destination_registry_identity": self._destinations.registry_identity,
                "destination_identity": destination.destination_identity,
                "envelope_id": envelope.envelope_id,
            },
        )
        payload = PromptAutomationPayload(
            dispatch_id=dispatch_id,
            destination_registry_identity=self._destinations.registry_identity,
            destination_identity=destination.destination_identity,
            destination_key=destination.destination_key,
            transport_name=destination.transport_name,
            envelope_id=envelope.envelope_id,
            task_request_id=envelope.task_request_id,
            route_registry_identity=envelope.route_registry_identity,
            profile_registry_identity=envelope.profile_registry_identity,
            route_identity=envelope.route_identity,
            profile_identity=envelope.profile_identity,
            build_manifest_id=envelope.build_manifest_id,
            build_record_id=envelope.build_record_id,
        )
        previous_payload_id = self._seen_dispatches.get(dispatch_id)
        if previous_payload_id is not None and previous_payload_id != payload.payload_id:
            raise PromptAutomationTransportError("dispatch identity was reused with conflicting payload")
        self._seen_dispatches[dispatch_id] = payload.payload_id
        return payload

    def dispatch(self, request: PromptAutomationDispatchRequest) -> PromptAutomationDispatchResult:
        """Enter the private dispatch scope and validate its exact acknowledgement."""
        return _dispatch_with_scope(self, request)
