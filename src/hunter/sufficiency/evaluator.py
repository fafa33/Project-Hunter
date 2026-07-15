from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from hunter.sufficiency.identity import sufficiency_id
from hunter.sufficiency.models import (
    DataAvailability,
    DataRequirement,
    DataSufficiencyClaimLink,
    DataSufficiencyConflictLink,
    DataSufficiencyEvidenceLink,
    DataSufficiencySpanLink,
    Directness,
    ProxySignalType,
    ReplayMode,
    SourceQualityState,
)
from hunter.sufficiency.repository import DataSufficiencyRepository

TRUSTED_IDENTITY_STATES = frozenset({"exact", "probable", "identified", "trusted", "screenable", "analyzable"})
ACTIVE_LIFECYCLE_STATES = frozenset({"active", "accepted", "verified", "current", "available"})
SOURCE_QUALITY_RANK: dict[str, int] = {
    "unavailable": 0,
    "low": 1,
    "medium": 2,
    "verified_or_persisted_hunter_evidence": 2,
    "high": 3,
}


@dataclass(frozen=True)
class CandidateTrustState:
    candidate_id: str
    identity_state: str
    trusted: bool
    effective_at: datetime
    recorded_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        _required("candidate_id", self.candidate_id)
        _required("identity_state", self.identity_state)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)

    def eligible(self) -> bool:
        return self.trusted and self.identity_state in TRUSTED_IDENTITY_STATES


@dataclass(frozen=True)
class ProviderAvailabilityState:
    source_type: str
    status: str
    checked_at: datetime
    recorded_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        _required("source_type", self.source_type)
        _required("status", self.status)
        _aware("checked_at", self.checked_at)
        _aware("recorded_at", self.recorded_at)

    def unavailable(self) -> bool:
        return self.status in {"unavailable", "rate_limited", "forbidden", "failed", "stale"}


@dataclass(frozen=True)
class SourceObservation:
    observation_id: str
    candidate_id: str
    source_type: str
    evidence_domain: str
    directness: Directness
    effective_at: datetime
    recorded_at: datetime
    confidence: float
    source_quality: SourceQualityState
    lineage_depth: int
    lifecycle_state: str
    proxy_type: ProxySignalType | None = None
    conflict_state: str = "none"
    evidence_id: str | None = None
    span_id: str | None = None
    claim_id: str | None = None
    conflict_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("observation_id", "candidate_id", "source_type", "evidence_domain", "source_quality"):
            _required(name, getattr(self, name))
        if self.directness == "proxy_signal" and self.proxy_type is None:
            msg = "proxy observations require proxy_type"
            raise ValueError(msg)
        if self.directness != "proxy_signal" and self.proxy_type is not None:
            msg = "proxy_type is only valid for proxy observations"
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0 and 1"
            raise ValueError(msg)
        if self.lineage_depth < 0:
            msg = "lineage_depth must be non-negative"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)

    def active(self) -> bool:
        return self.lifecycle_state in ACTIVE_LIFECYCLE_STATES


@dataclass(frozen=True)
class AvailabilityEvaluationContext:
    candidate_id: str
    candidate_trust: CandidateTrustState | None
    observations: tuple[SourceObservation, ...] = ()
    provider_states: tuple[ProviderAvailabilityState, ...] = ()
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    cutoff_at: datetime | None = None
    replay_mode: ReplayMode = "current"
    processing_run_id: str = "data-sufficiency-availability-evaluation"

    def __post_init__(self) -> None:
        _required("candidate_id", self.candidate_id)
        _required("processing_run_id", self.processing_run_id)
        _aware("evaluated_at", self.evaluated_at)
        _aware("cutoff_at", self.cutoff_at)


@dataclass(frozen=True)
class AvailabilityEvaluationResult:
    availability: DataAvailability
    evidence_links: tuple[DataSufficiencyEvidenceLink, ...] = ()
    span_links: tuple[DataSufficiencySpanLink, ...] = ()
    claim_links: tuple[DataSufficiencyClaimLink, ...] = ()
    conflict_links: tuple[DataSufficiencyConflictLink, ...] = ()


@dataclass(frozen=True)
class RequirementSelection:
    requirements: tuple[DataRequirement, ...]
    candidate_ids: tuple[str, ...]
    checkpoint_cursor: str | None = None


class DataRequirementSelector:
    def __init__(self, registry_requirements: tuple[DataRequirement, ...] = ()) -> None:
        self.registry_requirements = registry_requirements

    def select(
        self,
        *,
        engine_id: str | None = None,
        analysis_purpose: str | None = None,
        output_field: str | None = None,
        candidate_ids: tuple[str, ...],
        checkpoint_cursor: str | None = None,
    ) -> RequirementSelection:
        if not candidate_ids:
            msg = "candidate_ids are required for availability evaluation"
            raise ValueError(msg)
        requirements = tuple(
            requirement
            for requirement in self.registry_requirements
            if (engine_id is None or requirement.engine_id == engine_id)
            and (analysis_purpose is None or requirement.analysis_purpose == analysis_purpose)
            and (output_field is None or requirement.output_field == output_field)
        )
        return RequirementSelection(
            requirements=requirements,
            candidate_ids=tuple(dict.fromkeys(candidate_ids)),
            checkpoint_cursor=checkpoint_cursor,
        )

    @classmethod
    def from_repository(cls, repository: DataSufficiencyRepository) -> RepositoryBackedRequirementSelector:
        return RepositoryBackedRequirementSelector(repository)


class RepositoryBackedRequirementSelector:
    def __init__(self, repository: DataSufficiencyRepository) -> None:
        self.repository = repository

    def select(
        self,
        *,
        engine_id: str | None = None,
        analysis_purpose: str | None = None,
        output_field: str | None = None,
        candidate_ids: tuple[str, ...],
        checkpoint_processor: str | None = None,
        checkpoint_target: str | None = None,
    ) -> RequirementSelection:
        if not candidate_ids:
            msg = "candidate_ids are required for availability evaluation"
            raise ValueError(msg)
        checkpoint = None
        if checkpoint_processor is not None and checkpoint_target is not None:
            checkpoint = self.repository.checkpoint(checkpoint_processor, checkpoint_target)
        rows = self.repository.requirements(
            engine_id=engine_id,
            analysis_purpose=analysis_purpose,
            output_field=output_field,
        )
        return RequirementSelection(
            requirements=tuple(
                _requirement_from_row(
                    row,
                    required_source_types=self.repository.requirement_source_types(
                        str(row["requirement_id"]), str(row["schema_version"])
                    ),
                    accepted_proxy_types=self.repository.requirement_proxy_types(
                        str(row["requirement_id"]), str(row["schema_version"])
                    ),
                )
                for row in rows
            ),
            candidate_ids=tuple(dict.fromkeys(candidate_ids)),
            checkpoint_cursor=str(checkpoint["cursor"]) if checkpoint else None,
        )


@dataclass(frozen=True)
class AvailabilityEvaluationPolicy:
    allow_proxy_for_direct_requirement: bool = False


class DataAvailabilityEvaluator:
    def __init__(self, *, policy: AvailabilityEvaluationPolicy | None = None) -> None:
        self.policy = policy or AvailabilityEvaluationPolicy()

    def evaluate(
        self, requirement: DataRequirement, context: AvailabilityEvaluationContext
    ) -> AvailabilityEvaluationResult:
        trust = _eligible_trust(context)
        if trust is None:
            return _result(
                requirement,
                context,
                state="unavailable",
                directness="unavailable",
                missing_reason="candidate_identity_not_trusted",
                observations=(),
            )

        eligible = tuple(
            observation
            for observation in context.observations
            if observation.candidate_id == context.candidate_id
            and observation.source_type in requirement.required_source_types
            and observation.evidence_domain == requirement.evidence_domain
            and observation.active()
            and _visible_at(observation.effective_at, observation.recorded_at, context)
        )
        direct = tuple(
            observation
            for observation in eligible
            if observation.directness in {"direct_observation", "derived_from_direct"}
        )
        proxies = tuple(
            observation
            for observation in eligible
            if observation.directness == "proxy_signal"
            and observation.proxy_type in requirement.accepted_proxy_types
            and requirement.proxy_allowed
        )

        if requirement.direct_observation_required and not direct:
            if proxies and self.policy.allow_proxy_for_direct_requirement:
                return _result(
                    requirement,
                    context,
                    state="partial",
                    directness="proxy_signal",
                    proxy_type=proxies[0].proxy_type,
                    missing_reason="proxy_signal_cannot_fully_satisfy_direct_observation",
                    observations=proxies,
                )
            return _result(
                requirement,
                context,
                state="unavailable",
                directness="unavailable",
                missing_reason=_missing_reason(requirement, context),
                observations=proxies,
            )

        satisfying = direct or proxies
        if not satisfying:
            return _result(
                requirement,
                context,
                state="unavailable",
                directness="unavailable",
                missing_reason=_missing_reason(requirement, context),
                observations=eligible,
            )

        stale = tuple(
            observation
            for observation in satisfying
            if _freshness_seconds(observation, context) > requirement.minimum_freshness_seconds
        )
        if len(stale) == len(satisfying):
            return _result(
                requirement,
                context,
                state="stale",
                directness=_dominant_directness(satisfying),
                proxy_type=_dominant_proxy_type(satisfying),
                missing_reason="required_data_stale",
                observations=satisfying,
            )

        missing_sources = set(requirement.required_source_types) - {
            observation.source_type for observation in satisfying
        }
        incomplete_lineage = any(
            observation.lineage_depth < requirement.minimum_lineage_depth for observation in satisfying
        )
        conflicts = any(observation.conflict_state != "none" for observation in satisfying)
        low_quality = any(
            not _source_quality_satisfies(observation.source_quality, requirement.minimum_source_authority)
            for observation in satisfying
        )
        low_confidence = any(observation.confidence < requirement.minimum_confidence for observation in satisfying)
        if missing_sources or stale or incomplete_lineage or conflicts or low_quality or low_confidence:
            reasons = []
            if missing_sources:
                reasons.append("missing_required_source_types:" + ",".join(sorted(missing_sources)))
            if stale:
                reasons.append("some_required_data_stale")
            if incomplete_lineage:
                reasons.append("lineage_incomplete")
            if conflicts:
                reasons.append("source_conflict_present")
            if low_quality:
                reasons.append("source_authority_below_requirement")
            if low_confidence:
                reasons.append("confidence_below_requirement")
            return _result(
                requirement,
                context,
                state="partial",
                directness=_dominant_directness(satisfying),
                proxy_type=_dominant_proxy_type(satisfying),
                missing_reason=";".join(reasons),
                observations=satisfying,
            )

        return _result(
            requirement,
            context,
            state="available",
            directness=_dominant_directness(satisfying),
            proxy_type=_dominant_proxy_type(satisfying),
            missing_reason="",
            observations=satisfying,
        )

    def evaluate_many(
        self,
        requirements: tuple[DataRequirement, ...],
        contexts: tuple[AvailabilityEvaluationContext, ...],
    ) -> tuple[AvailabilityEvaluationResult, ...]:
        return tuple(self.evaluate(requirement, context) for context in contexts for requirement in requirements)


def _result(
    requirement: DataRequirement,
    context: AvailabilityEvaluationContext,
    *,
    state: str,
    directness: Directness,
    missing_reason: str,
    observations: tuple[SourceObservation, ...],
    proxy_type: ProxySignalType | None = None,
) -> AvailabilityEvaluationResult:
    availability_id = sufficiency_id(
        "availability",
        {
            "requirement_id": requirement.requirement_id,
            "candidate_id": context.candidate_id,
            "processing_run_id": context.processing_run_id,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at,
        },
    )
    freshness = max((_freshness_seconds(observation, context) for observation in observations), default=None)
    source_quality = _aggregate_source_quality(observations)
    conflict_state = (
        "conflicted" if any(observation.conflict_state != "none" for observation in observations) else "none"
    )
    availability = DataAvailability(
        availability_id=availability_id,
        requirement_id=requirement.requirement_id,
        candidate_id=context.candidate_id,
        engine_id=requirement.engine_id,
        analysis_purpose=requirement.analysis_purpose,
        availability_state=state,
        directness=directness,
        proxy_type=proxy_type,
        freshness_seconds=freshness,
        source_quality=source_quality,
        lineage_complete=all(
            observation.lineage_depth >= requirement.minimum_lineage_depth for observation in observations
        ),
        conflict_state=conflict_state,
        evidence_count=len(observations),
        missing_reason=missing_reason,
        effective_at=context.cutoff_at or context.evaluated_at,
        recorded_at=context.evaluated_at,
        cutoff_at=context.cutoff_at,
        replay_mode=context.replay_mode,
        processing_run_id=context.processing_run_id,
        schema_version=requirement.schema_version,
    )
    return AvailabilityEvaluationResult(
        availability=availability,
        evidence_links=_evidence_links(availability.availability_id, observations, requirement.schema_version),
        span_links=_span_links(availability.availability_id, observations, requirement.schema_version),
        claim_links=_claim_links(availability.availability_id, observations, requirement.schema_version),
        conflict_links=_conflict_links(availability.availability_id, observations, requirement.schema_version),
    )


def _eligible_trust(context: AvailabilityEvaluationContext) -> CandidateTrustState | None:
    trust = context.candidate_trust
    if trust is None or trust.candidate_id != context.candidate_id or not trust.eligible():
        return None
    if not _visible_at(trust.effective_at, trust.recorded_at, context):
        return None
    return trust


def _visible_at(effective_at: datetime, recorded_at: datetime, context: AvailabilityEvaluationContext) -> bool:
    cutoff = context.cutoff_at
    if cutoff is None or context.replay_mode == "current":
        return True
    if effective_at > cutoff:
        return False
    if context.replay_mode == "historical_strict_known_by_hunter" and recorded_at > cutoff:
        return False
    return True


def _freshness_seconds(observation: SourceObservation, context: AvailabilityEvaluationContext) -> int:
    as_of = context.cutoff_at or context.evaluated_at
    return max(int((as_of - observation.effective_at).total_seconds()), 0)


def _source_quality_satisfies(actual: str, minimum: str) -> bool:
    return SOURCE_QUALITY_RANK.get(actual, 0) >= SOURCE_QUALITY_RANK.get(minimum, 2)


def _aggregate_source_quality(observations: tuple[SourceObservation, ...]) -> SourceQualityState:
    if not observations:
        return "unavailable"
    if any(observation.conflict_state != "none" for observation in observations):
        return "conflicted"
    minimum = min(SOURCE_QUALITY_RANK.get(observation.source_quality, 0) for observation in observations)
    if minimum >= 3:
        return "high"
    if minimum == 2:
        return "medium"
    return "low"


def _dominant_directness(observations: tuple[SourceObservation, ...]) -> Directness:
    if any(observation.directness == "direct_observation" for observation in observations):
        return "direct_observation"
    if any(observation.directness == "derived_from_direct" for observation in observations):
        return "derived_from_direct"
    return "proxy_signal"


def _dominant_proxy_type(observations: tuple[SourceObservation, ...]) -> ProxySignalType | None:
    for observation in observations:
        if observation.directness == "proxy_signal":
            return observation.proxy_type
    return None


def _missing_reason(requirement: DataRequirement, context: AvailabilityEvaluationContext) -> str:
    unavailable = tuple(
        state
        for state in context.provider_states
        if state.source_type in requirement.required_source_types
        and state.unavailable()
        and _visible_at(state.checked_at, state.recorded_at, context)
    )
    if unavailable:
        return "provider_unavailable:" + ",".join(sorted(state.source_type for state in unavailable))
    return "missing_required_source_types:" + ",".join(sorted(requirement.required_source_types))


def _evidence_links(
    availability_id: str, observations: tuple[SourceObservation, ...], schema_version: str
) -> tuple[DataSufficiencyEvidenceLink, ...]:
    return tuple(
        DataSufficiencyEvidenceLink(
            link_id=sufficiency_id(
                "availability-evidence-link",
                {"availability_id": availability_id, "evidence_id": observation.evidence_id, "position": position},
            ),
            owner_type="availability",
            owner_id=availability_id,
            source_evidence_id=observation.evidence_id,
            role="supporting_evidence",
            position=position,
            created_at=observation.recorded_at,
            schema_version=schema_version,
        )
        for position, observation in enumerate(observations)
        if observation.evidence_id is not None
    )


def _span_links(
    availability_id: str, observations: tuple[SourceObservation, ...], schema_version: str
) -> tuple[DataSufficiencySpanLink, ...]:
    return tuple(
        DataSufficiencySpanLink(
            link_id=sufficiency_id(
                "availability-span-link",
                {"availability_id": availability_id, "span_id": observation.span_id, "position": position},
            ),
            owner_type="availability",
            owner_id=availability_id,
            span_id=observation.span_id,
            role="supporting_span",
            position=position,
            created_at=observation.recorded_at,
            schema_version=schema_version,
        )
        for position, observation in enumerate(observations)
        if observation.span_id is not None
    )


def _claim_links(
    availability_id: str, observations: tuple[SourceObservation, ...], schema_version: str
) -> tuple[DataSufficiencyClaimLink, ...]:
    return tuple(
        DataSufficiencyClaimLink(
            link_id=sufficiency_id(
                "availability-claim-link",
                {"availability_id": availability_id, "claim_id": observation.claim_id, "position": position},
            ),
            owner_type="availability",
            owner_id=availability_id,
            claim_id=observation.claim_id,
            role="supporting_claim",
            position=position,
            created_at=observation.recorded_at,
            schema_version=schema_version,
        )
        for position, observation in enumerate(observations)
        if observation.claim_id is not None
    )


def _conflict_links(
    availability_id: str, observations: tuple[SourceObservation, ...], schema_version: str
) -> tuple[DataSufficiencyConflictLink, ...]:
    return tuple(
        DataSufficiencyConflictLink(
            link_id=sufficiency_id(
                "availability-conflict-link",
                {"availability_id": availability_id, "conflict_id": observation.conflict_id, "position": position},
            ),
            owner_type="availability",
            owner_id=availability_id,
            conflict_id=observation.conflict_id,
            role="related_conflict",
            position=position,
            created_at=observation.recorded_at,
            schema_version=schema_version,
        )
        for position, observation in enumerate(observations)
        if observation.conflict_id is not None
    )


def _requirement_from_row(
    row: dict[str, Any], *, required_source_types: tuple[str, ...], accepted_proxy_types: tuple[str, ...]
) -> DataRequirement:
    return DataRequirement(
        requirement_id=str(row["requirement_id"]),
        engine_id=str(row["engine_id"]),
        analysis_purpose=str(row["analysis_purpose"]),
        output_field=str(row["output_field"]),
        requirement_kind=str(row["requirement_kind"]),
        evidence_domain=str(row["evidence_domain"]),
        required_entity_type=str(row["required_entity_type"]),
        required_source_types=required_source_types,
        direct_observation_required=bool(row["direct_observation_required"]),
        proxy_allowed=bool(row["proxy_allowed"]),
        accepted_proxy_types=accepted_proxy_types,
        minimum_freshness_seconds=int(row["minimum_freshness_seconds"]),
        minimum_source_authority=str(row["minimum_source_authority"]),
        minimum_lineage_depth=int(row["minimum_lineage_depth"]),
        minimum_confidence=float(row["minimum_confidence"]),
        historical_required=bool(row["historical_required"]),
        blocking_level=str(row["blocking_level"]),
        policy_id=str(row["policy_id"]),
        policy_version=str(row["policy_version"]),
        effective_at=datetime.fromisoformat(str(row["effective_at"])),
        recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
        schema_version=str(row["schema_version"]),
        metadata={},
    )


def _required(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
