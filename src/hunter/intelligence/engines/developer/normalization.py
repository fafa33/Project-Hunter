from __future__ import annotations

from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.models import (
    ContributorSnapshot,
    DeveloperDataset,
    DeveloperEvent,
    DeveloperRecord,
    DeveloperSnapshot,
    IssueSnapshot,
    PullRequestSnapshot,
    ReleaseSnapshot,
    RepositorySnapshot,
)


class DeveloperNormalizer:
    def __init__(self, configuration: DeveloperEngineConfiguration | None = None) -> None:
        self.configuration = configuration or DeveloperEngineConfiguration()

    def normalize(self, records: tuple[DeveloperRecord, ...]) -> DeveloperDataset:
        repositories: dict[str, RepositorySnapshot] = {}
        contributors: dict[str, ContributorSnapshot] = {}
        releases: dict[str, ReleaseSnapshot] = {}
        pull_requests: dict[str, PullRequestSnapshot] = {}
        issues: dict[str, IssueSnapshot] = {}
        events: dict[str, DeveloperEvent] = {}
        projects: list[str] = []
        missing_fields: set[str] = set()

        for record in records:
            if isinstance(record, DeveloperSnapshot):
                projects.append(record.project)
                for repository in record.repositories:
                    _add_repository(repositories, repository, self.configuration)
                contributors.update({item.id: item for item in record.contributors if not _is_filtered_actor(item.contributor_id, item.is_bot, self.configuration)})
                releases.update({item.id: item for item in record.releases})
                pull_requests.update({item.id: item for item in record.pull_requests if not _is_filtered_actor(item.author, item.is_bot, self.configuration)})
                issues.update({item.id: item for item in record.issues if not _is_filtered_actor(item.author, item.is_bot, self.configuration)})
                events.update({item.id: item for item in record.events if not _is_filtered_actor(item.actor, item.is_bot or item.automated, self.configuration)})
            elif isinstance(record, RepositorySnapshot):
                projects.append(record.project)
                _add_repository(repositories, record, self.configuration)
            elif isinstance(record, ContributorSnapshot):
                if not _is_filtered_actor(record.contributor_id, record.is_bot, self.configuration):
                    contributors[record.id] = record
            elif isinstance(record, ReleaseSnapshot):
                releases[record.id] = record
            elif isinstance(record, PullRequestSnapshot):
                if not _is_filtered_actor(record.author, record.is_bot, self.configuration):
                    pull_requests[record.id] = record
            elif isinstance(record, IssueSnapshot):
                if not _is_filtered_actor(record.author, record.is_bot, self.configuration):
                    issues[record.id] = record
            elif isinstance(record, DeveloperEvent) and not _is_filtered_actor(record.actor, record.is_bot or record.automated, self.configuration):
                events[record.id] = record

        repository_ids = set(repositories)
        contributors = {key: item for key, item in contributors.items() if item.repository_id in repository_ids}
        releases = {key: item for key, item in releases.items() if item.repository_id in repository_ids}
        pull_requests = {key: item for key, item in pull_requests.items() if item.repository_id in repository_ids}
        issues = {key: item for key, item in issues.items() if item.repository_id in repository_ids}
        events = {key: item for key, item in events.items() if item.repository_id in repository_ids}

        if not repositories:
            missing_fields.add("repositories")
        if not contributors:
            missing_fields.add("contributors")
        if not releases:
            missing_fields.add("releases")
        if not pull_requests:
            missing_fields.add("pull_requests")
        if not issues:
            missing_fields.add("issues")
        if not events:
            missing_fields.add("events")

        return DeveloperDataset(
            project=sorted(projects)[0] if projects else self.configuration.project,
            repositories=tuple(sorted(repositories.values(), key=lambda item: item.id)),
            contributors=tuple(sorted(contributors.values(), key=lambda item: item.id)),
            releases=tuple(sorted(releases.values(), key=lambda item: item.released_at.isoformat())),
            pull_requests=tuple(sorted(pull_requests.values(), key=lambda item: item.created_at.isoformat())),
            issues=tuple(sorted(issues.values(), key=lambda item: item.created_at.isoformat())),
            events=tuple(sorted(events.values(), key=lambda item: (item.timestamp.isoformat(), item.id))),
            missing_fields=tuple(sorted(missing_fields)),
            metadata={"bot_filtering": str(self.configuration.filter_bots).lower()},
        )


def _add_repository(
    repositories: dict[str, RepositorySnapshot],
    repository: RepositorySnapshot,
    configuration: DeveloperEngineConfiguration,
) -> None:
    if repository.is_archived and not configuration.include_archived_repositories:
        return
    is_core = repository.is_core or repository.id in configuration.core_repositories or repository.name in configuration.core_repositories
    repositories[repository.id] = RepositorySnapshot(
        id=repository.id,
        project=repository.project,
        name=repository.name,
        source=repository.source,
        timestamp=repository.timestamp,
        reliability=repository.reliability,
        is_core=is_core,
        is_archived=repository.is_archived,
        url=repository.url,
        created_at=repository.created_at,
        default_branch=repository.default_branch,
        aliases=repository.aliases,
        metadata=repository.metadata,
    )


def _is_filtered_actor(actor: str, flagged: bool, configuration: DeveloperEngineConfiguration) -> bool:
    if not configuration.filter_bots:
        return False
    normalized = actor.lower()
    return flagged or any(pattern in normalized for pattern in configuration.bot_patterns)
