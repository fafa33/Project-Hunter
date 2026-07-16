from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Intelligence
from hunter.intelligence.engines import FindingBatch, IntelligenceEngineService
from hunter.intelligence.engines.contracts import EngineContext, EvidenceBundle, Finding, finding_identity
from hunter.intelligence.engines.developer import (
    DEVELOPER_ANALYSIS_TRACE_VERSION,
    DEVELOPER_FINDING_TYPES,
    ContributorSnapshot,
    DeveloperEvent,
    DeveloperFoundationIntelligenceEngine,
    DeveloperIntelligenceEngine,
    DeveloperSnapshot,
    IssueSnapshot,
    PullRequestSnapshot,
    ReleaseSnapshot,
    RepositorySnapshot,
    create_plugin,
    developer_engine_definition,
)
from hunter.intelligence.engines.developer.analyzers import DeveloperAnalyzer
from hunter.intelligence.engines.developer.collectors import ContextDeveloperCollector
from hunter.intelligence.engines.developer.confidence import DeveloperConfidenceModel
from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.exceptions import DeveloperValidationError
from hunter.intelligence.engines.developer.indicators import DeveloperIndicatorCalculator
from hunter.intelligence.engines.developer.normalization import DeveloperNormalizer
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError
from hunter.intelligence.evidence import Evidence
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


class FindingRepository:
    def __init__(self, evidence: tuple[Evidence, ...], *, fail_on_persist: bool = False) -> None:
        self.evidence = evidence
        self.fail_on_persist = fail_on_persist
        self.findings: dict[str, Finding] = {}

    def load_engine_evidence(self, candidate_id: str) -> tuple[Evidence, ...]:
        assert candidate_id == "bitcoin"
        return self.evidence

    def persist_authorized_findings(self, batch: FindingBatch) -> None:
        if self.fail_on_persist:
            raise RuntimeError("persistence failed")
        staged = dict(self.findings)
        for finding in batch.findings:
            staged[finding.finding_id] = finding
        self.findings = staged


def repository(repository_id: str, *, archived: bool = False, core: bool = True) -> RepositorySnapshot:
    return RepositorySnapshot(
        id=repository_id,
        project="bitcoin",
        name=repository_id,
        source="fixture",
        timestamp=NOW,
        reliability=0.9,
        is_core=core,
        is_archived=archived,
        url=f"https://example.test/{repository_id}",
        created_at=NOW - timedelta(days=900),
    )


def contributor(
    contributor_id: str,
    commits: int,
    *,
    first_seen_days: int,
    last_seen_days: int,
    bot: bool = False,
    repository_id: str = "core",
) -> ContributorSnapshot:
    return ContributorSnapshot(
        id=f"{repository_id}-{contributor_id}",
        repository_id=repository_id,
        contributor_id=contributor_id,
        commits=commits,
        pull_requests=max(commits // 3, 1),
        reviews=max(commits // 4, 1),
        source="fixture",
        timestamp=NOW,
        reliability=0.9,
        is_bot=bot,
        is_core=not bot,
        first_seen=NOW - timedelta(days=first_seen_days),
        last_seen=NOW - timedelta(days=last_seen_days),
    )


def event(event_id: str, event_type: str, days_ago: int, *, actor: str = "alice", bot: bool = False) -> DeveloperEvent:
    return DeveloperEvent(
        id=event_id,
        repository_id="core",
        event_type=event_type,
        timestamp=NOW - timedelta(days=days_ago),
        source="fixture",
        reliability=0.9,
        actor=actor,
        is_bot=bot,
        automated=bot,
        reference=f"https://example.test/events/{event_id}",
    )


def release(release_id: str, days_ago: int) -> ReleaseSnapshot:
    return ReleaseSnapshot(
        id=release_id,
        repository_id="core",
        version=release_id,
        released_at=NOW - timedelta(days=days_ago),
        source="fixture",
        reliability=0.9,
    )


def pull_request(pr_id: str, days_ago: int, *, merged: bool = True, bot: bool = False) -> PullRequestSnapshot:
    created_at = NOW - timedelta(days=days_ago)
    return PullRequestSnapshot(
        id=pr_id,
        repository_id="core",
        created_at=created_at,
        source="fixture",
        reliability=0.9,
        author="dependabot" if bot else "alice",
        merged_at=created_at + timedelta(days=1) if merged else None,
        closed_at=created_at + timedelta(days=2),
        is_bot=bot,
    )


def issue(issue_id: str, days_ago: int, *, closed: bool = True) -> IssueSnapshot:
    created_at = NOW - timedelta(days=days_ago)
    return IssueSnapshot(
        id=issue_id,
        repository_id="core",
        created_at=created_at,
        source="fixture",
        reliability=0.9,
        author="bob",
        closed_at=created_at + timedelta(days=3) if closed else None,
    )


def github_evidence(
    evidence_id: str = "github-core",
    *,
    repository_name: str = "bitcoin/bitcoin",
    collected_at: datetime = NOW,
    raw_data: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> Evidence:
    payload = {
        "full_name": repository_name,
        "repository_name": repository_name.split("/")[-1],
        "default_branch": "master",
        "stars": 10,
        "forks": 3,
        "watchers": 10,
        "open_issues": 2,
        "closed_issues": 4,
        "contributors_count": 3,
        "active_contributors": 2,
        "commit_count": 12,
        "commits_30d": 2,
        "commits_90d": 5,
        "commits_365d": 12,
        "releases": 1,
        "latest_release": "v1.0.0",
        "tags": ("v1.0.0",),
        "license": "MIT",
        "archived": False,
        "disabled": False,
        "updated_at": "2026-07-09T00:00:00Z",
        "pushed_at": "2026-07-09T00:00:00Z",
        "last_commit_timestamp": "2026-07-09T00:00:00Z",
    }
    if raw_data is not None:
        payload = raw_data
    return Evidence(
        id=evidence_id,
        source="github",
        collected_at=collected_at,
        reliability=0.9,
        freshness=1.0,
        reference=f"https://github.com/{repository_name}",
        raw_data=payload,
        metadata=metadata if metadata is not None else {"evidence_contract": "github_repository_profile"},
    )


def execute_developer_foundation(evidence: tuple[Evidence, ...]) -> tuple[FindingBatch, FindingRepository]:
    repository = FindingRepository(evidence)
    batch = IntelligenceEngineService(repository).execute(
        DeveloperFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="developer-config-v1",
    )
    return batch, repository


def developer_snapshot() -> DeveloperSnapshot:
    return DeveloperSnapshot(
        project="bitcoin",
        repositories=(repository("core"), repository("sdk", core=False), repository("old", archived=True)),
        contributors=(
            contributor("alice", 30, first_seen_days=200, last_seen_days=2),
            contributor("bob", 20, first_seen_days=160, last_seen_days=5),
            contributor("carol", 8, first_seen_days=10, last_seen_days=1),
            contributor("dependabot", 99, first_seen_days=100, last_seen_days=1, bot=True),
        ),
        releases=(release("v1", 80), release("v2", 40), release("v3", 5)),
        pull_requests=(pull_request("pr1", 10), pull_request("pr2", 20), pull_request("pr3", 5, bot=True)),
        issues=(issue("i1", 20), issue("i2", 10, closed=False)),
        events=(
            event("c1", "commit", 1),
            event("c2", "commit", 3),
            event("c3", "commit", 55),
            event("r1", "review", 4),
            event("u1", "protocol_upgrade", 8),
            event("b1", "commit", 1, actor="dependabot", bot=True),
        ),
        source="fixture",
        timestamp=NOW,
    )


def normalized_dataset():
    return DeveloperNormalizer().normalize((developer_snapshot(),))


def test_context_collector_reads_replaceable_developer_inputs() -> None:
    context = PipelineContext(values={"developer_records": [developer_snapshot()]})

    collected = ContextDeveloperCollector().collect(context)

    assert len(collected) == 1
    assert isinstance(collected[0], DeveloperSnapshot)


def test_canonical_models_reject_invalid_records() -> None:
    with pytest.raises(DeveloperValidationError):
        RepositorySnapshot(id="", project="bitcoin", name="core", source="fixture", timestamp=NOW, reliability=0.9)


def test_normalization_handles_multi_repository_archived_and_bot_filtering() -> None:
    dataset = normalized_dataset()

    assert [repository.id for repository in dataset.repositories] == ["core", "sdk"]
    assert all(not contributor.is_bot for contributor in dataset.contributors)
    assert all(not event.is_bot for event in dataset.events)
    assert len(dataset.contributors) == 3


def test_archived_repository_handling_can_be_configured() -> None:
    configuration = DeveloperEngineConfiguration(include_archived_repositories=True)

    dataset = DeveloperNormalizer(configuration).normalize((developer_snapshot(),))

    assert "old" in {repository.id for repository in dataset.repositories}


def test_commit_momentum_contributor_growth_and_concentration_are_deterministic() -> None:
    dataset = normalized_dataset()
    indicators = {indicator.name: indicator for indicator in DeveloperIndicatorCalculator().calculate(dataset)}

    assert indicators["commit_momentum"].value > 0.5
    assert indicators["contributor_growth"].value > 0.0
    assert indicators["contributor_concentration"].value > 0.0


def test_release_pr_issue_and_health_analysis() -> None:
    dataset = normalized_dataset()
    indicators = {indicator.name: indicator for indicator in DeveloperIndicatorCalculator().calculate(dataset)}

    assert indicators["release_cadence"].value > 0.0
    assert indicators["pull_request_throughput"].value == 1.0
    assert indicators["issue_resolution_efficiency"].value == 0.5
    assert indicators["code_review_health"].value > 0.0


def test_analyzer_detects_developer_health_strengths_and_missing_evidence() -> None:
    analysis = DeveloperAnalyzer().analyze(normalized_dataset())

    assert analysis.health in {"strong", "stable"}
    assert analysis.trend in {"accelerating", "steady"}
    assert "release_cadence" in analysis.strengths


def test_confidence_uses_completeness_freshness_and_historical_depth() -> None:
    sparse = DeveloperNormalizer().normalize((repository("core"),))
    rich = normalized_dataset()

    sparse_confidence = DeveloperConfidenceModel().calculate(sparse)
    rich_confidence = DeveloperConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.completeness > sparse_confidence.completeness


def test_developer_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"developer_records": [developer_snapshot()]})
    engine = DeveloperIntelligenceEngine()

    dataset = engine.collect(context)
    analysis = engine.analyze(context, dataset)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "developer-intelligence"
    assert intelligence.project == "bitcoin"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("developer_health") in {"strong", "stable"}


def test_foundation_engine_produces_all_developer_findings_from_sufficient_evidence() -> None:
    evidence = (
        github_evidence(),
        github_evidence("github-migration", repository_name="bitcoin/bitcoin-old"),
    )

    batch, repository = execute_developer_foundation(evidence)

    assert {finding.finding_type for finding in batch.findings} == set(DEVELOPER_FINDING_TYPES)
    assert all(finding.supporting_evidence_ids for finding in batch.findings)
    assert all(finding.evidence_lineage for finding in batch.findings)
    assert set(repository.findings) == {finding.finding_id for finding in batch.findings}


def test_foundation_engine_suppresses_findings_when_required_evidence_is_absent() -> None:
    evidence = (github_evidence(raw_data={"full_name": "bitcoin/bitcoin"}),)

    batch, _repository = execute_developer_foundation(evidence)

    assert batch.findings == ()


def test_foundation_engine_reports_no_missing_evidence_when_required_contract_is_present() -> None:
    batch, _repository = execute_developer_foundation((github_evidence(),))

    assert batch.findings
    assert {finding.missing_evidence for finding in batch.findings} == {()}


def test_foundation_engine_does_not_reinterpret_github_source_as_required_contract() -> None:
    evidence = (github_evidence(metadata={}),)

    batch, _repository = execute_developer_foundation(evidence)

    assert batch.findings == ()


def test_foundation_engine_and_service_share_legacy_metric_contract_semantics() -> None:
    payload = dict(github_evidence().raw_data)
    payload["metric"] = "github_repository_profile"

    batch, _repository = execute_developer_foundation((github_evidence(raw_data=payload, metadata={}),))

    assert batch.findings
    assert {finding.missing_evidence for finding in batch.findings} == {()}


def test_foundation_findings_are_independent() -> None:
    full_batch, _repository = execute_developer_foundation((github_evidence(),))
    payload_without_releases = dict(github_evidence().raw_data)
    for key in ("releases", "latest_release", "tags"):
        payload_without_releases.pop(key)

    reduced_batch, _repository = execute_developer_foundation((github_evidence(raw_data=payload_without_releases),))
    full_by_type = {finding.finding_type: finding for finding in full_batch.findings}
    reduced_by_type = {finding.finding_type: finding for finding in reduced_batch.findings}

    assert "release_cadence" in full_by_type
    assert "release_cadence" not in reduced_by_type
    for finding_type, finding in reduced_by_type.items():
        assert finding == full_by_type[finding_type]


def test_foundation_engine_normalizes_reversed_evidence_order() -> None:
    first = github_evidence()
    second = github_evidence("github-migration", repository_name="bitcoin/bitcoin-old")

    forward, _repository = execute_developer_foundation((first, second))
    reversed_batch, _repository = execute_developer_foundation((second, first))

    assert reversed_batch == forward


def test_foundation_engine_repeated_execution_is_identical() -> None:
    evidence = (github_evidence(),)

    first, _repository = execute_developer_foundation(evidence)
    second, _repository = execute_developer_foundation(evidence)

    assert second == first


def test_foundation_engine_changed_evidence_changes_only_affected_findings() -> None:
    base, _repository = execute_developer_foundation((github_evidence(),))
    changed_payload = dict(github_evidence().raw_data)
    changed_payload["license"] = "Apache-2.0"

    changed, _repository = execute_developer_foundation((github_evidence(raw_data=changed_payload),))
    base_by_type = {finding.finding_type: finding for finding in base.findings}
    changed_by_type = {finding.finding_type: finding for finding in changed.findings}

    assert changed_by_type["repository_health_observation"] != base_by_type["repository_health_observation"]
    for finding_type, finding in changed_by_type.items():
        if finding_type != "repository_health_observation":
            assert finding == base_by_type[finding_type]


def test_foundation_engine_uses_explicit_context_not_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hunter.intelligence.engines.developer.foundation.datetime", object(), raising=False)

    batch, _repository = execute_developer_foundation((github_evidence(),))

    assert batch.as_of == NOW
    assert batch.evaluated_at == NOW


def test_foundation_engine_analysis_trace_version_changes_finding_identity() -> None:
    base, _repository = execute_developer_foundation((github_evidence(),))
    definition = replace(
        developer_engine_definition(),
        analysis_trace_version=f"{DEVELOPER_ANALYSIS_TRACE_VERSION}-next",
    )
    repository = FindingRepository((github_evidence(),))

    changed = IntelligenceEngineService(repository).execute(
        DeveloperFoundationIntelligenceEngine(definition=definition),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="developer-config-v1",
    )

    assert {finding.finding_type for finding in changed.findings} == {finding.finding_type for finding in base.findings}
    assert {finding.finding_id for finding in changed.findings} != {finding.finding_id for finding in base.findings}


def test_foundation_service_excludes_future_evidence_by_as_of() -> None:
    future = github_evidence("github-future", collected_at=NOW + timedelta(days=1))

    batch, repository = execute_developer_foundation((future,))

    assert batch.findings == ()
    assert repository.findings == {}


def test_foundation_service_rejects_forged_lineage() -> None:
    class ForgingDeveloperEngine(DeveloperFoundationIntelligenceEngine):
        def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
            batch = super().analyze(evidence, context)
            first = batch.findings[0]
            forged = replace(first, evidence_lineage=("forged-lineage",))
            forged = replace(
                forged,
                finding_id=finding_identity(
                    candidate_id=forged.candidate_id,
                    engine_id=forged.engine_id,
                    engine_version=forged.engine_version,
                    finding_type=forged.finding_type,
                    explanation=forged.explanation,
                    supporting_evidence_ids=forged.supporting_evidence_ids,
                    evidence_lineage=forged.evidence_lineage,
                    deterministic_confidence=forged.deterministic_confidence,
                    confidence_basis=forged.confidence_basis,
                    evaluated_at=forged.evaluated_at,
                    as_of=forged.as_of,
                    analysis_trace_version=forged.analysis_trace_version,
                    missing_evidence=forged.missing_evidence,
                    schema_version=forged.schema_version,
                ),
            )
            return replace(batch, findings=(forged, *batch.findings[1:]))

    with pytest.raises(IntelligenceEngineValidationError, match="evidence lineage"):
        IntelligenceEngineService(FindingRepository((github_evidence(),))).execute(
            ForgingDeveloperEngine(),
            candidate_id="bitcoin",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="developer-config-v1",
        )


def test_foundation_service_persistence_rollback_and_idempotency() -> None:
    failing_repository = FindingRepository((github_evidence(),), fail_on_persist=True)
    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(failing_repository).execute(
            DeveloperFoundationIntelligenceEngine(),
            candidate_id="bitcoin",
            as_of=NOW,
            evaluated_at=NOW,
            engine_configuration_fingerprint="developer-config-v1",
        )
    assert failing_repository.findings == {}

    repository = FindingRepository((github_evidence(),))
    service = IntelligenceEngineService(repository)
    first = service.execute(
        DeveloperFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="developer-config-v1",
    )
    second = service.execute(
        DeveloperFoundationIntelligenceEngine(),
        candidate_id="bitcoin",
        as_of=NOW,
        evaluated_at=NOW,
        engine_configuration_fingerprint="developer-config-v1",
    )

    assert second == first
    assert len(repository.findings) == len(first.findings)


def test_developer_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "developer-intelligence"
    assert "developer-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_developer_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  developer-intelligence: true
configuration: {}
load_order:
  - developer-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.developer:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["developer-intelligence"]


def test_pipeline_executes_developer_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  developer-intelligence: true
configuration: {}
load_order:
  - developer-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.developer:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"developer_records": [developer_snapshot()]})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "developer-intelligence"
    assert "developer:intelligence:execute" in result.events
