from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.competitive import (
    AlgorithmicPeerBuilder,
    AlgorithmicPeerPolicy,
    ComparisonDimension,
    CompetitiveAutomationManager,
    CompetitiveRelationship,
    CompetitiveRelationshipEvidenceLink,
    CompetitiveRelationshipSpanLink,
    CompetitiveReportContext,
    CompetitiveReporter,
    CompetitiveRepository,
    PeerSetBuilder,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 2, 1, tzinfo=UTC)


def test_phase_eight_cli_reports_coverage_peers_competitors_explain_and_conflicts(tmp_path, capsys) -> None:
    db = tmp_path / "competitive.sqlite"
    repository = CompetitiveRepository(db)
    seed_competitive_state(repository)

    assert main(["competitive", "--db", str(db), "coverage"]) == 0
    assert "peer_sets=1" in capsys.readouterr().out
    assert main(["competitive", "--db", str(db), "report"]) == 0
    assert "confidence=" in capsys.readouterr().out
    assert main(["competitive", "--db", str(db), "peers", "candidate-a"]) == 0
    peers = capsys.readouterr().out
    assert "kind=evidence_backed" in peers
    assert "kind=algorithmic_similarity" in peers
    assert main(["competitive", "--db", str(db), "competitors", "candidate-a"]) == 0
    assert "kind=evidence_backed" in capsys.readouterr().out
    assert main(["competitive", "--db", str(db), "explain", "candidate-a"]) == 0
    assert "replay_mode=current" in capsys.readouterr().out
    assert main(["competitive", "--db", str(db), "conflicts"]) == 0
    assert "conflicts=0" in capsys.readouterr().out


def test_phase_eight_competitive_automation_install_is_idempotent_and_scheduler_valid(tmp_path) -> None:
    config = tmp_path / "automation.yaml"
    manager = CompetitiveAutomationManager(config)

    first = manager.install()
    second = manager.install()
    status = manager.status()

    assert first.installed == 5
    assert first.created == 5
    assert second.installed == 5
    assert second.created == 0
    assert len(status) == 5
    assert {row["run_type"] for row in status} == {"competitive_intelligence_pipeline"}
    assert {row["metadata"]["scheduler_role"] for row in status} == {"operational_only"}


def test_phase_eight_cli_automation_status_uses_existing_scheduler_jobs(tmp_path, capsys) -> None:
    config = tmp_path / "automation.yaml"

    assert main(["competitive", "--automation-config", str(config), "automation", "install"]) == 0
    install_output = capsys.readouterr().out
    assert "created=5" in install_output
    assert main(["competitive", "--automation-config", str(config), "automation", "install"]) == 0
    assert "created=0" in capsys.readouterr().out
    assert main(["competitive", "--automation-config", str(config), "automation", "status"]) == 0
    status_output = capsys.readouterr().out
    assert "pipeline_owner=competitive_intelligence_pipeline" in status_output
    assert "scheduler_role=operational_only" in status_output


def test_phase_eight_report_uses_replay_context_without_current_state_leakage(tmp_path) -> None:
    db = tmp_path / "competitive.sqlite"
    repository = CompetitiveRepository(db)
    repository.save_relationship_with_lineage(
        competitive_relationship(),
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
    )
    PeerSetBuilder(repository=repository).build(
        subject_candidate_id="candidate-a",
        scope="defi",
        effective_at=NOW,
        recorded_at=LATER,
    )
    reporter = CompetitiveReporter(repository)

    strict = reporter.report(CompetitiveReportContext(cutoff=NOW, strict_known_by_hunter=True))
    reconstructed = reporter.report(CompetitiveReportContext(cutoff=NOW, strict_known_by_hunter=False))

    assert strict == ()
    assert reconstructed[0]["mode"] == "reconstructed_after_cutoff"
    assert reconstructed[0]["known_at_cutoff"] == "false"


def test_phase_eight_explain_uses_cutoff_safe_dimensions(tmp_path) -> None:
    db = tmp_path / "competitive.sqlite"
    repository = CompetitiveRepository(db)
    seed_competitive_state(repository)
    relationship_id = repository.algorithmic_relationships_for_subject("candidate-a")[0]["relationship_id"]
    dimension = repository.comparison_dimensions_for_relationship(relationship_id)[0]
    repository.save_comparison_dimension(
        ComparisonDimension(
            dimension_id=str(dimension["dimension_id"]),
            subject_candidate_id="candidate-a",
            peer_candidate_id=str(dimension["peer_candidate_id"]),
            dimension_type=str(dimension["dimension_type"]),
            subject_value="unavailable",
            peer_value="unavailable",
            match_status="missing",
            relationship_kind="algorithmic_similarity",
            relationship_id=relationship_id,
            policy_id=str(dimension["policy_id"]),
            policy_version=str(dimension["policy_version"]),
            confidence=0.0,
            effective_at=NOW,
            recorded_at=LATER,
            schema_version=str(dimension["schema_version"]),
        )
    )
    reporter = CompetitiveReporter(repository)

    strict_rows = reporter.explain(
        "candidate-a",
        CompetitiveReportContext(cutoff=NOW, strict_known_by_hunter=True),
    )
    current_rows = reporter.explain("candidate-a", CompetitiveReportContext())
    strict_algorithmic = next(row for row in strict_rows if row["kind"] == "algorithmic_similarity")
    current_algorithmic = next(row for row in current_rows if row["kind"] == "algorithmic_similarity")

    assert strict_algorithmic["missing_evidence"] == "1"
    assert strict_algorithmic["known_at_cutoff"] == "true"
    assert current_algorithmic["missing_evidence"] == "2"


def seed_competitive_state(repository: CompetitiveRepository) -> None:
    repository.save_relationship_with_lineage(
        competitive_relationship(),
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
    )
    AlgorithmicPeerBuilder(
        policy=AlgorithmicPeerPolicy(dimensions=("market_category", "chain", "use_case")),
        repository=repository,
    ).build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-c",
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "DeFi", "chain": "Base", "use_case": None},
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )
    PeerSetBuilder(repository=repository).build(
        subject_candidate_id="candidate-a",
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )


def competitive_relationship() -> CompetitiveRelationship:
    return CompetitiveRelationship(
        relationship_id="competitive-relationship-1",
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="direct_competitor",
        status="active",
        predicate_id="competes_with",
        predicate_schema_version="competitive-predicate-v1",
        claim_id="claim-1",
        subject_entity_id="entity-a",
        peer_entity_id="entity-b",
        scope="defi",
        modality="asserted",
        polarity="positive",
        confidence=0.8,
        freshness=0.9,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-relationship-v1",
    )


def relationship_evidence_link() -> CompetitiveRelationshipEvidenceLink:
    return CompetitiveRelationshipEvidenceLink(
        link_id="competitive-evidence-link-1",
        relationship_id="competitive-relationship-1",
        source_evidence_id="source-evidence-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def relationship_span_link() -> CompetitiveRelationshipSpanLink:
    return CompetitiveRelationshipSpanLink(
        link_id="competitive-span-link-1",
        relationship_id="competitive-relationship-1",
        span_id="span-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )
