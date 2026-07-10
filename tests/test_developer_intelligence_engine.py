from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.developer import (
    ContributorSnapshot,
    DeveloperEvent,
    DeveloperIntelligenceEngine,
    DeveloperSnapshot,
    IssueSnapshot,
    PullRequestSnapshot,
    ReleaseSnapshot,
    RepositorySnapshot,
    create_plugin,
)
from hunter.intelligence.engines.developer.analyzers import DeveloperAnalyzer
from hunter.intelligence.engines.developer.collectors import ContextDeveloperCollector
from hunter.intelligence.engines.developer.confidence import DeveloperConfidenceModel
from hunter.intelligence.engines.developer.configuration import DeveloperEngineConfiguration
from hunter.intelligence.engines.developer.exceptions import DeveloperValidationError
from hunter.intelligence.engines.developer.indicators import DeveloperIndicatorCalculator
from hunter.intelligence.engines.developer.normalization import DeveloperNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


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
