from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from hunter.tokenomics import (
    ADDRESS_CATEGORIES,
    EVIDENCE_LIFECYCLE_STATUSES,
    REPORT_MODES,
    SUPPLY_METRICS,
    AddressClassification,
    ClaimArtifactLink,
    EvidenceLifecycleEvent,
    HolderBalanceSnapshot,
    HolderEntry,
    ObservationConflict,
    ObservationConflictMember,
    TokenAsset,
    TokenomicsEvidenceArtifact,
    TokenomicsEvidenceClaim,
    TokenomicsIntegrityError,
    TokenomicsReportObservationLink,
    TokenomicsReportRun,
    TokenomicsReportSufficiencyLink,
    TokenomicsRepository,
    TokenomicsSufficiencyAssessmentRecord,
    TokenRepresentation,
    VenueMarketObservation,
)
from hunter.tokenomics.models import AddressCategory

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_a_vocabulary_preserves_explicit_states_and_excludes_whale() -> None:
    assert {
        "active",
        "corrected",
        "retracted",
        "superseded",
        "contested",
        "unavailable",
        "ambiguous",
        "proxy",
    }.issubset(EVIDENCE_LIFECYCLE_STATUSES)
    assert REPORT_MODES == {"known_by_hunter", "reconstructed"}
    assert {"circulating_supply", "total_supply", "max_supply", "unknown"}.issubset(SUPPLY_METRICS)
    assert {
        "treasury",
        "team",
        "investor",
        "market_maker",
        "exchange",
        "bridge",
        "contract",
        "burn",
        "staking",
        "unknown",
        "other_declared",
    } == ADDRESS_CATEGORIES
    assert "whale" not in ADDRESS_CATEGORIES


def test_phase_a_schema_tables_indexes_and_constraints(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)

    assert {
        "token_assets",
        "token_representations",
        "tokenomics_evidence_artifacts",
        "tokenomics_evidence_claims",
        "tokenomics_claim_artifact_links",
        "tokenomics_evidence_lifecycle_events",
        "tokenomics_supply_observations",
        "tokenomics_supply_definition_reconciliations",
        "tokenomics_allocation_definitions",
        "tokenomics_vesting_schedules",
        "tokenomics_vesting_schedule_segments",
        "tokenomics_unlock_events",
        "tokenomics_holder_balance_snapshots",
        "tokenomics_holder_entries",
        "tokenomics_address_classifications",
        "tokenomics_address_classification_evidence_links",
        "tokenomics_venue_market_observations",
        "tokenomics_transfer_observations",
        "tokenomics_exchange_flow_windows",
        "tokenomics_observation_conflicts",
        "tokenomics_conflict_members",
        "tokenomics_conflict_resolution_events",
        "tokenomics_report_runs",
        "tokenomics_report_observation_links",
        "tokenomics_sufficiency_assessments",
        "tokenomics_report_sufficiency_links",
    }.issubset(repo.table_names())
    assert {
        "token_representations_asset_chain_address_idx",
        "tokenomics_supply_observations_representation_metric_time_idx",
        "tokenomics_allocations_asset_category_effective_idx",
        "tokenomics_vesting_schedules_asset_allocation_effective_idx",
        "tokenomics_unlock_events_schedule_time_idx",
        "tokenomics_holder_entries_snapshot_address_idx",
        "tokenomics_address_classifications_address_category_validity_idx",
        "tokenomics_venue_market_observations_token_venue_window_idx",
        "tokenomics_exchange_flow_windows_token_venue_window_idx",
        "tokenomics_report_runs_execution_cutoff_idx",
        "tokenomics_evidence_lifecycle_predecessor_idx",
    }.issubset(repo.index_names())

    with pytest.raises(ValueError, match="unknown tokenomics table"):
        repo.count("configs")


def test_identical_logical_evidence_ingestion_is_idempotent(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    artifact = _artifact("artifact:one")
    claim = _claim("claim:circulating", "circulating_supply", "1000000")
    link = ClaimArtifactLink(
        link_id="claim-artifact-link:one",
        claim_id=claim.claim_id,
        artifact_id=artifact.artifact_id,
        role="source",
        position=0,
    )

    repo.save_claim_with_lineage(claim, artifacts=(artifact,), artifact_links=(link,))
    repo.save_claim_with_lineage(claim, artifacts=(artifact,), artifact_links=(link,))

    assert repo.count("tokenomics_evidence_artifacts") == 1
    assert repo.count("tokenomics_evidence_claims") == 1
    assert repo.count("tokenomics_claim_artifact_links") == 1


def test_divergent_duplicate_immutable_evidence_and_lineage_are_rejected(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    artifact = _artifact("artifact:immutable")
    claim = _claim("claim:immutable", "circulating_supply", "1000000")
    lifecycle = EvidenceLifecycleEvent(
        event_id="event:immutable",
        claim_id=claim.claim_id,
        lifecycle_status="active",
        effective_at=NOW,
        recorded_at=NOW,
    )
    conflict = ObservationConflict(
        conflict_id="conflict:immutable",
        asset_id="asset:hunter",
        conflict_state="open",
        detected_at=NOW,
        recorded_at=NOW,
    )
    run = TokenomicsReportRun(
        run_id="report:immutable",
        execution_identity="execution:immutable",
        report_mode="known_by_hunter",
        cutoff_at=NOW,
        started_at=NOW,
        recorded_at=NOW,
    )
    lineage = TokenomicsReportObservationLink(
        link_id="report-lineage:immutable",
        report_run_id=run.run_id,
        observation_table="tokenomics_evidence_claims",
        observation_id=claim.claim_id,
        role="supporting_claim",
        position=0,
    )

    repo.save_evidence_artifact(artifact)
    repo.save_evidence_claim(claim)
    repo.save_evidence_lifecycle_event(lifecycle)
    repo.save_observation_conflict(conflict)
    repo.save_report_run(run)
    repo.save_report_observation_link(lineage)

    for save, divergent in (
        (repo.save_evidence_artifact, replace(artifact, source_uri="https://example.test/divergent")),
        (repo.save_evidence_claim, replace(claim, value="2000000")),
        (repo.save_evidence_lifecycle_event, replace(lifecycle, lifecycle_status="retracted")),
        (repo.save_observation_conflict, replace(conflict, conflict_state="resolved")),
        (repo.save_report_observation_link, replace(lineage, observation_id="claim:other")),
    ):
        with pytest.raises(TokenomicsIntegrityError):
            save(divergent)

    assert _db_row(repo.path, "tokenomics_evidence_artifacts", "artifact_id", artifact.artifact_id)["source_uri"] == (
        artifact.source_uri
    )
    assert repo.evidence_claims(asset_id="asset:hunter", predicate="circulating_supply")[0]["value"] == "1000000"
    assert repo.claim_lifecycle(claim.claim_id)[0]["lifecycle_status"] == "active"
    assert (
        _db_row(repo.path, "tokenomics_observation_conflicts", "conflict_id", conflict.conflict_id)["conflict_state"]
        == "open"
    )
    assert _db_row(repo.path, "tokenomics_report_observation_links", "link_id", lineage.link_id)["observation_id"] == (
        claim.claim_id
    )


def test_corrections_and_retractions_append_lifecycle_history(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    claim = _claim("claim:team-allocation", "team_allocation", "20%")
    repo.save_evidence_claim(claim)

    active = EvidenceLifecycleEvent(
        event_id="event:active",
        claim_id=claim.claim_id,
        lifecycle_status="active",
        effective_at=NOW,
        recorded_at=NOW,
    )
    corrected = EvidenceLifecycleEvent(
        event_id="event:corrected",
        claim_id=claim.claim_id,
        lifecycle_status="corrected",
        effective_at=NOW + timedelta(days=1),
        recorded_at=NOW + timedelta(days=1),
        predecessor_event_id=active.event_id,
        predecessor_claim_id=claim.claim_id,
        reason="issuer correction",
    )
    retracted = EvidenceLifecycleEvent(
        event_id="event:retracted",
        claim_id=claim.claim_id,
        lifecycle_status="retracted",
        effective_at=NOW + timedelta(days=2),
        recorded_at=NOW + timedelta(days=2),
        predecessor_event_id=corrected.event_id,
        predecessor_claim_id=claim.claim_id,
        reason="source withdrawal",
    )

    repo.save_evidence_lifecycle_event(active)
    repo.save_evidence_lifecycle_event(corrected)
    repo.save_evidence_lifecycle_event(retracted)

    lifecycle = repo.claim_lifecycle(claim.claim_id)
    assert [row["lifecycle_status"] for row in lifecycle] == ["active", "corrected", "retracted"]
    assert lifecycle[1]["predecessor_event_id"] == active.event_id
    assert lifecycle[2]["predecessor_event_id"] == corrected.event_id
    assert repo.count("tokenomics_evidence_claims") == 1


def test_reused_lifecycle_identity_cannot_mutate_prior_correction(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    claim = _claim("claim:lifecycle-immutable", "team_allocation", "20%")
    repo.save_evidence_claim(claim)
    active = EvidenceLifecycleEvent(
        event_id="event:lifecycle-active",
        claim_id=claim.claim_id,
        lifecycle_status="active",
        effective_at=NOW,
        recorded_at=NOW,
    )
    correction = EvidenceLifecycleEvent(
        event_id="event:lifecycle-corrected",
        claim_id=claim.claim_id,
        lifecycle_status="corrected",
        effective_at=NOW + timedelta(days=1),
        recorded_at=NOW + timedelta(days=1),
        predecessor_event_id=active.event_id,
        predecessor_claim_id=claim.claim_id,
        reason="issuer correction",
    )
    repo.save_evidence_lifecycle_event(active)
    repo.save_evidence_lifecycle_event(correction)

    with pytest.raises(TokenomicsIntegrityError):
        repo.save_evidence_lifecycle_event(replace(correction, lifecycle_status="retracted"))

    lifecycle = repo.claim_lifecycle(claim.claim_id)
    assert [row["event_id"] for row in lifecycle] == [active.event_id, correction.event_id]
    assert lifecycle[1]["lifecycle_status"] == "corrected"
    assert lifecycle[1]["predecessor_event_id"] == active.event_id


def test_conflicting_claims_coexist_without_overwrite(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    first = _claim("claim:max-supply-a", "max_supply", "1000000")
    second = _claim("claim:max-supply-b", "max_supply", "1200000")
    repo.save_evidence_claim(first)
    repo.save_evidence_claim(second)
    conflict = ObservationConflict(
        conflict_id="conflict:max-supply",
        asset_id="asset:hunter",
        conflict_state="open",
        detected_at=NOW,
        recorded_at=NOW,
    )
    repo.save_observation_conflict(conflict)
    repo.save_observation_conflict_member(
        ObservationConflictMember(
            member_id="conflict-member:a",
            conflict_id=conflict.conflict_id,
            observation_table="tokenomics_evidence_claims",
            observation_id=first.claim_id,
            role="conflicting_claim",
        )
    )
    repo.save_observation_conflict_member(
        ObservationConflictMember(
            member_id="conflict-member:b",
            conflict_id=conflict.conflict_id,
            observation_table="tokenomics_evidence_claims",
            observation_id=second.claim_id,
            role="conflicting_claim",
        )
    )

    claims = repo.evidence_claims(asset_id="asset:hunter", predicate="max_supply")
    assert [row["value"] for row in claims] == ["1000000", "1200000"]
    assert len(repo.conflict_members(conflict.conflict_id)) == 2


def test_time_versioned_identity_state_is_replay_safe(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    later_effective = NOW + timedelta(days=1)
    later_recorded = NOW + timedelta(days=2)
    repo.save_token_asset(
        TokenAsset(
            asset_id="asset:hunter",
            candidate_id="candidate:hunter",
            symbol="HUNT2",
            name="Hunter Renamed",
            effective_at=later_effective,
            recorded_at=later_recorded,
        )
    )
    repo.save_token_representation(
        TokenRepresentation(
            representation_id="representation:hunter:eth",
            asset_id="asset:hunter",
            chain="ethereum",
            contract_address="0xhunterv2",
            decimals=18,
            effective_at=later_effective,
            recorded_at=later_recorded,
        )
    )

    assert repo.count("token_assets") == 2
    assert repo.count("token_representations") == 2

    strict_asset = repo.token_asset_at("asset:hunter", later_effective, report_mode="known_by_hunter")
    reconstructed_asset = repo.token_asset_at("asset:hunter", later_effective, report_mode="reconstructed")
    strict_representation = repo.token_representation_at(
        "representation:hunter:eth",
        later_effective,
        report_mode="known_by_hunter",
    )
    reconstructed_representation = repo.token_representation_at(
        "representation:hunter:eth",
        later_effective,
        report_mode="reconstructed",
    )

    assert strict_asset is not None
    assert strict_asset["symbol"] == "HUNT"
    assert strict_asset["report_mode"] == "known_by_hunter"
    assert strict_asset["reconstructed_after_cutoff"] is False
    assert reconstructed_asset is not None
    assert reconstructed_asset["symbol"] == "HUNT2"
    assert reconstructed_asset["report_mode"] == "reconstructed"
    assert reconstructed_asset["reconstructed_after_cutoff"] is True
    assert strict_representation is not None
    assert strict_representation["contract_address"] == "0xhunter"
    assert reconstructed_representation is not None
    assert reconstructed_representation["contract_address"] == "0xhunterv2"
    assert reconstructed_representation["reconstructed_after_cutoff"] is True


def test_multi_valued_lineage_uses_relation_tables(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    first_artifact = _artifact("artifact:first")
    second_artifact = _artifact("artifact:second")
    claim = _claim("claim:lineage", "unlocked_supply", "300000")
    repo.save_claim_with_lineage(
        claim,
        artifacts=(first_artifact, second_artifact),
        artifact_links=(
            ClaimArtifactLink(
                link_id="link:first",
                claim_id=claim.claim_id,
                artifact_id=first_artifact.artifact_id,
                role="source",
                position=0,
            ),
            ClaimArtifactLink(
                link_id="link:second",
                claim_id=claim.claim_id,
                artifact_id=second_artifact.artifact_id,
                role="supporting_source",
                position=1,
            ),
        ),
    )

    assert [row["artifact_id"] for row in repo.claim_lineage(claim.claim_id)] == ["artifact:first", "artifact:second"]
    for table in ("tokenomics_evidence_claims", "tokenomics_claim_artifact_links"):
        columns = set(repo.columns(table))
        assert not {"artifact_ids", "claim_ids", "lineage"}.intersection(columns)


def test_balance_only_holder_entry_cannot_create_identity_attribution(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    snapshot = HolderBalanceSnapshot(
        snapshot_id="snapshot:one",
        representation_id="representation:hunter:eth",
        observed_at=NOW,
        recorded_at=NOW,
        coverage_state="partial",
    )
    repo.save_holder_balance_snapshot(snapshot)
    repo.save_holder_entry(
        HolderEntry(
            entry_id="holder:large-balance",
            snapshot_id=snapshot.snapshot_id,
            address="0xlarge",
            balance="5000000000",
            unit="HUNT",
            attribution_basis="balance_only",
        )
    )

    assert repo.count("tokenomics_holder_entries") == 1
    assert repo.count("tokenomics_address_classifications") == 0
    with pytest.raises(ValueError, match="balance_only attribution cannot assign"):
        AddressClassification(
            classification_id="classification:team",
            representation_id="representation:hunter:eth",
            address="0xlarge",
            category="team",
            attribution_basis="balance_only",
            verification_state="unverified",
            confidence_state="low",
            valid_from=NOW,
            valid_to=None,
            recorded_at=NOW,
        )
    with pytest.raises(ValueError, match="category must be one of"):
        AddressClassification(
            classification_id="classification:whale",
            representation_id="representation:hunter:eth",
            address="0xlarge",
            category=cast(AddressCategory, "whale"),
            attribution_basis="manual_review",
            verification_state="verified",
            confidence_state="high",
            valid_from=NOW,
            valid_to=None,
            recorded_at=NOW,
        )


def test_address_classification_validity_intervals(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    address = "0xvenue"
    repo.save_address_classification(
        AddressClassification(
            classification_id="classification:exchange",
            representation_id="representation:hunter:eth",
            address=address,
            category="exchange",
            attribution_basis="provider_label",
            verification_state="verified",
            confidence_state="high",
            valid_from=NOW,
            valid_to=NOW + timedelta(days=10),
            recorded_at=NOW,
        )
    )
    repo.save_address_classification(
        AddressClassification(
            classification_id="classification:unknown",
            representation_id="representation:hunter:eth",
            address=address,
            category="unknown",
            attribution_basis="manual_review",
            verification_state="unverified",
            confidence_state="unknown",
            valid_from=NOW + timedelta(days=10),
            valid_to=None,
            recorded_at=NOW + timedelta(days=10),
        )
    )

    early = repo.address_classifications_at("representation:hunter:eth", address, NOW + timedelta(days=5))
    later = repo.address_classifications_at("representation:hunter:eth", address, NOW + timedelta(days=15))

    assert [row["category"] for row in early] == ["exchange"]
    assert [row["category"] for row in later] == ["unknown"]


def test_report_lineage_and_sufficiency_links_are_normalized(tmp_path: Path) -> None:
    repo = _repo_with_token(tmp_path)
    observation = VenueMarketObservation(
        observation_id="market:one",
        representation_id="representation:hunter:eth",
        venue="example-exchange",
        pair="HUNT/USD",
        price="1.00",
        volume_24h="1000",
        window_start=NOW,
        window_end=NOW + timedelta(hours=1),
        observed_at=NOW + timedelta(hours=1),
        recorded_at=NOW + timedelta(hours=1),
        coverage_state="partial",
    )
    repo.save_venue_market_observation(observation)
    run = TokenomicsReportRun(
        run_id="report:known",
        execution_identity="execution:known",
        report_mode="known_by_hunter",
        cutoff_at=NOW,
        started_at=NOW,
        recorded_at=NOW,
    )
    repo.save_report_run(run)
    repo.save_report_observation_link(
        TokenomicsReportObservationLink(
            link_id="report-observation:one",
            report_run_id=run.run_id,
            observation_table="tokenomics_venue_market_observations",
            observation_id=observation.observation_id,
            role="supporting_observation",
            position=0,
        )
    )
    assessment = TokenomicsSufficiencyAssessmentRecord(
        assessment_id="tokenomics-sufficiency:one",
        report_run_id=run.run_id,
        asset_id="asset:hunter",
        assessment_scope="market_liquidity",
        availability_state="partial",
        confidence_state="low",
        limitation="venue coverage is partial",
        effective_at=NOW,
        recorded_at=NOW,
    )
    repo.save_sufficiency_assessment(assessment)
    repo.save_report_sufficiency_link(
        TokenomicsReportSufficiencyLink(
            link_id="report-sufficiency:one",
            report_run_id=run.run_id,
            assessment_id=assessment.assessment_id,
            role="degraded_mode_limitation",
            position=0,
        )
    )

    assert repo.count("tokenomics_report_observation_links") == 1
    assert repo.count("tokenomics_report_sufficiency_links") == 1
    for table in (
        "tokenomics_report_runs",
        "tokenomics_report_observation_links",
        "tokenomics_report_sufficiency_links",
    ):
        columns = set(repo.columns(table))
        assert not {"observation_ids", "assessment_ids", "members"}.intersection(columns)


def test_phase_a_does_not_wire_tokenomics_into_runtime_or_configs() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_files = (
        "src/hunter/cli.py",
        "src/hunter/pipeline.py",
        "src/hunter/automation/execution.py",
        "configs/automation.yaml",
    )
    for filename in runtime_files:
        assert "tokenomics" not in (root / filename).read_text()


def _repo_with_token(tmp_path: Path) -> TokenomicsRepository:
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    repo.save_token_asset(
        TokenAsset(
            asset_id="asset:hunter",
            candidate_id="candidate:hunter",
            symbol="HUNT",
            name="Hunter",
            effective_at=NOW,
            recorded_at=NOW,
        )
    )
    repo.save_token_representation(
        TokenRepresentation(
            representation_id="representation:hunter:eth",
            asset_id="asset:hunter",
            chain="ethereum",
            contract_address="0xhunter",
            decimals=18,
            effective_at=NOW,
            recorded_at=NOW,
        )
    )
    return repo


def _artifact(artifact_id: str) -> TokenomicsEvidenceArtifact:
    return TokenomicsEvidenceArtifact(
        artifact_id=artifact_id,
        source_type="official_disclosure",
        source_uri=f"https://example.test/{artifact_id}",
        content_hash=f"sha256:{artifact_id}",
        observed_at=NOW,
        recorded_at=NOW,
        lifecycle_status="active",
    )


def _claim(claim_id: str, predicate: str, value: str) -> TokenomicsEvidenceClaim:
    return TokenomicsEvidenceClaim(
        claim_id=claim_id,
        asset_id="asset:hunter",
        subject="asset:hunter",
        predicate=predicate,
        value=value,
        unit="HUNT",
        evidence_status="active",
        confidence_state="medium",
        effective_at=NOW,
        recorded_at=NOW,
    )


def _db_row(path: Path, table: str, column: str, value: str) -> dict[str, object]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchone()
    assert row is not None
    return dict(row)
