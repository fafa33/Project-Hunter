from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.indicators import DeveloperIndicatorCalculator
from hunter.intelligence.engines.developer.models import DeveloperAnalysis, DeveloperDataset, DeveloperIndicator


class DeveloperAnalyzer:
    def __init__(
        self,
        *,
        indicator_calculator: DeveloperIndicatorCalculator | None = None,
        configuration: DeveloperEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or DeveloperEngineConfiguration()
        self._indicator_calculator = indicator_calculator or DeveloperIndicatorCalculator(self.configuration)

    def analyze(self, dataset: DeveloperDataset) -> DeveloperAnalysis:
        indicators = self._indicator_calculator.calculate(dataset)
        strengths = tuple(indicator.name for indicator in indicators if indicator.direction == "positive" and indicator.value >= 0.6)
        risks = tuple(indicator.name for indicator in indicators if indicator.direction == "negative" and indicator.value >= 0.45)
        missing = tuple(sorted({missing for indicator in indicators for missing in indicator.missing_evidence} | set(dataset.missing_fields)))
        health = self._health(indicators)
        trend = self._trend(indicators)
        return DeveloperAnalysis(
            indicators=indicators,
            health=health,
            trend=trend,
            strengths=strengths,
            risks=risks,
            missing_evidence=missing,
            metadata={
                "repository_count": str(len(dataset.repositories)),
                "core_repository_count": str(len(dataset.core_repositories())),
                "active_contributor_count": str(len(dataset.contributors)),
            },
        )

    def _health(self, indicators: tuple[DeveloperIndicator, ...]) -> str:
        scored = tuple(indicator.value for indicator in indicators if indicator.name != "development_deterioration")
        if not scored:
            return "unknown"
        score = mean(scored)
        if score >= 0.7:
            return "strong"
        if score >= 0.45:
            return "stable"
        return "weak"

    def _trend(self, indicators: tuple[DeveloperIndicator, ...]) -> str:
        by_name = {indicator.name: indicator for indicator in indicators}
        acceleration = by_name.get("development_acceleration")
        deterioration = by_name.get("development_deterioration")
        if acceleration and deterioration and acceleration.value > deterioration.value and acceleration.value >= 0.55:
            return "accelerating"
        if deterioration and deterioration.value >= 0.55:
            return "deteriorating"
        return "steady"
