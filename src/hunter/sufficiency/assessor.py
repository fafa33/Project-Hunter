from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from hunter.sufficiency.identity import sufficiency_id
from hunter.sufficiency.models import (
    ConflictState,
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessment,
    DegradedModeOutcome,
    FreshnessState,
    LineageState,
    ReplayMode,
    SourceDisagreement,
    SourceQualityState,
    SufficiencyState,
)
from hunter.sufficiency.policies import DegradedModeDecision, DegradedModePolicy

SOURCE_QUALITY_RANK: dict[str, int] = {
    "unavailable": 0,
    "low": 1,
    "medium": 2,
    "verified_or_persisted_hunter_evidence": 2,
    "high": 3,
    "conflicted": 0,
}


@dataclass(frozen=True)
class SufficiencyAssessmentContext:
    candidate_id: str
    assessment_scope: str = "candidate_report"
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    cutoff_at: datetime | None = None
    replay_mode: ReplayMode = "current"
    processing_run_id: str = "data-sufficiency-assessment"

    def __post_init__(self) -> None:
        _required("candidate_id", self.candidate_id)
        _required("assessment_scope", self.assessment_scope)
        _required("processing_run_id", self.processing_run_id)
        _aware("evaluated_at", self.evaluated_at)
        _aware("cutoff_at", self.cutoff_at)


@dataclass(frozen=True)
class RequirementDegradedModeDecision:
    requirement: DataRequirement
    availability: DataAvailability | None
    decision: DegradedModeDecision


@dataclass(frozen=True)
class SufficiencyAssessmentResult:
    assessment: DataSufficiencyAssessment
    requirement_decisions: tuple[RequirementDegradedModeDecision, ...]
    missing_requirements: tuple[str, ...]
    supportable_conclusions: tuple[str, ...]
    unsupported_conclusions: tuple[str, ...]
    limitations: tuple[str, ...]


class DegradedModePolicyEngine:
    def __init__(self, policy: DegradedModePolicy) -> None:
        self.policy = policy

    def decide(
        self,
        *,
        requirement: DataRequirement,
        availability: DataAvailability | None,
    ) -> RequirementDegradedModeDecision:
        if availability is None:
            decision = DegradedModeDecision(
                outcome=(
                    self.policy.unavailable_required_outcome
                    if _material(requirement)
                    else self.policy.optional_missing_outcome
                ),
                reason="required_data_unavailable",
                blocks_output=_material(requirement)
                and self.policy.unavailable_required_outcome == "blocked_insufficient_evidence",
            )
            return RequirementDegradedModeDecision(requirement, None, decision)
        return RequirementDegradedModeDecision(
            requirement=requirement,
            availability=availability,
            decision=self.policy.decide(requirement=requirement, availability=availability),
        )


class DataSufficiencyAssessor:
    def __init__(self, *, policy_engine: DegradedModePolicyEngine) -> None:
        self.policy_engine = policy_engine

    def assess(
        self,
        *,
        requirements: tuple[DataRequirement, ...],
        availabilities: tuple[DataAvailability, ...],
        disagreements: tuple[SourceDisagreement, ...] = (),
        context: SufficiencyAssessmentContext,
    ) -> SufficiencyAssessmentResult:
        if not requirements:
            msg = "requirements are required for sufficiency assessment"
            raise ValueError(msg)
        engine_id = _single_value("engine_id", tuple(requirement.engine_id for requirement in requirements))
        analysis_purpose = _single_value(
            "analysis_purpose", tuple(requirement.analysis_purpose for requirement in requirements)
        )
        availability_by_requirement = {
            availability.requirement_id: availability
            for availability in availabilities
            if availability.candidate_id == context.candidate_id
            and availability.engine_id == engine_id
            and availability.analysis_purpose == analysis_purpose
            and _visible_at(availability.effective_at, availability.recorded_at, context)
        }
        applicable_disagreements = tuple(
            disagreement
            for disagreement in disagreements
            if disagreement.candidate_id == context.candidate_id
            and disagreement.engine_id == engine_id
            and disagreement.analysis_purpose == analysis_purpose
            and _visible_at(disagreement.effective_at, disagreement.recorded_at, context)
        )
        decisions = tuple(
            self.policy_engine.decide(
                requirement=requirement,
                availability=availability_by_requirement.get(requirement.requirement_id),
            )
            for requirement in requirements
        )
        missing = _missing_requirements(decisions)
        supportable = _supportable_conclusions(decisions)
        unsupported = _unsupported_conclusions(decisions)
        limitations = _limitations(decisions, applicable_disagreements, context.replay_mode)
        assessment = DataSufficiencyAssessment(
            assessment_id=sufficiency_id(
                "assessment",
                {
                    "candidate_id": context.candidate_id,
                    "engine_id": engine_id,
                    "analysis_purpose": analysis_purpose,
                    "assessment_scope": context.assessment_scope,
                    "processing_run_id": context.processing_run_id,
                    "replay_mode": context.replay_mode,
                    "cutoff_at": context.cutoff_at,
                },
            ),
            candidate_id=context.candidate_id,
            engine_id=engine_id,
            analysis_purpose=analysis_purpose,
            assessment_scope=context.assessment_scope,
            sufficiency_state=_sufficiency_state(decisions, applicable_disagreements),
            degraded_mode=_degraded_mode(decisions),
            coverage_score=_coverage_score(decisions),
            freshness_state=_freshness_state(decisions),
            source_quality_state=_source_quality_state(decisions, applicable_disagreements),
            lineage_state=_lineage_state(decisions),
            conflict_state=_conflict_state(decisions, applicable_disagreements),
            direct_observation_coverage=_direct_observation_coverage(decisions),
            proxy_signal_coverage=_proxy_signal_coverage(decisions),
            material_missing_count=len(missing),
            limitations_summary="; ".join(limitations),
            policy_id=self.policy_engine.policy.policy_id,
            policy_version=self.policy_engine.policy.policy_version,
            effective_at=context.cutoff_at or context.evaluated_at,
            recorded_at=context.evaluated_at,
            cutoff_at=context.cutoff_at,
            replay_mode=context.replay_mode,
            processing_run_id=context.processing_run_id,
            schema_version=requirements[0].schema_version,
            metadata={
                "missing_requirements": missing,
                "supportable_conclusions": supportable,
                "unsupported_conclusions": unsupported,
                "disagreement_ids": tuple(disagreement.disagreement_id for disagreement in applicable_disagreements),
                "preserves_score": True,
                "treats_missing_as_negative": False,
                "report_field_gating": {
                    requirement.output_field: _field_gate(decision)
                    for requirement, decision in ((item.requirement, item) for item in decisions)
                },
            },
        )
        return SufficiencyAssessmentResult(
            assessment=assessment,
            requirement_decisions=decisions,
            missing_requirements=missing,
            supportable_conclusions=supportable,
            unsupported_conclusions=unsupported,
            limitations=limitations,
        )


def _sufficiency_state(
    decisions: tuple[RequirementDegradedModeDecision, ...],
    disagreements: tuple[SourceDisagreement, ...],
) -> SufficiencyState:
    if all(
        decision.availability is None or decision.availability.availability_state == "unavailable"
        for decision in decisions
    ):
        return "unavailable"
    if any(decision.decision.blocks_output for decision in decisions):
        return "insufficient"
    if any(decision.decision.outcome == "degraded_material_limitation" for decision in decisions):
        return "degraded"
    if disagreements or any(decision.decision.outcome != "normal" for decision in decisions):
        return "sufficient_with_limitations"
    return "sufficient"


def _degraded_mode(decisions: tuple[RequirementDegradedModeDecision, ...]) -> DegradedModeOutcome:
    outcomes = tuple(decision.decision.outcome for decision in decisions)
    if "blocked_insufficient_evidence" in outcomes:
        return "blocked_insufficient_evidence"
    if "unavailable" in outcomes:
        return "unavailable"
    if "degraded_material_limitation" in outcomes:
        return "degraded_material_limitation"
    if "degraded_non_blocking" in outcomes:
        return "degraded_non_blocking"
    return "normal"


def _coverage_score(decisions: tuple[RequirementDegradedModeDecision, ...]) -> float:
    available = sum(
        1
        for decision in decisions
        if decision.availability is not None and decision.availability.availability_state == "available"
    )
    return available / len(decisions)


def _direct_observation_coverage(decisions: tuple[RequirementDegradedModeDecision, ...]) -> float:
    direct_required = tuple(decision for decision in decisions if decision.requirement.direct_observation_required)
    if not direct_required:
        return 1.0
    direct_available = sum(
        1
        for decision in direct_required
        if decision.availability is not None
        and decision.availability.directness == "direct_observation"
        and decision.availability.availability_state == "available"
    )
    return direct_available / len(direct_required)


def _proxy_signal_coverage(decisions: tuple[RequirementDegradedModeDecision, ...]) -> float:
    proxy_allowed = tuple(decision for decision in decisions if decision.requirement.proxy_allowed)
    if not proxy_allowed:
        return 0.0
    proxy_available = sum(
        1
        for decision in proxy_allowed
        if decision.availability is not None and decision.availability.directness == "proxy_signal"
    )
    return proxy_available / len(proxy_allowed)


def _freshness_state(decisions: tuple[RequirementDegradedModeDecision, ...]) -> FreshnessState:
    if any(
        decision.availability is not None and decision.availability.availability_state == "stale"
        for decision in decisions
    ):
        return "stale"
    if any(
        decision.availability is None or decision.availability.availability_state == "unavailable"
        for decision in decisions
    ):
        return "unavailable"
    return "fresh"


def _source_quality_state(
    decisions: tuple[RequirementDegradedModeDecision, ...],
    disagreements: tuple[SourceDisagreement, ...],
) -> SourceQualityState:
    if disagreements:
        return "conflicted"
    qualities = tuple(
        decision.availability.source_quality for decision in decisions if decision.availability is not None
    )
    if not qualities:
        return "unavailable"
    minimum = min(SOURCE_QUALITY_RANK.get(quality, 0) for quality in qualities)
    if minimum >= 3:
        return "high"
    if minimum == 2:
        return "medium"
    if minimum == 1:
        return "low"
    return "unavailable"


def _lineage_state(decisions: tuple[RequirementDegradedModeDecision, ...]) -> LineageState:
    if any(decision.availability is None for decision in decisions):
        return "missing"
    if any(not decision.availability.lineage_complete for decision in decisions if decision.availability is not None):
        return "partial"
    return "complete"


def _conflict_state(
    decisions: tuple[RequirementDegradedModeDecision, ...],
    disagreements: tuple[SourceDisagreement, ...],
) -> ConflictState:
    if disagreements:
        return "disputed"
    if any(
        decision.availability is not None and decision.availability.conflict_state == "conflicted"
        for decision in decisions
    ):
        return "conflicted"
    if any(
        decision.availability is not None and decision.availability.conflict_state == "disputed"
        for decision in decisions
    ):
        return "disputed"
    if any(decision.availability is None for decision in decisions):
        return "unavailable"
    return "none"


def _missing_requirements(decisions: tuple[RequirementDegradedModeDecision, ...]) -> tuple[str, ...]:
    return tuple(
        decision.requirement.output_field
        for decision in decisions
        if decision.availability is None or decision.availability.availability_state == "unavailable"
    )


def _supportable_conclusions(decisions: tuple[RequirementDegradedModeDecision, ...]) -> tuple[str, ...]:
    return tuple(
        decision.requirement.output_field
        for decision in decisions
        if decision.availability is not None and not decision.decision.blocks_output
    )


def _unsupported_conclusions(decisions: tuple[RequirementDegradedModeDecision, ...]) -> tuple[str, ...]:
    return tuple(
        decision.requirement.output_field
        for decision in decisions
        if decision.decision.blocks_output or decision.availability is None
    )


def _limitations(
    decisions: tuple[RequirementDegradedModeDecision, ...],
    disagreements: tuple[SourceDisagreement, ...],
    replay_mode: ReplayMode,
) -> tuple[str, ...]:
    values: list[str] = []
    for decision in decisions:
        availability = decision.availability
        if availability is None:
            values.append(f"{decision.requirement.output_field}: required data unavailable")
            continue
        if availability.availability_state in {"unavailable", "stale", "partial"}:
            values.append(f"{decision.requirement.output_field}: {availability.missing_reason}")
        if availability.directness == "proxy_signal":
            values.append(f"{decision.requirement.output_field}: proxy signal only")
    if disagreements:
        values.append("cross-source disagreement is data-quality metadata, not project-negative evidence")
    if replay_mode == "historical_strict_known_by_hunter":
        values.append("strict historical assessment uses only records known by Hunter at cutoff")
    if replay_mode == "reconstructed_after_cutoff":
        values.append("reconstructed assessment uses later-recorded records and is not known-at-cutoff")
    return tuple(dict.fromkeys(values))


def _field_gate(decision: RequirementDegradedModeDecision) -> dict[str, object]:
    availability = decision.availability
    return {
        "supportable": availability is not None and not decision.decision.blocks_output,
        "outcome": decision.decision.outcome,
        "reason": decision.decision.reason,
        "directness": availability.directness if availability is not None else "unavailable",
        "availability_state": availability.availability_state if availability is not None else "unavailable",
        "preserves_score": decision.decision.preserves_score,
        "treats_missing_as_negative": decision.decision.treats_missing_as_negative,
    }


def _visible_at(effective_at: datetime, recorded_at: datetime, context: SufficiencyAssessmentContext) -> bool:
    cutoff = context.cutoff_at
    if cutoff is None or context.replay_mode == "current":
        return True
    if effective_at > cutoff:
        return False
    if context.replay_mode == "historical_strict_known_by_hunter" and recorded_at > cutoff:
        return False
    return True


def _material(requirement: DataRequirement) -> bool:
    return requirement.blocking_level in {
        "required_for_output",
        "required_for_high_confidence",
        "required_for_full_report",
    }


def _single_value(name: str, values: tuple[str, ...]) -> str:
    unique = tuple(dict.fromkeys(values))
    if len(unique) != 1:
        msg = f"requirements must share one {name}"
        raise ValueError(msg)
    return unique[0]


def _required(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
