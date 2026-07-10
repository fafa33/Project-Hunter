from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.protocol.configuration import ProtocolEngineConfiguration
from hunter.intelligence.engines.protocol.indicators import ProtocolIndicatorCalculator
from hunter.intelligence.engines.protocol.models import ProtocolAnalysis, ProtocolDataset, ProtocolIndicator


class ProtocolAnalyzer:
    def __init__(
        self,
        *,
        indicator_calculator: ProtocolIndicatorCalculator | None = None,
        configuration: ProtocolEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or ProtocolEngineConfiguration()
        self._indicator_calculator = indicator_calculator or ProtocolIndicatorCalculator(self.configuration)

    def analyze(self, dataset: ProtocolDataset) -> ProtocolAnalysis:
        indicators = self._indicator_calculator.calculate(dataset)
        strengths = tuple(indicator.name for indicator in indicators if indicator.direction == "positive" and indicator.value >= 0.6)
        risks = tuple(indicator.name for indicator in indicators if indicator.direction == "negative" and indicator.value >= 0.4)
        missing = tuple(sorted(set(dataset.missing_fields) | {missing for indicator in indicators for missing in indicator.missing_evidence}))
        return ProtocolAnalysis(
            indicators=indicators,
            health=self._health(indicators),
            operational_trend=self._label(indicators, ("network_reliability", "validator_health", "protocol_resilience")),
            economic_trend=self._label(indicators, ("fee_growth", "revenue_growth", "value_capture_efficiency")),
            adoption_trend=self._label(indicators, ("user_growth", "returning_user_ratio", "application_breadth")),
            resilience=self._label(indicators, ("network_reliability", "incident_frequency", "liquidity_stability")),
            sustainability=self._label(indicators, ("organic_tvl_ratio", "treasury_runway", "incentive_dependence", "emissions_dependence")),
            strengths=strengths,
            risks=risks,
            missing_evidence=missing,
            metadata={
                "chain_count": str(len(dataset.chains())),
                "deployment_count": str(len(dataset.deployments())),
                "record_count": str(len(dataset.records)),
            },
        )

    def _health(self, indicators: tuple[ProtocolIndicator, ...]) -> str:
        available = tuple(indicator.value for indicator in indicators if not indicator.missing_evidence and indicator.name != "protocol_deterioration")
        if not available:
            return "unknown"
        score = mean(available)
        if score >= 0.7:
            return "strong"
        if score >= 0.45:
            return "stable"
        return "weak"

    def _label(self, indicators: tuple[ProtocolIndicator, ...], names: tuple[str, ...]) -> str:
        by_name = {indicator.name: indicator for indicator in indicators}
        values = [by_name[name].value for name in names if name in by_name and not by_name[name].missing_evidence]
        if not values:
            return "unknown"
        score = mean(values)
        if score >= 0.65:
            return "improving"
        if score <= 0.35:
            return "deteriorating"
        return "stable"
