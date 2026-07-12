from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from hunter.acquisition.project_identifiers import load_project_identifiers
from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.execution.identity import identity
from hunter.market_validation.configuration import load_market_validation_config
from hunter.narrative.configuration import SUPPORTED_NARRATIVE_PROVIDERS

CONFIGURED_SOURCE_TYPES: tuple[str, ...] = ("github_releases", "github_tags")
REPORT_SOURCE_TYPES: tuple[str, ...] = (
    "official_website",
    "rss_feed",
    "official_blog",
    "documentation",
    "developer_docs",
    "github_repository",
    "github_releases",
    "github_discussions",
    "github_tags",
    "medium",
    "mirror",
    "governance_forum",
    "developer_portal",
    "knowledge_base",
    "foundation_website",
    "developer_blog",
)


@dataclass(frozen=True)
class DiscoveredNarrativeSource:
    evidence_id: str
    repository_id: str
    project_id: str
    source_type: str
    provider: str
    url: str
    domain: str
    source: str
    official: bool
    verified: bool
    priority: int
    language: str
    discovery_timestamp: datetime
    validation_timestamp: datetime
    trust_score: float
    freshness: float
    validation_status: str
    reason: str = ""


@dataclass(frozen=True)
class NarrativeDiscoveryRun:
    run_id: str
    started_at: datetime
    finished_at: datetime
    configured_projects: int
    discovered_sources: int
    verified_sources: int
    projects_resolved: int
    projects_partially_resolved: int
    projects_unresolved: int
    rejected_sources: int = 0
    duplicate_sources: int = 0


class NarrativeSourceDiscoveryEngine:
    def __init__(
        self,
        *,
        acquisition_repository: FileAcquisitionRepository | None = None,
        root: str | Path = "data/narrative_discovery",
    ) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()
        self.repository = NarrativeSourceDiscoveryRepository(root)

    def discover(self, project_ids: tuple[str, ...], *, as_of: datetime | None = None) -> NarrativeDiscoveryRun:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        raw_candidates = tuple(self._candidates(project_ids, timestamp))
        candidates = _dedupe(raw_candidates)
        self.repository.save_sources(candidates)
        self.merge_config(candidates)
        coverage = source_coverage(candidates, project_ids=project_ids)
        rejected = sum(1 for item in candidates if not item.verified)
        run = NarrativeDiscoveryRun(
            run_id=identity(
                "narrative-source-discovery-run",
                {
                    "projects": project_ids,
                    "timestamp": timestamp,
                    "sources": tuple(item.evidence_id for item in candidates),
                },
            ),
            started_at=timestamp,
            finished_at=timestamp,
            configured_projects=len(project_ids),
            discovered_sources=len(candidates),
            verified_sources=sum(1 for item in candidates if item.verified),
            projects_resolved=coverage.projects_resolved,
            projects_partially_resolved=coverage.projects_partially_resolved,
            projects_unresolved=coverage.projects_unresolved,
            rejected_sources=rejected,
            duplicate_sources=max(len(raw_candidates) - len(candidates), 0),
        )
        self.repository.save_run(run)
        return run

    def validate(self, project_ids: tuple[str, ...]) -> tuple[DiscoveredNarrativeSource, ...]:
        stored = self.repository.sources()
        return tuple(source for source in stored if source.project_id in set(project_ids))

    def merge_config(
        self, sources: tuple[DiscoveredNarrativeSource, ...], path: str | Path = "configs/narrative_sources.yaml"
    ) -> None:
        config_path = Path(path)
        payload = _read_config_payload(config_path)
        existing_sources = tuple(item for item in payload.get("sources", ()) if isinstance(item, dict))
        by_id: dict[str, dict[str, Any]] = {str(item.get("source_id", "")): dict(item) for item in existing_sources}
        for source in sources:
            if source.source_type not in CONFIGURED_SOURCE_TYPES or not source.verified:
                continue
            source_id = _source_id(source)
            if source_id in by_id and (
                by_id[source_id].get("manual") or by_id[source_id].get("approved") or by_id[source_id].get("deprecated")
            ):
                continue
            by_id.setdefault(source_id, _config_row(source, source_id))
        updated = {
            "enabled": bool(payload.get("enabled", True)),
            "expired_after_days": int(payload.get("expired_after_days", 365)),
            "sources": tuple(sorted(by_id.values(), key=lambda item: str(item.get("source_id", "")))),
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")

    def _candidates(self, project_ids: tuple[str, ...], timestamp: datetime) -> tuple[DiscoveredNarrativeSource, ...]:
        identifiers = load_project_identifiers()
        github_evidence = _valid_latest(
            self.acquisition_repository, provider="github", metric="github_repository_profile"
        )
        coingecko_detail = _valid_latest(
            self.acquisition_repository, provider="coingecko", metric="coingecko_detail_metadata"
        )
        rows = []
        for project_id in project_ids:
            detail = coingecko_detail.get(project_id)
            if detail is not None:
                for url in _urls(detail.raw_metrics.get("homepage")):
                    rows.append(
                        _source(
                            project_id,
                            "official_website",
                            "official_docs",
                            url,
                            "CoinGecko verified homepage",
                            timestamp,
                            0.82,
                        )
                    )
            identifier = identifiers.get(project_id)
            mapped_repositories = tuple(identifier.github_repositories if identifier else ())
            evidence_repository = (github_evidence[project_id].raw_source_id,) if project_id in github_evidence else ()
            repositories = (*mapped_repositories, *evidence_repository)
            for repository in repositories:
                github = github_evidence.get(project_id)
                if github is None:
                    continue
                repo_url = f"https://github.com/{repository}"
                rows.extend(
                    (
                        _source(
                            project_id,
                            "github_repository",
                            "github_tags",
                            repo_url,
                            "GitHub repository",
                            timestamp,
                            0.9,
                        ),
                        _source(
                            project_id,
                            "github_releases",
                            "github_releases",
                            f"{repo_url}/releases.atom",
                            "GitHub releases",
                            timestamp,
                            0.9,
                        ),
                        _source(
                            project_id,
                            "github_tags",
                            "github_tags",
                            f"{repo_url}/tags.atom",
                            "GitHub tags",
                            timestamp,
                            0.9,
                        ),
                        _source(
                            project_id,
                            "github_discussions",
                            "github_discussions",
                            f"{repo_url}/discussions",
                            "GitHub discussions",
                            timestamp,
                            0.78,
                        ),
                    )
                )
        return tuple(rows)


class NarrativeSourceDiscoveryRepository:
    def __init__(self, root: str | Path = "data/narrative_discovery") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_sources(self, sources: tuple[DiscoveredNarrativeSource, ...]) -> None:
        _write_jsonl(self.root / "sources.jsonl", (_source_payload(item) for item in sources))
        _write_jsonl(self.root / "validations.jsonl", (_validation_payload(item) for item in sources))

    def save_run(self, run: NarrativeDiscoveryRun) -> None:
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)

    def sources(self) -> tuple[DiscoveredNarrativeSource, ...]:
        return tuple(_source_from_payload(item) for item in _read_jsonl(self.root / "sources.jsonl"))

    def runs(self) -> tuple[NarrativeDiscoveryRun, ...]:
        return tuple(_run_from_payload(item) for item in _read_jsonl(self.root / "runs.jsonl"))


@dataclass(frozen=True)
class NarrativeSourceCoverage:
    configured_projects: int
    projects_resolved: int
    projects_partially_resolved: int
    projects_unresolved: int
    coverage_percent: float
    missing_by_project: dict[str, tuple[str, ...]]


def configured_project_ids(path: str | Path = "configs/market_validation.yaml") -> tuple[str, ...]:
    config = load_market_validation_config(Path(path))
    return tuple(project.project_id for project in config.project_universe)


def source_coverage(
    sources: tuple[DiscoveredNarrativeSource, ...],
    *,
    project_ids: tuple[str, ...],
) -> NarrativeSourceCoverage:
    verified_by_project: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        if source.verified:
            verified_by_project[source.project_id].add(source.source_type)
    missing = {
        project_id: tuple(
            source_type for source_type in REPORT_SOURCE_TYPES if source_type not in verified_by_project[project_id]
        )
        for project_id in project_ids
    }
    resolved = sum(1 for project_id in project_ids if not missing[project_id])
    partial = sum(1 for project_id in project_ids if verified_by_project[project_id] and missing[project_id])
    unresolved = sum(1 for project_id in project_ids if not verified_by_project[project_id])
    total_slots = max(len(project_ids) * len(REPORT_SOURCE_TYPES), 1)
    found_slots = sum(len(verified_by_project[project_id]) for project_id in project_ids)
    return NarrativeSourceCoverage(
        configured_projects=len(project_ids),
        projects_resolved=resolved,
        projects_partially_resolved=partial,
        projects_unresolved=unresolved,
        coverage_percent=round((found_slots / total_slots) * 100.0, 2),
        missing_by_project=missing,
    )


def _source(
    project_id: str,
    source_type: str,
    provider: str,
    url: str,
    source: str,
    timestamp: datetime,
    trust_score: float,
) -> DiscoveredNarrativeSource:
    domain = urlparse(url).netloc.lower()
    verified = bool(project_id and domain and provider in SUPPORTED_NARRATIVE_PROVIDERS)
    evidence_id = identity("narrative-source", {"project": project_id, "source_type": source_type, "url": url})
    return DiscoveredNarrativeSource(
        evidence_id=evidence_id,
        repository_id=f"narrative-source:{project_id}:{source_type}:{domain}",
        project_id=project_id,
        source_type=source_type,
        provider=provider,
        url=url,
        domain=domain,
        source=source,
        official=verified,
        verified=verified,
        priority=_priority(source_type),
        language="en",
        discovery_timestamp=timestamp,
        validation_timestamp=timestamp,
        trust_score=round(trust_score if verified else 0.0, 4),
        freshness=1.0 if verified else 0.0,
        validation_status="VALID" if verified else "INVALID",
        reason="verified from persisted upstream evidence" if verified else "unverified source",
    )


def _dedupe(sources: tuple[DiscoveredNarrativeSource, ...]) -> tuple[DiscoveredNarrativeSource, ...]:
    by_url: dict[str, DiscoveredNarrativeSource] = {}
    for source in sources:
        current = by_url.get(source.url)
        if current is None or source.trust_score > current.trust_score:
            by_url[source.url] = source
    return tuple(sorted(by_url.values(), key=lambda item: (item.project_id, item.source_type, item.url)))


def _valid_latest(repository: FileAcquisitionRepository, *, provider: str, metric: str):
    rows = {}
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if (
            evidence.provider != provider
            or evidence.metric != metric
            or validation is None
            or validation.status != "valid"
        ):
            continue
        current = rows.get(evidence.target_id)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            rows[evidence.target_id] = evidence
    return rows


def _urls(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _priority(source_type: str) -> int:
    return {
        "rss_feed": 1,
        "official_blog": 2,
        "github_releases": 3,
        "official_website": 4,
        "documentation": 5,
        "github_tags": 6,
        "github_repository": 7,
        "github_discussions": 8,
    }.get(source_type, 10)


def _source_id(source: DiscoveredNarrativeSource) -> str:
    return f"{source.project_id}-{source.source_type}-{identity('url', source.url)[-12:]}"


def _config_row(source: DiscoveredNarrativeSource, source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "provider": source.provider,
        "project_id": source.project_id,
        "url": source.url,
        "source": source.source,
        "language": source.language,
        "enabled": True,
        "discovered": True,
        "source_type": source.source_type,
        "priority": source.priority,
        "trust_score": source.trust_score,
        "repository_id": source.repository_id,
        "evidence_id": source.evidence_id,
    }


def _read_config_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"enabled": True, "expired_after_days": 365, "sources": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(payload) if isinstance(payload, dict) else {"enabled": True, "expired_after_days": 365, "sources": []}


def _source_payload(item: DiscoveredNarrativeSource) -> dict[str, Any]:
    payload = asdict(item)
    payload["discovery_timestamp"] = item.discovery_timestamp.isoformat()
    payload["validation_timestamp"] = item.validation_timestamp.isoformat()
    return payload


def _validation_payload(item: DiscoveredNarrativeSource) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "repository_id": item.repository_id,
        "validation_status": item.validation_status,
        "validated_at": item.validation_timestamp.isoformat(),
        "trust_score": item.trust_score,
        "freshness": item.freshness,
        "reason": item.reason,
    }


def _run_payload(item: NarrativeDiscoveryRun) -> dict[str, Any]:
    payload = asdict(item)
    payload["started_at"] = item.started_at.isoformat()
    payload["finished_at"] = item.finished_at.isoformat()
    return payload


def _source_from_payload(payload: dict[str, Any]) -> DiscoveredNarrativeSource:
    return DiscoveredNarrativeSource(
        evidence_id=str(payload["evidence_id"]),
        repository_id=str(payload["repository_id"]),
        project_id=str(payload["project_id"]),
        source_type=str(payload["source_type"]),
        provider=str(payload["provider"]),
        url=str(payload["url"]),
        domain=str(payload["domain"]),
        source=str(payload["source"]),
        official=bool(payload["official"]),
        verified=bool(payload["verified"]),
        priority=int(payload["priority"]),
        language=str(payload["language"]),
        discovery_timestamp=datetime.fromisoformat(str(payload["discovery_timestamp"])).astimezone(UTC),
        validation_timestamp=datetime.fromisoformat(str(payload["validation_timestamp"])).astimezone(UTC),
        trust_score=float(payload["trust_score"]),
        freshness=float(payload["freshness"]),
        validation_status=str(payload["validation_status"]),
        reason=str(payload.get("reason", "")),
    )


def _run_from_payload(payload: dict[str, Any]) -> NarrativeDiscoveryRun:
    return NarrativeDiscoveryRun(
        run_id=str(payload["run_id"]),
        started_at=datetime.fromisoformat(str(payload["started_at"])).astimezone(UTC),
        finished_at=datetime.fromisoformat(str(payload["finished_at"])).astimezone(UTC),
        configured_projects=int(payload["configured_projects"]),
        discovered_sources=int(payload["discovered_sources"]),
        verified_sources=int(payload["verified_sources"]),
        projects_resolved=int(payload["projects_resolved"]),
        projects_partially_resolved=int(payload["projects_partially_resolved"]),
        projects_unresolved=int(payload["projects_unresolved"]),
        rejected_sources=int(payload.get("rejected_sources", 0)),
        duplicate_sources=int(payload.get("duplicate_sources", 0)),
    )


def _write_jsonl(path: Path, rows: Any, *, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
