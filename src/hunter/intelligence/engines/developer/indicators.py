from __future__ import annotations

from datetime import timedelta
from statistics import mean, pstdev

from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.models import DeveloperDataset, DeveloperEvent, DeveloperIndicator


class DeveloperIndicatorCalculator:
    def __init__(self, configuration: DeveloperEngineConfiguration | None = None) -> None:
        self.configuration = configuration or DeveloperEngineConfiguration()

    def calculate(self, dataset: DeveloperDataset) -> tuple[DeveloperIndicator, ...]:
        return (
            self.commit_momentum(dataset),
            self.contributor_growth(dataset),
            self.contributor_concentration(dataset),
            self.developer_retention(dataset),
            self.release_cadence(dataset),
            self.release_consistency(dataset),
            self.pull_request_throughput(dataset),
            self.issue_resolution_efficiency(dataset),
            self.code_review_health(dataset),
            self.repository_maintenance_health(dataset),
            self.ecosystem_breadth(dataset),
            self.roadmap_delivery_consistency(dataset),
            self.engineering_activity_quality(dataset),
            self.development_acceleration(dataset),
            self.development_deterioration(dataset),
        )

    def commit_momentum(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        commit_events = tuple(event for event in dataset.events if event.event_type == "commit")
        value = _recent_share(commit_events, self.configuration.recent_window_days)
        return _indicator("commit_momentum", value, "positive", "Recent commit activity compared with older activity.", commit_events)

    def contributor_growth(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.contributors:
            return _missing("contributor_growth", "contributors")
        latest = _latest_timestamp(dataset)
        recent = sum(
            1
            for contributor in dataset.contributors
            if contributor.first_seen and latest and contributor.first_seen >= latest - timedelta(days=self.configuration.recent_window_days)
        )
        value = _bounded_ratio(recent, len(dataset.contributors))
        return _indicator("contributor_growth", value, "positive", "New contributor share across active repositories.", dataset.contributors)

    def contributor_concentration(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        human_contributors = tuple(contributor for contributor in dataset.contributors if contributor.commits > 0)
        if not human_contributors:
            return _missing("contributor_concentration", "contributors")
        total = sum(contributor.commits for contributor in human_contributors)
        largest = max(contributor.commits for contributor in human_contributors)
        concentration = _bounded_ratio(largest, total)
        value = 1.0 - concentration
        direction = "negative" if concentration >= self.configuration.contributor_concentration_risk else "positive"
        return DeveloperIndicator(
            name="contributor_concentration",
            value=round(value, 4),
            direction=direction,
            confidence=0.9,
            description="Lower concentration indicates healthier contributor distribution.",
            metadata={"largest_commit_share": str(round(concentration, 4))},
        )

    def developer_retention(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.contributors:
            return _missing("developer_retention", "contributors")
        latest = _latest_timestamp(dataset)
        retained = sum(
            1
            for contributor in dataset.contributors
            if contributor.first_seen
            and contributor.last_seen
            and latest
            and contributor.first_seen < latest - timedelta(days=self.configuration.recent_window_days)
            and contributor.last_seen >= latest - timedelta(days=self.configuration.recent_window_days)
        )
        historical = sum(
            1
            for contributor in dataset.contributors
            if contributor.first_seen and latest and contributor.first_seen < latest - timedelta(days=self.configuration.recent_window_days)
        )
        return _indicator("developer_retention", _bounded_ratio(retained, historical), "positive", "Share of historical contributors still active.", dataset.contributors)

    def release_cadence(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.releases:
            return _missing("release_cadence", "releases")
        latest = _latest_timestamp(dataset)
        recent = sum(
            1
            for release in dataset.releases
            if latest and release.released_at >= latest - timedelta(days=90)
        )
        return _indicator("release_cadence", min(recent / 4, 1.0), "positive", "Release frequency over the last ninety days.", dataset.releases)

    def release_consistency(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if len(dataset.releases) < 3:
            return _missing("release_consistency", "release_history")
        dates = sorted(release.released_at for release in dataset.releases)
        gaps = [(right - left).days for left, right in zip(dates, dates[1:], strict=False)]
        value = 1.0 - min(pstdev(gaps) / max(mean(gaps), 1.0), 1.0)
        return _indicator("release_consistency", value, "positive", "Release gap consistency across historical releases.", dataset.releases)

    def pull_request_throughput(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.pull_requests:
            return _missing("pull_request_throughput", "pull_requests")
        merged = sum(1 for pull_request in dataset.pull_requests if pull_request.merged_at is not None)
        return _indicator("pull_request_throughput", _bounded_ratio(merged, len(dataset.pull_requests)), "positive", "Pull request merge rate.", dataset.pull_requests)

    def issue_resolution_efficiency(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.issues:
            return _missing("issue_resolution_efficiency", "issues")
        closed = sum(1 for issue in dataset.issues if issue.closed_at is not None)
        return _indicator("issue_resolution_efficiency", _bounded_ratio(closed, len(dataset.issues)), "positive", "Issue closure rate.", dataset.issues)

    def code_review_health(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        review_events = tuple(event for event in dataset.events if event.event_type == "review")
        value = _bounded_ratio(len(review_events), max(len(dataset.pull_requests), 1))
        return _indicator("code_review_health", value, "positive", "Review events relative to pull request volume.", review_events)

    def repository_maintenance_health(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        if not dataset.repositories:
            return _missing("repository_maintenance_health", "repositories")
        active = len(dataset.active_repositories())
        core = len(dataset.core_repositories())
        value = mean((_bounded_ratio(active, len(dataset.repositories)), _bounded_ratio(core, len(dataset.repositories))))
        return _indicator("repository_maintenance_health", value, "positive", "Active and core repository share.", dataset.repositories)

    def ecosystem_breadth(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        active_repositories = dataset.active_repositories()
        value = min(len(active_repositories) / 5, 1.0)
        return _indicator("ecosystem_breadth", value, "positive", "Breadth of maintained repositories.", active_repositories)

    def roadmap_delivery_consistency(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        upgrade_events = tuple(event for event in dataset.events if event.event_type in {"protocol_upgrade", "roadmap_delivery"})
        value = min(len(upgrade_events) / 3, 1.0)
        return _indicator("roadmap_delivery_consistency", value, "positive", "Observed protocol upgrade and roadmap delivery events.", upgrade_events)

    def engineering_activity_quality(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        indicators = (
            self.pull_request_throughput(dataset),
            self.issue_resolution_efficiency(dataset),
            self.code_review_health(dataset),
            self.release_cadence(dataset),
        )
        available = tuple(indicator for indicator in indicators if not indicator.missing_evidence)
        if not available:
            return _missing("engineering_activity_quality", "delivery_quality")
        value = mean(indicator.value for indicator in available)
        return DeveloperIndicator(
            name="engineering_activity_quality",
            value=round(value, 4),
            direction="positive",
            confidence=round(mean(indicator.confidence for indicator in available), 4),
            description="Composite delivery quality from PR, issue, review, and release evidence.",
        )

    def development_acceleration(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        momentum = self.commit_momentum(dataset)
        growth = self.contributor_growth(dataset)
        value = mean((momentum.value, growth.value))
        return DeveloperIndicator(
            name="development_acceleration",
            value=round(value, 4),
            direction="positive" if value >= 0.55 else "neutral",
            confidence=round(mean((momentum.confidence, growth.confidence)), 4),
            description="Combined commit momentum and contributor growth.",
            missing_evidence=tuple(sorted((*momentum.missing_evidence, *growth.missing_evidence))),
        )

    def development_deterioration(self, dataset: DeveloperDataset) -> DeveloperIndicator:
        momentum = self.commit_momentum(dataset)
        retention = self.developer_retention(dataset)
        deterioration = 1.0 - mean((momentum.value, retention.value))
        return DeveloperIndicator(
            name="development_deterioration",
            value=round(deterioration, 4),
            direction="negative" if deterioration >= 0.55 else "neutral",
            confidence=round(mean((momentum.confidence, retention.confidence)), 4),
            description="Risk of declining recent activity and developer retention.",
            missing_evidence=tuple(sorted((*momentum.missing_evidence, *retention.missing_evidence))),
        )


def _recent_share(events: tuple[DeveloperEvent, ...], window_days: int) -> float:
    if not events:
        return 0.0
    latest = max(event.timestamp for event in events)
    recent = sum(1 for event in events if event.timestamp >= latest - timedelta(days=window_days))
    previous = len(events) - recent
    if previous == 0:
        return 0.7
    return _bounded_ratio(recent, recent + previous)


def _indicator(name: str, value: float, direction: str, description: str, evidence: tuple[object, ...]) -> DeveloperIndicator:
    return DeveloperIndicator(
        name=name,
        value=round(_clamp(value), 4),
        direction=direction,
        confidence=0.85 if evidence else 0.2,
        description=description,
        missing_evidence=() if evidence else (name,),
    )


def _missing(name: str, evidence_name: str) -> DeveloperIndicator:
    return DeveloperIndicator(
        name=name,
        value=0.0,
        direction="unknown",
        confidence=0.0,
        description=f"Missing {evidence_name} evidence.",
        missing_evidence=(evidence_name,),
    )


def _latest_timestamp(dataset: DeveloperDataset):
    timestamps = [
        *(repository.timestamp for repository in dataset.repositories),
        *(contributor.timestamp for contributor in dataset.contributors),
        *(release.released_at for release in dataset.releases),
        *(pull_request.created_at for pull_request in dataset.pull_requests),
        *(issue.created_at for issue in dataset.issues),
        *(event.timestamp for event in dataset.events),
    ]
    return max(timestamps) if timestamps else None


def _bounded_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(float(numerator) / float(denominator))


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
