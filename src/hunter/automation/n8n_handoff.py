"""Split-domain Smart Prompt Machine handoff for the concrete n8n worker.

The issuer serializes only a signed, non-content ``PromptAutomationEnvelope``.
The network-capable worker reconstructs that exact envelope, verifies it with a
process-bound public verifier, fixes the governed n8n destination, and delegates
through the existing Phase C dispatcher and n8n transport.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

from hunter.automation.n8n import N8N_DESTINATION, build_n8n_prompt_automation_dispatcher
from hunter.evidence_intelligence.smart_prompt_machine import SmartPromptMachineError
from hunter.evidence_intelligence.smart_prompt_routing import (
    PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION,
    PromptAutomationEnvelope,
    PromptAutomationVerifier,
)
from hunter.evidence_intelligence.smart_prompt_transport import (
    PromptAutomationDispatcher,
    PromptAutomationDispatchRequest,
    PromptAutomationDispatchResult,
    PromptAutomationTransportError,
)

PROMPT_AUTOMATION_HANDOFF_SCHEMA_VERSION = "smart-prompt-automation-envelope-handoff-v1"
_MAX_HANDOFF_BYTES = 16 * 1024


class PromptAutomationHandoffError(PromptAutomationTransportError):
    """Raised when the issuer-to-worker handoff cannot prove canonical lineage."""


class _DuplicateJSONKeyError(ValueError):
    """Raised when an untrusted handoff JSON object repeats a key."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise _DuplicateJSONKeyError(key)
        values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class PromptAutomationEnvelopeHandoff:
    """Exact non-content wire representation of one signed automation envelope."""

    task_request_id: str
    route_registry_identity: str
    profile_registry_identity: str
    route_identity: str
    profile_identity: str
    build_manifest_id: str
    build_record_id: str
    issuer_signature: str
    envelope_schema_version: str = PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION
    schema_version: str = PROMPT_AUTOMATION_HANDOFF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Reject unknown handoff/envelope schemas and malformed envelope fields."""
        if self.schema_version != PROMPT_AUTOMATION_HANDOFF_SCHEMA_VERSION:
            raise PromptAutomationHandoffError("unknown automation handoff schema version")
        if self.envelope_schema_version != PROMPT_AUTOMATION_ENVELOPE_SCHEMA_VERSION:
            raise PromptAutomationHandoffError("unknown automation envelope schema version")
        try:
            self.to_envelope()
        except (SmartPromptMachineError, TypeError, ValueError):
            raise PromptAutomationHandoffError("automation handoff envelope is not canonical") from None

    @classmethod
    def from_envelope(cls, envelope: PromptAutomationEnvelope) -> PromptAutomationEnvelopeHandoff:
        """Serialize only an exact canonical envelope; never accept prompt/build content."""
        if type(envelope) is not PromptAutomationEnvelope:
            raise PromptAutomationHandoffError("handoff requires an exact PromptAutomationEnvelope")
        return cls(
            task_request_id=envelope.task_request_id,
            route_registry_identity=envelope.route_registry_identity,
            profile_registry_identity=envelope.profile_registry_identity,
            route_identity=envelope.route_identity,
            profile_identity=envelope.profile_identity,
            build_manifest_id=envelope.build_manifest_id,
            build_record_id=envelope.build_record_id,
            issuer_signature=envelope.issuer_signature,
            envelope_schema_version=envelope.schema_version,
        )

    @classmethod
    def from_json(cls, document: str | bytes) -> PromptAutomationEnvelopeHandoff:
        """Parse one bounded exact-schema JSON handoff and reject ambiguous objects."""
        if isinstance(document, bytes):
            if len(document) > _MAX_HANDOFF_BYTES:
                raise PromptAutomationHandoffError("automation handoff document is too large")
            try:
                text = document.decode("utf-8")
            except UnicodeDecodeError:
                raise PromptAutomationHandoffError("automation handoff document must be UTF-8 JSON") from None
        elif isinstance(document, str):
            try:
                encoded = document.encode("utf-8")
            except UnicodeEncodeError:
                raise PromptAutomationHandoffError("automation handoff document must be UTF-8 JSON") from None
            if len(encoded) > _MAX_HANDOFF_BYTES:
                raise PromptAutomationHandoffError("automation handoff document is too large")
            text = document
        else:
            raise TypeError("automation handoff document must be str or bytes")

        try:
            decoded = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        except _DuplicateJSONKeyError:
            raise PromptAutomationHandoffError("automation handoff document contains duplicate JSON keys") from None
        except (RecursionError, ValueError):
            raise PromptAutomationHandoffError("automation handoff document is malformed JSON") from None

        if not isinstance(decoded, dict):
            raise PromptAutomationHandoffError("automation handoff document must be a JSON object")
        expected_fields = frozenset(field.name for field in fields(cls))
        if frozenset(decoded) != expected_fields:
            raise PromptAutomationHandoffError("automation handoff document schema mismatch")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()):
            raise PromptAutomationHandoffError("automation handoff document must contain string fields only")
        try:
            return cls(**decoded)
        except PromptAutomationHandoffError:
            raise
        except (SmartPromptMachineError, TypeError, ValueError):
            raise PromptAutomationHandoffError("automation handoff document is not canonical") from None

    def to_envelope(self) -> PromptAutomationEnvelope:
        """Reconstruct the canonical signed envelope without introducing new authority."""
        return PromptAutomationEnvelope(
            task_request_id=self.task_request_id,
            route_registry_identity=self.route_registry_identity,
            profile_registry_identity=self.profile_registry_identity,
            route_identity=self.route_identity,
            profile_identity=self.profile_identity,
            build_manifest_id=self.build_manifest_id,
            build_record_id=self.build_record_id,
            issuer_signature=self.issuer_signature,
            schema_version=self.envelope_schema_version,
        )

    def to_json(self) -> str:
        """Return deterministic canonical JSON containing non-content lineage only."""
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


class N8nPromptAutomationWorker:
    """Verify a signed non-content handoff and dispatch it to the fixed n8n destination."""

    __slots__ = ("_dispatcher", "_verifier")

    def __init__(
        self,
        *,
        dispatcher: PromptAutomationDispatcher,
        verifier: PromptAutomationVerifier,
    ) -> None:
        if type(dispatcher) is not PromptAutomationDispatcher:
            raise TypeError("n8n handoff worker requires the canonical dispatcher")
        if type(verifier) is not PromptAutomationVerifier:
            raise TypeError("n8n handoff worker requires the process-bound issuer verifier")
        self._dispatcher = dispatcher
        self._verifier = verifier

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> N8nPromptAutomationWorker:
        """Build the network worker from verifier-only and operational n8n configuration."""
        source = os.environ if environ is None else environ
        verifier = PromptAutomationVerifier.from_environment(environ=source)
        dispatcher = build_n8n_prompt_automation_dispatcher(environ=source, opener=opener)
        return cls(dispatcher=dispatcher, verifier=verifier)

    def dispatch_document(self, document: str | bytes) -> PromptAutomationDispatchResult:
        """Verify the handoff before delegating to the canonical fixed-destination dispatcher."""
        handoff = PromptAutomationEnvelopeHandoff.from_json(document)
        envelope = handoff.to_envelope()
        try:
            PromptAutomationEnvelope.verify_issuer_signature(envelope, self._verifier)
        except SmartPromptMachineError:
            raise PromptAutomationHandoffError("automation handoff issuer signature could not be verified") from None
        request = PromptAutomationDispatchRequest(
            destination_key=N8N_DESTINATION.destination_key,
            envelope=envelope,
        )
        return self._dispatcher.dispatch(request)


def serialize_prompt_automation_handoff(envelope: PromptAutomationEnvelope) -> str:
    """Return the deterministic non-content document handed from issuer to worker."""
    return PromptAutomationEnvelopeHandoff.from_envelope(envelope).to_json()


__all__ = [
    "N8nPromptAutomationWorker",
    "PROMPT_AUTOMATION_HANDOFF_SCHEMA_VERSION",
    "PromptAutomationEnvelopeHandoff",
    "PromptAutomationHandoffError",
    "serialize_prompt_automation_handoff",
]
