"""Shared Phase A Model Adapter fixtures.

Builds a real governed Source Handling authority store and a real ADR 0031
pre-model build, so the Model Adapter tests exercise the actual authority
resolution path rather than a stub that would make the assertions vacuous.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from evidence_pre_model_source_handling_fixture import source_handling_authority

from hunter.evidence_intelligence.model_adapter import ModelExecutionProfile
from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
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
FIELD_MAP: Mapping[str, list[str]] = {
    "model_attempt": ["AUDIT_FIELD"],
    "model_handoff": ["AUDIT_FIELD"],
    "provider_request_content": ["SOURCE_BYTES", "SOURCE_DERIVED_TEXT", "CONTENT_DERIVED_ID"],
    "dispatch_capability": ["OPERATIONAL_METADATA"],
}


def dispositions(*, request_content: bool = True, dispatch_capability: bool = True) -> dict[str, dict[str, str]]:
    """Per-category durable dispositions for a Phase A authority store."""
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


def build_record(artifact: EvidencePromptArtifact) -> EvidencePreModelBuildRecord:
    return EvidencePreModelBuildRecord(
        execution_owner_id="pipeline-run:1",
        intent_id="intent:v1",
        ledger_id=artifact.ledger_id,
        allocation_id=artifact.allocation_id,
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
