from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.onchain.configuration import OnchainEngineConfiguration
from hunter.intelligence.engines.onchain.models import OnchainDataset


class OnchainConfidenceModel:
    def __init__(self, configuration: OnchainEngineConfiguration | None = None) -> None:
        self.configuration = configuration or OnchainEngineConfiguration()

    def calculate(self, dataset: OnchainDataset) -> Confidence:
        completeness = self._completeness(dataset)
        quality = self._quality(dataset)
        freshness = self._freshness(dataset)
        coverage = mean(
            (
                self._chain_coverage(dataset),
                self._asset_coverage(dataset),
                self._historical_depth(dataset),
                self._duplicate_quality(dataset),
                dataset.cross_engine_alignment,
            )
        )
        uncertainty = 1.0 - mean((completeness, quality, freshness, coverage))
        return Confidence.calculate(
            completeness=completeness, evidence_quality=quality, freshness=freshness, uncertainty=uncertainty
        )

    def _completeness(self, dataset: OnchainDataset) -> float:
        groups = (
            dataset.addresses,
            dataset.transactions,
            dataset.capital_flows,
            dataset.holders,
            dataset.contract_activity,
        )
        return sum(bool(group) for group in groups) / len(groups)

    def _quality(self, dataset: OnchainDataset) -> float:
        if not dataset.records:
            return 0.0
        values = []
        for record in dataset.records:
            values.append(
                mean(
                    (record.reliability, _optional(record.attribution_quality), _optional(record.entity_label_quality))
                )
            )
        return mean(values)

    def _freshness(self, dataset: OnchainDataset) -> float:
        if not dataset.records:
            return 0.0
        age_days = max((datetime.now(UTC) - max(record.timestamp for record in dataset.records)).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _chain_coverage(self, dataset: OnchainDataset) -> float:
        chains = {record.chain for record in dataset.records}
        return min(len(chains) / max(self.configuration.minimum_chain_coverage, 1), 1.0)

    def _asset_coverage(self, dataset: OnchainDataset) -> float:
        assets = {record.asset for record in dataset.records}
        return min(len(assets) / 2, 1.0)

    def _historical_depth(self, dataset: OnchainDataset) -> float:
        if len(dataset.records) < 2:
            return 0.0
        depth = (
            max(record.timestamp for record in dataset.records) - min(record.timestamp for record in dataset.records)
        ).days
        return min(depth / max(self.configuration.minimum_historical_depth_days, 1), 1.0)

    def _duplicate_quality(self, dataset: OnchainDataset) -> float:
        return 1.0 - min(
            (len(dataset.duplicates) + len(dataset.overlapping_windows)) / max(len(dataset.records), 1), 1.0
        )


def _optional(value: float | None) -> float:
    return 0.5 if value is None else value
