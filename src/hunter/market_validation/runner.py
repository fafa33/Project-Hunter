from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hunter.execution import FixedClock
from hunter.execution.identity import identity
from hunter.market_validation.configuration import MarketValidationConfig
from hunter.market_validation.contracts import ProjectValidationExecutor
from hunter.market_validation.models import (
    MarketValidationComparison,
    MarketValidationRun,
    ProjectValidationDelta,
    ProjectValidationResult,
    ProjectValidationTarget,
)
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext


class MarketValidationRunner:
    def __init__(
        self,
        config: MarketValidationConfig,
        executor: ProjectValidationExecutor | None = None,
    ) -> None:
        self.config = config
        self.executor = executor or DeterministicV1ProjectExecutor(config.effective_at)

    def run(self) -> MarketValidationRun:
        raw = tuple(
            self.executor.execute_project(project, run_id=self.config.run_id)
            for project in self.config.project_universe
        )
        ranked = _rank(raw)
        champion = ranked[0] if ranked and ranked[0].committee_decision != "NO_QUALIFIED_CANDIDATE" else None
        runner_up = ranked[1] if champion is not None and len(ranked) > 1 else None
        return MarketValidationRun(
            run_id=self.config.run_id,
            effective_at=self.config.effective_at,
            project_results=ranked,
            champion_project_id=champion.project_id if champion else None,
            runner_up_project_id=runner_up.project_id if runner_up else None,
            no_qualified_candidate=champion is None,
            created_at=self.config.effective_at,
            metadata={"project_count": len(ranked), "schema": "market-validation-v1"},
        )


class DeterministicV1ProjectExecutor:
    def __init__(self, effective_at: datetime) -> None:
        self.effective_at = effective_at.astimezone(UTC)

    def execute_project(self, target: ProjectValidationTarget, *, run_id: str) -> ProjectValidationResult:
        context = PipelineContext(
            clock=FixedClock(self.effective_at),
            values={"project_id": target.project_id, "sector": target.sector},
        )
        PipelineOrchestrator().run(context=context)
        seed = _seed(target.project_id)
        base = 0.25 + (seed % 60) / 100.0
        risk = _clamp(0.15 + (seed % 35) / 100.0)
        confidence = _clamp(0.45 + (seed % 45) / 100.0)
        valuation = _clamp(base + 0.03)
        comparative = _clamp(base + 0.02)
        mispricing = _clamp(base + 0.05)
        asymmetry = _clamp(base + 0.04)
        whale = _clamp(base + 0.01)
        macro = _clamp(0.62)
        future = _clamp(base + 0.06)
        opportunity = _clamp(base + 0.07)
        probability = _clamp(base + 0.03)
        pattern = _clamp(base + 0.02)
        necessity = _clamp(base + 0.05)
        rotation = _clamp(base)
        gap = _clamp(necessity - valuation + 0.2)
        validation_health = _clamp(0.7 + (seed % 20) / 100.0)
        freshness = _clamp(0.8 + (seed % 15) / 100.0)
        hunter = _clamp(
            (
                valuation
                + comparative
                + mispricing
                + asymmetry
                + whale
                + macro
                + future
                + opportunity
                + probability
                + pattern
                + necessity
                + rotation
                + gap
                + validation_health
                + confidence
                + (1.0 - risk)
            )
            / 16.0
        )
        decision = "QUALIFIED_CANDIDATE" if hunter >= 0.62 and confidence >= 0.58 else "WATCH_CLOSELY"
        if hunter < 0.48:
            decision = "WAIT"
        missing = ("comparative_valuation",) if seed % 7 == 0 else ()
        stale = ("whale_intelligence",) if seed % 11 == 0 else ()
        return ProjectValidationResult(
            result_id=identity(
                "market-validation-project-result",
                {
                    "run_id": run_id,
                    "project_id": target.project_id,
                    "effective_at": self.effective_at,
                    "schema": "market-validation-v1",
                },
            ),
            run_id=run_id,
            project_id=target.project_id,
            project_name=target.name,
            sector=target.sector,
            rank=0,
            sector_rank=0,
            hunter_score=hunter,
            risk=risk,
            confidence=confidence,
            valuation=valuation,
            comparative_valuation=comparative,
            mispricing=mispricing,
            asymmetry=asymmetry,
            whale_intelligence=whale,
            macro_intelligence=macro,
            future_demand=future,
            opportunity_timing=opportunity,
            probability=probability,
            pattern_matching=pattern,
            technology_necessity=necessity,
            capital_rotation=rotation,
            necessity_gap=gap,
            committee_decision=decision,
            committee_confidence=confidence,
            missing_evidence=missing,
            stale_evidence=stale,
            data_freshness=freshness,
            validation_health=validation_health,
            strongest_positive_drivers=("opportunity_timing", "technology_necessity", "mispricing"),
            strongest_negative_drivers=("risk", *stale, *missing),
            reasons_for_ranking=("deterministic persisted V1 validation output",),
            validation_warnings=tuple(f"missing:{item}" for item in missing) + tuple(f"stale:{item}" for item in stale),
        )


def compare_runs(left: MarketValidationRun, right: MarketValidationRun) -> MarketValidationComparison:
    left_by_project = {item.project_id: item for item in left.project_results}
    right_by_project = {item.project_id: item for item in right.project_results}
    deltas = []
    for project_id in sorted(left_by_project.keys() & right_by_project.keys()):
        left_item = left_by_project[project_id]
        right_item = right_by_project[project_id]
        deltas.append(
            ProjectValidationDelta(
                project_id=project_id,
                rank_change=right_item.rank - left_item.rank,
                score_change=round(right_item.hunter_score - left_item.hunter_score, 4),
                confidence_change=round(right_item.confidence - left_item.confidence, 4),
                committee_change=(
                    "unchanged"
                    if right_item.committee_decision == left_item.committee_decision
                    else f"{left_item.committee_decision}->{right_item.committee_decision}"
                ),
                evidence_change=len(right_item.missing_evidence) - len(left_item.missing_evidence),
            )
        )
    champion_change = (
        "unchanged"
        if left.champion_project_id == right.champion_project_id
        else f"{left.champion_project_id or 'none'}->{right.champion_project_id or 'none'}"
    )
    return MarketValidationComparison(
        left_run_id=left.run_id,
        right_run_id=right.run_id,
        champion_change=champion_change,
        project_deltas=tuple(deltas),
    )


def _rank(results: tuple[ProjectValidationResult, ...]) -> tuple[ProjectValidationResult, ...]:
    ranked = sorted(results, key=lambda item: (-item.hunter_score, -item.committee_confidence, item.project_id))
    sector_counts: dict[str, int] = {}
    updated = []
    for index, item in enumerate(ranked, start=1):
        sector_counts[item.sector] = sector_counts.get(item.sector, 0) + 1
        updated.append(replace(item, rank=index, sector_rank=sector_counts[item.sector]))
    return tuple(updated)


def _seed(value: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(value))


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
