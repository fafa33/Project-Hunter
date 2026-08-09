from __future__ import annotations

from dataclasses import dataclass

from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidenceContextSelectionPolicy,
    EvidenceExtractionIntent,
    EvidencePreModelBuildResult,
    EvidencePromptSpecification,
    PreModelInvariantError,
    build_evidence_pre_model,
)
from hunter.evidence_intelligence.pre_model_repository import (
    load_canonical_evidence_span_inventory,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository


@dataclass(frozen=True)
class EvidencePreModelOrchestrationRequest:
    document_id: str
    execution_owner_id: str
    intent: EvidenceExtractionIntent
    policy_id: str
    policy_version: str
    required_span_ids: tuple[str, ...]
    specification: EvidencePromptSpecification
    capability: EvidenceCapabilityConstraint
    retain_exact_prompt: bool = True

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("document_id must be non-empty")
        if not self.execution_owner_id:
            raise ValueError("execution_owner_id must be non-empty")
        if not self.policy_id:
            raise ValueError("policy_id must be non-empty")
        if not self.policy_version:
            raise ValueError("policy_version must be non-empty")
        object.__setattr__(self, "required_span_ids", tuple(sorted(set(self.required_span_ids))))


@dataclass(frozen=True)
class EvidencePreModelOrchestrationResult:
    document_id: str
    canonical_span_ids: tuple[str, ...]
    policy: EvidenceContextSelectionPolicy
    build_result: EvidencePreModelBuildResult


def orchestrate_evidence_pre_model(
    *,
    repository: EvidenceIntelligenceRepository,
    request: EvidencePreModelOrchestrationRequest,
) -> EvidencePreModelOrchestrationResult:
    """Build one provider-free Evidence Intelligence pre-model runtime slice.

    Repository-backed EvidenceSpan inventory is the candidate-set authority. The
    caller may identify spans that are required, but cannot prefilter or silently
    omit canonical spans: every remaining canonical span is deterministically
    represented as optional policy coverage.
    """

    if request.intent.target_id != request.document_id:
        raise PreModelInvariantError("TARGET_DOCUMENT_MISMATCH")
    if request.intent.context_policy_id != request.policy_id:
        raise PreModelInvariantError("CONTEXT_POLICY_ID_MISMATCH")
    if request.intent.historical_cutoff is not None:
        raise PreModelInvariantError("HISTORICAL_REPOSITORY_SPAN_INVENTORY_UNSUPPORTED")

    inventory = load_canonical_evidence_span_inventory(
        repository,
        document_id=request.document_id,
    )
    canonical_ids = set(inventory.span_ids)
    missing_required = set(request.required_span_ids).difference(canonical_ids)
    if missing_required:
        raise PreModelInvariantError("REQUIRED_SPAN_NOT_IN_CANONICAL_INVENTORY")

    optional_span_ids = tuple(sorted(canonical_ids.difference(request.required_span_ids)))
    policy = EvidenceContextSelectionPolicy(
        policy_id=request.policy_id,
        version=request.policy_version,
        required_span_ids=request.required_span_ids,
        optional_span_ids=optional_span_ids,
    )
    build_result = build_evidence_pre_model(
        execution_owner_id=request.execution_owner_id,
        intent=request.intent,
        policy=policy,
        specification=request.specification,
        capability=request.capability,
        canonical_inventory=inventory.spans,
        candidate_span_ids=inventory.span_ids,
        retain_exact_prompt=request.retain_exact_prompt,
    )
    return EvidencePreModelOrchestrationResult(
        document_id=request.document_id,
        canonical_span_ids=inventory.span_ids,
        policy=policy,
        build_result=build_result,
    )
