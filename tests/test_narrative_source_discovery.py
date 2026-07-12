from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.narrative.discovery import (
    NarrativeSourceDiscoveryEngine,
    NarrativeSourceDiscoveryRepository,
    source_coverage,
)

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_discovery_validation_trust_scoring_persistence_and_coverage(tmp_path: Path) -> None:
    acquisition = InMemoryAcquisitionRepository()
    acquisition.save_normalized((_github("bitcoin", "bitcoin/bitcoin"), _homepage("bitcoin", "https://bitcoin.org/")))
    acquisition.save_validations((_validation("github-bitcoin"), _validation("home-bitcoin")))
    engine = NarrativeSourceDiscoveryEngine(acquisition_repository=acquisition, root=tmp_path / "discovery")

    run = engine.discover(("bitcoin",), as_of=NOW)
    sources = engine.repository.sources()
    coverage = source_coverage(sources, project_ids=("bitcoin",))

    assert run.discovered_sources == 5
    assert run.verified_sources == 5
    assert run.duplicate_sources == 4
    assert run.rejected_sources == 0
    assert coverage.projects_partially_resolved == 1
    assert all(source.trust_score > 0 for source in sources)
    assert {source.source_type for source in sources} >= {"official_website", "github_releases", "github_tags"}
    persisted_run = NarrativeSourceDiscoveryRepository(tmp_path / "discovery").runs()[0]
    assert persisted_run.run_id == run.run_id
    assert persisted_run.duplicate_sources == 4


def test_deduplication_repository_integrity_and_configuration_generation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("configs").mkdir()
    Path("configs/narrative_sources.yaml").write_text(
        """
enabled: true
expired_after_days: 365
sources:
  - source_id: manual-source
    provider: rss
    project_id: bitcoin
    url: https://manual.example/feed.xml
    source: Manual
    manual: true
""",
        encoding="utf-8",
    )
    acquisition = InMemoryAcquisitionRepository()
    acquisition.save_normalized((_github("bitcoin", "bitcoin/bitcoin"), _github("bitcoin", "bitcoin/bitcoin")))
    acquisition.save_validations((_validation("github-bitcoin"), _validation("github-bitcoin-duplicate")))

    engine = NarrativeSourceDiscoveryEngine(acquisition_repository=acquisition, root=tmp_path / "discovery")
    engine.discover(("bitcoin",), as_of=NOW)

    payload = yaml.safe_load(Path("configs/narrative_sources.yaml").read_text(encoding="utf-8"))
    source_ids = {source["source_id"] for source in payload["sources"]}
    urls = [source["url"] for source in payload["sources"]]

    assert "manual-source" in source_ids
    assert urls.count("https://github.com/bitcoin/bitcoin/releases.atom") == 1
    assert urls.count("https://github.com/bitcoin/bitcoin/tags.atom") == 1


def test_configuration_merge_preserves_deprecated_manual_sources_and_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("configs").mkdir()
    Path("configs/narrative_sources.yaml").write_text(
        """
enabled: true
expired_after_days: 365
sources:
  - source_id: deprecated-source
    provider: rss
    project_id: bitcoin
    url: https://deprecated.example/releases.atom
    source: Deprecated
    deprecated: true
""",
        encoding="utf-8",
    )
    acquisition = InMemoryAcquisitionRepository()
    acquisition.save_normalized((_github("bitcoin", "bitcoin/bitcoin"),))
    acquisition.save_validations((_validation("github-bitcoin"),))
    engine = NarrativeSourceDiscoveryEngine(acquisition_repository=acquisition, root=tmp_path / "discovery")

    first = engine.discover(("bitcoin",), as_of=NOW)
    second = engine.discover(("bitcoin",), as_of=NOW)
    payload = yaml.safe_load(Path("configs/narrative_sources.yaml").read_text(encoding="utf-8"))
    deprecated = next(source for source in payload["sources"] if source["source_id"] == "deprecated-source")

    assert deprecated["url"] == "https://deprecated.example/releases.atom"
    assert len(engine.repository.runs()) == 2
    assert engine.repository.runs() == (first, second)


def test_unverified_sources_are_not_merged_into_configuration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("configs").mkdir()
    Path("configs/narrative_sources.yaml").write_text("enabled: true\nsources: []\n", encoding="utf-8")
    acquisition = InMemoryAcquisitionRepository()
    acquisition.save_normalized((_github("bitcoin", "bitcoin/bitcoin"),))
    acquisition.save_validations((_validation("github-bitcoin", status="invalid"),))

    engine = NarrativeSourceDiscoveryEngine(acquisition_repository=acquisition, root=tmp_path / "discovery")
    run = engine.discover(("bitcoin",), as_of=NOW)
    payload = yaml.safe_load(Path("configs/narrative_sources.yaml").read_text(encoding="utf-8"))

    assert run.discovered_sources == 0
    assert run.rejected_sources == 0
    assert payload["sources"] == []


def _github(project_id: str, repository: str) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=f"github-{project_id}",
        repository_id=f"github:{project_id}:{repository}",
        provider="github",
        collector="github-rest-api",
        raw_source_id=repository,
        domain="github",
        metric="github_repository_profile",
        target_id=project_id,
        value=repository,
        raw_metrics={"full_name": repository},
        normalized_metrics={"schema_completeness": 1.0},
        source_url=f"https://github.com/{repository}",
        retrieved_at=NOW,
        normalized_at=NOW,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=repository,
    )


def _homepage(project_id: str, url: str) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=f"home-{project_id}",
        repository_id=f"coingecko-detail:{project_id}",
        provider="coingecko",
        collector="coingecko-api",
        raw_source_id=project_id,
        domain="market",
        metric="coingecko_detail_metadata",
        target_id=project_id,
        value=project_id,
        raw_metrics={"homepage": (url,)},
        normalized_metrics={"detail_metadata_completeness": 1.0},
        source_url=url,
        retrieved_at=NOW,
        normalized_at=NOW,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=project_id,
    )


def _validation(evidence_id: str, *, status: str = "valid") -> EvidenceValidation:
    return EvidenceValidation(
        evidence_id=evidence_id,
        status=status,  # type: ignore[arg-type]
        validated_at=NOW,
        confidence=1.0 if status == "valid" else 0.0,
        freshness=1.0 if status == "valid" else 0.0,
    )
