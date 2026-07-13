from __future__ import annotations

from dataclasses import replace

from hunter.backtest.models import BacktestRun
from hunter.market_validation.models import EngineValidationSource
from hunter.weights.configuration import WeightConfig, load_weight_config
from hunter.weights.models import ScoreContribution, WeightedScore, WeightRecommendation


class WeightEngine:
    def __init__(self, config: WeightConfig | None = None) -> None:
        self.config = config or load_weight_config()

    def score(self, sources: tuple[EngineValidationSource, ...]) -> WeightedScore:
        contributions = tuple(self._contribution(source) for source in sorted(sources, key=lambda item: item.engine))
        return WeightedScore(
            hunter_score=_clamp(sum(item.raw_score * item.base_weight for item in contributions)),
            final_score=_clamp(sum(item.weighted_contribution for item in contributions)),
            scoring_version=self.config.version,
            contributions=contributions,
        )

    def apply(self, sources: tuple[EngineValidationSource, ...]) -> tuple[EngineValidationSource, ...]:
        contributions = {item.engine: item for item in self.score(sources).contributions}
        weighted = []
        for source in sources:
            contribution = contributions[source.engine]
            weighted.append(
                replace(
                    source,
                    base_weight=contribution.base_weight,
                    adjusted_weight=contribution.adjusted_weight,
                    applied_weight=contribution.adjusted_weight,
                    weighted_contribution=contribution.weighted_contribution,
                    evidence_coverage=contribution.evidence_coverage,
                    scoring_version=self.config.version,
                    raw_input_metrics=dict(source.raw_input_metrics),
                    normalized_inputs=dict(source.normalized_inputs),
                )
            )
        return tuple(weighted)

    def _contribution(self, source: EngineValidationSource) -> ScoreContribution:
        base_weight = round(self.config.weights.get(source.engine, 0.0), 6)
        available = source.status == "AVAILABLE" and source.confidence > 0.0 and not source.missing_fields
        raw_score = source.score if available else 0.0
        normalized_score = _normalized_score(source.engine, raw_score) if available else 0.0
        evidence_coverage = _evidence_coverage(source) if available else 0.0
        adjusted_weight = _clamp(base_weight * source.confidence * source.freshness * evidence_coverage)
        weighted_contribution = _clamp(normalized_score * adjusted_weight)
        return ScoreContribution(
            engine=source.engine,
            raw_score=round(raw_score, 4),
            normalized_score=round(normalized_score, 4),
            base_weight=base_weight,
            adjusted_weight=adjusted_weight,
            weighted_contribution=weighted_contribution,
            confidence=round(source.confidence if available else 0.0, 4),
            freshness=round(source.freshness if available else 0.0, 4),
            evidence_coverage=evidence_coverage,
            scoring_version=self.config.version,
        )


def recommend_weight_adjustments(config: WeightConfig, backtest_run: BacktestRun | None) -> WeightRecommendation:
    sample_size = backtest_run.historical_runs if backtest_run is not None else 0
    if sample_size < config.minimum_historical_sample_size:
        return WeightRecommendation(
            status="INSUFFICIENT_SAMPLE_SIZE",
            scoring_version=config.version,
            sample_size=sample_size,
            minimum_sample_size=config.minimum_historical_sample_size,
            recommended_adjustments={},
        )
    assert backtest_run is not None
    return WeightRecommendation(
        status="RECOMMENDATION_AVAILABLE",
        scoring_version=config.version,
        sample_size=sample_size,
        minimum_sample_size=config.minimum_historical_sample_size,
        recommended_adjustments=dict(sorted(backtest_run.calibration.recommended_weight_adjustments.items())),
    )


def _normalized_score(engine: str, raw_score: float) -> float:
    if engine == "risk":
        return _clamp(1.0 - raw_score)
    return _clamp(raw_score)


def _evidence_coverage(source: EngineValidationSource) -> float:
    checks = (
        bool(source.source_record_ids),
        bool(source.evidence_ids),
        bool(source.repository_ids),
        source.validation_status == "VALID",
    )
    return round(sum(1 for item in checks if item) / len(checks), 4)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
