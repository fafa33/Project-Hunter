from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime

from hunter.execution.identity import fingerprint, identity
from hunter.intelligence.fusion.models import FusionTarget
from hunter.opportunity.acceleration import assess_acceleration
from hunter.opportunity.confidence import calculate_confidence
from hunter.opportunity.configuration import OpportunityConfig, OpportunityTimingConfig
from hunter.opportunity.confirmation import assess_confirmation
from hunter.opportunity.divergence import assess_divergence
from hunter.opportunity.exceptions import InsufficientFusionInputError, ReplaySafetyError
from hunter.opportunity.history import compare_history
from hunter.opportunity.metrics import NEGATIVE_FACTORS, OpportunityMetricSnapshot
from hunter.opportunity.models import OpportunityAssessment, OpportunityFactor, OpportunityTimingAssessment
from hunter.opportunity.phases import classify_phase
from hunter.opportunity.risk import assess_risk
from hunter.opportunity.scoring import timing_score
from hunter.opportunity.temporal import analyze_temporal
from hunter.opportunity.windows import classify_window
from hunter.persistence.records import (
    FusedIntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    OpportunityTimingSnapshotRecord,
)

IDENTITY_SCHEMA_VERSION = "opportunity-timing-identity-v1"


class OpportunityTimingEngine:
    def __init__(self, config: OpportunityTimingConfig | None = None) -> None:
        self.config = config or OpportunityTimingConfig()

    def assess(
        self,
        fused_records: Iterable[FusedIntelligenceRecord],
        target: FusionTarget,
        *,
        as_of: datetime | None = None,
        replay: bool = False,
        historical_snapshots: Iterable[OpportunityTimingSnapshotRecord | OpportunityTimingAssessmentRecord] = (),
        config: OpportunityTimingConfig | None = None,
    ) -> OpportunityTimingAssessment:
        active_config = config or self.config
        aligned_records = tuple(
            sorted(
                (record for record in fused_records if _aligned(record, target)),
                key=lambda item: (item.effective_at, item.id),
            )
        )
        if not aligned_records:
            raise InsufficientFusionInputError("Opportunity Timing requires persisted FusedIntelligence for the target")
        if replay and as_of is None:
            raise ReplaySafetyError("Replay and backtest Opportunity Timing calls require explicit as_of")
        effective_as_of = _as_of(as_of, aligned_records)
        records = tuple(record for record in aligned_records if record.effective_at <= effective_as_of)
        if not records:
            raise InsufficientFusionInputError("Opportunity Timing requires FusedIntelligence at or before as_of")
        current = records[-1]
        configuration_fingerprint = fingerprint("opportunity-timing-configuration", asdict(active_config))
        model_fingerprint = _model_fingerprint(active_config)
        historical_window = _historical_window(records)
        temporal = analyze_temporal(records, required_depth=active_config.required_historical_depth)
        confirmation = assess_confirmation(records, active_config)
        acceleration = assess_acceleration(records, active_config)
        divergence = assess_divergence(records, active_config)
        risk = assess_risk(records, confirmation, divergence, temporal)
        score = timing_score(records, temporal, confirmation, acceleration, divergence, risk, active_config)
        confidence = calculate_confidence(records, temporal, confirmation, risk, active_config, as_of=effective_as_of)
        phase = classify_phase(score, confirmation, acceleration, risk, temporal, active_config)
        window = classify_window(score, phase, risk, temporal, active_config)
        history = compare_history(tuple(historical_snapshots), as_of=effective_as_of)
        source_ids = tuple(record.id for record in records)
        source_run_ids = tuple(sorted({run_id for record in records for run_id in record.source_run_ids}))
        assessment_id = identity(
            "opportunity-timing-assessment",
            {
                "target": target,
                "effective_at": current.effective_at,
                "as_of": effective_as_of,
                "source_fused_intelligence_ids": source_ids,
                "configuration_fingerprint": configuration_fingerprint,
                "model_fingerprint": model_fingerprint,
                "historical_window": historical_window,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )
        missing = tuple(
            sorted(
                {
                    str(item)
                    for record in records
                    for item in record.missing_evidence.get("missing_categories", ()) or ()
                }
            )
        )
        contradictions = tuple(
            sorted(
                {
                    str(item)
                    for record in records
                    for item in record.contradictions.get("contradicted_categories", ()) or ()
                }
            )
        )
        supporting = _supporting_factors(confirmation, acceleration, temporal)
        opposing = _opposing_factors(risk, divergence, temporal)
        return OpportunityTimingAssessment(
            assessment_id=assessment_id,
            target=target,
            effective_at=current.effective_at,
            source_fused_intelligence_ids=source_ids,
            source_run_ids=source_run_ids,
            opportunity_phase=phase,
            opportunity_window=window,
            timing_score=score,
            confidence=confidence,
            evidence_quality=_evidence_quality(records),
            confirmation_state=confirmation,
            acceleration_state=acceleration,
            divergence_state=divergence,
            risk_state=risk,
            expected_horizon=_expected_horizon(score, temporal.historical_depth, acceleration.state, active_config),
            supporting_factors=supporting,
            opposing_factors=opposing,
            contradictions=contradictions,
            missing_evidence=missing,
            invalidation_conditions=_invalidation_conditions(
                phase, confirmation, risk, divergence, missing, active_config
            ),
            canonical_evidence_refs=_canonical_refs(records),
            historical_comparisons=history,
            metadata={
                "configuration_fingerprint": configuration_fingerprint,
                "model_fingerprint": model_fingerprint,
                "historical_window": "|".join(historical_window),
                "as_of": effective_as_of.isoformat(),
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "source_fused_count": len(records),
            },
        )


class OpportunityEngine:
    def __init__(self, config: OpportunityConfig | None = None) -> None:
        self.config = config or OpportunityConfig()

    def evaluate(
        self,
        snapshot: OpportunityMetricSnapshot,
        *,
        config: OpportunityConfig | None = None,
    ) -> OpportunityAssessment:
        active_config = config or self.config
        values = snapshot.values.as_dict()
        weighted_factors = _weighted_factors(values, active_config)
        positive_total = sum(item.contribution for item in weighted_factors if item.name not in NEGATIVE_FACTORS)
        risk_penalty = values.get("risk", 0.0) * active_config.risk_weight
        missing_value = max(
            values.get("missing_evidence", 0.0),
            len(snapshot.missing_evidence) / max(1, len(dict(active_config.factor_weights))),
        )
        missing_penalty = missing_value * active_config.missing_evidence_weight
        validation_health = values.get("validation_health", 1.0)
        validation_penalty = (1.0 - validation_health) * active_config.validation_gate_weight
        opportunity_score = _clamp01(positive_total - risk_penalty - missing_penalty - validation_penalty)
        conviction_score = _conviction(values, opportunity_score, missing_value)
        contributors = tuple(
            sorted(
                (item for item in weighted_factors if item.contribution > 0),
                key=lambda item: (-item.contribution, item.name),
            )[:5]
        )
        risks = _risk_factors(values, active_config, missing_value, validation_health)
        assessment_id = identity(
            "opportunity-assessment",
            {
                "project_id": snapshot.project_id,
                "effective_at": snapshot.effective_at,
                "values": values,
                "evidence_ids": snapshot.evidence_ids,
                "missing_evidence": snapshot.missing_evidence,
                "configuration_fingerprint": fingerprint("opportunity-configuration", asdict(active_config)),
                "identity_schema_version": "opportunity-entry-v1",
            },
        )
        return OpportunityAssessment(
            assessment_id=assessment_id,
            project_id=snapshot.project_id,
            effective_at=snapshot.effective_at,
            opportunity_score=opportunity_score,
            opportunity_label=_threshold_label(opportunity_score, active_config.label_thresholds),
            conviction_score=conviction_score,
            conviction_explanation=_conviction_explanation(conviction_score, values, missing_value),
            risk_reward_balance=_risk_reward(values.get("risk", 0.0), opportunity_score),
            opportunity_window=_threshold_label(opportunity_score, active_config.window_thresholds_entry),
            positive_factors=tuple(item.name for item in contributors),
            negative_factors=tuple(item.name for item in risks),
            largest_contributors=contributors,
            largest_risks=risks,
            supporting_evidence=snapshot.evidence_ids,
            missing_evidence=snapshot.missing_evidence,
            confidence={
                "score": conviction_score,
                "evidence_completeness": _clamp01(1.0 - missing_value),
                "evidence_freshness": values.get("evidence_freshness", 0.0),
                "input_confidence": values.get("confidence", 0.0),
                "backtesting_quality": values.get("backtesting_quality", 0.0),
            },
            metadata={
                "configuration_fingerprint": fingerprint("opportunity-configuration", asdict(active_config)),
                "identity_schema_version": "opportunity-entry-v1",
            },
        )


def opportunity_factor_trace(
    snapshot: OpportunityMetricSnapshot,
    config: OpportunityConfig | None = None,
) -> tuple[OpportunityFactor, ...]:
    """Return the exact pure factor contributions used by OpportunityEngine."""

    active_config = config or OpportunityConfig()
    values = snapshot.values.as_dict()
    missing_value = max(
        values.get("missing_evidence", 0.0),
        len(snapshot.missing_evidence) / max(1, len(dict(active_config.factor_weights))),
    )
    validation_health = values.get("validation_health", 1.0)
    return (
        *_weighted_factors(values, active_config),
        *_risk_factors(values, active_config, missing_value, validation_health),
    )


def _weighted_factors(values: dict[str, float], config: OpportunityConfig) -> tuple[OpportunityFactor, ...]:
    factors: list[OpportunityFactor] = []
    for name, weight in config.factor_weights:
        value = _clamp01(values.get(name, 0.0))
        factors.append(
            OpportunityFactor(
                name=name,
                value=value,
                weight=weight,
                contribution=round(value * weight, 4),
                evidence_id=None,
                explanation=f"{name} contributes {round(value * weight, 4)}",
            )
        )
    return tuple(factors)


def _risk_factors(
    values: dict[str, float],
    config: OpportunityConfig,
    missing_value: float,
    validation_health: float,
) -> tuple[OpportunityFactor, ...]:
    risks = (
        OpportunityFactor(
            "risk", _clamp01(values.get("risk", 0.0)), config.risk_weight, -values.get("risk", 0.0) * config.risk_weight
        ),
        OpportunityFactor(
            "missing_evidence",
            _clamp01(missing_value),
            config.missing_evidence_weight,
            -missing_value * config.missing_evidence_weight,
        ),
        OpportunityFactor(
            "validation_health",
            _clamp01(validation_health),
            config.validation_gate_weight,
            -(1.0 - validation_health) * config.validation_gate_weight,
        ),
    )
    return tuple(sorted(risks, key=lambda item: (item.contribution, item.name)))


def _conviction(values: dict[str, float], opportunity_score: float, missing_value: float) -> float:
    return _clamp01(
        opportunity_score * 0.45
        + values.get("confidence", 0.0) * 0.2
        + values.get("evidence_freshness", 0.0) * 0.12
        + values.get("backtesting_quality", 0.0) * 0.13
        + (1.0 - missing_value) * 0.1
    )


def _conviction_explanation(conviction_score: float, values: dict[str, float], missing_value: float) -> str:
    if missing_value > 0.4:
        return "Conviction is constrained by missing evidence."
    if values.get("confidence", 0.0) < 0.5:
        return "Conviction is constrained by low source confidence."
    if conviction_score >= 0.75:
        return (
            "Conviction is high because opportunity score, confidence, freshness, and backtesting support are aligned."
        )
    if conviction_score >= 0.5:
        return "Conviction is moderate because support is present but not fully confirmed."
    return "Conviction is low because evidence support is limited."


def _risk_reward(risk: float, opportunity_score: float) -> str:
    if risk >= 0.75:
        return "Extreme"
    if risk >= 0.55:
        return "High"
    if risk >= 0.3 or opportunity_score < 0.45:
        return "Moderate"
    return "Low"


def _threshold_label(score: float, thresholds: tuple[tuple[str, float], ...]) -> str:
    for label, threshold in thresholds:
        if score <= threshold:
            return label
    return thresholds[-1][0]


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def opportunity_assessment_to_record(
    assessment: OpportunityTimingAssessment,
    *,
    pipeline_run_id: str,
    created_at: datetime,
) -> OpportunityTimingAssessmentRecord:
    return OpportunityTimingAssessmentRecord(
        id=assessment.assessment_id,
        created_at=created_at,
        effective_at=assessment.effective_at,
        pipeline_run_id=pipeline_run_id,
        target_id=assessment.target.target_id,
        target_type=assessment.target.target_type,
        source_fused_intelligence_ids=assessment.source_fused_intelligence_ids,
        source_run_ids=assessment.source_run_ids,
        configuration_fingerprint=str(assessment.metadata.get("configuration_fingerprint") or ""),
        model_fingerprint=str(assessment.metadata.get("model_fingerprint") or ""),
        historical_window=(
            tuple(str(assessment.metadata.get("historical_window") or "").split("|"))
            if assessment.metadata.get("historical_window")
            else ()
        ),
        opportunity_phase=assessment.opportunity_phase,
        opportunity_window=assessment.opportunity_window,
        timing_score=assessment.timing_score,
        confidence=assessment.confidence.as_dict(),
        evidence_quality=assessment.evidence_quality,
        confirmation_state=asdict(assessment.confirmation_state),
        acceleration_state=asdict(assessment.acceleration_state),
        divergence_state=asdict(assessment.divergence_state),
        risk_state=asdict(assessment.risk_state),
        expected_horizon=assessment.expected_horizon,
        supporting_factors=assessment.supporting_factors,
        opposing_factors=assessment.opposing_factors,
        contradictions=assessment.contradictions,
        missing_evidence=assessment.missing_evidence,
        invalidation_conditions=assessment.invalidation_conditions,
        historical_comparisons=tuple(asdict(item) for item in assessment.historical_comparisons),
        canonical_evidence_refs=assessment.canonical_evidence_refs,
        metadata=assessment.metadata.as_dict(),
    )


def opportunity_snapshot_from_assessment(
    assessment: OpportunityTimingAssessment,
    *,
    created_at: datetime,
) -> OpportunityTimingSnapshotRecord:
    snapshot_id = identity(
        "opportunity-timing-snapshot",
        {
            "assessment_id": assessment.assessment_id,
            "target": assessment.target,
            "effective_at": assessment.effective_at,
            "phase": assessment.opportunity_phase,
            "window": assessment.opportunity_window,
        },
    )
    return OpportunityTimingSnapshotRecord(
        id=snapshot_id,
        created_at=created_at,
        effective_at=assessment.effective_at,
        target_id=assessment.target.target_id,
        target_type=assessment.target.target_type,
        assessment_id=assessment.assessment_id,
        opportunity_phase=assessment.opportunity_phase,
        opportunity_window=assessment.opportunity_window,
        timing_score=assessment.timing_score,
        confidence=assessment.confidence.as_dict(),
        source_fused_intelligence_ids=assessment.source_fused_intelligence_ids,
        source_run_ids=assessment.source_run_ids,
        metadata=assessment.metadata.as_dict(),
    )


def _aligned(record: FusedIntelligenceRecord, target: FusionTarget) -> bool:
    return record.target_id == target.target_id and record.target_type == target.target_type


def _as_of(as_of: datetime | None, records: tuple[FusedIntelligenceRecord, ...]) -> datetime:
    if as_of is None:
        return records[-1].effective_at
    if as_of.tzinfo is None:
        msg = "as_of must be timezone-aware"
        raise ReplaySafetyError(msg)
    return as_of.astimezone(UTC)


def _model_fingerprint(config: OpportunityTimingConfig) -> str:
    return fingerprint(
        "opportunity-timing-model",
        {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "configuration": asdict(config),
            "phase_classifier": "config-threshold-upper-bound-v1",
            "window_classifier": "config-threshold-upper-bound-v1",
            "confirmation": "category-coverage-independent-canonical-evidence-v1",
            "acceleration": "three-point-delta-v1",
            "divergence": "category-pattern-v1",
            "risk": "deterministic-risk-v1",
            "freshness": "as-of-effective-window-decay-v1",
            "invalidation": "configured-rule-set-v1",
        },
    )


def _historical_window(records: tuple[FusedIntelligenceRecord, ...]) -> tuple[str, str]:
    return (records[0].effective_at.isoformat(), records[-1].effective_at.isoformat())


def _evidence_quality(records: tuple[FusedIntelligenceRecord, ...]) -> float:
    counts = [len(record.canonical_evidence_groups) for record in records]
    return round(min(1.0, sum(counts) / max(1, len(counts) * 4)), 4)


def _supporting_factors(confirmation: object, acceleration: object, temporal: object) -> tuple[str, ...]:
    factors: list[str] = []
    if getattr(confirmation, "confirmed", False):
        factors.append("independent_confirmation")
    if getattr(acceleration, "state", "") == "positive_acceleration":
        factors.append("positive_acceleration")
    if getattr(temporal, "persistence", 0.0) >= 0.6:
        factors.append("persistent_change")
    return tuple(factors or ("limited_supporting_evidence",))


def _opposing_factors(risk: object, divergence: object, temporal: object) -> tuple[str, ...]:
    factors = list(getattr(risk, "risks", ()))
    factors.extend(getattr(divergence, "divergences", ()))
    if getattr(temporal, "deterioration", False):
        factors.append("deterioration")
    return tuple(sorted(set(factors))) or ("no_material_opposing_factor",)


def _invalidation_conditions(
    phase: str,
    confirmation: object,
    risk: object,
    divergence: object,
    missing: tuple[str, ...],
    config: OpportunityTimingConfig,
) -> tuple[str, ...]:
    allowed = set(config.invalidation_rules)
    conditions = [
        "loss_of_independent_confirmation",
        "material_increase_in_contradiction_severity",
        "sustained_negative_acceleration",
    ]
    if missing:
        conditions.append("continued_absence_of_required_evidence")
    if getattr(risk, "score", 0.0) > 0.4:
        conditions.append("risk_state_worsens")
    if getattr(divergence, "severity", 0.0) > 0:
        conditions.append("divergence_remains_unresolved")
    if phase in {"confirmed_entry", "expansion"} and not getattr(confirmation, "confirmed", False):
        conditions.append("confirmation_threshold_not_maintained")
    return tuple(sorted(set(conditions).intersection(allowed)))


def _expected_horizon(score: float, depth: int, acceleration: str, config: OpportunityTimingConfig) -> str:
    if depth < 2:
        return "indeterminate"
    rules = dict(config.horizon_rules)
    if acceleration == "positive_acceleration" and score >= rules.get("weeks", 75.0):
        return "weeks"
    if score >= rules.get("1-3 months", 75.0):
        return "1-3 months"
    if score >= rules.get("3-6 months", 60.0):
        return "3-6 months"
    if score >= rules.get("6-12 months", 40.0):
        return "6-12 months"
    if score >= rules.get("12-24 months", 20.0):
        return "12-24 months"
    return "indeterminate"


def _canonical_refs(records: tuple[FusedIntelligenceRecord, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(group.get("canonical_key", ""))
                for record in records
                for group in record.canonical_evidence_groups
                if group.get("canonical_key")
            }
        )
    )
