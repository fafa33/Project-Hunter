from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.models import DeveloperDataset


class DeveloperConfidenceModel:
    def __init__(self, configuration: DeveloperEngineConfiguration | None = None) -> None:
        self.configuration = configuration or DeveloperEngineConfiguration()

    def calculate(self, dataset: DeveloperDataset) -> Confidence:
        completeness = self._completeness(dataset)
        evidence_quality = self._evidence_quality(dataset)
        freshness = self._freshness(dataset)
        uncertainty = 1.0 - mean(
            (
                completeness,
                evidence_quality,
                freshness,
                self._repository_coverage(dataset),
                self._historical_depth(dataset),
                self._attribution_quality(dataset),
            )
        )
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=evidence_quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )

    def _completeness(self, dataset: DeveloperDataset) -> float:
        domains = (
            bool(dataset.repositories),
            bool(dataset.contributors),
            bool(dataset.releases),
            bool(dataset.pull_requests),
            bool(dataset.issues),
            bool(dataset.events),
        )
        return sum(domains) / len(domains)

    def _evidence_quality(self, dataset: DeveloperDataset) -> float:
        reliabilities = [
            *(repository.reliability for repository in dataset.repositories),
            *(contributor.reliability for contributor in dataset.contributors),
            *(release.reliability for release in dataset.releases),
            *(pull_request.reliability for pull_request in dataset.pull_requests),
            *(issue.reliability for issue in dataset.issues),
            *(event.reliability for event in dataset.events),
        ]
        return mean(reliabilities) if reliabilities else 0.0

    def _freshness(self, dataset: DeveloperDataset) -> float:
        timestamps = [
            *(repository.timestamp for repository in dataset.repositories),
            *(contributor.timestamp for contributor in dataset.contributors),
            *(release.released_at for release in dataset.releases),
            *(pull_request.created_at for pull_request in dataset.pull_requests),
            *(issue.created_at for issue in dataset.issues),
            *(event.timestamp for event in dataset.events),
        ]
        if not timestamps:
            return 0.0
        age_days = max((datetime.now(UTC) - max(timestamps)).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _repository_coverage(self, dataset: DeveloperDataset) -> float:
        if not dataset.repositories:
            return 0.0
        return min(len(dataset.core_repositories()) / max(len(dataset.repositories), 1), 1.0)

    def _historical_depth(self, dataset: DeveloperDataset) -> float:
        timestamps = [
            *(event.timestamp for event in dataset.events),
            *(release.released_at for release in dataset.releases),
            *(contributor.first_seen for contributor in dataset.contributors if contributor.first_seen),
        ]
        if len(timestamps) < 2:
            return 0.0
        depth_days = (max(timestamps) - min(timestamps)).days
        return min(depth_days / max(self.configuration.minimum_historical_depth_days, 1), 1.0)

    def _attribution_quality(self, dataset: DeveloperDataset) -> float:
        if not dataset.contributors:
            return 0.0
        named = sum(1 for contributor in dataset.contributors if contributor.contributor_id.strip())
        return named / len(dataset.contributors)
