"""Shared Model Adapter fixtures for ADR 0034 Phases A and B.

Builds a real governed Source Handling authority store and a real ADR 0031
pre-model build, so the Model Adapter tests exercise the actual authority
resolution path rather than a stub that would make the assertions vacuous.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import timezone; UTC = timezone.utc; from datetime import datetime, timedelta

from evidence_pre_model_source_handling_fixture import (
    publish_policy_successor,
    source_handling_authority,
)

from hunter.evidence_intelligence.model_adapter import ModelExecutionProfile
from hunter.evidence_intelligence.model_adapter_transport import (
    DispatchAuthorization,
    TransportCredential,
    TransportRequest,
    TransportResult,
)
from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidenceContextAllocationResult,
    EvidencePreModelBuildRecord,
    EvidencePromptArtifact,
)

DOCUMENT_ID = "doc-model-adapter"
BUILD_CUTOFF = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
ATTEMPT_CUTOFF = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
RECORDED_AT = datetime(2026, 8, 22, 12, 5, tzinfo=UTC)

_ALLOW = {
    "PERSIST": "ALLOW",
    "READ_ACCESS": "ALLOW",
    "RECONSTRUCT": "ALLOW",
    "DELETE_OR_EXPIRE": "ALLOW",
}
_DENY = {
    "PERSIST": "DENY",
    "READ_ACCESS": "DENY",
    "RECONSTRUCT": "DENY",
    "DELETE_OR_EXPIRE": "ALLOW",
}

# Every durable surface the Model Adapter writes, plus the content-derived and
# operational-metadata categories ADR 0034 governs independently.
#
# Phase B adds the outcome, response-content, and provider-correlation surfaces.
# Response content maps to the same content-derived categories as request content
# because it is content of the same kind; the two are still governed
# independently, because the *decision* is per attempt and per category, and a
# fixture can deny either surface without touching the other.
FIELD_MAP: Mapping[str, list[str]] = {
    "model_attempt": ["AUDIT_FIELD"],
    "model_handoff": ["AUDIT_FIELD"],
    "model_attempt_outcome": ["AUDIT_FIELD"],
    "provider_request_content": ["SOURCE_BYTES", "SOURCE_DERIVED_TEXT", "CONTENT_DERIVED_ID"],
    "provider_response_content": ["SOURCE_BYTES", "SOURCE_DERIVED_TEXT", "CONTENT_DERIVED_ID"],
    "dispatch_capability": ["OPERATIONAL_METADATA"],
    "provider_correlation": ["OPERATIONAL_METADATA"],
}


def dispositions(*, request_content: bool = True, dispatch_capability: bool = True) -> dict[str, dict[str, str]]:
    """Per-category durable dispositions for a governed authority store.

    `request_content` names the content-derived categories, which govern request
    *and* response content alike: they are the same categories, so denying them
    denies both. That is the honest shape of the registry rather than a
    test-only convenience, and Phase B relies on it to prove that an authorized
    send can still be forbidden to retain a single response byte.
    """
    content = dict(_ALLOW) if request_content else dict(_DENY)
    return {
        "AUDIT_FIELD": dict(_ALLOW),
        "SOURCE_BYTES": content,
        "SOURCE_DERIVED_TEXT": content,
        "CONTENT_DERIVED_ID": content,
        "OPERATIONAL_METADATA": dict(_ALLOW) if dispatch_capability else dict(_DENY),
    }


def attempt_authority(
    *,
    cutoff: datetime = ATTEMPT_CUTOFF,
    processing: str = "ALLOW",
    retention: str = "ALLOW",
    reconstruction: str = "ALLOW",
    request_content: bool = True,
    dispatch_capability: bool = True,
    document_id: str = DOCUMENT_ID,
):
    """A governed authority resolvable strict-known at `cutoff`."""
    return source_handling_authority(
        document_id=document_id,
        cutoff=cutoff,
        processing=processing,
        retention=retention,
        reconstruction=reconstruction,
        field_map=FIELD_MAP,
        durable_dispositions_override=dispositions(
            request_content=request_content,
            dispatch_capability=dispatch_capability,
        ),
    )


def capability(*, version: str = "1") -> EvidenceCapabilityConstraint:
    """A real ADR 0031 capability constraint; its identity is derived, never set."""
    return EvidenceCapabilityConstraint(
        constraint_id="capability:phase-a",
        version=version,
        maximum_input_bytes=4096,
        reserved_completion_bytes=512,
    )


def allocation(*, capability_identity: str | None = None) -> EvidenceContextAllocationResult:
    """The build's own allocation, which records the governing capability."""
    return EvidenceContextAllocationResult(
        ledger_id="ledger:v1",
        capability_identity=capability_identity or capability().constraint_identity,
        prompt_specification_identity="spec:v1",
        outcome="READY",
        included_span_ids=("span:1",),
        budget_excluded_span_ids=(),
        preflight_size_bytes=22,
        available_input_bytes=capability().available_input_bytes,
        reason_codes=(),
    )


def prompt_artifact(content: str = "canonical prompt bytes") -> EvidencePromptArtifact:
    encoded = content.encode("utf-8")
    return EvidencePromptArtifact(
        plan_id="plan:v1",
        ledger_id="ledger:v1",
        allocation_id="allocation:v1",
        package_id="package:v1",
        prompt_specification_identity="spec:v1",
        compiler_version="1",
        canonicalization_version="1",
        encoding="utf-8",
        content=content,
        content_hash=hashlib.sha256(encoded).hexdigest(),
        measured_size_bytes=len(encoded),
    )


def build_record(
    artifact: EvidencePromptArtifact,
    *,
    allocation_result: EvidenceContextAllocationResult | None = None,
) -> EvidencePreModelBuildRecord:
    resolved_allocation = allocation_result or allocation()
    return EvidencePreModelBuildRecord(
        execution_owner_id="pipeline-run:1",
        intent_id="intent:v1",
        ledger_id=artifact.ledger_id,
        allocation_id=resolved_allocation.allocation_id,
        package_id=artifact.package_id,
        prompt_plan_id=artifact.plan_id,
        prompt_artifact_id=artifact.artifact_id,
        reconstruction_outcome="AVAILABLE",
        reason_codes=(),
    )


def execution_profile(
    *,
    required_capability_identity: str | None = None,
    idempotency_capability: str = "SUPPORTED",
    profile_version: str = "1",
    **overrides,
) -> ModelExecutionProfile:
    defaults: dict[str, object] = {
        "profile_name": "phase-a-neutral",
        "profile_version": profile_version,
        "provider_identity": "provider-neutral-fake",
        "model_identity": "model-neutral-fake",
        "model_version": "1",
        "endpoint_class_identity": "endpoint-class:none",
        "transport_identity": "transport:no-network",
        "transport_version": "1",
        "request_protocol_identity": "protocol:neutral",
        "request_protocol_version": "1",
        "required_capability_identity": (
            required_capability_identity
            if required_capability_identity is not None
            else capability().constraint_identity
        ),
        "response_format_identity": "response-format:neutral",
        "idempotency_capability": idempotency_capability,
        "parameters": (("temperature", "0"),),
        "prohibited_capabilities": ("tool_use", "network_fetch"),
    }
    defaults.update(overrides)
    return ModelExecutionProfile(**defaults)  # type: ignore[arg-type]


def later(minutes: int) -> datetime:
    return ATTEMPT_CUTOFF + timedelta(minutes=minutes)


def deny_successor(
    authority,
    *,
    cutoff: datetime = ATTEMPT_CUTOFF,
    request_content: bool = True,
    dispatch_capability: bool = True,
):
    """A DENY successor published into `authority`'s own store, viewed at `cutoff`.

    The predecessor decision stays resolvable at its earlier cutoff, so one
    lineage carries both. Tests that must show an earlier ALLOW does not carry
    forward need exactly that: the permissive head reachable in the same store
    the adapter is handed.
    """
    return publish_policy_successor(
        authority,
        cutoff=cutoff,
        processing="DENY",
        durable_dispositions_override=dispositions(
            request_content=request_content,
            dispatch_capability=dispatch_capability,
        ),
    )


# --- Phase B ----------------------------------------------------------------

PHASE_B_ENDPOINT_CLASS = "endpoint-class:phase-b-fake"
PHASE_B_ENDPOINTS = {PHASE_B_ENDPOINT_CLASS: "https://fake.invalid/v1/chat/completions"}
DISPATCHED_AT = datetime(2026, 8, 22, 12, 10, tzinfo=UTC)
CONCLUDED_AT = datetime(2026, 8, 22, 12, 11, tzinfo=UTC)

FAKE_TRANSPORT_IDENTITY = "transport:phase-b-fake"
FAKE_TRANSPORT_VERSION = "1"
# A second fake with a materially different wire shape, so CO-02 provider
# neutrality is exercised rather than asserted.
ALTERNATE_TRANSPORT_IDENTITY = "transport:phase-b-fake-alternate"
ALTERNATE_TRANSPORT_VERSION = "2"


def phase_b_profile(
    *,
    transport_identity: str = FAKE_TRANSPORT_IDENTITY,
    transport_version: str = FAKE_TRANSPORT_VERSION,
    idempotency_capability: str = "SUPPORTED",
    **overrides,
) -> ModelExecutionProfile:
    """The single explicitly configured Phase B execution profile."""
    defaults: dict[str, object] = {
        "profile_name": "phase-b-single",
        "endpoint_class_identity": PHASE_B_ENDPOINT_CLASS,
        "transport_identity": transport_identity,
        "transport_version": transport_version,
        "idempotency_capability": idempotency_capability,
    }
    defaults.update(overrides)
    return execution_profile(**defaults)  # type: ignore[arg-type]


class FakeTransport:
    """A deterministic transport double for the Model Adapter contract.

    Harness fidelity (`docs/HUNTER_IMPLEMENTATION_CONTRACT.md`): this reproduces
    the external semantics the adapter actually depends on, and no more. It

    * requires the same `DispatchAuthorization` the real transport requires, so a
      test cannot dispatch without one where production could not;
    * requires a real `TransportCredential`, so credential handling is exercised;
    * returns a `TransportResult` with the same three-axis shape — result class,
      delivery certainty, execution evidence — and never a cleaner one, so it
      cannot resolve an ambiguity the real transport leaves ambiguous;
    * records every send, so "exactly once" is observed rather than assumed.

    It deliberately does *not* filter, order, or de-duplicate anything, because
    the real transport does not either.
    """

    def __init__(
        self,
        result: TransportResult | None = None,
        *,
        raises: BaseException | None = None,
        transport_identity: str = FAKE_TRANSPORT_IDENTITY,
        transport_version: str = FAKE_TRANSPORT_VERSION,
    ) -> None:
        self.transport_identity = transport_identity
        self.transport_version = transport_version
        self._result = result
        self._raises = raises
        self.sends: list[tuple[TransportRequest, DispatchAuthorization]] = []
        self.seen_credentials: list[object] = []

    def send(
        self,
        request: TransportRequest,
        *,
        authorization: DispatchAuthorization,
        credential: TransportCredential,
    ) -> TransportResult:
        if not isinstance(authorization, DispatchAuthorization):
            raise AssertionError("the adapter must supply a dispatch authorization")
        if not isinstance(credential, TransportCredential):
            raise AssertionError("the adapter must supply a non-durable transport credential")
        self.sends.append((request, authorization))
        self.seen_credentials.append(credential)
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def transport_result(
    *,
    result_class: str = "RESPONSE_RECEIVED",
    delivery_certainty: str = "ANSWERED",
    execution_evidence: str = "PROVIDER_RETURNED_COMPLETION",
    response_text: str | None = '{"choices": [{"message": {"content": "ok"}}]}',
    provider_status_metadata: tuple[tuple[str, str], ...] = (("http_status", "200"),),
    correlation_identity: str | None = "req_abc123",
    transport_identity: str = FAKE_TRANSPORT_IDENTITY,
    transport_version: str = FAKE_TRANSPORT_VERSION,
    reason_code: str = "",
) -> TransportResult:
    return TransportResult(
        result_class=result_class,  # type: ignore[arg-type]
        delivery_certainty=delivery_certainty,  # type: ignore[arg-type]
        execution_evidence=execution_evidence,  # type: ignore[arg-type]
        transport_identity=transport_identity,
        transport_version=transport_version,
        response_protocol_identity="response-protocol:phase-b-fake",
        response_protocol_version="1",
        response_text=response_text,
        provider_status_metadata=provider_status_metadata,
        correlation_identity=correlation_identity,
        reason_code=reason_code,
    )


def credential(secret: str = "sk-fake-phase-b-secret-value") -> TransportCredential:
    return TransportCredential(secret, slot_identity="slot:phase-b")
