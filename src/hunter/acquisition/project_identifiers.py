from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

IdentifierStatus = Literal[
    "RESOLVED",
    "INVALID_ID",
    "AMBIGUOUS",
    "NOT_FOUND",
    "RATE_LIMITED",
    "REQUEST_FAILED",
    "UNSUPPORTED",
]


@dataclass(frozen=True)
class ProjectIdentifier:
    project_id: str
    coingecko_id: str | None = None
    defillama_slug: str | None = None
    github_repositories: tuple[str, ...] = ()
    unsupported: bool = False
    ambiguous: bool = False
    defillama_unsupported: bool = False
    defillama_ambiguous: bool = False
    github_unsupported: bool = False
    github_ambiguous: bool = False

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            msg = "project_id is required"
            raise ValueError(msg)
        if self.coingecko_id is not None and not self.coingecko_id.strip():
            msg = "coingecko_id cannot be blank"
            raise ValueError(msg)
        if self.defillama_slug is not None and not self.defillama_slug.strip():
            msg = "defillama_slug cannot be blank"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "github_repositories",
            tuple(str(item).strip() for item in self.github_repositories if str(item).strip()),
        )


@dataclass(frozen=True)
class ProjectIdentifierResolution:
    project_id: str
    coingecko_id: str | None
    status: IdentifierStatus
    reason: str = ""


def load_project_identifiers(path: str | Path = "configs/project_identifiers.yaml") -> dict[str, ProjectIdentifier]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "project identifier configuration must be a mapping"
        raise ValueError(msg)
    identifiers = {}
    for project_id, raw in payload.items():
        if not isinstance(raw, dict):
            msg = f"project identifier entry must be a mapping: {project_id}"
            raise ValueError(msg)
        github_repositories = raw.get("github_repositories", ())
        if isinstance(github_repositories, str):
            github_repositories = (github_repositories,)
        identifiers[str(project_id)] = ProjectIdentifier(
            project_id=str(project_id),
            coingecko_id=str(raw["coingecko_id"]) if raw.get("coingecko_id") else None,
            defillama_slug=str(raw["defillama_slug"]) if raw.get("defillama_slug") else None,
            github_repositories=tuple(str(item) for item in github_repositories),
            unsupported=bool(raw.get("unsupported", False)),
            ambiguous=bool(raw.get("ambiguous", False)),
            defillama_unsupported=bool(raw.get("defillama_unsupported", False)),
            defillama_ambiguous=bool(raw.get("defillama_ambiguous", False)),
            github_unsupported=bool(raw.get("github_unsupported", False)),
            github_ambiguous=bool(raw.get("github_ambiguous", False)),
        )
    return identifiers


def resolve_configured_identifiers(
    project_ids: tuple[str, ...],
    identifiers: dict[str, ProjectIdentifier],
    available_coingecko_ids: set[str],
    *,
    failed: bool = False,
    rate_limited: bool = False,
) -> tuple[ProjectIdentifierResolution, ...]:
    rows = []
    for project_id in project_ids:
        identifier = identifiers.get(project_id)
        if identifier is None:
            rows.append(ProjectIdentifierResolution(project_id, None, "INVALID_ID", "missing explicit mapping"))
            continue
        if identifier.unsupported:
            rows.append(
                ProjectIdentifierResolution(project_id, identifier.coingecko_id, "UNSUPPORTED", "marked unsupported")
            )
            continue
        if identifier.ambiguous:
            rows.append(
                ProjectIdentifierResolution(
                    project_id, identifier.coingecko_id, "AMBIGUOUS", "mapping marked ambiguous"
                )
            )
            continue
        if not identifier.coingecko_id:
            rows.append(ProjectIdentifierResolution(project_id, None, "INVALID_ID", "missing coingecko_id"))
            continue
        if rate_limited:
            rows.append(
                ProjectIdentifierResolution(
                    project_id, identifier.coingecko_id, "RATE_LIMITED", "validation rate limited"
                )
            )
            continue
        if failed:
            rows.append(
                ProjectIdentifierResolution(project_id, identifier.coingecko_id, "REQUEST_FAILED", "validation failed")
            )
            continue
        if identifier.coingecko_id not in available_coingecko_ids:
            rows.append(
                ProjectIdentifierResolution(
                    project_id, identifier.coingecko_id, "NOT_FOUND", "CoinGecko market data not found"
                )
            )
            continue
        rows.append(ProjectIdentifierResolution(project_id, identifier.coingecko_id, "RESOLVED"))
    return tuple(rows)


def coingecko_sync_ids(resolutions: tuple[ProjectIdentifierResolution, ...]) -> tuple[str, ...]:
    return tuple(row.coingecko_id for row in resolutions if row.status == "RESOLVED" and row.coingecko_id)


def coingecko_target_map(resolutions: tuple[ProjectIdentifierResolution, ...]) -> dict[str, str]:
    return {
        str(row.coingecko_id): row.project_id for row in resolutions if row.status == "RESOLVED" and row.coingecko_id
    }


def resolve_defillama_identifiers(
    project_ids: tuple[str, ...],
    identifiers: dict[str, ProjectIdentifier],
    available_slugs: set[str],
    *,
    failed: bool = False,
    rate_limited: bool = False,
) -> tuple[ProjectIdentifierResolution, ...]:
    rows = []
    for project_id in project_ids:
        identifier = identifiers.get(project_id)
        if identifier is None:
            rows.append(ProjectIdentifierResolution(project_id, None, "INVALID_ID", "missing explicit mapping"))
            continue
        if identifier.defillama_unsupported:
            rows.append(
                ProjectIdentifierResolution(
                    project_id,
                    identifier.defillama_slug,
                    "UNSUPPORTED",
                    "marked unsupported",
                )
            )
            continue
        if identifier.defillama_ambiguous:
            rows.append(
                ProjectIdentifierResolution(
                    project_id,
                    identifier.defillama_slug,
                    "AMBIGUOUS",
                    "mapping marked ambiguous",
                )
            )
            continue
        if not identifier.defillama_slug:
            rows.append(ProjectIdentifierResolution(project_id, None, "INVALID_ID", "missing defillama_slug"))
            continue
        if rate_limited:
            rows.append(
                ProjectIdentifierResolution(
                    project_id,
                    identifier.defillama_slug,
                    "RATE_LIMITED",
                    "validation rate limited",
                )
            )
            continue
        if failed:
            rows.append(
                ProjectIdentifierResolution(
                    project_id,
                    identifier.defillama_slug,
                    "REQUEST_FAILED",
                    "validation failed",
                )
            )
            continue
        if identifier.defillama_slug not in available_slugs:
            rows.append(
                ProjectIdentifierResolution(
                    project_id,
                    identifier.defillama_slug,
                    "NOT_FOUND",
                    "DefiLlama protocol not found",
                )
            )
            continue
        rows.append(ProjectIdentifierResolution(project_id, identifier.defillama_slug, "RESOLVED"))
    return tuple(rows)


def defillama_sync_ids(resolutions: tuple[ProjectIdentifierResolution, ...]) -> tuple[str, ...]:
    return tuple(row.coingecko_id for row in resolutions if row.status == "RESOLVED" and row.coingecko_id)


def defillama_target_map(resolutions: tuple[ProjectIdentifierResolution, ...]) -> dict[str, str]:
    return {
        str(row.coingecko_id): row.project_id for row in resolutions if row.status == "RESOLVED" and row.coingecko_id
    }


@dataclass(frozen=True)
class GitHubRepositoryResolution:
    project_id: str
    repository: str | None
    status: IdentifierStatus
    reason: str = ""


def resolve_github_identifiers(
    project_ids: tuple[str, ...],
    identifiers: dict[str, ProjectIdentifier],
    available_repositories: set[str],
    *,
    failed: bool = False,
    rate_limited: bool = False,
) -> tuple[GitHubRepositoryResolution, ...]:
    rows = []
    for project_id in project_ids:
        identifier = identifiers.get(project_id)
        if identifier is None:
            rows.append(GitHubRepositoryResolution(project_id, None, "INVALID_ID", "missing explicit mapping"))
            continue
        if identifier.github_unsupported:
            rows.append(GitHubRepositoryResolution(project_id, None, "UNSUPPORTED", "marked unsupported"))
            continue
        if identifier.github_ambiguous:
            rows.append(GitHubRepositoryResolution(project_id, None, "AMBIGUOUS", "mapping marked ambiguous"))
            continue
        if not identifier.github_repositories:
            rows.append(GitHubRepositoryResolution(project_id, None, "INVALID_ID", "missing github_repositories"))
            continue
        for repository in identifier.github_repositories:
            if rate_limited:
                rows.append(
                    GitHubRepositoryResolution(project_id, repository, "RATE_LIMITED", "validation rate limited")
                )
            elif failed:
                rows.append(GitHubRepositoryResolution(project_id, repository, "REQUEST_FAILED", "validation failed"))
            elif repository.lower() not in available_repositories:
                rows.append(
                    GitHubRepositoryResolution(project_id, repository, "NOT_FOUND", "GitHub repository not found")
                )
            else:
                rows.append(GitHubRepositoryResolution(project_id, repository, "RESOLVED"))
    return tuple(rows)


def github_sync_ids(resolutions: tuple[GitHubRepositoryResolution, ...]) -> tuple[str, ...]:
    return tuple(row.repository for row in resolutions if row.status == "RESOLVED" and row.repository)


def github_target_map(resolutions: tuple[GitHubRepositoryResolution, ...]) -> dict[str, str]:
    return {
        str(row.repository).lower(): row.project_id
        for row in resolutions
        if row.status == "RESOLVED" and row.repository
    }
