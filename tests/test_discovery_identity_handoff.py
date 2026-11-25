"""Focused regressions for the bounded identity -> screening -> queue handoff.

Scenarios A-R cover the demonstrated lifecycle defects at base HEAD 87f87d6a:
disconnected identity, fixed-prefix starvation, contradictory status columns,
ungated deep-analysis eligibility, and repeated screening during queue refresh.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from hunter.data_ops import OPERATION_COMMANDS, READ_ONLY_DISCOVERY_OPERATIONS
from hunter.discovery import (
    CandidateAlias,
    CandidateDiscoveryEngine,
    CandidateIdentifier,
    CandidateIdentityResolutionEngine,
    CandidateRegistryRepository,
    CandidateScreeningResult,
    CandidateSource,
)
from hunter.discovery.automation import require_discovery_automation
from hunter.discovery.configuration import DiscoveryConfig
from hunter.discovery.identity import identity_is_accepted
from hunter.discovery.models import (
    IDENTITY_POLICY_VERSION,
    QUEUE_STATUS_ELIGIBLE,
    QUEUE_STATUS_NOT_QUEUED,
    SCREENING_STATUS_NOT_SCREENED,
    CandidateQueueEntry,
    CandidateRecord,
)
from hunter.discovery.repository import _identity_from_row


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _add(
    repository: CandidateRegistryRepository,
    *,
    candidate_id: str,
    slug: str,
    lifecycle: str = "analyzable",
    symbol: str = "COIN",
    identifiers: tuple[CandidateIdentifier, ...] = (),
    aliases: tuple[CandidateAlias, ...] = (),
    sources: tuple[CandidateSource, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> None:
    now = _now()
    repository.upsert_candidate(
        CandidateRecord(
            candidate_id=candidate_id,
            slug=slug,
            name=slug.replace("-", " ").title(),
            symbol=symbol,
            sector="defi",
            primary_chain="ethereum",
            candidate_type="token",  # type: ignore[arg-type]
            lifecycle_status=lifecycle,  # type: ignore[arg-type]
            discovery_source="coingecko",
            first_seen_at=now,
            last_seen_at=now,
            confidence=1.0,
            identifiers=identifiers,
            aliases=aliases,
            sources=sources,
            metadata=metadata or {},
        )
    )


def _engine(repository: CandidateRegistryRepository) -> CandidateDiscoveryEngine:
    config = DiscoveryConfig(
        registry_path=str(repository.path),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    return CandidateDiscoveryEngine(repository, config, providers={})


def _contract_candidate(
    candidate_id: str, slug: str, source_id: str
) -> tuple[tuple[CandidateIdentifier, ...], tuple[CandidateSource, ...]]:
    now = _now()
    return (
        (
            CandidateIdentifier(
                candidate_id=candidate_id,
                namespace="contract:eth",
                value=f"0x{source_id}",
                source="geckoterminal",
            ),
        ),
        (
            CandidateSource(
                source_id=source_id,
                candidate_id=candidate_id,
                provider="geckoterminal",
                source_type="public_provider_listing",
                source_url="https://example.test/source",
                source_ref=source_id,
                observed_at=now,
            ),
        ),
    )


def test_A_identity_batch_persists_policy_fingerprint_and_status(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:exact"
    _add(
        repository,
        candidate_id=candidate_id,
        slug="exact-project",
        lifecycle="screenable",
        identifiers=(
            CandidateIdentifier(
                candidate_id=candidate_id, namespace="hunter_project", value="exact-project", source="seed"
            ),
        ),
    )

    summary = CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    result = repository.identity_result_for(candidate_id)

    assert summary.evaluated == 1
    assert result is not None and result.outcome == "exact"
    assert result.policy_version == IDENTITY_POLICY_VERSION and result.evidence_fingerprint
    assert repository.get(candidate_id).identity_resolution_status == "exact"
    assert repository.get(candidate_id).lifecycle_status == "identified"


def test_B_unchanged_evidence_is_not_re_evaluated(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:stable"
    identifiers, sources = _contract_candidate(candidate_id, "stable", "source-stable")
    _add(repository, candidate_id=candidate_id, slug="stable", identifiers=identifiers, sources=sources)
    engine = CandidateIdentityResolutionEngine(repository)

    first = engine.resolve_batch(limit=10)
    second = engine.resolve_batch(limit=10)

    assert first.evaluated == 1
    assert second.evaluated == 0 and second.unchanged == 1


def test_C_evidence_change_invalidates_fingerprint(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:changed"
    identifiers, sources = _contract_candidate(candidate_id, "changed", "source-changed")
    _add(repository, candidate_id=candidate_id, slug="changed", identifiers=identifiers, sources=sources)
    engine = CandidateIdentityResolutionEngine(repository)
    engine.resolve_batch(limit=10)
    before = repository.identity_result_for(candidate_id)

    extra = identifiers + (
        CandidateIdentifier(candidate_id=candidate_id, namespace="coingecko", value="changed", source="coingecko"),
    )
    _add(repository, candidate_id=candidate_id, slug="changed", identifiers=extra, sources=sources)
    after_summary = engine.resolve_batch(limit=10)
    after = repository.identity_result_for(candidate_id)

    assert before is not None and after is not None
    assert after_summary.evaluated == 1
    assert after.evidence_fingerprint != before.evidence_fingerprint


def test_D_ticker_collision_invalidates_prior_outcome(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    first_id, second_id = "candidate:collision-a", "candidate:collision-b"
    identifiers = (
        CandidateIdentifier(candidate_id=first_id, namespace="coingecko", value="collision-a", source="coingecko"),
    )
    _add(
        repository,
        candidate_id=first_id,
        slug="collision-a",
        identifiers=identifiers,
        aliases=(CandidateAlias(first_id, "SHARE", "ticker_symbol", "coingecko"),),
    )
    engine = CandidateIdentityResolutionEngine(repository)
    engine.resolve_batch(limit=10)
    assert repository.identity_result_for(first_id).outcome == "probable"  # type: ignore[union-attr]

    _add(
        repository,
        candidate_id=second_id,
        slug="collision-b",
        aliases=(CandidateAlias(second_id, "SHARE", "ticker_symbol", "coingecko"),),
    )
    summary = engine.resolve_batch(limit=10)

    assert summary.by_outcome["ambiguous"] == 2
    assert repository.identity_result_for(first_id).outcome == "ambiguous"  # type: ignore[union-attr]
    assert "ticker_collision" in repository.identity_result_for(first_id).conflicts  # type: ignore[union-attr]
    assert len([item for item in repository.conflicts() if item.conflict_type == "identity_ambiguous"]) == 2


def test_E_fair_cursor_covers_all_candidates_and_advances(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    for index in range(12):
        _add(
            repository,
            candidate_id=f"candidate:z-{index:02d}",
            slug=f"aaa-prefix-{index:02d}",
        )

    batches = [repository.fair_candidates("identity", 5) for _ in range(3)]
    covered = {item.candidate_id for batch in batches for item in batch}
    first_ids = [item.candidate_id for item in batches[0]]
    second_ids = [item.candidate_id for item in batches[1]]

    assert len(covered) == 12
    assert first_ids == sorted(first_ids)
    assert not set(first_ids) & set(second_ids)
    assert repository.discovery_page_cursor("processing:identity") == 0
    with repository._connect() as conn:
        row = conn.execute("SELECT cursor FROM checkpoints WHERE provider = ?", ("processing:identity",)).fetchone()
    assert row is not None and str(row["cursor"]).startswith("id:")


def test_F_screening_status_reconciles_with_result_table(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:screen-status"
    _add(repository, candidate_id=candidate_id, slug="screen-status")
    repository.save_screening_result(
        CandidateScreeningResult(
            screening_id="screening-1",
            candidate_id=candidate_id,
            screened_at=_now(),
            status="advanced",
            score=0.9,
            advanced=True,
            reasons=("has evidence",),
            missing_evidence=(),
            confidence=0.9,
            coverage=1.0,
        )
    )
    assert repository.get(candidate_id).screening_status == "advanced"

    repository.reconcile_candidate_status()
    assert repository.get(candidate_id).screening_status == "advanced"
    assert SCREENING_STATUS_NOT_SCREENED == "not_screened"


def test_G_queue_status_reconciles_with_result_table(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:queue-status"
    _add(repository, candidate_id=candidate_id, slug="queue-status")
    now = _now()
    repository.save_queue_entries(
        (
            CandidateQueueEntry(
                queue_entry_id="queue-1",
                candidate_id=candidate_id,
                priority_score=0.9,
                priority="critical",  # type: ignore[arg-type]
                priority_reasons=(),
                missing_evidence=(),
                lifecycle_state="analyzable",
                created_at=now,
                updated_at=now,
                source_run_id="test",
                eligible_for_deep_analysis=True,
            ),
        )
    )
    assert repository.get(candidate_id).queue_status == QUEUE_STATUS_ELIGIBLE

    repository.reconcile_candidate_status()
    assert repository.get(candidate_id).queue_status == QUEUE_STATUS_ELIGIBLE
    assert QUEUE_STATUS_NOT_QUEUED == "not_queued"


def test_H_status_survives_provider_upsert(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:upsert"
    identifiers, sources = _contract_candidate(candidate_id, "upsert", "source-upsert")
    _add(repository, candidate_id=candidate_id, slug="upsert", identifiers=identifiers, sources=sources)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    engine = _engine(repository)
    engine.refresh_queue(limit=10)

    before = repository.get(candidate_id)
    _add(repository, candidate_id=candidate_id, slug="upsert", identifiers=identifiers, sources=sources)
    after = repository.get(candidate_id)

    assert before.identity_resolution_status == after.identity_resolution_status == "probable"
    assert before.screening_status == after.screening_status
    assert before.queue_status == after.queue_status


def test_I_no_eligibility_without_accepted_identity(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:ungated"
    identifiers, sources = _contract_candidate(candidate_id, "ungated", "source-ungated")
    _add(repository, candidate_id=candidate_id, slug="ungated", identifiers=identifiers, sources=sources)
    engine = _engine(repository)

    engine.refresh_queue(limit=10)

    assert all(not entry.eligible_for_deep_analysis for entry in repository.all_queue_entries())


def test_J_accepted_identity_enables_eligibility(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:gated"
    identifiers, sources = _contract_candidate(candidate_id, "gated", "source-gated")
    _add(repository, candidate_id=candidate_id, slug="gated", identifiers=identifiers, sources=sources)
    engine = _engine(repository)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)

    entries = engine.refresh_queue(limit=10)

    assert len(entries) == 1 and entries[0].eligible_for_deep_analysis is True
    assert engine.handoff_manifest()["shortlist_size"] == 1


def test_K_stale_eligibility_invalidated_on_conflict(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:conflict"
    identifiers, sources = _contract_candidate(candidate_id, "conflict", "source-conflict")
    _add(repository, candidate_id=candidate_id, slug="conflict", identifiers=identifiers, sources=sources)
    engine = _engine(repository)
    identity_engine = CandidateIdentityResolutionEngine(repository)
    identity_engine.resolve_batch(limit=10)
    engine.refresh_queue(limit=10)
    assert engine.handoff_manifest()["shortlist_size"] == 1

    _add(
        repository,
        candidate_id=candidate_id,
        slug="conflict",
        identifiers=identifiers,
        sources=sources,
        metadata={"provider_disagreements": [{"field": "reserve_usd"}]},
    )
    identity_engine.resolve_batch(limit=10)
    changes = engine.reconcile_eligibility()

    assert changes == 1
    assert repository.identity_result_for(candidate_id).outcome == "conflict"  # type: ignore[union-attr]
    assert engine.handoff_manifest()["shortlist_size"] == 0


def test_L_queue_refresh_reuses_unchanged_screening(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:reuse"
    identifiers, sources = _contract_candidate(candidate_id, "reuse", "source-reuse")
    _add(repository, candidate_id=candidate_id, slug="reuse", identifiers=identifiers, sources=sources)
    engine = _engine(repository)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    engine.refresh_queue(limit=10)
    with repository._connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0]

    engine.refresh_queue(limit=10)
    with repository._connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM screening_results").fetchone()[0]

    assert before == after == 1


def test_M_run_market_discovery_wires_identity_screening_queue(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    engine = _engine(repository)

    result = engine.run_market_discovery(limit=1000)

    assert result["identity_evaluated"] == 50
    assert result["screened"] == 50 and result["queued"] == 50
    status_changes = result["status_changes"]
    assert isinstance(status_changes, dict)
    assert status_changes["screening_status"] == 0
    assert repository.stats().total_candidates == 50
    assert all(
        repository.get(entry.candidate_id).screening_status == "advanced" for entry in repository.all_queue_entries()
    )
    assert all(entry.eligible_for_deep_analysis for entry in repository.all_queue_entries())
    assert repository.reconcile_candidate_status() == {
        "identity_resolution_status": 0,
        "screening_status": 0,
        "not_queued": 0,
        "queued": 0,
        "deep_analysis_eligible": 0,
    }


def test_N_identity_operation_is_read_only_allowlisted() -> None:
    assert "discovery_identity_resolve" in READ_ONLY_DISCOVERY_OPERATIONS
    assert OPERATION_COMMANDS["discovery_identity_resolve"] == (
        "discovery",
        "identity",
        "resolve",
        "--batch-size",
        "500",
    )


def test_O_canonical_config_declares_ordered_handoff() -> None:
    payload = yaml.safe_load(Path("configs/automation.yaml").read_text(encoding="utf-8"))
    jobs = {str(item.get("job_id")): item for item in payload["jobs"] if isinstance(item, dict)}
    identity = jobs["discovery-identity-resolution"]
    screening = jobs["discovery-candidate-screening"]
    queue = jobs["discovery-queue-refresh"]

    assert identity["metadata"]["evidence_gated"] is True
    assert [
        identity["schedule"]["expression"],
        screening["schedule"]["expression"],
        queue["schedule"]["expression"],
    ] == [
        "5 */6 * * *",
        "10 */6 * * *",
        "15 */6 * * *",
    ]


def test_P_identity_promotion_records_governed_transition(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:transition"
    _add(
        repository,
        candidate_id=candidate_id,
        slug="transition",
        lifecycle="screenable",
        identifiers=(
            CandidateIdentifier(
                candidate_id=candidate_id, namespace="hunter_project", value="transition", source="seed"
            ),
        ),
    )

    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)

    with repository._connect() as conn:
        rows = conn.execute(
            "SELECT previous_state, new_state, discovery_run_id FROM lifecycle_transitions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["previous_state"] == "screenable"
    assert dict(rows[0])["new_state"] == "identified"
    assert dict(rows[0])["discovery_run_id"] == "identity-resolution"


def test_Q_latest_screening_results_deduplicates_equal_timestamps(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:dedupe"
    _add(repository, candidate_id=candidate_id, slug="dedupe")
    timestamp = _now()
    for index, status in enumerate(("advanced", "deferred")):
        repository.save_screening_result(
            CandidateScreeningResult(
                screening_id=f"screening-{index}",
                candidate_id=candidate_id,
                screened_at=timestamp,
                status=status,  # type: ignore[arg-type]
                score=0.5 - index,
                advanced=index == 0,
                reasons=(),
                missing_evidence=(),
                confidence=0.5,
                coverage=1.0,
            )
        )

    results = repository.latest_screening_results()

    assert len(results) == 1 and results[0].screening_id == "screening-1"


def test_R_handoff_manifest_is_evidence_scoped_and_read_only(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    candidate_id = "candidate:handoff"
    identifiers, sources = _contract_candidate(candidate_id, "handoff", "source-handoff")
    _add(repository, candidate_id=candidate_id, slug="handoff", identifiers=identifiers, sources=sources)
    engine = _engine(repository)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    engine.refresh_queue(limit=10)

    manifest = engine.handoff_manifest(limit=1)

    assert manifest["shortlist_size"] == 1
    assert manifest["handoff_boundary"].startswith("read-only evidence manifest")
    item = manifest["shortlist"][0]  # type: ignore[index]
    assert item["candidate_id"] == candidate_id
    assert item["identity_outcome"] == "probable"
    assert identity_is_accepted(repository.identity_result_for(candidate_id))


def test_S_new_collision_invalidates_handoff_before_resolution(tmp_path):
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    first = "candidate:a"
    identifiers, sources = _contract_candidate(first, "a", "source-a")
    _add(
        repository,
        candidate_id=first,
        slug="a",
        identifiers=identifiers,
        sources=sources,
        aliases=(CandidateAlias(first, "SHARE", "ticker_symbol", "coingecko"),),
    )
    engine = _engine(repository)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    engine.refresh_queue(limit=10)
    assert engine.handoff_manifest()["shortlist_size"] == 1
    _add(
        repository,
        candidate_id="candidate:b",
        slug="b",
        aliases=(CandidateAlias("candidate:b", "SHARE", "ticker_symbol", "coingecko"),),
    )
    assert engine.handoff_manifest()["shortlist_size"] == 0
    engine.reconcile_eligibility()
    assert not any(e.eligible_for_deep_analysis for e in repository.all_queue_entries())


def test_T_collision_fanout_respects_batch_limit(tmp_path):
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    for i in range(12):
        cid = f"candidate:{i:02}"
        _add(
            repository,
            candidate_id=cid,
            slug=f"coin-{i}",
            aliases=(CandidateAlias(cid, "SHARE", "ticker_symbol", "coingecko"),),
        )
    engine = CandidateIdentityResolutionEngine(repository)
    for _ in range(4):
        assert engine.resolve_batch(limit=3).evaluated <= 3
    assert len(repository.latest_identity_by_candidate()) == 12


def test_U_identity_row_legacy_and_current_columns(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    identifiers, sources = _contract_candidate("candidate:row", "row", "source-row")
    _add(repository, candidate_id="candidate:row", slug="row", identifiers=identifiers, sources=sources)
    CandidateIdentityResolutionEngine(repository).resolve_batch(limit=10)
    with repository._connect() as conn:
        current = conn.execute("SELECT * FROM identity_results").fetchone()
        assert isinstance(current, sqlite3.Row)
        names = [name for name in current.keys() if name not in {"policy_version", "evidence_fingerprint"}]
        legacy = conn.execute("SELECT " + ", ".join(names) + " FROM identity_results").fetchone()
        assert isinstance(legacy, sqlite3.Row)
    assert _identity_from_row(current).policy_version == IDENTITY_POLICY_VERSION
    assert _identity_from_row(current).evidence_fingerprint
    assert _identity_from_row(legacy).policy_version == "identity-policy-v1"
    assert _identity_from_row(legacy).evidence_fingerprint == ""


def test_V_partial_discovery_automation_fails_closed(tmp_path) -> None:
    payload = yaml.safe_load(Path("configs/automation.yaml").read_text(encoding="utf-8"))
    payload["jobs"] = [
        job for job in payload["jobs"] if isinstance(job, dict) and job.get("job_id") != "discovery-identity-resolution"
    ]
    path = tmp_path / "automation.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="discovery-identity-resolution"):
        require_discovery_automation(path)
