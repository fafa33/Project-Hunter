from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from hunter.evidence_intelligence.models import EvidenceSpan

ResolutionStatus = Literal[
    "RESOLVED",
    "UNRESOLVED_REQUIRED",
    "UNRESOLVED_OPTIONAL",
    "NOT_ATTEMPTED",
]
AllocationOutcome = Literal["READY", "REPLAN_REQUIRED", "INSUFFICIENT_BUDGET"]
ReconstructionOutcome = Literal["AVAILABLE", "UNAVAILABLE"]


class PreModelInvariantError(RuntimeError):
    """Raised when a deterministic pre-model invariant is violated."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _identity(kind: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_json(value)).hexdigest()
    return f"{kind}:{digest}"


@dataclass(frozen=True)
class EvidenceExtractionIntent:
    task_type: str
    objective: str
    workflow_stage: str
    target_id: str
    output_contract_id: str
    output_contract_version: str
    context_policy_id: str
    replay_mode: str
    historical_cutoff: datetime | None
    schema_version: str = "1"
    authority_ceiling: str = "ExtractionProposal"

    @property
    def intent_id(self) -> str:
        return _identity("evidence-intent", asdict(self))


@dataclass(frozen=True)
class EvidenceContextSelectionPolicy:
    policy_id: str
    version: str
    required_span_ids: tuple[str, ...]
    optional_span_ids: tuple[str, ...]
    schema_version: str = "1"

    def __post_init__(self) -> None:
        required = tuple(sorted(set(self.required_span_ids)))
        optional = tuple(sorted(set(self.optional_span_ids)))
        overlap = set(required).intersection(optional)
        if overlap:
            raise ValueError("required and optional span ids must be disjoint")
        object.__setattr__(self, "required_span_ids", required)
        object.__setattr__(self, "optional_span_ids", optional)

    @property
    def policy_identity(self) -> str:
        return _identity("evidence-context-policy", asdict(self))


@dataclass(frozen=True)
class EvidencePromptSpecification:
    specification_id: str
    version: str
    compiler_version: str
    trusted_system_constraints: str
    task_instruction: str
    output_contract: str
    schema_version: str = "1"

    @property
    def specification_identity(self) -> str:
        return _identity("evidence-prompt-spec", asdict(self))


@dataclass(frozen=True)
class EvidenceCapabilityConstraint:
    constraint_id: str
    version: str
    maximum_input_bytes: int
    reserved_completion_bytes: int
    accounting_unit: str = "bytes"

    def __post_init__(self) -> None:
        if self.accounting_unit != "bytes":
            raise ValueError("phase 1 supports exact byte accounting only")
        if self.maximum_input_bytes <= 0:
            raise ValueError("maximum_input_bytes must be positive")
        if self.reserved_completion_bytes < 0:
            raise ValueError("reserved_completion_bytes must be non-negative")
        if self.reserved_completion_bytes >= self.maximum_input_bytes:
            raise ValueError("reserved completion capacity must leave input capacity")

    @property
    def constraint_identity(self) -> str:
        return _identity("evidence-capability", asdict(self))

    @property
    def available_input_bytes(self) -> int:
        return self.maximum_input_bytes - self.reserved_completion_bytes


@dataclass(frozen=True)
class EvidenceContextDecision:
    span_id: str
    required: bool
    resolution_status: ResolutionStatus
    selected: bool
    reason_code: str
    content_hash: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class EvidenceContextSelectionLedger:
    intent_id: str
    policy_identity: str
    decisions: tuple[EvidenceContextDecision, ...]
    schema_version: str = "1"

    @property
    def ledger_id(self) -> str:
        return _identity("evidence-context-ledger", asdict(self))


@dataclass(frozen=True)
class EvidenceContextAllocationResult:
    ledger_id: str
    capability_identity: str
    prompt_specification_identity: str
    outcome: AllocationOutcome
    included_span_ids: tuple[str, ...]
    budget_excluded_span_ids: tuple[str, ...]
    preflight_size_bytes: int | None
    available_input_bytes: int
    reason_codes: tuple[str, ...]
    schema_version: str = "1"

    @property
    def allocation_id(self) -> str:
        return _identity("evidence-context-allocation", asdict(self))


@dataclass(frozen=True)
class EvidenceContextPackage:
    allocation_id: str
    ordered_span_ids: tuple[str, ...]
    ordered_content_hashes: tuple[str, ...]
    schema_version: str = "1"

    @property
    def package_id(self) -> str:
        return _identity("evidence-context-package", asdict(self))


@dataclass(frozen=True)
class EvidencePromptPlan:
    intent_id: str
    package_id: str
    prompt_specification_identity: str
    missingness_reason_codes: tuple[str, ...]
    schema_version: str = "1"

    @property
    def plan_id(self) -> str:
        return _identity("evidence-prompt-plan", asdict(self))


@dataclass(frozen=True)
class EvidencePromptArtifact:
    plan_id: str
    ledger_id: str
    allocation_id: str
    package_id: str
    prompt_specification_identity: str
    compiler_version: str
    canonicalization_version: str
    encoding: str
    content: str
    content_hash: str
    measured_size_bytes: int
    schema_version: str = "1"

    @property
    def artifact_id(self) -> str:
        payload = asdict(self)
        payload.pop("content")
        return _identity("evidence-prompt-artifact", payload)


@dataclass(frozen=True)
class EvidencePreModelBuildRecord:
    execution_owner_id: str
    intent_id: str
    ledger_id: str
    allocation_id: str | None
    package_id: str | None
    prompt_plan_id: str | None
    prompt_artifact_id: str | None
    reconstruction_outcome: ReconstructionOutcome
    reason_codes: tuple[str, ...]
    schema_version: str = "1"

    @property
    def build_record_id(self) -> str:
        return _identity("evidence-pre-model-build", asdict(self))


@dataclass(frozen=True)
class EvidencePreModelBuildResult:
    ledger: EvidenceContextSelectionLedger
    allocation: EvidenceContextAllocationResult
    package: EvidenceContextPackage | None
    prompt_plan: EvidencePromptPlan | None
    prompt_artifact: EvidencePromptArtifact | None
    build_record: EvidencePreModelBuildRecord


def _validate_candidate_set(
    canonical_inventory: tuple[EvidenceSpan, ...],
    candidate_span_ids: tuple[str, ...],
) -> None:
    canonical_ids = tuple(sorted(span.span_id for span in canonical_inventory))
    supplied_ids = tuple(sorted(candidate_span_ids))
    if canonical_ids != supplied_ids:
        raise PreModelInvariantError("CANDIDATE_SET_MISMATCH")


def _build_ledger(
    *,
    intent: EvidenceExtractionIntent,
    policy: EvidenceContextSelectionPolicy,
    canonical_inventory: tuple[EvidenceSpan, ...],
) -> EvidenceContextSelectionLedger:
    required = set(policy.required_span_ids)
    optional = set(policy.optional_span_ids)
    policy_ids = required.union(optional)
    inventory_ids = {span.span_id for span in canonical_inventory}
    if policy_ids != inventory_ids:
        raise PreModelInvariantError("POLICY_COVERAGE_MISMATCH")

    decisions: list[EvidenceContextDecision] = []
    for span in sorted(canonical_inventory, key=lambda item: item.span_id):
        is_required = span.span_id in required
        resolved = span.span_status in {"active", "historical_only"}
        if resolved:
            status: ResolutionStatus = "RESOLVED"
            selected = True
            reason = "SELECTED_REQUIRED" if is_required else "SELECTED_OPTIONAL"
        else:
            status = "UNRESOLVED_REQUIRED" if is_required else "UNRESOLVED_OPTIONAL"
            selected = False
            reason = status
        decisions.append(
            EvidenceContextDecision(
                span_id=span.span_id,
                required=is_required,
                resolution_status=status,
                selected=selected,
                reason_code=reason,
                content_hash=span.text_hash,
                start_offset=span.start_offset,
                end_offset=span.end_offset,
            )
        )
    return EvidenceContextSelectionLedger(
        intent_id=intent.intent_id,
        policy_identity=policy.policy_identity,
        decisions=tuple(decisions),
    )


def _render_prompt(
    *,
    intent: EvidenceExtractionIntent,
    specification: EvidencePromptSpecification,
    spans: tuple[EvidenceSpan, ...],
    missingness_reason_codes: tuple[str, ...],
) -> str:
    context_blocks = []
    for span in spans:
        context_blocks.append(
            "\n".join(
                (
                    f"<evidence span_id={json.dumps(span.span_id)} ",
                    f"content_hash={json.dumps(span.text_hash)}>",
                    span.excerpt,
                    "</evidence>",
                )
            )
        )
    missingness = ",".join(missingness_reason_codes) if missingness_reason_codes else "NONE"
    sections = (
        f"SYSTEM\n{specification.trusted_system_constraints}",
        f"TASK\n{specification.task_instruction}\nOBJECTIVE\n{intent.objective}",
        "AUTHORITY\nMaximum output authority: ExtractionProposal. No canonical promotion is authorized.",
        f"OUTPUT_CONTRACT\n{specification.output_contract}",
        "CONTEXT\n" + "\n\n".join(context_blocks),
        f"MISSINGNESS\n{missingness}",
    )
    return "\n\n".join(sections).replace("\r\n", "\n").replace("\r", "\n")


def build_evidence_pre_model(
    *,
    execution_owner_id: str,
    intent: EvidenceExtractionIntent,
    policy: EvidenceContextSelectionPolicy,
    specification: EvidencePromptSpecification,
    capability: EvidenceCapabilityConstraint,
    canonical_inventory: tuple[EvidenceSpan, ...],
    candidate_span_ids: tuple[str, ...],
    retain_exact_prompt: bool = True,
    forced_final_size_delta: int = 0,
) -> EvidencePreModelBuildResult:
    """Build a deterministic provider-free Evidence Intelligence pre-model record.

    `forced_final_size_delta` exists only as an invariant-test seam. Production callers
    must leave it at zero.
    """

    _validate_candidate_set(canonical_inventory, candidate_span_ids)
    ledger = _build_ledger(intent=intent, policy=policy, canonical_inventory=canonical_inventory)
    spans_by_id = {span.span_id: span for span in canonical_inventory}

    required_unresolved = tuple(
        decision.reason_code
        for decision in ledger.decisions
        if decision.resolution_status == "UNRESOLVED_REQUIRED"
    )
    optional_unresolved = tuple(
        decision.reason_code
        for decision in ledger.decisions
        if decision.resolution_status == "UNRESOLVED_OPTIONAL"
    )
    selected_required = [
        decision.span_id for decision in ledger.decisions if decision.selected and decision.required
    ]
    selected_optional = [
        decision.span_id for decision in ledger.decisions if decision.selected and not decision.required
    ]

    if required_unresolved:
        allocation = EvidenceContextAllocationResult(
            ledger_id=ledger.ledger_id,
            capability_identity=capability.constraint_identity,
            prompt_specification_identity=specification.specification_identity,
            outcome="REPLAN_REQUIRED",
            included_span_ids=(),
            budget_excluded_span_ids=(),
            preflight_size_bytes=None,
            available_input_bytes=capability.available_input_bytes,
            reason_codes=tuple(sorted(set(required_unresolved))),
        )
        build = EvidencePreModelBuildRecord(
            execution_owner_id=execution_owner_id,
            intent_id=intent.intent_id,
            ledger_id=ledger.ledger_id,
            allocation_id=allocation.allocation_id,
            package_id=None,
            prompt_plan_id=None,
            prompt_artifact_id=None,
            reconstruction_outcome="UNAVAILABLE",
            reason_codes=allocation.reason_codes,
        )
        return EvidencePreModelBuildResult(ledger, allocation, None, None, None, build)

    included = selected_required + selected_optional
    excluded: list[str] = []
    missingness = list(optional_unresolved)

    def preflight(span_ids: list[str]) -> int:
        prompt = _render_prompt(
            intent=intent,
            specification=specification,
            spans=tuple(spans_by_id[span_id] for span_id in span_ids),
            missingness_reason_codes=tuple(sorted(missingness + ["BUDGET_EXCLUDED"] * len(excluded))),
        )
        return len(prompt.encode("utf-8"))

    size = preflight(included)
    while size > capability.available_input_bytes and selected_optional:
        removed = selected_optional.pop()
        included.remove(removed)
        excluded.append(removed)
        size = preflight(included)

    if size > capability.available_input_bytes:
        allocation = EvidenceContextAllocationResult(
            ledger_id=ledger.ledger_id,
            capability_identity=capability.constraint_identity,
            prompt_specification_identity=specification.specification_identity,
            outcome="INSUFFICIENT_BUDGET",
            included_span_ids=tuple(included),
            budget_excluded_span_ids=tuple(sorted(excluded)),
            preflight_size_bytes=size,
            available_input_bytes=capability.available_input_bytes,
            reason_codes=("INSUFFICIENT_BUDGET",),
        )
        build = EvidencePreModelBuildRecord(
            execution_owner_id=execution_owner_id,
            intent_id=intent.intent_id,
            ledger_id=ledger.ledger_id,
            allocation_id=allocation.allocation_id,
            package_id=None,
            prompt_plan_id=None,
            prompt_artifact_id=None,
            reconstruction_outcome="UNAVAILABLE",
            reason_codes=allocation.reason_codes,
        )
        return EvidencePreModelBuildResult(ledger, allocation, None, None, None, build)

    allocation_reasons = tuple(["BUDGET_EXCLUDED"] if excluded else [])
    allocation = EvidenceContextAllocationResult(
        ledger_id=ledger.ledger_id,
        capability_identity=capability.constraint_identity,
        prompt_specification_identity=specification.specification_identity,
        outcome="READY",
        included_span_ids=tuple(included),
        budget_excluded_span_ids=tuple(sorted(excluded)),
        preflight_size_bytes=size,
        available_input_bytes=capability.available_input_bytes,
        reason_codes=allocation_reasons,
    )
    package = EvidenceContextPackage(
        allocation_id=allocation.allocation_id,
        ordered_span_ids=tuple(included),
        ordered_content_hashes=tuple(spans_by_id[span_id].text_hash for span_id in included),
    )
    prompt_plan = EvidencePromptPlan(
        intent_id=intent.intent_id,
        package_id=package.package_id,
        prompt_specification_identity=specification.specification_identity,
        missingness_reason_codes=tuple(
            sorted(missingness + (["BUDGET_EXCLUDED"] if excluded else []))
        ),
    )
    content = _render_prompt(
        intent=intent,
        specification=specification,
        spans=tuple(spans_by_id[span_id] for span_id in included),
        missingness_reason_codes=prompt_plan.missingness_reason_codes,
    )
    final_size = len(content.encode("utf-8")) + forced_final_size_delta
    if final_size != allocation.preflight_size_bytes:
        raise PreModelInvariantError("PROMPT_PREFLIGHT_SIZE_MISMATCH")

    artifact_content = content if retain_exact_prompt else ""
    artifact = EvidencePromptArtifact(
        plan_id=prompt_plan.plan_id,
        ledger_id=ledger.ledger_id,
        allocation_id=allocation.allocation_id,
        package_id=package.package_id,
        prompt_specification_identity=specification.specification_identity,
        compiler_version=specification.compiler_version,
        canonicalization_version="utf8-lf-v1",
        encoding="utf-8",
        content=artifact_content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        measured_size_bytes=final_size,
    )
    reconstruction: ReconstructionOutcome = "AVAILABLE" if retain_exact_prompt else "UNAVAILABLE"
    reasons = tuple(
        sorted(
            set(
                allocation.reason_codes
                + optional_unresolved
                + (() if retain_exact_prompt else ("EXACT_PROMPT_RETENTION_PROHIBITED",))
            )
        )
    )
    build = EvidencePreModelBuildRecord(
        execution_owner_id=execution_owner_id,
        intent_id=intent.intent_id,
        ledger_id=ledger.ledger_id,
        allocation_id=allocation.allocation_id,
        package_id=package.package_id,
        prompt_plan_id=prompt_plan.plan_id,
        prompt_artifact_id=artifact.artifact_id,
        reconstruction_outcome=reconstruction,
        reason_codes=reasons,
    )
    return EvidencePreModelBuildResult(ledger, allocation, package, prompt_plan, artifact, build)
