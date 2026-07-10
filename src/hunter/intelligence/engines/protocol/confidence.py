from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.protocol.configuration import ProtocolEngineConfiguration
from hunter.intelligence.engines.protocol.models import ProtocolDataset


class ProtocolConfidenceModel:
    def __init__(self, configuration: ProtocolEngineConfiguration | None = None) -> None:
        self.configuration = configuration or ProtocolEngineConfiguration()

    def calculate(self, dataset: ProtocolDataset) -> Confidence:
        completeness = self._completeness(dataset)
        quality = self._source_quality(dataset)
        freshness = self._freshness(dataset)
        uncertainty = 1.0 - mean(
            (
                completeness,
                quality,
                freshness,
                self._coverage_breadth(dataset),
                self._historical_depth(dataset),
                self._provider_agreement(dataset),
            )
        )
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )

    def _completeness(self, dataset: ProtocolDataset) -> float:
        groups = (
            dataset.usage,
            dataset.transactions,
            dataset.fees,
            dataset.revenues,
            dataset.tvl,
            dataset.liquidity,
            dataset.applications,
            dataset.validators,
            dataset.incidents,
            dataset.governance,
            dataset.treasury,
            dataset.incentives,
        )
        return sum(bool(group) for group in groups) / len(groups)

    def _source_quality(self, dataset: ProtocolDataset) -> float:
        if not dataset.records:
            return 0.0
        return mean(record.reliability for record in dataset.records)

    def _freshness(self, dataset: ProtocolDataset) -> float:
        if not dataset.records:
            return 0.0
        age_days = max((datetime.now(UTC) - max(record.timestamp for record in dataset.records)).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _coverage_breadth(self, dataset: ProtocolDataset) -> float:
        chain_score = min(len(dataset.chains()) / 3, 1.0) if dataset.chains() else 0.3 if dataset.records else 0.0
        deployment_score = min(len(dataset.deployments()) / 3, 1.0) if dataset.deployments() else 0.3 if dataset.records else 0.0
        return mean((chain_score, deployment_score))

    def _historical_depth(self, dataset: ProtocolDataset) -> float:
        if len(dataset.records) < 2:
            return 0.0
        depth = (max(record.timestamp for record in dataset.records) - min(record.timestamp for record in dataset.records)).days
        return min(depth / max(self.configuration.minimum_historical_depth_days, 1), 1.0)

    def _provider_agreement(self, dataset: ProtocolDataset) -> float:
        sources = {record.source for record in dataset.records}
        if not sources:
            return 0.0
        return min(len(sources) / 3, 1.0)
