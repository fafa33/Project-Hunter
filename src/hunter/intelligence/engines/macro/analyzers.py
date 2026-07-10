from __future__ import annotations

from hunter.intelligence.engines.macro.indicators import MacroIndicatorCalculator
from hunter.intelligence.engines.macro.models import MacroAnalysis, MacroDataset


class MacroAnalyzer:
    def __init__(self, indicator_calculator: MacroIndicatorCalculator | None = None) -> None:
        self._indicator_calculator = indicator_calculator or MacroIndicatorCalculator()

    def analyze(self, dataset: MacroDataset) -> MacroAnalysis:
        points = dataset.by_domain()
        indicators = self._indicator_calculator.calculate(points)
        strengthening = tuple(indicator.domain for indicator in indicators if indicator.direction in {"strengthening", "risk_on"})
        weakening = tuple(indicator.domain for indicator in indicators if indicator.direction in {"weakening", "risk_off"})
        risk_indicator = next((indicator for indicator in indicators if indicator.name == "market_cycle"), None)
        liquidity = next((indicator for indicator in indicators if indicator.name == "liquidity_expansion"), None)
        notable_events = tuple(
            f"{indicator.name}:{indicator.direction}"
            for indicator in indicators
            if indicator.direction not in {"unknown", "stable"}
        )
        return MacroAnalysis(
            indicators=indicators,
            strengthening_domains=strengthening,
            weakening_domains=weakening,
            risk_regime=risk_indicator.direction if risk_indicator else "unknown",
            liquidity_flow=liquidity.direction if liquidity else "unknown",
            notable_events=notable_events,
            metadata={"engine": "macro"},
        )

