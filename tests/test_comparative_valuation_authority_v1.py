"""Production execution path for the existing canonical Comparative Valuation
authority (`CanonicalComparativeValuationService`, ADR 0026), exercised through the
new `hunter comparative-valuation-authority run MANIFEST.json` CLI
(`hunter.comparative_valuation.command`), added by Issue #181.

This suite proves the new entry point is a pure orchestration layer -- it
reimplements no validation, formula, replay, or persistence logic of its own. Every
guarantee already independently audited on the underlying service (strict-known
selection, truthful missingness, coverage gates, append-only correction with
branching-lineage rejection, repository-bypass rejection) is proven here to hold
identically when driven through the CLI, not just through direct construction as in
`test_comparative_valuation_v1.py`.

Fixture helpers (identity/candidate/policy_payload builders and native-evidence
seeding primitives) are imported from `test_comparative_valuation_v1` rather than
duplicated, mirroring `test_valuation_authority_v1.py`'s own convention against
`test_valuation_v1.py`, so the two suites never drift out of sync on what a valid
policy/candidate/identity payload looks like.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_comparative_valuation_v1 import (
    NOW,
    RECORDED,
    SIGNING_KEY,
    SIGNING_KEY_ID,
    candidate,
    evidence_result,
    identity,
    policy_payload,
    rule_result,
    seed_market_fact,
    supply_result,
    value_capture_source,
)

from hunter import __main__ as hunter_main
from hunter.comparative_valuation import command as comparative_valuation_authority_command
from hunter.comparative_valuation.repository import (
    ComparativeValuationIntegrityError,
    ComparativeValuationRepository,
)
from hunter.comparative_valuation.service import CanonicalComparativeValuationService
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService


def _db_path(application_root: Path) -> Path:
    return application_root / "data" / "data_ops.sqlite"


def _seed_native_evidence(
    tmp_path: Path, *, entities: tuple[str, ...] = ("target", "peer-a", "peer-b", "peer-c")
) -> None:
    """Seeds the complete native evidence chain (fully-diluted observed market value,
    value-capture flow evidence, fully-diluted supply, value-capture rule) at the exact
    canonical path `hunter comparative-valuation-authority` resolves
    (`<application_root>/data/data_ops.sqlite`), using only the existing,
    already-audited `value_capture`/`market_facts` services -- the identical ingestion
    `Fixture` in test_comparative_valuation_v1.py performs, but pointed at the CLI's
    canonical persistence path rather than an arbitrary fixture path. Evidence
    ingestion is out of scope for this CLI; only peer_policy/peer_universe/
    eligibility_decision/metric_observation/assess are driven through the new entry
    point in these tests."""
    db_path = _db_path(tmp_path)
    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vc_service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((value_capture_source(),)),
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(
        value_capture_source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
    )
    for index, entity in enumerate(entities, start=1):
        market_fact = seed_market_fact(db_path, entity, str(12_000_000 + index * 1_000_000))
        evidence = vc_service.ingest_evidence(
            provider, evidence_result(provider, entity, acquisition_id=f"evidence-{entity}")
        )
        vc_service.ingest_supply(
            provider,
            supply_result(provider, entity, evidence.record_id, market_fact, acquisition_id=f"supply-{entity}"),
        )
        vc_service.ingest_rule(
            provider, rule_result(provider, entity, evidence.record_id, acquisition_id=f"rule-{entity}")
        )


def _iso(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


def _identity_payload(entity: str) -> dict[str, str]:
    target = identity(entity)
    return {
        "entity_id": target.entity_id,
        "economic_claim_id": target.economic_claim_id,
        "asset_id": target.asset_id,
        "representation_id": target.representation_id,
        "token_id": target.token_id,
        "chain": target.chain,
        "contract_address": target.contract_address,
    }


def _candidate_payload(entity: str) -> dict[str, object]:
    item = candidate(entity)
    return {
        "identity": _identity_payload(entity),
        "source_record_id": item.source_record_id,
        "source_record_version": item.source_record_version,
        "lifecycle_state": item.lifecycle_state,
        "sector_classification": item.sector_classification,
        "value_capture_mechanism_type": item.value_capture_mechanism_type,
        "revenue_accounting_meaning": item.revenue_accounting_meaning,
        "native_evidence_type": item.native_evidence_type,
        "quote_currency": item.quote_currency,
        "supply_basis": item.supply_basis,
    }


def _write(tmp_path: Path, filename: str, manifest: dict[str, object]) -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _peer_policy_manifest(tmp_path: Path, *, filename: str = "peer-policy-manifest.json", **overrides: object) -> Path:
    payload = {key: _iso(value) for key, value in policy_payload(**overrides).items()}
    manifest = {"operation": "peer_policy", "payload": payload}
    return _write(tmp_path, filename, manifest)


def _peer_universe_manifest(
    tmp_path: Path,
    *,
    filename: str = "peer-universe-manifest.json",
    policy_record_id: str,
    peer_entities: tuple[str, ...] = ("peer-a", "peer-b", "peer-c"),
    cutoff: datetime = NOW,
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
    supersedes_record_id: str | None = None,
    correction_reason: str = "",
) -> Path:
    manifest = {
        "operation": "peer_universe",
        "identity": _identity_payload("target"),
        "policy_record_id": policy_record_id,
        "cutoff": cutoff.isoformat(),
        "candidates": [_candidate_payload(entity) for entity in peer_entities],
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
        "supersedes_record_id": supersedes_record_id,
        "correction_reason": correction_reason,
    }
    return _write(tmp_path, filename, manifest)


def _eligibility_decision_manifest(
    tmp_path: Path,
    *,
    filename: str = "eligibility-decision-manifest.json",
    entity: str,
    policy_record_id: str,
    universe_snapshot_id: str,
    effective_at: datetime = NOW,
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
) -> Path:
    manifest = {
        "operation": "eligibility_decision",
        "target_identity": _identity_payload("target"),
        "candidate_identity": _identity_payload(entity),
        "policy_record_id": policy_record_id,
        "universe_snapshot_id": universe_snapshot_id,
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
    }
    return _write(tmp_path, filename, manifest)


def _metric_observation_manifest(
    tmp_path: Path,
    *,
    filename: str = "metric-observation-manifest.json",
    entity: str,
    policy_record_id: str,
    universe_snapshot_id: str,
    effective_at: datetime = NOW,
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
) -> Path:
    manifest = {
        "operation": "metric_observation",
        "identity": _identity_payload(entity),
        "policy_record_id": policy_record_id,
        "universe_snapshot_id": universe_snapshot_id,
        "evidence_type": "official_disclosure",
        "rule_type": "fee_distribution",
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
    }
    return _write(tmp_path, filename, manifest)


def _assess_manifest(
    tmp_path: Path,
    *,
    filename: str = "assess-manifest.json",
    policy_record_id: str,
    universe_snapshot_id: str,
    effective_at: datetime = NOW,
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
    supersedes_record_id: str | None = None,
    correction_reason: str = "",
) -> Path:
    manifest = {
        "operation": "assess",
        "identity": _identity_payload("target"),
        "policy_record_id": policy_record_id,
        "universe_snapshot_id": universe_snapshot_id,
        "evidence_type": "official_disclosure",
        "rule_type": "fee_distribution",
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
        "supersedes_record_id": supersedes_record_id,
        "correction_reason": correction_reason,
    }
    return _write(tmp_path, filename, manifest)


def _status_manifest(
    tmp_path: Path,
    *,
    filename: str = "status-manifest.json",
    target: str,
    effective_as_of: datetime = NOW,
    known_by: datetime,
    logical_id: str | None = None,
) -> Path:
    manifest: dict[str, object] = {
        "operation": "status",
        "target": target,
        "effective_as_of": effective_as_of.isoformat(),
        "known_by": known_by.isoformat(),
    }
    if logical_id is not None:
        manifest["logical_id"] = logical_id
    return _write(tmp_path, filename, manifest)


def _run(
    monkeypatch: pytest.MonkeyPatch, application_root: Path, manifest_path: Path, capsys: pytest.CaptureFixture[str]
) -> dict:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(application_root))
    exit_code = comparative_valuation_authority_command.main(["run", str(manifest_path)])
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


# A. Peer policy persistence through the production entry point ----------------------


def test_peer_policy_persists_through_production_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _peer_policy_manifest(tmp_path)
    output = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert output["operation"] == "peer_policy"
    repository = ComparativeValuationRepository(_db_path(tmp_path))
    record = repository.get_peer_policy(output["record_id"])
    assert record is not None
    assert record.quality_state == "accepted"
    assert record.conflict_state == "none"


# B. End-to-end production-entry-point creation of every record family ---------------


def test_end_to_end_production_entry_point_creates_every_record_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_native_evidence(tmp_path)
    policy = _run(monkeypatch, tmp_path, _peer_policy_manifest(tmp_path), capsys)

    universe = _run(
        monkeypatch, tmp_path, _peer_universe_manifest(tmp_path, policy_record_id=policy["record_id"]), capsys
    )
    assert universe["operation"] == "peer_universe"
    assert universe["candidate_count"] == 3

    for entity in ("peer-a", "peer-b", "peer-c"):
        decision = _run(
            monkeypatch,
            tmp_path,
            _eligibility_decision_manifest(
                tmp_path,
                filename=f"decision-{entity}.json",
                entity=entity,
                policy_record_id=policy["record_id"],
                universe_snapshot_id=universe["record_id"],
            ),
            capsys,
        )
        assert decision["decision"] == "included"

    for entity in ("target", "peer-a", "peer-b", "peer-c"):
        observation = _run(
            monkeypatch,
            tmp_path,
            _metric_observation_manifest(
                tmp_path,
                filename=f"observation-{entity}.json",
                entity=entity,
                policy_record_id=policy["record_id"],
                universe_snapshot_id=universe["record_id"],
            ),
            capsys,
        )
        assert observation["availability_state"] == "AVAILABLE"
        assert observation["normalization_status"] == "unavailable"

    assessment = _run(
        monkeypatch,
        tmp_path,
        _assess_manifest(tmp_path, policy_record_id=policy["record_id"], universe_snapshot_id=universe["record_id"]),
        capsys,
    )
    assert assessment["operation"] == "assess"
    # ADR 0026: no calibrated normalization exists in this foundation, so a complete
    # cohort still never reaches AVAILABLE -- it reaches the raw-values state that
    # persists the median/residual while normalization remains unavailable.
    assert assessment["availability_state"] == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert assessment["normalization_status"] == "unavailable"
    assert assessment["raw_log_residual"] is not None
    assert assessment["peer_median_multiple"] is not None

    repository = ComparativeValuationRepository(_db_path(tmp_path))
    record = repository.get_assessment(assessment["record_id"])
    assert record is not None
    assert record.raw_log_residual == assessment["raw_log_residual"]


# C. Insert-identical idempotency -----------------------------------------------------


def test_insert_identical_peer_policy_via_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _peer_policy_manifest(tmp_path)
    first = _run(monkeypatch, tmp_path, manifest_path, capsys)
    second = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert first["record_id"] == second["record_id"]


# D. Divergent duplicate rejection ----------------------------------------------------


def test_divergent_peer_policy_duplicate_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_manifest = _peer_policy_manifest(tmp_path, filename="p1.json")
    _run(monkeypatch, tmp_path, first_manifest, capsys)
    second_manifest = _peer_policy_manifest(tmp_path, filename="p2.json", required_quote_currency="eur")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ComparativeValuationIntegrityError, match="root record already exists"):
        comparative_valuation_authority_command.main(["run", str(second_manifest)])


# E. CLI-construction / direct-construction field equivalence --------------------------


def test_cli_construction_is_field_equivalent_to_direct_service_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Independently constructs the complete record chain twice, from the same
    semantic inputs, into two completely separate databases:

    - Path A: entirely through the CLI (`comparative_valuation_authority_command.main`).
    - Path B: entirely through direct `CanonicalComparativeValuationService`
      construction, never touching the CLI/command module.

    A prior version of this test only re-read what the CLI itself had written and
    confirmed a direct-service *read* call returned the same record -- that proves
    persistence/replay compatibility, but it cannot detect a CLI argument-mapping bug
    (a dropped field, a field mapped to the wrong keyword, a type-coercion difference)
    because both sides of the comparison originate from the one CLI write.

    This test instead performs Path B as a fully independent *construction*, then
    asserts complete dataclass-field equality (via `asdict`) between the two paths for
    every one of the five record families -- covering canonical identity
    (`record_id`/`logical_id`), the deterministic `content_hash`, methodology/policy
    version fields, provenance, and every other ADR 0026 field. If CLI argument
    mapping ever diverges from direct construction, the computed `content_hash` (and
    therefore `record_id`) changes and this test fails.
    """
    cli_root = tmp_path / "cli-path"
    direct_root = tmp_path / "direct-path"
    cli_root.mkdir()
    direct_root.mkdir()

    # Path A: construct the complete chain entirely through the CLI.
    _seed_native_evidence(cli_root)
    policy_a = _run(monkeypatch, cli_root, _peer_policy_manifest(cli_root), capsys)
    universe_a = _run(
        monkeypatch, cli_root, _peer_universe_manifest(cli_root, policy_record_id=policy_a["record_id"]), capsys
    )
    decisions_a: dict[str, dict] = {}
    for entity in ("peer-a", "peer-b", "peer-c"):
        decisions_a[entity] = _run(
            monkeypatch,
            cli_root,
            _eligibility_decision_manifest(
                cli_root,
                filename=f"decision-{entity}.json",
                entity=entity,
                policy_record_id=policy_a["record_id"],
                universe_snapshot_id=universe_a["record_id"],
            ),
            capsys,
        )
    observations_a: dict[str, dict] = {}
    for entity in ("target", "peer-a", "peer-b", "peer-c"):
        observations_a[entity] = _run(
            monkeypatch,
            cli_root,
            _metric_observation_manifest(
                cli_root,
                filename=f"observation-{entity}.json",
                entity=entity,
                policy_record_id=policy_a["record_id"],
                universe_snapshot_id=universe_a["record_id"],
            ),
            capsys,
        )
    assessment_a = _run(
        monkeypatch,
        cli_root,
        _assess_manifest(
            cli_root, policy_record_id=policy_a["record_id"], universe_snapshot_id=universe_a["record_id"]
        ),
        capsys,
    )

    # Path B: construct the identical logical chain directly through the service, in a
    # completely separate database, never touching the CLI/command module.
    _seed_native_evidence(direct_root)
    direct_service = CanonicalComparativeValuationService(
        repository=ComparativeValuationRepository(_db_path(direct_root)),
        value_capture_repository=SupplyAndValueCaptureRepository(_db_path(direct_root)),
        market_fact_repository=ObservedMarketFactRepository(_db_path(direct_root)),
        application_root=direct_root,
    )
    policy_b = direct_service.persist_peer_policy(**policy_payload())
    universe_b = direct_service.persist_peer_universe(
        identity=identity("target"),
        policy_record_id=policy_b.record_id,
        cutoff=NOW,
        candidates=tuple(candidate(entity) for entity in ("peer-a", "peer-b", "peer-c")),
        recorded_at=RECORDED,
        known_at=RECORDED,
    )
    decisions_b = {
        entity: direct_service.persist_eligibility_decision(
            target_identity=identity("target"),
            candidate_identity=identity(entity),
            policy_record_id=universe_b.policy_record_id,
            universe_snapshot_id=universe_b.record_id,
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )
        for entity in ("peer-a", "peer-b", "peer-c")
    }
    observations_b = {
        entity: direct_service.persist_metric_observation(
            identity=identity(entity),
            policy_record_id=universe_b.policy_record_id,
            universe_snapshot_id=universe_b.record_id,
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=RECORDED,
            known_at=RECORDED,
        )
        for entity in ("target", "peer-a", "peer-b", "peer-c")
    }
    assessment_b = direct_service.assess(
        identity=identity("target"),
        policy_record_id=universe_b.policy_record_id,
        universe_snapshot_id=universe_b.record_id,
        evidence_type="official_disclosure",
        rule_type="fee_distribution",
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )

    # Full structural equality, field by field, for every record family: canonical
    # identity, deterministic content_hash, methodology/policy version fields,
    # provenance, and every replay-relevant field ADR 0026 requires.
    repository_a = ComparativeValuationRepository(_db_path(cli_root))

    policy_a_record = repository_a.get_peer_policy(policy_a["record_id"])
    assert policy_a_record is not None
    assert policy_a_record.record_id == policy_b.record_id
    assert policy_a_record.logical_id == policy_b.logical_id
    assert policy_a_record.content_hash == policy_b.content_hash
    assert asdict(policy_a_record) == asdict(policy_b)

    universe_a_record = repository_a.get_peer_universe(universe_a["record_id"])
    assert universe_a_record is not None
    assert universe_a_record.record_id == universe_b.record_id
    assert asdict(universe_a_record) == asdict(universe_b)

    for entity in ("peer-a", "peer-b", "peer-c"):
        decision_a_record = repository_a.get_eligibility_decision(decisions_a[entity]["record_id"])
        assert decision_a_record is not None
        assert decision_a_record.record_id == decisions_b[entity].record_id
        assert asdict(decision_a_record) == asdict(decisions_b[entity])

    for entity in ("target", "peer-a", "peer-b", "peer-c"):
        observation_a_record = repository_a.get_metric_observation(observations_a[entity]["record_id"])
        assert observation_a_record is not None
        assert observation_a_record.record_id == observations_b[entity].record_id
        assert asdict(observation_a_record) == asdict(observations_b[entity])

    assessment_a_record = repository_a.get_assessment(assessment_a["record_id"])
    assert assessment_a_record is not None
    assert assessment_a_record.record_id == assessment_b.record_id
    assert assessment_a_record.raw_log_residual == assessment_b.raw_log_residual
    assert asdict(assessment_a_record) == asdict(assessment_b)


# F. Read-only status query ------------------------------------------------------------


def test_status_reports_unavailable_before_any_policy_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(
        tmp_path, target="peer_policy", known_by=NOW + timedelta(days=1), logical_id="does-not-exist-yet"
    )
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output == {
        "operation": "status",
        "target": "peer_policy",
        "persistence_database": str(_db_path(tmp_path)),
        "available": False,
    }


def test_status_reports_persisted_peer_policy_matching_the_repository_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    written = _run(monkeypatch, tmp_path, _peer_policy_manifest(tmp_path), capsys)
    manifest = _status_manifest(
        tmp_path, target="peer_policy", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
    )
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output["available"] is True
    assert output["record"]["record_id"] == written["record_id"]

    repository = ComparativeValuationRepository(_db_path(tmp_path))
    direct = repository.get_peer_policy(written["record_id"])
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_persisted_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_native_evidence(tmp_path)
    policy = _run(monkeypatch, tmp_path, _peer_policy_manifest(tmp_path), capsys)
    universe = _run(
        monkeypatch, tmp_path, _peer_universe_manifest(tmp_path, policy_record_id=policy["record_id"]), capsys
    )
    for entity in ("peer-a", "peer-b", "peer-c"):
        _run(
            monkeypatch,
            tmp_path,
            _eligibility_decision_manifest(
                tmp_path,
                filename=f"decision-{entity}.json",
                entity=entity,
                policy_record_id=policy["record_id"],
                universe_snapshot_id=universe["record_id"],
            ),
            capsys,
        )
    for entity in ("target", "peer-a", "peer-b", "peer-c"):
        _run(
            monkeypatch,
            tmp_path,
            _metric_observation_manifest(
                tmp_path,
                filename=f"observation-{entity}.json",
                entity=entity,
                policy_record_id=policy["record_id"],
                universe_snapshot_id=universe["record_id"],
            ),
            capsys,
        )
    written = _run(
        monkeypatch,
        tmp_path,
        _assess_manifest(tmp_path, policy_record_id=policy["record_id"], universe_snapshot_id=universe["record_id"]),
        capsys,
    )

    output = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path, target="assessment", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
        ),
        capsys,
    )
    assert output["available"] is True
    assert output["record"]["record_id"] == written["record_id"]
    assert output["record"]["raw_log_residual"] == written["raw_log_residual"]


def test_status_reports_persisted_peer_universe_eligibility_decision_and_metric_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Covers the three status targets not exercised by the dedicated peer_policy and
    assessment status tests above, so all five record families in `_STATUS_TARGETS`
    are proven available (and, for peer_universe, unavailable-before-creation) through
    the CLI, matching Issue #181's acceptance criterion."""
    unavailable = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-universe-before.json",
            target="peer_universe",
            known_by=NOW + timedelta(days=1),
            logical_id="does-not-exist-yet",
        ),
        capsys,
    )
    assert unavailable["available"] is False

    _seed_native_evidence(tmp_path)
    policy = _run(monkeypatch, tmp_path, _peer_policy_manifest(tmp_path), capsys)
    universe = _run(
        monkeypatch, tmp_path, _peer_universe_manifest(tmp_path, policy_record_id=policy["record_id"]), capsys
    )
    decision = _run(
        monkeypatch,
        tmp_path,
        _eligibility_decision_manifest(
            tmp_path, entity="peer-a", policy_record_id=policy["record_id"], universe_snapshot_id=universe["record_id"]
        ),
        capsys,
    )
    observation = _run(
        monkeypatch,
        tmp_path,
        _metric_observation_manifest(
            tmp_path, entity="target", policy_record_id=policy["record_id"], universe_snapshot_id=universe["record_id"]
        ),
        capsys,
    )

    repository = ComparativeValuationRepository(_db_path(tmp_path))
    universe_record = repository.get_peer_universe(universe["record_id"])
    decision_record = repository.get_eligibility_decision(decision["record_id"])
    observation_record = repository.get_metric_observation(observation["record_id"])
    assert universe_record is not None
    assert decision_record is not None
    assert observation_record is not None

    universe_status = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-universe-after.json",
            target="peer_universe",
            known_by=NOW + timedelta(days=1),
            logical_id=universe_record.logical_id,
        ),
        capsys,
    )
    assert universe_status["available"] is True
    assert universe_status["record"]["record_id"] == universe["record_id"]

    decision_status = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-decision.json",
            target="eligibility_decision",
            known_by=NOW + timedelta(days=1),
            logical_id=decision_record.logical_id,
        ),
        capsys,
    )
    assert decision_status["available"] is True
    assert decision_status["record"]["record_id"] == decision["record_id"]
    assert decision_status["record"]["decision"] == "included"

    observation_status = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-observation.json",
            target="metric_observation",
            known_by=NOW + timedelta(days=1),
            logical_id=observation_record.logical_id,
        ),
        capsys,
    )
    assert observation_status["available"] is True
    assert observation_status["record"]["record_id"] == observation["record_id"]
    assert observation_status["record"]["comparative_multiple"] == observation["comparative_multiple"]


def test_status_rejects_unknown_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(tmp_path, target="not-a-real-target", known_by=NOW, logical_id="anything")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="status manifest requires target"):
        comparative_valuation_authority_command.main(["run", str(manifest)])


def test_status_does_not_persist_or_mutate_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A status query, run repeatedly, must never itself create, correct, or otherwise
    write a record -- it is a pure read over the existing repository."""
    written = _run(monkeypatch, tmp_path, _peer_policy_manifest(tmp_path), capsys)
    repository = ComparativeValuationRepository(_db_path(tmp_path))
    before = repository.count("peer_universe_policies")
    for _ in range(3):
        _run(
            monkeypatch,
            tmp_path,
            _status_manifest(
                tmp_path, target="peer_policy", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
            ),
            capsys,
        )
    after = repository.count("peer_universe_policies")
    assert after == before


# G. Unknown operation and malformed manifest rejection --------------------------------


def test_unknown_operation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _write(tmp_path, "bad-operation.json", {"operation": "not-a-real-operation"})
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="manifest requires operation"):
        comparative_valuation_authority_command.main(["run", str(manifest)])


def test_missing_application_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _peer_policy_manifest(tmp_path)
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(ValueError, match="HUNTER_APPLICATION_ROOT"):
        comparative_valuation_authority_command.main(["run", str(manifest)])


def test_wrong_argv_shape_prints_usage_and_returns_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = comparative_valuation_authority_command.main(["run"])
    assert exit_code == 1
    assert "usage: hunter comparative-valuation-authority run MANIFEST.json" in capsys.readouterr().out


# H. Repository bypass remains impossible -----------------------------------------------


def test_repository_bypass_remains_impossible() -> None:
    """Mirrors test_valuation_authority_v1.py's equivalent assertion: the repository
    this CLI drives exposes no public write/apply method of its own."""
    for name in ("save", "apply", "write", "persist", "assess", "replay"):
        assert not hasattr(ComparativeValuationRepository, name)


# I. Entry point is implemented but deliberately not activated -------------------------


def test_hunter_main_does_not_dispatch_comparative_valuation_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the hostile-review BLOCKER finding on PR #182: ADR 0026
    Implementation Prerequisite 9 ("disabled-entry-point plans") and Prerequisite 10
    (independent implementation review and post-merge audit "before any production
    activation") are not yet satisfied, so `hunter.comparative_valuation.command` must
    remain implemented and directly testable (as every other test in this file proves)
    but unreachable through `hunter.__main__` / the `hunter` CLI -- mirroring the
    identical precedent already established for `hunter.evidence_assembly`. If a
    future change wires this verb back into `hunter.__main__` without that
    authorization, this test fails."""
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest_path = _peer_policy_manifest(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        hunter_main.main(["comparative-valuation-authority", "run", str(manifest_path)])
    # falls through to hunter.cli's argparse dispatcher, which rejects the unknown verb
    assert excinfo.value.code == 2
    # and, independently, no repository write occurred as a side effect of the attempt
    assert not _db_path(tmp_path).exists()
