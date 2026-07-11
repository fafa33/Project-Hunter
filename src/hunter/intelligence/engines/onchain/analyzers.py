from __future__ import annotations

from hunter.intelligence.engines.onchain.anomalies import OnchainAnomalyModel
from hunter.intelligence.engines.onchain.configuration import OnchainEngineConfiguration
from hunter.intelligence.engines.onchain.indicators import OnchainIndicatorCalculator
from hunter.intelligence.engines.onchain.models import OnchainAnalysis, OnchainDataset, OnchainIndicator


class OnchainAnalyzer:
    def __init__(
        self,
        *,
        indicators: OnchainIndicatorCalculator | None = None,
        anomalies: OnchainAnomalyModel | None = None,
        configuration: OnchainEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or OnchainEngineConfiguration()
        self._indicators = indicators or OnchainIndicatorCalculator(self.configuration)
        self._anomalies = anomalies or OnchainAnomalyModel(self.configuration)

    def analyze(self, dataset: OnchainDataset) -> OnchainAnalysis:
        indicators = self._indicators.calculate(dataset)
        by_name = {indicator.name: indicator for indicator in indicators}
        anomaly = self._anomalies.assess(dataset)
        strengths = tuple(
            sorted(
                indicator.name
                for indicator in indicators
                if indicator.direction == "positive" and indicator.value >= 0.6
            )
        )
        risks = tuple(
            sorted(
                indicator.name
                for indicator in indicators
                if indicator.direction == "negative" and indicator.value >= 0.45
            )
        )
        missing = tuple(
            sorted(
                set(dataset.missing_fields) | {item for indicator in indicators for item in indicator.missing_evidence}
            )
        )
        return OnchainAnalysis(
            indicators=indicators,
            anomaly=anomaly,
            health=self._health(by_name),
            capital_flow_trend=self._trend(by_name.get("net_capital_flow"), "inflow", "outflow"),
            address_trend=self._trend(by_name.get("active_address_momentum"), "growing", "shrinking"),
            holder_trend=self._trend(by_name.get("holder_growth"), "broadening", "weakening"),
            concentration=(
                "concentrated"
                if by_name.get("holder_concentration", _empty()).value
                >= self.configuration.holder_concentration_threshold
                else "distributed"
            ),
            decentralization=(
                "improving" if by_name.get("supply_distribution_quality", _empty()).value >= 0.55 else "weak"
            ),
            contract_activity=self._trend(by_name.get("contract_activity_growth"), "broadening", "stagnant"),
            migration=self._trend(by_name.get("bridge_netflow"), "inbound", "outbound"),
            strengths=strengths,
            risks=risks,
            missing_evidence=missing,
            metadata={
                "chains": str(len({record.chain for record in dataset.records})),
                "assets": str(len({record.asset for record in dataset.records})),
            },
        )

    def _health(self, indicators: dict[str, OnchainIndicator]) -> str:
        acceleration = indicators.get("onchain_acceleration", _empty()).value
        deterioration = indicators.get("onchain_deterioration", _empty()).value
        if deterioration >= 0.65:
            return "deteriorating"
        if acceleration >= 0.65 and deterioration < 0.5:
            return "healthy"
        return "mixed"

    def _trend(self, indicator: OnchainIndicator | None, positive: str, negative: str) -> str:
        if indicator is None or indicator.confidence == 0.0:
            return "unknown"
        if indicator.value >= 0.58:
            return positive
        if indicator.value <= 0.42:
            return negative
        return "stable"


def _empty() -> OnchainIndicator:
    return OnchainIndicator(name="empty", value=0.0, direction="unknown", confidence=0.0, description="")
