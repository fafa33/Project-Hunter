from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.narrative.configuration import NarrativeEngineConfiguration
from hunter.intelligence.engines.narrative.models import NarrativeDataset


class NarrativeConfidenceModel:
    def __init__(self, configuration: NarrativeEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NarrativeEngineConfiguration()

    def calculate(self, dataset: NarrativeDataset) -> Confidence:
        completeness = 1.0 if dataset.evidence else 0.0
        quality = self._quality(dataset)
        freshness = self._freshness(dataset)
        uncertainty = 1.0 - mean(
            (
                completeness,
                quality,
                freshness,
                self._diversity(dataset),
                self._cross_engine_agreement(dataset),
                self._historical_persistence(dataset),
            )
        )
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )

    def _quality(self, dataset: NarrativeDataset) -> float:
        if not dataset.evidence:
            return 0.0
        penalty = (len(dataset.duplicates) + len(dataset.filtered)) / max(len(dataset.evidence), 1)
        raw = mean(item.reliability * item.strength for item in dataset.evidence)
        return max(raw * (1.0 - min(penalty, 1.0)), 0.0)

    def _freshness(self, dataset: NarrativeDataset) -> float:
        if not dataset.evidence:
            return 0.0
        age_days = max((datetime.now(UTC) - max(item.timestamp for item in dataset.evidence)).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _diversity(self, dataset: NarrativeDataset) -> float:
        sources = {item.source for item in dataset.evidence}
        categories = {item.category for item in dataset.evidence}
        return mean((min(len(sources) / 4, 1.0), min(len(categories) / 4, 1.0))) if dataset.evidence else 0.0

    def _cross_engine_agreement(self, dataset: NarrativeDataset) -> float:
        engines = {item.engine for item in dataset.evidence}
        return min(len(engines) / 5, 1.0) if engines else 0.0

    def _historical_persistence(self, dataset: NarrativeDataset) -> float:
        if len(dataset.evidence) < 2:
            return 0.0
        depth = (
            max(item.timestamp for item in dataset.evidence) - min(item.timestamp for item in dataset.evidence)
        ).days
        return min(depth / 180, 1.0)
