from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.macro.models import MacroAnalysis


class MacroEnvironmentScorer:
    def environment_strength(self, analysis: MacroAnalysis) -> float:
        values = [indicator.value for indicator in analysis.indicators]
        return round(mean(values), 4) if values else 0.0

    def severity(self, value: float) -> float:
        return round(abs(value - 0.5) * 2, 4)

