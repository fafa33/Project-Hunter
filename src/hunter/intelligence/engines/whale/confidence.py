from __future__ import annotations

from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.whale.models import WHALE_SIGNAL_TYPES, WhaleDataset


class WhaleConfidenceModel:
    def calculate(self, dataset: WhaleDataset) -> Confidence:
        if not dataset.events:
            return Confidence.calculate(
                completeness=0.0,
                evidence_quality=0.0,
                freshness=0.0,
                uncertainty=1.0,
            )
        completeness = len({event.event_type for event in dataset.events}) / len(WHALE_SIGNAL_TYPES)
        evidence_quality = mean(
            mean([event.reliability, event.wallet_attribution_quality, event.confirmation]) for event in dataset.events
        )
        freshness = 1.0
        sources_by_type: dict[str, set[str]] = {}
        for event in dataset.events:
            sources_by_type.setdefault(event.event_type, set()).add(event.source)
        agreement = mean(min(len(sources), 2) / 2 for sources in sources_by_type.values())
        uncertainty = 1.0 - ((0.65 * completeness) + (0.35 * agreement))
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=evidence_quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )
