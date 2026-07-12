from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition import AcquisitionConfig, AcquisitionPipeline, AcquisitionRequest, CacheConfig, RetryConfig
from hunter.acquisition.project_identifiers import (
    ProjectIdentifier,
    github_sync_ids,
    github_target_map,
    resolve_github_identifiers,
)
from hunter.acquisition.providers.github import (
    GitHubEvidenceNormalizer,
    GitHubHTTPError,
    GitHubProvider,
    GitHubProviderConfig,
    GitHubRateLimiter,
    GitHubResponse,
)
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.acquisition.validator import EvidenceAcquisitionValidator
from hunter.cli import main

NOW = datetime(2026, 7, 11, tzinfo=UTC)


class FakeGitHubTransport:
    def __init__(
        self,
        *,
        fail_once: bool = False,
        not_modified: bool = False,
        rate_limited_repository: bool = False,
    ) -> None:
        self.fail_once = fail_once
        self.not_modified = not_modified
        self.rate_limited_repository = rate_limited_repository
        self.calls: list[tuple[str, dict[str, object], str | None]] = []

    def get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        etag: str | None = None,
    ) -> GitHubResponse:
        self.calls.append((path, dict(params or {}), etag))
        if self.fail_once:
            self.fail_once = False
            raise GitHubHTTPError(429, "rate limited", retry_after=0)
        if self.rate_limited_repository and path == "/repos/bitcoin/bitcoin":
            raise GitHubHTTPError(403, "rate limited", retry_after=0)
        if self.not_modified and path == "/repos/bitcoin/bitcoin" and etag:
            return GitHubResponse({}, etag=etag, not_modified=True)
        if path == "/repos/bitcoin/bitcoin":
            return GitHubResponse(_repo(), etag='"repo-etag"')
        if path.endswith("/contributors"):
            return GitHubResponse([{"login": "alice"}, {"login": "bob"}])
        if path.endswith("/commits"):
            return GitHubResponse([_commit("alice"), _commit("bob")])
        if path.endswith("/releases"):
            return GitHubResponse([{"tag_name": "v1.0.0"}])
        if path.endswith("/tags"):
            return GitHubResponse([{"name": "v1.0.0"}, {"name": "v0.9.0"}])
        if path.endswith("/languages"):
            return GitHubResponse({"C++": 100, "Python": 5})
        if path.endswith("/issues"):
            return GitHubResponse([{"number": 1}])
        raise GitHubHTTPError(404, "not found")


def request(
    repositories: tuple[str, ...] = ("bitcoin/bitcoin",),
    *,
    mode: str = "incremental",
    checkpoint: str | None = None,
    response_cache: dict[str, dict[str, object]] | None = None,
) -> AcquisitionRequest:
    parameters: dict[str, object] = {"project_ids": repositories, "target_map": {"bitcoin/bitcoin": "bitcoin"}}
    if response_cache is not None:
        parameters["response_cache"] = response_cache
    return AcquisitionRequest(
        domain="github",
        metric="github_repository_profile",
        target_id="configured-projects",
        requested_at=NOW,
        mode=mode,  # type: ignore[arg-type]
        checkpoint=checkpoint,
        parameters=parameters,
    )


def provider(transport: FakeGitHubTransport, *, min_interval_seconds: float = 0.0) -> GitHubProvider:
    return GitHubProvider(
        GitHubProviderConfig(max_attempts=2, backoff_seconds=0, min_interval_seconds=min_interval_seconds),
        transport=transport,
        rate_limiter=GitHubRateLimiter(min_interval_seconds=min_interval_seconds),
        sleeper=lambda _delay: None,
    )


def test_github_repository_mapping_and_api_parsing() -> None:
    raw = provider(FakeGitHubTransport()).fetch(request())
    normalized = GitHubEvidenceNormalizer().normalize(raw, request())

    assert raw[0].target_id == "bitcoin"
    assert raw[0].payload["repository_name"] == "bitcoin"
    assert raw[0].payload["owner"] == "bitcoin"
    assert raw[0].payload["stars"] == 10
    assert raw[0].payload["contributors_count"] == 2
    assert raw[0].payload["commit_count"] == 2
    assert raw[0].payload["releases"] == 1
    assert raw[0].payload["languages"] == {"C++": 100, "Python": 5}
    assert normalized[0].confidence == 1.0


def test_github_pagination_etag_cache_resume_rate_limit_persistence_and_duplicate_detection() -> None:
    repository = InMemoryAcquisitionRepository()
    transport = FakeGitHubTransport(fail_once=True)
    pipeline = AcquisitionPipeline(
        normalizer=GitHubEvidenceNormalizer(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=True, ttl_seconds=300)),
    )
    github = provider(transport, min_interval_seconds=1.0)

    first = pipeline.sync(github, request(("bitcoin/bitcoin", "bitcoin/bitcoin")))
    second = pipeline.sync(github, request(("bitcoin/bitcoin", "bitcoin/bitcoin")))
    resumed = pipeline.sync(github, request(("bitcoin/bitcoin", "bitcoin/bitcoin"), mode="resume"))
    cached = provider(FakeGitHubTransport(not_modified=True)).fetch(
        request(
            response_cache={
                "bitcoin/bitcoin": {
                    "payload": dict(next(iter(repository.raw.values())).payload),
                    "etags": {"repo": '"repo-etag"'},
                }
            }
        )
    )
    duplicate_validations = EvidenceAcquisitionValidator().validate(
        GitHubEvidenceNormalizer().normalize((cached[0], cached[0]), request()),
        as_of=NOW,
    )

    assert first.raw_count == 2
    assert second.raw_count == 2
    assert resumed.raw_count == 1
    assert len(repository.raw) == 1
    assert cached[0].payload["etag"] == '"repo-etag"'
    assert github.statistics.rate_limit_count == 1
    assert github.rate_limiter.delays
    assert {item.status for item in duplicate_validations} == {"valid", "duplicate"}


def test_github_identifier_resolution_rejects_invalid_and_reuses_resolved() -> None:
    resolutions = resolve_github_identifiers(
        ("bitcoin", "missing", "unsupported"),
        {
            "bitcoin": ProjectIdentifier("bitcoin", github_repositories=("bitcoin/bitcoin",)),
            "unsupported": ProjectIdentifier("unsupported", github_unsupported=True),
        },
        {"bitcoin/bitcoin"},
    )

    assert {item.project_id: item.status for item in resolutions} == {
        "bitcoin": "RESOLVED",
        "missing": "INVALID_ID",
        "unsupported": "UNSUPPORTED",
    }
    assert github_sync_ids(resolutions) == ("bitcoin/bitcoin",)
    assert github_target_map(resolutions) == {"bitcoin/bitcoin": "bitcoin"}


def test_github_repository_resolution_preserves_rate_limit_failures() -> None:
    github = provider(FakeGitHubTransport(rate_limited_repository=True))

    try:
        github.repository_exists("bitcoin/bitcoin")
    except GitHubHTTPError as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover
        raise AssertionError("rate-limited repository lookup must not be reported as not found")


def test_github_cli_commands_execute_without_enabled_provider(tmp_path) -> None:
    config = tmp_path / "acquisition.yaml"
    config.write_text(
        """
enabled: true
providers:
  - name: github
    enabled: false
    capabilities: [github]
    supported_metrics: [github_repository_profile]
""",
        encoding="utf-8",
    )

    assert main(["github", "--acquisition-config", str(config), "status"]) == 0
    assert main(["github", "--acquisition-config", str(config), "validate"]) == 0
    assert main(["github", "--acquisition-config", str(config), "sync"]) == 0
    assert main(["github", "--acquisition-config", str(config), "resolve"]) == 0
    assert main(["github", "--acquisition-config", str(config), "unresolved"]) == 0
    assert main(["github", "--acquisition-config", str(config), "statistics"]) == 0


def _repo() -> dict[str, object]:
    return {
        "name": "bitcoin",
        "full_name": "bitcoin/bitcoin",
        "owner": {"login": "bitcoin"},
        "default_branch": "master",
        "stargazers_count": 10,
        "forks_count": 3,
        "watchers_count": 10,
        "open_issues_count": 2,
        "created_at": "2009-01-03T00:00:00Z",
        "updated_at": "2026-07-11T00:00:00Z",
        "license": {"spdx_id": "MIT"},
    }


def _commit(login: str) -> dict[str, object]:
    return {"author": {"login": login}, "commit": {"committer": {"date": "2026-07-10T00:00:00Z"}}}
