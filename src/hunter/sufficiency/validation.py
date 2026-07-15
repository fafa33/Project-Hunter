from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations

from hunter.sufficiency.evaluator import ProviderAvailabilityState, SourceObservation
from hunter.sufficiency.identity import sufficiency_id
from hunter.sufficiency.models import (
    DataRequirement,
    DataSufficiencyClaimLink,
    DataSufficiencyConflictLink,
    DataSufficiencyEvidenceLink,
    DataSufficiencySpanLink,
    FreshnessState,
    ReplayMode,
    SourceDisagreement,
    SourceQualityState,
    SourceValidationResult,
)

SOURCE_QUALITY_RANK: dict[str, int] = {
    "unavailable": 0,
    "low": 1,
    "medium": 2,
    "verified_or_persisted_hunter_evidence": 2,
    "high": 3,
}


@dataclass(frozen=True)
class SourceMetricContext:
    metric_name: str
    unit: str
    scope: str
    chain: str
    product: str
    period_start: datetime
    period_end: datetime

    def __post_init__(self) -> None:
        for name in ("metric_name", "unit", "scope", "chain", "product"):
            _required(name, getattr(self, name))
        _aware("period_start", self.period_start)
        _aware("period_end", self.period_end)
        if self.period_end < self.period_start:
            msg = "period_end must be on or after period_start"
            raise ValueError(msg)


@dataclass(frozen=True)
class ComparableSourceObservation:
    observation: SourceObservation
    metric_context: SourceMetricContext
    value: str | int | float | bool

    def __post_init__(self) -> None:
        if self.value is None:
            msg = "value is required"
            raise ValueError(msg)


@dataclass(frozen=True)
class SourceCompatibilityResult:
    compatible: bool
    reason: str


@dataclass(frozen=True)
class CrossSourceValidationContext:
    candidate_id: str
    observations: tuple[ComparableSourceObservation, ...] = ()
    provider_states: tuple[ProviderAvailabilityState, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    cutoff_at: datetime | None = None
    replay_mode: ReplayMode = "current"
    processing_run_id: str = "data-sufficiency-cross-source-validation"

    def __post_init__(self) -> None:
        _required("candidate_id", self.candidate_id)
        _required("processing_run_id", self.processing_run_id)
        _aware("evaluated_at", self.evaluated_at)
        _aware("cutoff_at", self.cutoff_at)


@dataclass(frozen=True)
class CrossSourceValidationRecord:
    validation: SourceValidationResult
    evidence_links: tuple[DataSufficiencyEvidenceLink, ...] = ()
    span_links: tuple[DataSufficiencySpanLink, ...] = ()
    claim_links: tuple[DataSufficiencyClaimLink, ...] = ()
    conflict_links: tuple[DataSufficiencyConflictLink, ...] = ()


@dataclass(frozen=True)
class CrossSourceDisagreementRecord:
    disagreement: SourceDisagreement
    evidence_links: tuple[DataSufficiencyEvidenceLink, ...] = ()
    span_links: tuple[DataSufficiencySpanLink, ...] = ()
    claim_links: tuple[DataSufficiencyClaimLink, ...] = ()
    conflict_links: tuple[DataSufficiencyConflictLink, ...] = ()


@dataclass(frozen=True)
class CrossSourceValidationOutput:
    validations: tuple[CrossSourceValidationRecord, ...] = ()
    disagreements: tuple[CrossSourceDisagreementRecord, ...] = ()


class SourceCompatibilityPolicy:
    def compare(
        self,
        left: ComparableSourceObservation,
        right: ComparableSourceObservation,
    ) -> SourceCompatibilityResult:
        left_context = left.metric_context
        right_context = right.metric_context
        fields = ("metric_name", "unit", "scope", "chain", "product", "period_start", "period_end")
        mismatched = tuple(field for field in fields if getattr(left_context, field) != getattr(right_context, field))
        if mismatched:
            return SourceCompatibilityResult(False, "incompatible_" + ",".join(mismatched))
        return SourceCompatibilityResult(True, "compatible")


class CrossSourceValidationService:
    def __init__(self, *, compatibility_policy: SourceCompatibilityPolicy | None = None) -> None:
        self.compatibility_policy = compatibility_policy or SourceCompatibilityPolicy()

    def validate(
        self,
        requirement: DataRequirement,
        context: CrossSourceValidationContext,
    ) -> CrossSourceValidationOutput:
        eligible = _eligible_observations(requirement, context)
        if not eligible:
            return CrossSourceValidationOutput(
                validations=(
                    _validation_record(
                        requirement,
                        context,
                        source_a=_source_label(requirement),
                        source_b="missing",
                        status=_missing_status(requirement, context),
                        compatible_scope=False,
                        source_authority_state="unavailable",
                        freshness_state="unavailable",
                        reason=_missing_reason(requirement, context),
                        observations=(),
                    ),
                )
            )

        conflicted = tuple(item for item in eligible if item.observation.conflict_state != "none")
        if conflicted:
            return CrossSourceValidationOutput(
                validations=(
                    _validation_record(
                        requirement,
                        context,
                        source_a=_source_label_for_observations(conflicted),
                        source_b="conflict",
                        status="conflict",
                        compatible_scope=True,
                        source_authority_state=_aggregate_source_quality(conflicted),
                        freshness_state=_freshness_state(requirement, context, conflicted),
                        reason="source_conflict_present",
                        observations=conflicted,
                    ),
                )
            )

        if len(eligible) < 2:
            return CrossSourceValidationOutput(
                validations=(
                    _validation_record(
                        requirement,
                        context,
                        source_a=eligible[0].observation.source_type,
                        source_b="insufficient_sources",
                        status="unavailable",
                        compatible_scope=True,
                        source_authority_state=_aggregate_source_quality(eligible),
                        freshness_state=_freshness_state(requirement, context, eligible),
                        reason="insufficient_comparable_sources",
                        observations=eligible,
                    ),
                )
            )

        validations: list[CrossSourceValidationRecord] = []
        disagreements: list[CrossSourceDisagreementRecord] = []
        for left, right in combinations(eligible, 2):
            compatibility = self.compatibility_policy.compare(left, right)
            if not compatibility.compatible:
                validations.append(
                    _validation_record(
                        requirement,
                        context,
                        source_a=left.observation.source_type,
                        source_b=right.observation.source_type,
                        status="incompatible_scope",
                        compatible_scope=False,
                        source_authority_state=_aggregate_source_quality((left, right)),
                        freshness_state=_freshness_state(requirement, context, (left, right)),
                        reason=compatibility.reason,
                        observations=(left, right),
                    )
                )
                continue

            if _canonical_value(left.value) == _canonical_value(right.value):
                validations.append(
                    _validation_record(
                        requirement,
                        context,
                        source_a=left.observation.source_type,
                        source_b=right.observation.source_type,
                        status="agreement",
                        compatible_scope=True,
                        source_authority_state=_aggregate_source_quality((left, right)),
                        freshness_state=_freshness_state(requirement, context, (left, right)),
                        reason="compatible_sources_agree",
                        observations=(left, right),
                    )
                )
                continue

            validation = _validation_record(
                requirement,
                context,
                source_a=left.observation.source_type,
                source_b=right.observation.source_type,
                status="disagreement",
                compatible_scope=True,
                source_authority_state=_aggregate_source_quality((left, right)),
                freshness_state=_freshness_state(requirement, context, (left, right)),
                reason="compatible_sources_disagree",
                observations=(left, right),
            )
            disagreement = _disagreement_record(requirement, context, (left, right))
            validations.append(validation)
            disagreements.append(disagreement)
        return CrossSourceValidationOutput(validations=tuple(validations), disagreements=tuple(disagreements))


def _eligible_observations(
    requirement: DataRequirement,
    context: CrossSourceValidationContext,
) -> tuple[ComparableSourceObservation, ...]:
    matching = tuple(
        item
        for item in context.observations
        if item.observation.candidate_id == context.candidate_id
        and item.observation.source_type in requirement.required_source_types
        and item.observation.evidence_domain == requirement.evidence_domain
        and item.observation.active()
        and _visible_at(item.observation.effective_at, item.observation.recorded_at, context)
    )
    if requirement.direct_observation_required:
        matching = tuple(item for item in matching if item.observation.directness == "direct_observation")
    else:
        matching = tuple(
            item
            for item in matching
            if item.observation.directness != "proxy_signal"
            or (requirement.proxy_allowed and item.observation.proxy_type in requirement.accepted_proxy_types)
        )
    return tuple(
        item
        for item in matching
        if _source_quality_satisfies(item.observation.source_quality, requirement.minimum_source_authority)
        and item.observation.confidence >= requirement.minimum_confidence
        and item.observation.lineage_depth >= requirement.minimum_lineage_depth
        and _freshness_seconds(item.observation, context) <= requirement.minimum_freshness_seconds
    )


def _validation_record(
    requirement: DataRequirement,
    context: CrossSourceValidationContext,
    *,
    source_a: str,
    source_b: str,
    status: str,
    compatible_scope: bool,
    source_authority_state: SourceQualityState,
    freshness_state: FreshnessState,
    reason: str,
    observations: tuple[ComparableSourceObservation, ...],
) -> CrossSourceValidationRecord:
    validation_id = sufficiency_id(
        "source-validation",
        {
            "candidate_id": context.candidate_id,
            "requirement_id": requirement.requirement_id,
            "source_a": source_a,
            "source_b": source_b,
            "status": status,
            "reason": reason,
            "processing_run_id": context.processing_run_id,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at,
        },
    )
    validation = SourceValidationResult(
        validation_id=validation_id,
        candidate_id=context.candidate_id,
        requirement_id=requirement.requirement_id,
        engine_id=requirement.engine_id,
        analysis_purpose=requirement.analysis_purpose,
        source_a=source_a,
        source_b=source_b,
        validation_status=status,
        compatible_scope=compatible_scope,
        source_authority_state=source_authority_state,
        freshness_state=freshness_state,
        reason=reason,
        effective_at=context.cutoff_at or context.evaluated_at,
        recorded_at=context.evaluated_at,
        cutoff_at=context.cutoff_at,
        replay_mode=context.replay_mode,
        processing_run_id=context.processing_run_id,
        schema_version=requirement.schema_version,
        metadata={"project_negative_evidence": False},
    )
    return CrossSourceValidationRecord(
        validation=validation,
        evidence_links=_evidence_links("validation", validation_id, observations, requirement.schema_version),
        span_links=_span_links("validation", validation_id, observations, requirement.schema_version),
        claim_links=_claim_links("validation", validation_id, observations, requirement.schema_version),
        conflict_links=_conflict_links("validation", validation_id, observations, requirement.schema_version),
    )


def _disagreement_record(
    requirement: DataRequirement,
    context: CrossSourceValidationContext,
    observations: tuple[ComparableSourceObservation, ComparableSourceObservation],
) -> CrossSourceDisagreementRecord:
    source_ids = tuple(sorted(item.observation.source_type for item in observations))
    disagreement_id = sufficiency_id(
        "source-disagreement",
        {
            "candidate_id": context.candidate_id,
            "requirement_id": requirement.requirement_id,
            "source_ids": source_ids,
            "metric": observations[0].metric_context.metric_name,
            "values": tuple(_canonical_value(item.value) for item in observations),
            "processing_run_id": context.processing_run_id,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at,
        },
    )
    disagreement = SourceDisagreement(
        disagreement_id=disagreement_id,
        candidate_id=context.candidate_id,
        requirement_id=requirement.requirement_id,
        engine_id=requirement.engine_id,
        analysis_purpose=requirement.analysis_purpose,
        disagreement_state="disagreement",
        compared_source_count=len(observations),
        compatible_scope=True,
        reason="data_quality_state:compatible_sources_disagree",
        effective_at=context.cutoff_at or context.evaluated_at,
        recorded_at=context.evaluated_at,
        replay_mode=context.replay_mode,
        processing_run_id=context.processing_run_id,
        schema_version=requirement.schema_version,
    )
    return CrossSourceDisagreementRecord(
        disagreement=disagreement,
        evidence_links=_evidence_links("disagreement", disagreement_id, observations, requirement.schema_version),
        span_links=_span_links("disagreement", disagreement_id, observations, requirement.schema_version),
        claim_links=_claim_links("disagreement", disagreement_id, observations, requirement.schema_version),
        conflict_links=_conflict_links("disagreement", disagreement_id, observations, requirement.schema_version),
    )


def _missing_status(requirement: DataRequirement, context: CrossSourceValidationContext) -> str:
    provider_missing = any(
        state.source_type in requirement.required_source_types
        and state.unavailable()
        and _visible_at(state.checked_at, state.recorded_at, context)
        for state in context.provider_states
    )
    if provider_missing:
        return "missing_provider"
    stale = any(
        item.observation.candidate_id == context.candidate_id
        and item.observation.source_type in requirement.required_source_types
        and item.observation.evidence_domain == requirement.evidence_domain
        and item.observation.active()
        and _visible_at(item.observation.effective_at, item.observation.recorded_at, context)
        and _freshness_seconds(item.observation, context) > requirement.minimum_freshness_seconds
        for item in context.observations
    )
    if stale:
        return "stale_source"
    return "unavailable"


def _missing_reason(requirement: DataRequirement, context: CrossSourceValidationContext) -> str:
    unavailable = tuple(
        state.source_type
        for state in context.provider_states
        if state.source_type in requirement.required_source_types
        and state.unavailable()
        and _visible_at(state.checked_at, state.recorded_at, context)
    )
    if unavailable:
        return "provider_unavailable:" + ",".join(sorted(unavailable))
    has_proxy = any(
        item.observation.candidate_id == context.candidate_id
        and item.observation.source_type in requirement.required_source_types
        and item.observation.directness == "proxy_signal"
        and _visible_at(item.observation.effective_at, item.observation.recorded_at, context)
        for item in context.observations
    )
    if requirement.direct_observation_required and has_proxy:
        return "direct_observation_missing_proxy_only"
    return "missing_comparable_source_observations"


def _visible_at(effective_at: datetime, recorded_at: datetime, context: CrossSourceValidationContext) -> bool:
    cutoff = context.cutoff_at
    if cutoff is None or context.replay_mode == "current":
        return True
    if effective_at > cutoff:
        return False
    if context.replay_mode == "historical_strict_known_by_hunter" and recorded_at > cutoff:
        return False
    return True


def _freshness_seconds(observation: SourceObservation, context: CrossSourceValidationContext) -> int:
    as_of = context.cutoff_at or context.evaluated_at
    return max(int((as_of - observation.effective_at).total_seconds()), 0)


def _freshness_state(
    requirement: DataRequirement,
    context: CrossSourceValidationContext,
    observations: tuple[ComparableSourceObservation, ...],
) -> FreshnessState:
    if not observations:
        return "unavailable"
    if any(
        _freshness_seconds(item.observation, context) > requirement.minimum_freshness_seconds for item in observations
    ):
        return "stale"
    return "fresh"


def _source_quality_satisfies(actual: str, minimum: str) -> bool:
    return SOURCE_QUALITY_RANK.get(actual, 0) >= SOURCE_QUALITY_RANK.get(minimum, 2)


def _aggregate_source_quality(observations: tuple[ComparableSourceObservation, ...]) -> SourceQualityState:
    if not observations:
        return "unavailable"
    if any(item.observation.conflict_state != "none" for item in observations):
        return "conflicted"
    minimum = min(SOURCE_QUALITY_RANK.get(item.observation.source_quality, 0) for item in observations)
    if minimum >= 3:
        return "high"
    if minimum == 2:
        return "medium"
    return "low"


def _evidence_links(
    owner_type: str,
    owner_id: str,
    observations: tuple[ComparableSourceObservation, ...],
    schema_version: str,
) -> tuple[DataSufficiencyEvidenceLink, ...]:
    return tuple(
        DataSufficiencyEvidenceLink(
            link_id=sufficiency_id(
                f"{owner_type}-evidence-link",
                {"owner_id": owner_id, "evidence_id": item.observation.evidence_id, "position": position},
            ),
            owner_type=owner_type,
            owner_id=owner_id,
            source_evidence_id=item.observation.evidence_id,
            role="compared_source_observation",
            position=position,
            created_at=item.observation.recorded_at,
            schema_version=schema_version,
        )
        for position, item in enumerate(observations)
        if item.observation.evidence_id is not None
    )


def _span_links(
    owner_type: str,
    owner_id: str,
    observations: tuple[ComparableSourceObservation, ...],
    schema_version: str,
) -> tuple[DataSufficiencySpanLink, ...]:
    return tuple(
        DataSufficiencySpanLink(
            link_id=sufficiency_id(
                f"{owner_type}-span-link",
                {"owner_id": owner_id, "span_id": item.observation.span_id, "position": position},
            ),
            owner_type=owner_type,
            owner_id=owner_id,
            span_id=item.observation.span_id,
            role="compared_source_span",
            position=position,
            created_at=item.observation.recorded_at,
            schema_version=schema_version,
        )
        for position, item in enumerate(observations)
        if item.observation.span_id is not None
    )


def _claim_links(
    owner_type: str,
    owner_id: str,
    observations: tuple[ComparableSourceObservation, ...],
    schema_version: str,
) -> tuple[DataSufficiencyClaimLink, ...]:
    return tuple(
        DataSufficiencyClaimLink(
            link_id=sufficiency_id(
                f"{owner_type}-claim-link",
                {"owner_id": owner_id, "claim_id": item.observation.claim_id, "position": position},
            ),
            owner_type=owner_type,
            owner_id=owner_id,
            claim_id=item.observation.claim_id,
            role="compared_source_claim",
            position=position,
            created_at=item.observation.recorded_at,
            schema_version=schema_version,
        )
        for position, item in enumerate(observations)
        if item.observation.claim_id is not None
    )


def _conflict_links(
    owner_type: str,
    owner_id: str,
    observations: tuple[ComparableSourceObservation, ...],
    schema_version: str,
) -> tuple[DataSufficiencyConflictLink, ...]:
    return tuple(
        DataSufficiencyConflictLink(
            link_id=sufficiency_id(
                f"{owner_type}-conflict-link",
                {"owner_id": owner_id, "conflict_id": item.observation.conflict_id, "position": position},
            ),
            owner_type=owner_type,
            owner_id=owner_id,
            conflict_id=item.observation.conflict_id,
            role="related_conflict",
            position=position,
            created_at=item.observation.recorded_at,
            schema_version=schema_version,
        )
        for position, item in enumerate(observations)
        if item.observation.conflict_id is not None
    )


def _canonical_value(value: str | int | float | bool) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value).strip().lower()


def _source_label(requirement: DataRequirement) -> str:
    return ",".join(requirement.required_source_types)


def _source_label_for_observations(observations: tuple[ComparableSourceObservation, ...]) -> str:
    return ",".join(sorted({item.observation.source_type for item in observations}))


def _required(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
