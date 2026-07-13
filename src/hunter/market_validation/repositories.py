from __future__ import annotations

import json

from hunter.market_validation.contracts import MarketValidationRunRepository
from hunter.market_validation.models import MarketValidationRun, ProjectValidationResult
from hunter.persistence.records import MarketValidationProjectResultRecord, MarketValidationRunRecord


class InMemoryMarketValidationRunRepository(MarketValidationRunRepository):
    def __init__(self) -> None:
        self._runs: dict[str, MarketValidationRun] = {}

    def save(self, run: MarketValidationRun) -> MarketValidationRun:
        existing = self._runs.get(run.run_id)
        if existing is not None:
            return existing
        self._runs[run.run_id] = run
        return run

    def load(self, run_id: str) -> MarketValidationRun | None:
        return self._runs.get(run_id)

    def history(self) -> tuple[MarketValidationRun, ...]:
        return tuple(sorted(self._runs.values(), key=lambda item: (item.effective_at, item.run_id)))


def run_to_record(run: MarketValidationRun) -> MarketValidationRunRecord:
    return MarketValidationRunRecord(
        id=run.run_id,
        created_at=run.created_at,
        effective_at=run.effective_at,
        validation_run_id=run.run_id,
        project_result_ids=tuple(item.result_id for item in run.project_results),
        champion_project_id=run.champion_project_id,
        runner_up_project_id=run.runner_up_project_id,
        no_qualified_candidate=run.no_qualified_candidate,
        project_count=len(run.project_results),
        metadata=dict(run.metadata),
    )


def result_to_record(result: ProjectValidationResult, *, effective_at) -> MarketValidationProjectResultRecord:
    return MarketValidationProjectResultRecord(
        id=result.result_id,
        created_at=effective_at,
        effective_at=effective_at,
        validation_run_id=result.run_id,
        project_id=result.project_id,
        project_name=result.project_name,
        sector=result.sector,
        rank=result.rank,
        sector_rank=result.sector_rank,
        hunter_score=result.hunter_score,
        risk=result.risk,
        confidence=result.confidence,
        valuation=result.valuation,
        comparative_valuation=result.comparative_valuation,
        mispricing=result.mispricing,
        asymmetry=result.asymmetry,
        whale_intelligence=result.whale_intelligence,
        macro_intelligence=result.macro_intelligence,
        future_demand=result.future_demand,
        opportunity_timing=result.opportunity_timing,
        probability=result.probability,
        pattern_matching=result.pattern_matching,
        technology_necessity=result.technology_necessity,
        capital_rotation=result.capital_rotation,
        necessity_gap=result.necessity_gap,
        committee_decision=result.committee_decision,
        committee_confidence=result.committee_confidence,
        missing_evidence=result.missing_evidence,
        stale_evidence=result.stale_evidence,
        data_freshness=result.data_freshness,
        validation_health=result.validation_health,
        strongest_positive_drivers=result.strongest_positive_drivers,
        strongest_negative_drivers=result.strongest_negative_drivers,
        reasons_for_ranking=result.reasons_for_ranking,
        validation_warnings=result.validation_warnings,
        metadata={
            "engine_sources": json.dumps(
                [
                    {
                        "engine": source.engine,
                        "raw_score": source.score,
                        "source_record_ids": list(source.source_record_ids),
                        "evidence_ids": list(source.evidence_ids),
                        "repository_ids": list(source.repository_ids),
                        "raw_input_metrics": dict(source.raw_input_metrics),
                        "normalized_inputs": dict(source.normalized_inputs),
                        "base_weight": source.base_weight,
                        "adjusted_weight": source.adjusted_weight,
                        "applied_weight": source.applied_weight,
                        "weighted_contribution": source.weighted_contribution,
                        "confidence": source.confidence,
                        "freshness": source.freshness,
                        "evidence_coverage": source.evidence_coverage,
                        "scoring_version": source.scoring_version,
                    }
                    for source in result.engine_sources
                ],
                sort_keys=True,
            ),
            "final_score": result.final_score,
            "scoring_version": result.scoring_version,
        },
    )
