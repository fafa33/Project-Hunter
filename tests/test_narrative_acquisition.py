from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hunter.acquisition import AcquisitionConfig, AcquisitionPipeline, AcquisitionRequest, CacheConfig, RetryConfig
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.cli import main
from hunter.market_validation.acquisition_sources import acquisition_engine_sources
from hunter.narrative import (
    NarrativeEvidenceNormalizer,
    NarrativeEvidenceValidator,
    NarrativeProvider,
    load_narrative_config,
)
from hunter.narrative.configuration import NarrativeSourceConfig
from hunter.narrative.provider import NARRATIVE_ENGINES, NarrativeProviderConfig
from hunter.narrative.repository import NarrativeRepository, narrative_statistics

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_rss_official_blog_parsing_normalization_validation_and_persistence(tmp_path: Path) -> None:
    feed = _feed(
        "Chainlink Roadmap v2.1",
        "Chainlink launches Ethereum oracle roadmap with staking, mainnet, AI, and $LINK expansion.",
    )
    provider = NarrativeProvider(NarrativeProviderConfig(sources=(_source(tmp_path, feed),)))
    repository = InMemoryAcquisitionRepository()
    run = _sync(provider, repository)

    assert run.raw_count == 1
    assert run.valid_count == 1
    evidence = next(iter(repository.normalized.values()))
    assert evidence.raw_metrics["topics"] == ("ai", "roadmap")
    assert "ethereum" in evidence.raw_metrics["chains"]
    assert "staking" in evidence.raw_metrics["technology_references"]
    assert evidence.raw_metrics["version_references"] == ("v2.1",)
    assert evidence.raw_metrics["token_references"] == ("$LINK",)
    assert NarrativeRepository(repository).valid_evidence_ids() == (evidence.evidence_id,)


def test_duplicate_detection_and_repository_integrity(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        _feed("Aave Update", "Aave roadmap mainnet upgrade.", duplicate=True),
        project_id="aave",
    )
    repository = InMemoryAcquisitionRepository()
    run = _sync(NarrativeProvider(NarrativeProviderConfig(sources=(source,))), repository)
    stats = narrative_statistics(repository)

    assert run.raw_count == 2
    assert run.valid_count == 1
    assert run.duplicate_count == 1
    assert stats.valid == 1
    assert stats.duplicate == 1


def test_project_mapping_unknown_provider_broken_url_missing_timestamp_and_invalid_language(tmp_path: Path) -> None:
    bad_feed = tmp_path / "bad.xml"
    bad_feed.write_text(
        """
<rss><channel>
  <item><title>Broken</title><description>Missing timestamp</description><link>not-a-url</link></item>
</channel></rss>
""",
        encoding="utf-8",
    )
    source = NarrativeSourceConfig(
        source_id="bad",
        provider="unknown",
        project_id="",
        url=bad_feed.as_uri(),
        source="bad-source",
        language="english",
    )
    repository = InMemoryAcquisitionRepository()
    _sync(NarrativeProvider(NarrativeProviderConfig(sources=(source,))), repository)

    validation = next(iter(repository.validations.values()))
    assert validation.status == "invalid"
    assert {issue.code for issue in validation.issues} >= {"missing", "provider", "encoding"}


def test_resume_checkpoint_incremental_and_deterministic_output(tmp_path: Path) -> None:
    sources = (
        _source(tmp_path, _feed("First", "Ethereum rollup mainnet."), source_id="first", project_id="ethereum"),
        _source(tmp_path, _feed("Second", "Solana roadmap v1.2."), source_id="second", project_id="solana"),
    )
    repository = InMemoryAcquisitionRepository()
    provider = NarrativeProvider(NarrativeProviderConfig(sources=sources))
    config = AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=True, ttl_seconds=300))
    pipeline = AcquisitionPipeline(
        normalizer=NarrativeEvidenceNormalizer(),
        validator=NarrativeEvidenceValidator(),
        repository=repository,
        config=config,
    )
    first = pipeline.sync(provider, _request(mode="incremental"))
    resumed = pipeline.sync(provider, _request(mode="resume"))

    assert first.raw_count == 2
    assert resumed.raw_count == 1
    assert len(repository.checkpoints) == 1
    assert tuple(repository.normalized) == tuple(repository.normalized)


def test_narrative_evidence_wires_to_repository_only_engine_sources(tmp_path: Path) -> None:
    repository = InMemoryAcquisitionRepository()
    _sync(
        NarrativeProvider(
            NarrativeProviderConfig(
                sources=(
                    _source(
                        tmp_path,
                        _feed("Ethereum AI Roadmap", "AI agents and Ethereum roadmap milestone."),
                        project_id="ethereum",
                    ),
                )
            )
        ),
        repository,
    )
    sources = acquisition_engine_sources(repository, as_of=NOW)
    source_by_engine = {source.engine: source for source in sources["ethereum"]}

    assert set(NARRATIVE_ENGINES).issubset(source_by_engine)
    narrative = source_by_engine["narrative"]
    assert narrative.evidence_ids
    assert narrative.repository_ids
    assert narrative.source == "narrative"
    assert narrative.validation_status == "VALID"


def test_narrative_configuration_and_cli_commands(tmp_path: Path) -> None:
    feed = tmp_path / "feed.xml"
    feed.write_text(_feed("API3 Announcement", "API3 oracle roadmap."), encoding="utf-8")
    config = tmp_path / "narrative.yaml"
    config.write_text(
        f"""
enabled: true
expired_after_days: 365
sources:
  - source_id: api3-blog
    provider: official_blog
    project_id: api3
    url: {feed.as_uri()}
    source: API3 Blog
    language: en
""",
        encoding="utf-8",
    )

    loaded = load_narrative_config(config)
    assert loaded.sources[0].provider == "official_blog"
    assert main(["narrative", "--narrative-config", str(config), "status"]) == 0
    assert main(["narrative", "--narrative-config", str(config), "providers"]) == 0
    assert main(["narrative", "--narrative-config", str(config), "validate"]) == 0
    assert main(["narrative", "--narrative-config", str(config), "statistics"]) == 0
    assert main(["narrative", "--narrative-config", str(config), "coverage"]) == 0


def _sync(provider: NarrativeProvider, repository: InMemoryAcquisitionRepository):
    return AcquisitionPipeline(
        normalizer=NarrativeEvidenceNormalizer(),
        validator=NarrativeEvidenceValidator(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1)),
    ).sync(provider, _request())


def _request(*, mode: str = "incremental") -> AcquisitionRequest:
    return AcquisitionRequest(
        domain="narrative",
        metric="narrative_item",
        target_id="configured-projects",
        requested_at=NOW,
        mode=mode,  # type: ignore[arg-type]
    )


def _source(
    tmp_path: Path,
    feed: str,
    *,
    source_id: str = "chainlink-blog",
    project_id: str = "chainlink",
) -> NarrativeSourceConfig:
    path = tmp_path / f"{source_id}.xml"
    path.write_text(feed, encoding="utf-8")
    return NarrativeSourceConfig(
        source_id=source_id,
        provider="official_blog",
        project_id=project_id,
        url=path.as_uri(),
        source="Official Blog",
        language="en",
        tags=("roadmap",),
        categories=("oracle",),
    )


def _feed(title: str, description: str, *, duplicate: bool = False) -> str:
    item = f"""
  <item>
    <title>{title}</title>
    <description>{description}</description>
    <link>https://example.com/{title.lower().replace(" ", "-")}</link>
    <pubDate>Sat, 11 Jul 2026 00:00:00 GMT</pubDate>
    <author>Project Team</author>
  </item>
"""
    return f"<rss><channel>{item}{item if duplicate else ''}</channel></rss>"
