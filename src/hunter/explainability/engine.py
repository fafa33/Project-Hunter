from __future__ import annotations

from datetime import datetime

from hunter.market_validation.models import MarketValidationRun, ProjectValidationResult

from .models import (
    ContributionBreakdown,
    DecisionAudit,
    EngineExplanation,
    EvidenceTrace,
    RankComparison,
    ScoreDifference,
    SensitivityItem,
)

SCORE_WEIGHT = round(1.0 / 16.0, 6)
ZERO_WEIGHT = 0.0

SCORE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("Valuation", "valuation", "valuation"),
    ("Comparative Valuation", "comparative_valuation", "comparative_valuation"),
    ("Mispricing", "mispricing", "mispricing"),
    ("Asymmetry", "asymmetry", "asymmetry"),
    ("Whale Intelligence", "whale_intelligence", "whale_intelligence"),
    ("Macro", "macro_intelligence", "macro_intelligence"),
    ("Future Demand", "future_demand", "future_demand"),
    ("Opportunity Timing", "opportunity_timing", "opportunity_timing"),
    ("Probability", "probability", "probability"),
    ("Pattern Matching", "pattern_matching", "pattern_matching"),
    ("Technology Necessity", "technology_necessity", "technology_necessity"),
    ("Capital Rotation", "capital_rotation", "capital_rotation"),
    ("Necessity Gap", "necessity_gap", "necessity_gap"),
    ("Validation Health", "validation_health", "validation_health"),
    ("Committee", "committee_confidence", "committee"),
    ("Risk Penalty", "risk", "risk"),
)

TRACE_ONLY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("Developer", "developer", "developer"),
    ("Protocol", "protocol", "protocol"),
    ("News", "news", "news"),
    ("Social", "social", "social"),
)


class DecisionExplainabilityEngine:
    def explain_project(self, run: MarketValidationRun, project_id: str) -> DecisionAudit:
        result = _project(run, project_id)
        contributions = _contributions(result)
        traces = _evidence_trace(result, run.effective_at)
        explanations = tuple(_engine_explanation(item, result) for item in contributions)
        positives = tuple(sorted(contributions, key=lambda item: (-item.final_score_contribution, item.engine))[:10])
        negatives = tuple(sorted(contributions, key=lambda item: (item.final_score_contribution, item.engine))[:10])
        return DecisionAudit(
            project_id=result.project_id,
            project_name=result.project_name,
            final_score=result.hunter_score,
            rank=result.rank,
            committee_decision=result.committee_decision,
            committee_confidence=result.committee_confidence,
            contributions=contributions,
            evidence_trace=traces,
            explanations=explanations,
            decision_tree=_decision_tree(result),
            invalidation_conditions=_invalidation_conditions(result),
            top_positive_contributors=positives,
            top_negative_contributors=negatives,
            sensitivity=tuple(
                SensitivityItem(item.engine, item.final_score_contribution)
                for item in contributions
                if item.final_score_contribution > 0.0
            ),
        )

    def compare_projects(self, run: MarketValidationRun, left_project_id: str, right_project_id: str) -> RankComparison:
        left = _project(run, left_project_id)
        right = _project(run, right_project_id)
        differences = tuple(_score_difference(label, field, left, right) for label, field, _ in SCORE_FIELDS)
        return RankComparison(
            left_project_id=left.project_id,
            right_project_id=right.project_id,
            left_rank=left.rank,
            right_rank=right.rank,
            final_ranking_difference=right.rank - left.rank,
            engine_preferences=differences,
            largest_score_differences=tuple(
                sorted(differences, key=lambda item: (-abs(item.difference), item.engine))[:10]
            ),
            largest_confidence_difference=round(left.confidence - right.confidence, 4),
            largest_risk_difference=round(left.risk - right.risk, 4),
            largest_valuation_difference=round(left.valuation - right.valuation, 4),
            largest_macro_difference=round(left.macro_intelligence - right.macro_intelligence, 4),
            largest_future_demand_difference=round(left.future_demand - right.future_demand, 4),
            largest_committee_difference=round(left.committee_confidence - right.committee_confidence, 4),
        )

    def explain_ranking(self, run: MarketValidationRun) -> tuple[DecisionAudit, ...]:
        return tuple(self.explain_project(run, item.project_id) for item in run.project_results)


def _project(run: MarketValidationRun, project_id: str) -> ProjectValidationResult:
    normalized = project_id.lower()
    for result in run.project_results:
        if result.project_id.lower() == normalized or result.project_name.lower() == normalized:
            return result
    msg = f"Unknown project: {project_id}"
    raise LookupError(msg)


def _contributions(result: ProjectValidationResult) -> tuple[ContributionBreakdown, ...]:
    items: list[ContributionBreakdown] = []
    sources = {source.engine: source for source in result.engine_sources}
    for label, field, engine in SCORE_FIELDS:
        raw = float(getattr(result, field))
        normalized = round(1.0 - raw, 4) if label == "Risk Penalty" else round(raw, 4)
        source = sources.get(engine)
        applied_weight = source.applied_weight if source is not None else 0.0
        contribution = source.weighted_contribution if source is not None and source.confidence > 0.0 else 0.0
        items.append(
            ContributionBreakdown(
                engine=label,
                raw_score=round(raw, 4),
                normalized_score=normalized,
                applied_weight=round(applied_weight, 6),
                final_score_contribution=round(contribution, 4),
            )
        )
    for label, _, engine in TRACE_ONLY_FIELDS:
        source = sources.get(engine)
        score = source.score if source is not None else 0.0
        weight = source.applied_weight if source is not None else ZERO_WEIGHT
        contribution = source.weighted_contribution if source is not None and source.confidence > 0.0 else 0.0
        items.append(ContributionBreakdown(label, score, score, weight, contribution))
    return tuple(items)


def _evidence_trace(result: ProjectValidationResult, effective_at: datetime) -> tuple[EvidenceTrace, ...]:
    traces = []
    sources = {source.engine: source for source in result.engine_sources}
    for label, field, engine in (*SCORE_FIELDS, *TRACE_ONLY_FIELDS):
        source = sources.get(engine)
        missing = (
            tuple(item for item in result.missing_evidence if item == engine or item == field)
            if source is None
            else source.missing_fields
        )
        if source is None and not missing:
            missing = (engine,)
        stale = tuple(item for item in result.stale_evidence if item == engine or item == field)
        warnings = (
            tuple(item for item in result.validation_warnings if item.endswith(engine) or item.endswith(field))
            if source is None
            else source.warnings
        )
        score = float(getattr(result, field, 0.0))
        traces.append(
            EvidenceTrace(
                engine=label,
                input_evidence=tuple(
                    f"{key}={value}" for key, value in sorted((source.raw_input_metrics if source else {}).items())
                ),
                evidence_ids=source.evidence_ids if source is not None else (),
                repository_ids=source.source_record_ids if source is not None else (),
                timestamp=source.timestamp if source is not None else effective_at,
                confidence=round(source.confidence if source is not None and score > 0.0 else 0.0, 4),
                freshness=round(source.freshness if source is not None else 0.0, 4),
                missing_evidence=missing,
                stale_evidence=stale,
                validation_warnings=warnings or ((f"missing:{engine}",) if source is None else ()),
            )
        )
    return tuple(traces)


def _engine_explanation(contribution: ContributionBreakdown, result: ProjectValidationResult) -> EngineExplanation:
    reasons = [
        f"raw={contribution.raw_score:.4f}",
        f"normalized={contribution.normalized_score:.4f}",
        f"weight={contribution.applied_weight:.6f}",
        f"contribution={contribution.final_score_contribution:.4f}",
    ]
    if contribution.engine in result.strongest_positive_drivers:
        reasons.append("listed as a strongest positive driver")
    if contribution.engine.lower().replace(" ", "_") in result.strongest_negative_drivers:
        reasons.append("listed as a strongest negative driver")
    return EngineExplanation(contribution.engine, contribution.normalized_score, tuple(reasons))


def _decision_tree(result: ProjectValidationResult) -> tuple[str, ...]:
    return (
        "Final Candidate" if result.rank == 1 else "Ranked Candidate",
        f"Committee {result.committee_decision}",
        f"Probability {result.probability:.4f}",
        f"Technology Necessity {result.technology_necessity:.4f}",
        f"Macro {result.macro_intelligence:.4f}",
        f"Future Demand {result.future_demand:.4f}",
        f"Comparative Valuation {result.comparative_valuation:.4f}",
        f"Risk Acceptable {1.0 - result.risk:.4f}",
    )


def _invalidation_conditions(result: ProjectValidationResult) -> tuple[str, ...]:
    conditions = [
        "Macro deterioration",
        "Revenue decline",
        "Developer decline",
        "Whale distribution",
        "Capital rotation deterioration",
        "Technology replacement",
        "Demand reduction",
        "Stale evidence",
        "Missing validation",
    ]
    conditions.extend(f"Resolve missing evidence: {item}" for item in result.missing_evidence)
    conditions.extend(f"Refresh stale evidence: {item}" for item in result.stale_evidence)
    return tuple(sorted(set(conditions)))


def _score_difference(
    label: str, field: str, left: ProjectValidationResult, right: ProjectValidationResult
) -> ScoreDifference:
    left_score = round(float(getattr(left, field)), 4)
    right_score = round(float(getattr(right, field)), 4)
    if label == "Risk Penalty":
        left_score = round(1.0 - left_score, 4)
        right_score = round(1.0 - right_score, 4)
    difference = round(left_score - right_score, 4)
    preferred = "tie" if difference == 0.0 else left.project_id if difference > 0 else right.project_id
    return ScoreDifference(label, left_score, right_score, difference, preferred)
