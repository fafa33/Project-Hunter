from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

from hunter.committee.configuration import InvestmentCommitteeConfig
from hunter.committee.metrics import approval_score, conflict_score, consensus_score, evidence_robustness
from hunter.committee.models import (
    CommitteeDecision,
    CommitteeInputSet,
    CommitteeVote,
    CycleChampionSnapshot,
    EligibilityState,
    InvestmentCommitteeAssessment,
)
from hunter.execution.identity import identity


class InvestmentCommitteeEngine:
    def __init__(self, config: InvestmentCommitteeConfig | None = None) -> None:
        self.config = config or InvestmentCommitteeConfig()

    def evaluate(
        self, inputs: CommitteeInputSet, previous: InvestmentCommitteeAssessment | None = None
    ) -> InvestmentCommitteeAssessment:
        votes = _votes(inputs, self.config)
        eligibility = _eligibility(inputs, votes, self.config)
        missing_ratio = sum(1 for vote in votes if vote.vote == "ABSTAIN_MISSING") / max(1, len(votes))
        stale_ratio = sum(1 for vote in votes if vote.vote == "ABSTAIN_STALE") / max(1, len(votes))
        approve = approval_score(votes)
        oppose = conflict_opposition = conflict_score(votes) / 2.0
        consensus = consensus_score(votes)
        conflict = conflict_score(votes)
        robustness = evidence_robustness(votes, missing_ratio, stale_ratio)
        confidence = _clamp((approve + consensus + robustness) / 3.0 - conflict / 3.0)
        fragility = _clamp((conflict + oppose + missing_ratio + stale_ratio) / 4.0)
        decision = _decision(eligibility, confidence, consensus, conflict, approve, self.config)
        assessment_id = identity(
            "investment-committee-assessment",
            {
                "project_id": inputs.project_id,
                "effective_at": inputs.effective_at,
                "votes": tuple(
                    (vote.engine_name, vote.vote, vote.source_score, vote.source_confidence) for vote in votes
                ),
                "source_record_ids": _source_ids(inputs),
                "schema": "investment-committee-v1",
            },
        )
        votes = tuple(replace(vote, assessment_id=assessment_id) for vote in votes)
        positives = tuple(vote.engine_name for vote in votes if vote.vote in {"STRONG_APPROVE", "APPROVE"})
        negatives = tuple(vote.engine_name for vote in votes if vote.vote in {"STRONG_OPPOSE", "OPPOSE"})
        abstentions = tuple(vote.engine_name for vote in votes if vote.vote.startswith("ABSTAIN"))
        change = _change(previous, decision, confidence, consensus, conflict)
        return InvestmentCommitteeAssessment(
            id=assessment_id,
            project_id=inputs.project_id,
            created_at=inputs.effective_at,
            eligibility_state=eligibility,
            decision=decision,
            approval_score=approve,
            opposition_score=conflict_opposition,
            consensus_score=consensus,
            conflict_score=conflict,
            evidence_robustness=robustness,
            committee_confidence=confidence,
            thesis_fragility=fragility,
            rank=0,
            votes=votes,
            positive_drivers=positives[: self.config.maximum_displayed_contributors],
            negative_drivers=negatives[: self.config.maximum_displayed_contributors],
            conflicts=negatives,
            abstentions=abstentions,
            risks=_risks(inputs, votes),
            invalidation_conditions=_invalidation_conditions(self.config),
            runner_up_comparison="not evaluated in single-project mode",
            explanation=(
                f"eligibility={eligibility}",
                f"decision={decision}",
                f"approval={approve:.4f}",
                f"consensus={consensus:.4f}",
                *change,
            ),
            source_record_ids=_source_ids(inputs),
            metadata={"schema": "investment-committee-v1"},
        )

    def select_champion(
        self, inputs: tuple[CommitteeInputSet, ...]
    ) -> tuple[CycleChampionSnapshot, tuple[InvestmentCommitteeAssessment, ...]]:
        assessments = rank_committee_assessments(tuple(self.evaluate(item) for item in inputs))
        winner = assessments[0] if assessments else None
        runner_up = assessments[1] if len(assessments) > 1 else None
        reason = "No qualified candidate"
        selected = None
        decision: CommitteeDecision = "NO_QUALIFIED_CANDIDATE"
        lead = 0.0
        if winner is not None:
            lead = _clamp(winner.committee_confidence - (runner_up.committee_confidence if runner_up else 0.0))
            if _winner_ok(winner, lead, self.config):
                selected = winner.project_id
                decision = "HIGHEST_CONVICTION_CANDIDATE"
                reason = "Configured winner conditions satisfied"
            elif runner_up is not None and lead < self.config.winner_minimums.lead_margin:
                reason = "No decisive evidence-backed leader"
        snapshot = CycleChampionSnapshot(
            id=identity(
                "cycle-champion-snapshot", {"selected": selected, "assessments": tuple(item.id for item in assessments)}
            ),
            created_at=(assessments[0].created_at if assessments else datetime.now(UTC)),
            selected_project_id=selected,
            runner_up_project_id=runner_up.project_id if runner_up else None,
            decision=decision,
            committee_confidence=winner.committee_confidence if winner else 0.0,
            consensus_score=winner.consensus_score if winner else 0.0,
            lead_margin=lead,
            selection_reason=reason,
            no_selection_reason=None if selected else reason,
        )
        return snapshot, assessments


def rank_committee_assessments(
    assessments: tuple[InvestmentCommitteeAssessment, ...]
) -> tuple[InvestmentCommitteeAssessment, ...]:
    ordered = sorted(
        assessments,
        key=lambda item: (
            item.eligibility_state not in {"ELIGIBLE", "CONDITIONALLY_ELIGIBLE"},
            -item.committee_confidence,
            -item.consensus_score,
            -item.evidence_robustness,
            item.conflict_score,
            item.project_id,
        ),
    )
    return tuple(replace(item, rank=index) for index, item in enumerate(ordered, start=1))


def _votes(inputs: CommitteeInputSet, config: InvestmentCommitteeConfig) -> tuple[CommitteeVote, ...]:
    weights = dict(config.engine_weights)
    raw = {
        "valuation": _snapshot(inputs, "valuation"),
        "mispricing": _snapshot(inputs, "mispricing_quality"),
        "asymmetry": _snapshot(inputs, "asymmetry"),
        "whale": _engine(inputs, "whale"),
        "macro": _engine(inputs, "macro"),
        "future_demand": _engine(inputs, "future"),
        "opportunity": (
            (
                inputs.opportunity.timing_score / 100.0,
                _avg_conf(inputs.opportunity.confidence),
                inputs.opportunity.effective_at,
                inputs.opportunity.assessment_id,
            )
            if inputs.opportunity
            else None
        ),
        "probability": (
            (
                inputs.probability.probability_score,
                inputs.probability.decision_confidence,
                inputs.probability.effective_at,
                inputs.probability.assessment_id,
            )
            if inputs.probability
            else None
        ),
        "pattern": (
            (
                inputs.pattern.overall_similarity,
                inputs.pattern.historical_confidence,
                inputs.pattern.effective_at,
                inputs.pattern.assessment_id,
            )
            if inputs.pattern
            else None
        ),
        "technology_necessity": (
            (
                inputs.necessity.overall_necessity,
                inputs.necessity.confidence,
                inputs.necessity.effective_at,
                inputs.necessity.assessment_id,
            )
            if inputs.necessity
            else None
        ),
        "capital_rotation": (
            (
                inputs.necessity.capital_rotation_score,
                inputs.necessity.confidence,
                inputs.necessity.effective_at,
                inputs.necessity.assessment_id,
            )
            if inputs.necessity
            else None
        ),
        "validation": _engine(inputs, "validation"),
        "backtesting": _snapshot(inputs, "backtesting_reliability"),
        "risk": (
            1.0 - _snapshot(inputs, "risk")[0],
            _snapshot(inputs, "risk")[1],
            inputs.effective_at,
            _snapshot(inputs, "risk")[3],
        ),
        "evidence_quality": (
            _evidence_quality(inputs),
            _evidence_quality(inputs),
            inputs.effective_at,
            ",".join(record.id for record in inputs.evidence),
        ),
    }
    return tuple(
        _vote(inputs.project_id, name, raw[name], weights.get(name, 0.0), inputs.effective_at, config)
        for name in weights
    )


def _vote(
    project_id: str,
    name: str,
    payload: tuple[float, float, datetime, str] | None,
    weight: float,
    as_of: datetime,
    config: InvestmentCommitteeConfig,
) -> CommitteeVote:
    if payload is None:
        state = "ABSTAIN_MISSING"
        score = confidence = 0.0
        timestamp = None
        reference = ""
        freshness = "missing"
    else:
        score, confidence, timestamp, reference = payload
        age_days = max(0, (as_of.astimezone(UTC) - timestamp.astimezone(UTC)).days)
        freshness = "stale" if age_days > config.stale_after_days else "current"
        if freshness == "stale":
            state = "ABSTAIN_STALE"
        elif confidence < config.low_confidence_threshold:
            state = "ABSTAIN_LOW_CONFIDENCE"
        elif score >= config.strong_approve_threshold:
            state = "STRONG_APPROVE"
        elif score >= config.approve_threshold:
            state = "APPROVE"
        elif score <= config.strong_oppose_threshold:
            state = "STRONG_OPPOSE"
        elif score <= config.oppose_threshold:
            state = "OPPOSE"
        else:
            state = "NEUTRAL"
    return CommitteeVote(
        id=identity(
            "committee-vote",
            {
                "project_id": project_id,
                "engine": name,
                "score": score,
                "confidence": confidence,
                "reference": reference,
            },
        ),
        assessment_id="pending",
        engine_name=name,
        vote=state,
        normalized_contribution=weight * confidence,
        source_score=score,
        source_confidence=confidence,
        source_timestamp=timestamp,
        freshness_state=freshness,
        explanation=f"{name} vote derived from persisted committee input",
        supporting_references=(reference,) if reference and score >= config.approve_threshold else (),
        opposing_references=(reference,) if reference and score <= config.oppose_threshold else (),
        missing_fields=(name,) if payload is None else (),
    )


def _eligibility(
    inputs: CommitteeInputSet, votes: tuple[CommitteeVote, ...], config: InvestmentCommitteeConfig
) -> EligibilityState:
    active = sum(1 for vote in votes if not vote.vote.startswith("ABSTAIN"))
    if active < config.eligibility.minimum_available_engines:
        return "INSUFFICIENT_EVIDENCE"
    if len(inputs.alerts) > config.eligibility.maximum_critical_alert_count:
        return "INELIGIBLE"
    if _evidence_quality(inputs) < config.eligibility.minimum_evidence_completeness:
        return "CONDITIONALLY_ELIGIBLE"
    if (
        inputs.probability
        and inputs.probability.decision_confidence < config.eligibility.minimum_probability_reliability
    ):
        return "CONDITIONALLY_ELIGIBLE"
    return "ELIGIBLE"


def _decision(
    eligibility: EligibilityState,
    confidence: float,
    consensus: float,
    conflict: float,
    approval: float,
    config: InvestmentCommitteeConfig,
) -> CommitteeDecision:
    if eligibility == "INSUFFICIENT_EVIDENCE":
        return "INSUFFICIENT_EVIDENCE"
    if eligibility == "INELIGIBLE":
        return "REJECT"
    if conflict > config.winner_minimums.maximum_conflict:
        return "WAIT"
    if confidence >= 0.78 and consensus >= 0.7 and approval >= 0.65:
        return "STRONG_CANDIDATE"
    if confidence >= 0.62 and approval >= 0.55:
        return "QUALIFIED_CANDIDATE"
    if approval >= 0.45:
        return "WATCH_CLOSELY"
    return "WAIT"


def _winner_ok(item: InvestmentCommitteeAssessment, lead: float, config: InvestmentCommitteeConfig) -> bool:
    minimums = config.winner_minimums
    return (
        item.committee_confidence >= minimums.committee_confidence
        and item.consensus_score >= minimums.consensus
        and item.evidence_robustness >= minimums.evidence_robustness
        and item.conflict_score <= minimums.maximum_conflict
        and lead >= minimums.lead_margin
        and item.decision in {"STRONG_CANDIDATE", "QUALIFIED_CANDIDATE"}
    )


def _source_ids(inputs: CommitteeInputSet) -> tuple[str, ...]:
    ids = {
        *(record.id for record in inputs.intelligence),
        *(record.id for record in inputs.fused_intelligence),
        *(record.id for record in inputs.evidence),
        *(record.id for record in inputs.snapshots),
    }
    for item in (inputs.opportunity, inputs.probability, inputs.pattern, inputs.necessity):
        if item is not None:
            ids.add(getattr(item, "assessment_id", getattr(item, "id", "")))
    return tuple(sorted(item for item in ids if item))


def _engine(inputs: CommitteeInputSet, token: str) -> tuple[float, float, datetime, str] | None:
    for record in inputs.fused_intelligence:
        categories = tuple(str(item) for item in record.metadata.get("categories", ()))
        if token in categories or token in record.fusion_strategy:
            return record.fused_score, _avg_conf(record.confidence), record.effective_at, record.id
    for record in inputs.intelligence:
        if token in record.engine_id:
            return _intelligence_score(record), _avg_conf(record.confidence), record.effective_at, record.id
    return None


def _snapshot(inputs: CommitteeInputSet, key: str) -> tuple[float, float, datetime, str]:
    for record in inputs.snapshots:
        if key in record.payload:
            value = float(record.payload[key])
            return value, float(record.metadata.get("confidence", 0.0)), record.effective_at, record.id
    return 0.0, 0.0, inputs.effective_at, ""


def _evidence_quality(inputs: CommitteeInputSet) -> float:
    scores = [float(item.reliability) for item in inputs.evidence]
    scores.extend(float(item.metadata.get("confidence", 0.0)) for item in inputs.snapshots)
    return sum(scores) / len(scores) if scores else 0.0


def _intelligence_score(record: object) -> float:
    strengths = tuple(float(item) for item in getattr(record, "signal_strengths", ()))
    return sum(strengths) / len(strengths) if strengths else 0.0


def _avg_conf(value: object) -> float:
    if isinstance(value, Mapping):
        numeric = [float(item) for item in value.values() if isinstance(item, (int, float))]
        return sum(numeric) / len(numeric) if numeric else 0.0
    try:
        aggregate = value.aggregate  # type: ignore[attr-defined]
    except AttributeError:
        return float(value)
    return float(aggregate)


def _risks(inputs: CommitteeInputSet, votes: tuple[CommitteeVote, ...]) -> tuple[str, ...]:
    risks = [f"{vote.engine_name}:{vote.vote}" for vote in votes if vote.vote in {"STRONG_OPPOSE", "OPPOSE"}]
    risks.extend(inputs.alerts)
    return tuple(sorted(set(risks)))


def _invalidation_conditions(config: InvestmentCommitteeConfig) -> tuple[str, ...]:
    return (
        f"committee_confidence_below_{config.winner_minimums.committee_confidence}",
        f"consensus_below_{config.winner_minimums.consensus}",
        f"conflict_above_{config.winner_minimums.maximum_conflict}",
    )


def _change(
    previous: InvestmentCommitteeAssessment | None,
    decision: CommitteeDecision,
    confidence: float,
    consensus: float,
    conflict: float,
) -> tuple[str, ...]:
    if previous is None:
        return ("first committee assessment",)
    return (
        f"decision:{previous.decision}->{decision}",
        f"confidence_delta={confidence - previous.committee_confidence:+.4f}",
        f"consensus_delta={consensus - previous.consensus_score:+.4f}",
        f"conflict_delta={conflict - previous.conflict_score:+.4f}",
    )


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
