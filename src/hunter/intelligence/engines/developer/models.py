from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.developer.exceptions import DeveloperValidationError

DEVELOPER_DOMAINS = (
    "commit_activity",
    "active_contributors",
    "contributor_concentration",
    "new_contributor_growth",
    "core_developer_retention",
    "release_frequency",
    "release_consistency",
    "pull_request_activity",
    "pull_request_merge_rate",
    "issue_resolution",
    "code_review_activity",
    "repository_maintenance",
    "repository_breadth",
    "ecosystem_repository_growth",
    "documentation_activity",
    "sdk_tooling_development",
    "protocol_upgrade_delivery",
    "roadmap_delivery",
    "development_momentum",
    "engineering_health",
)


@dataclass(frozen=True)
class RepositorySnapshot:
    id: str
    project: str
    name: str
    source: str
    timestamp: datetime
    reliability: float
    is_core: bool = True
    is_archived: bool = False
    url: str = ""
    created_at: datetime | None = None
    default_branch: str = "main"
    aliases: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "repository id")
        _require_text(self.project, "project")
        _require_text(self.name, "repository name")
        _require_text(self.source, "repository source")
        _require_datetime(self.timestamp, "repository timestamp")
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class ContributorSnapshot:
    id: str
    repository_id: str
    contributor_id: str
    commits: int
    pull_requests: int
    reviews: int
    source: str
    timestamp: datetime
    reliability: float
    is_bot: bool = False
    is_core: bool = False
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "contributor snapshot id")
        _require_text(self.repository_id, "contributor repository id")
        _require_text(self.contributor_id, "contributor id")
        _require_text(self.source, "contributor source")
        _require_datetime(self.timestamp, "contributor timestamp")
        object.__setattr__(self, "commits", max(self.commits, 0))
        object.__setattr__(self, "pull_requests", max(self.pull_requests, 0))
        object.__setattr__(self, "reviews", max(self.reviews, 0))
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class ReleaseSnapshot:
    id: str
    repository_id: str
    version: str
    released_at: datetime
    source: str
    reliability: float
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "release id")
        _require_text(self.repository_id, "release repository id")
        _require_text(self.version, "release version")
        _require_datetime(self.released_at, "release timestamp")
        _require_text(self.source, "release source")
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class PullRequestSnapshot:
    id: str
    repository_id: str
    created_at: datetime
    source: str
    reliability: float
    author: str = ""
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    is_bot: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "pull request id")
        _require_text(self.repository_id, "pull request repository id")
        _require_datetime(self.created_at, "pull request created_at")
        _require_text(self.source, "pull request source")
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class IssueSnapshot:
    id: str
    repository_id: str
    created_at: datetime
    source: str
    reliability: float
    author: str = ""
    closed_at: datetime | None = None
    is_bot: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "issue id")
        _require_text(self.repository_id, "issue repository id")
        _require_datetime(self.created_at, "issue created_at")
        _require_text(self.source, "issue source")
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class DeveloperEvent:
    id: str
    repository_id: str
    event_type: str
    timestamp: datetime
    source: str
    reliability: float
    actor: str = ""
    is_bot: bool = False
    automated: bool = False
    reference: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "developer event id")
        _require_text(self.repository_id, "developer event repository id")
        _require_text(self.event_type, "developer event type")
        _require_datetime(self.timestamp, "developer event timestamp")
        _require_text(self.source, "developer event source")
        object.__setattr__(self, "event_type", self.event_type.strip().lower().replace("-", "_").replace(" ", "_"))
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class DeveloperSnapshot:
    project: str
    repositories: tuple[RepositorySnapshot, ...] = ()
    contributors: tuple[ContributorSnapshot, ...] = ()
    releases: tuple[ReleaseSnapshot, ...] = ()
    pull_requests: tuple[PullRequestSnapshot, ...] = ()
    issues: tuple[IssueSnapshot, ...] = ()
    events: tuple[DeveloperEvent, ...] = ()
    source: str = "unknown"
    timestamp: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.project, "developer snapshot project")
        object.__setattr__(self, "repositories", tuple(self.repositories))
        object.__setattr__(self, "contributors", tuple(self.contributors))
        object.__setattr__(self, "releases", tuple(self.releases))
        object.__setattr__(self, "pull_requests", tuple(self.pull_requests))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class DeveloperDataset:
    project: str = "global-crypto"
    repositories: tuple[RepositorySnapshot, ...] = ()
    contributors: tuple[ContributorSnapshot, ...] = ()
    releases: tuple[ReleaseSnapshot, ...] = ()
    pull_requests: tuple[PullRequestSnapshot, ...] = ()
    issues: tuple[IssueSnapshot, ...] = ()
    events: tuple[DeveloperEvent, ...] = ()
    missing_fields: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def active_repositories(self) -> tuple[RepositorySnapshot, ...]:
        return tuple(repository for repository in self.repositories if not repository.is_archived)

    def core_repositories(self) -> tuple[RepositorySnapshot, ...]:
        return tuple(
            repository for repository in self.repositories if repository.is_core and not repository.is_archived
        )


@dataclass(frozen=True)
class DeveloperIndicator:
    name: str
    value: float
    direction: str
    confidence: float
    description: str
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeveloperAnalysis:
    indicators: tuple[DeveloperIndicator, ...]
    health: str
    trend: str
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


DeveloperRecord = (
    DeveloperSnapshot
    | RepositorySnapshot
    | ContributorSnapshot
    | ReleaseSnapshot
    | PullRequestSnapshot
    | IssueSnapshot
    | DeveloperEvent
)


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise DeveloperValidationError(f"Missing {field_name}")


def _require_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise DeveloperValidationError(f"Missing {field_name}")


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
