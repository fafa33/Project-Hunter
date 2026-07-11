from __future__ import annotations

from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.macro.models import MACRO_DOMAINS, MacroDataset


class MacroConfidenceModel:
    def calculate(self, dataset: MacroDataset) -> Confidence:
        if not dataset.points:
            return Confidence.calculate(
                completeness=0.0,
                evidence_quality=0.0,
                freshness=0.0,
                uncertainty=1.0,
            )
        completeness = len({point.domain for point in dataset.points}) / len(MACRO_DOMAINS)
        evidence_quality = mean(point.reliability for point in dataset.points)
        freshness = 1.0
        sources_by_domain = {}
        for point in dataset.points:
            sources_by_domain.setdefault(point.domain, set()).add(point.source)
        agreement = mean(min(len(sources), 2) / 2 for sources in sources_by_domain.values())
        uncertainty = 1.0 - ((0.7 * completeness) + (0.3 * agreement))
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=evidence_quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )
