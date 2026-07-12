from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.acquisition.models import (
    AcquisitionRequest,
    NormalizedEvidence,
    ProviderHealth,
    ProviderMetadata,
    RateLimit,
    RawEvidence,
)
from hunter.execution.identity import identity

GITHUB_MANDATORY_FIELDS: tuple[str, ...] = (
    "repository_name",
    "owner",
    "default_branch",
    "stars",
    "forks",
    "watchers",
    "open_issues",
    "updated_at",
)


@dataclass(frozen=True)
class GitHubResponse:
    payload: object
    etag: str | None = None
    not_modified: bool = False


class GitHubTransport(Protocol):
    def get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        etag: str | None = None,
    ) -> GitHubResponse:
        raise NotImplementedError


@dataclass(frozen=True)
class GitHubProviderConfig:
    base_url: str = "https://api.github.com"
    token: str | None = None
    request_timeout_seconds: int = 30
    per_page: int = 100
    max_pages: int = 3
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    jitter_seconds: float = 0.25
    min_interval_seconds: float = 0.0
    commit_period_days: int = 365

    def __post_init__(self) -> None:
        if self.per_page < 1 or self.per_page > 100:
            msg = "GitHub per_page must be between 1 and 100"
            raise ValueError(msg)
        if self.max_pages < 1 or self.max_attempts < 1 or self.commit_period_days < 1:
            msg = "GitHub pagination, retry, and period settings must be positive"
            raise ValueError(msg)
        if self.backoff_seconds < 0 or self.jitter_seconds < 0 or self.min_interval_seconds < 0:
            msg = "GitHub timing settings must be non-negative"
            raise ValueError(msg)


@dataclass
class GitHubProviderStatistics:
    request_count: int = 0
    success_count: int = 0
    not_modified_count: int = 0
    retry_count: int = 0
    rate_limit_count: int = 0
    accepted_record_count: int = 0
    rejected_record_count: int = 0
    commit_record_count: int = 0
    contributor_record_count: int = 0
    release_record_count: int = 0
    etag_reused_count: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def success_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return round(self.success_count / self.request_count, 4)


class GitHubHTTPError(ProviderUnavailableError):
    def __init__(self, status_code: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class GitHubClient:
    def __init__(self, config: GitHubProviderConfig) -> None:
        self.config = config

    def get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        etag: str | None = None,
    ) -> GitHubResponse:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
        }
        token = self.config.token or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["authorization"] = f"Bearer {token}"
        if etag:
            headers["if-none-match"] = etag
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
                return GitHubResponse(json.loads(payload), etag=response.headers.get("ETag"))
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return GitHubResponse({}, etag=etag, not_modified=True)
            raise GitHubHTTPError(exc.code, str(exc), retry_after=_retry_after(exc)) from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"GitHub request failed: {exc}") from exc


class GitHubRateLimiter:
    def __init__(self, *, min_interval_seconds: float = 0.0) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.next_allowed_at: datetime | None = None
        self.delays: list[float] = []

    def before_request(self, now: datetime) -> float:
        delay = 0.0
        if self.next_allowed_at is not None and now < self.next_allowed_at:
            delay = (self.next_allowed_at - now).total_seconds()
            self.delays.append(delay)
        self.next_allowed_at = now + timedelta(seconds=self.min_interval_seconds)
        return delay


class GitHubProvider:
    def __init__(
        self,
        config: GitHubProviderConfig | None = None,
        *,
        transport: GitHubTransport | None = None,
        rate_limiter: GitHubRateLimiter | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or GitHubProviderConfig()
        self.transport = transport or GitHubClient(self.config)
        self.rate_limiter = rate_limiter or GitHubRateLimiter(min_interval_seconds=self.config.min_interval_seconds)
        self.sleeper = sleeper or time.sleep
        self.statistics = GitHubProviderStatistics()
        self._last_sync: datetime | None = None
        self._enrichment_degraded = False

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="github",
            capabilities=("developer", "github", "repository"),
            supported_metrics=("github_repository_profile",),
            rate_limits=(RateLimit(requests=1, window_seconds=max(1, int(self.config.min_interval_seconds or 1))),),
            last_sync=self._last_sync,
            availability="available",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name="github",
            availability="available",
            checked_at=datetime.now(tz=UTC),
            last_sync=self._last_sync,
            message=(
                f"configured success_rate={self.statistics.success_rate:.4f} "
                f"requests={self.statistics.request_count} retries={self.statistics.retry_count} "
                f"rate_limits={self.statistics.rate_limit_count}"
            ),
        )

    def repository_exists(self, repository: str) -> bool:
        try:
            response = self._request(f"/repos/{repository}")
        except GitHubHTTPError as exc:
            if exc.status_code == 404:
                return False
            raise
        except ProviderUnavailableError:
            raise
        return isinstance(response.payload, dict) and bool(response.payload.get("full_name"))

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        repositories = _project_ids(request)
        target_map = _target_map(request)
        response_cache = _response_cache(request)
        rows = []
        for page, repository in enumerate(repositories, start=1):
            if request.checkpoint and page < _checkpoint_page(request.checkpoint):
                continue
            try:
                payload = self._repository_payload(repository, request, response_cache, page=page)
            except ProviderUnavailableError as exc:
                self.statistics.rejected_record_count += 1
                self.statistics.rejection_reasons[f"repository_unavailable:{type(exc).__name__}"] += 1
                continue
            if _completeness(payload) < 1.0:
                self.statistics.rejected_record_count += 1
                for field_name in _missing_fields(payload):
                    self.statistics.rejection_reasons[f"missing_mandatory:{field_name}"] += 1
                continue
            self.statistics.accepted_record_count += 1
            if payload.get("commit_count"):
                self.statistics.commit_record_count += 1
            if payload.get("contributors_count"):
                self.statistics.contributor_record_count += 1
            if payload.get("releases"):
                self.statistics.release_record_count += 1
            rows.append(
                RawEvidence(
                    provider="github",
                    collector="github-rest-api",
                    raw_source_id=repository.lower(),
                    domain="github",
                    metric="github_repository_profile",
                    target_id=target_map.get(repository.lower(), repository.lower()),
                    retrieved_at=request.requested_at,
                    payload=payload,
                    source_url=f"https://github.com/{repository}",
                    repository_id=f"github:{target_map.get(repository.lower(), repository.lower())}:{repository.lower()}",
                )
            )
        self._last_sync = request.requested_at
        return tuple(rows)

    def _repository_payload(
        self,
        repository: str,
        request: AcquisitionRequest,
        response_cache: dict[str, dict[str, Any]],
        page: int,
    ) -> dict[str, Any]:
        repo_cache = response_cache.get(repository.lower(), {})
        repo_response = self._request(f"/repos/{repository}", etag=_etag(repo_cache, "repo"))
        if repo_response.not_modified:
            cached_payload = repo_cache.get("payload")
            if isinstance(cached_payload, dict):
                self.statistics.etag_reused_count += 1
                return dict(cached_payload)
        repo = _dict_payload(repo_response.payload, "GitHub repository response must be an object")
        owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
        contributors: tuple[dict[str, Any], ...] = ()
        commits_365: tuple[dict[str, Any], ...] = ()
        commits_30: tuple[dict[str, Any], ...] = ()
        commits_90: tuple[dict[str, Any], ...] = ()
        releases: tuple[dict[str, Any], ...] = ()
        tags: tuple[dict[str, Any], ...] = ()
        languages: dict[str, Any] = {}
        closed_issues: tuple[dict[str, Any], ...] = ()
        if not self._enrichment_degraded:
            try:
                contributors = self._paged(f"/repos/{repository}/contributors", {"anon": "false"})
                since = (
                    (request.requested_at - timedelta(days=self.config.commit_period_days))
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                commits_365 = self._paged(f"/repos/{repository}/commits", {"since": since})
                commits_30 = self._paged(
                    f"/repos/{repository}/commits",
                    {"since": (request.requested_at - timedelta(days=30)).isoformat().replace("+00:00", "Z")},
                )
                commits_90 = self._paged(
                    f"/repos/{repository}/commits",
                    {"since": (request.requested_at - timedelta(days=90)).isoformat().replace("+00:00", "Z")},
                )
                releases = self._paged(f"/repos/{repository}/releases", {})
                tags = self._paged(f"/repos/{repository}/tags", {})
                languages_response = self._request(f"/repos/{repository}/languages")
                languages = languages_response.payload if isinstance(languages_response.payload, dict) else {}
                closed_issues = self._paged(f"/repos/{repository}/issues", {"state": "closed"})
            except ProviderUnavailableError:
                self._enrichment_degraded = True
        payload = {
            "repository_name": repo.get("name"),
            "full_name": repo.get("full_name") or repository,
            "owner": owner.get("login") or repository.split("/", 1)[0],
            "default_branch": repo.get("default_branch"),
            "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"),
            "watchers": repo.get("watchers_count"),
            "open_issues": repo.get("open_issues_count"),
            "closed_issues": len(closed_issues),
            "contributors_count": len(contributors),
            "commit_count": len(commits_365),
            "commits_30d": len(commits_30),
            "commits_90d": len(commits_90),
            "commits_365d": len(commits_365),
            "active_contributors": len(_active_contributors(commits_365)),
            "releases": len(releases),
            "latest_release": _latest_release(releases),
            "tags": tuple(str(item.get("name")) for item in tags if isinstance(item, dict) and item.get("name")),
            "languages": dict(languages),
            "license": _license(repo),
            "created_at": repo.get("created_at"),
            "updated_at": repo.get("updated_at"),
            "last_commit_timestamp": _last_commit_timestamp(commits_365),
            "etag": repo_response.etag,
            "last_updated": repo.get("updated_at") or request.requested_at.isoformat(),
            "page": page,
        }
        return payload

    def _paged(self, path: str, params: dict[str, object]) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for page in range(1, self.config.max_pages + 1):
            response = self._request(path, {**params, "per_page": self.config.per_page, "page": page})
            if response.not_modified:
                break
            if not isinstance(response.payload, list):
                raise ProviderUnavailableError("GitHub paged response must be a list")
            page_rows = tuple(item for item in response.payload if isinstance(item, dict))
            rows.extend(page_rows)
            if len(page_rows) < self.config.per_page:
                break
        return tuple(rows)

    def _request(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        etag: str | None = None,
    ) -> GitHubResponse:
        errors: list[Exception] = []
        for attempt in range(self.config.max_attempts):
            try:
                pacing_delay = self.rate_limiter.before_request(datetime.now(tz=UTC))
                if pacing_delay > 0:
                    self.sleeper(pacing_delay)
                self.statistics.request_count += 1
                response = self.transport.get_json(path, params or {}, etag=etag)
                if response.not_modified:
                    self.statistics.not_modified_count += 1
                else:
                    self.statistics.success_count += 1
                return response
            except GitHubHTTPError as exc:
                if exc.status_code == 429 or exc.status_code == 403:
                    self.statistics.rate_limit_count += 1
                    self._enrichment_degraded = True
                if exc.status_code not in {403, 408, 409, 425, 429, 500, 502, 503, 504}:
                    raise
                errors.append(exc)
            except ProviderUnavailableError as exc:
                errors.append(exc)
            if attempt < self.config.max_attempts - 1:
                self.statistics.retry_count += 1
                delay = _retry_delay(errors[-1], attempt=attempt, config=self.config, path=path)
                if delay > 0:
                    self.sleeper(delay)
        raise errors[-1]


class GitHubEvidenceNormalizer:
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        evidence = []
        for item in raw:
            confidence = _completeness(item.payload)
            evidence.append(
                NormalizedEvidence(
                    evidence_id=identity(
                        "github-evidence",
                        {
                            "repository": item.raw_source_id,
                            "last_updated": item.payload.get("last_updated"),
                            "retrieved_at": item.retrieved_at,
                        },
                    ),
                    repository_id=item.repository_id,
                    provider=item.provider,
                    collector=item.collector,
                    raw_source_id=item.raw_source_id,
                    domain=item.domain,
                    metric=item.metric,
                    target_id=item.target_id,
                    value=item.raw_source_id,
                    raw_metrics=dict(item.payload),
                    normalized_metrics={
                        "schema_completeness": confidence,
                        "has_commits": 1.0 if item.payload.get("commit_count") else 0.0,
                        "has_contributors": 1.0 if item.payload.get("contributors_count") else 0.0,
                        "has_releases": 1.0 if item.payload.get("releases") else 0.0,
                    },
                    source_url=item.source_url,
                    retrieved_at=item.retrieved_at,
                    normalized_at=request.requested_at,
                    confidence=confidence,
                    freshness=_freshness(item.payload, request.requested_at),
                    raw_evidence_id=item.raw_source_id,
                )
            )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))


def _dict_payload(payload: object, message: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProviderUnavailableError(message)
    return payload


def _etag(cache: dict[str, Any], name: str) -> str | None:
    etags = cache.get("etags")
    if not isinstance(etags, dict):
        return None
    raw = etags.get(name)
    return str(raw) if raw else None


def _project_ids(request: AcquisitionRequest) -> tuple[str, ...]:
    raw = request.parameters.get("project_ids")
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    if isinstance(raw, tuple | list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def _target_map(request: AcquisitionRequest) -> dict[str, str]:
    raw = request.parameters.get("target_map")
    if not isinstance(raw, dict):
        return {}
    return {str(key).lower(): str(value) for key, value in raw.items() if str(key).strip() and str(value).strip()}


def _response_cache(request: AcquisitionRequest) -> dict[str, dict[str, Any]]:
    raw = request.parameters.get("response_cache")
    if not isinstance(raw, dict):
        return {}
    return {str(key).lower(): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _checkpoint_page(checkpoint: str | None) -> int:
    if not checkpoint:
        return 1
    try:
        return max(1, int(checkpoint.split(":", 1)[-1]))
    except ValueError:
        return 1


def _active_contributors(commits: tuple[dict[str, Any], ...]) -> set[str]:
    contributors = set()
    for commit in commits:
        author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
        login = author.get("login")
        if login:
            contributors.add(str(login))
    return contributors


def _latest_release(releases: tuple[dict[str, Any], ...]) -> str | None:
    for release in releases:
        name = release.get("tag_name") or release.get("name")
        if name:
            return str(name)
    return None


def _license(repo: dict[str, Any]) -> str | None:
    raw = repo.get("license")
    if not isinstance(raw, dict):
        return None
    key = raw.get("spdx_id") or raw.get("key") or raw.get("name")
    return str(key) if key else None


def _last_commit_timestamp(commits: tuple[dict[str, Any], ...]) -> str | None:
    for commit in commits:
        payload = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        committer = payload.get("committer") if isinstance(payload.get("committer"), dict) else {}
        date = committer.get("date")
        if date:
            return str(date)
    return None


def _completeness(payload: dict[str, Any]) -> float:
    present = sum(1 for field in GITHUB_MANDATORY_FIELDS if field in payload and _present(payload.get(field)))
    return round(present / len(GITHUB_MANDATORY_FIELDS), 4)


def _missing_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    return tuple(field for field in GITHUB_MANDATORY_FIELDS if field not in payload or not _present(payload.get(field)))


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple | list | dict):
        return bool(value)
    return True


def _freshness(payload: dict[str, Any], as_of: datetime) -> float:
    last_updated = payload.get("last_updated")
    if not isinstance(last_updated, str) or not last_updated:
        return 0.0
    try:
        updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return 0.0
    age_days = max((as_of - updated).total_seconds() / 86_400, 0.0)
    return round(max(0.0, min(1.0, 1.0 - (age_days / 30.0))), 4)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed.astimezone(UTC) - datetime.now(tz=UTC)).total_seconds())


def _retry_delay(
    error: Exception,
    *,
    attempt: int,
    config: GitHubProviderConfig,
    path: str,
) -> float:
    if isinstance(error, GitHubHTTPError) and error.retry_after is not None:
        return error.retry_after
    base = config.backoff_seconds * (2**attempt)
    if base == 0:
        return 0.0
    jitter_seed = sum(ord(char) for char in f"{path}:{attempt}") % 1000
    jitter = (jitter_seed / 1000) * config.jitter_seconds
    return round(base + jitter, 4)
