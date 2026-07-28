"""Production execution path for the two existing canonical valuation authorities
(`CanonicalValuationMethodologyAuthority`, `CanonicalValuationService`), exercised
through the new `hunter valuation-authority run MANIFEST.json` CLI
(`hunter.valuation_authority.command`).

This suite proves the new entry point is a pure orchestration layer -- it reimplements
no validation, formula, replay, or persistence logic of its own. Every guarantee
already independently audited on the two underlying services (strict-known selection,
truthful missingness, conflict rejection, horizon/accounting-period compatibility,
append-only correction with branching-lineage rejection, repository-bypass rejection,
leakage safety) is proven here to hold identically when driven through the CLI, not
just through direct construction as in `test_valuation_v1.py`.

Fixture helpers are imported from `test_valuation_v1` rather than duplicated, mirroring
`test_valuation_calibration_v1.py`'s own convention, so all three suites never drift out
of sync on what a valid evidence/supply/rule/methodology payload looks like.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_valuation_v1 import (
    NOW,
    SIGNING_KEY,
    SIGNING_KEY_ID,
    evidence_result,
    identity,
    methodology_payload,
    rule_result,
    seed_observed_market_fact,
    source,
    supply_result,
)

from hunter import __main__ as hunter_main
from hunter.valuation.repository import CanonicalValuationIntegrityError, CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationAuthorityError
from hunter.valuation_authority import command as valuation_authority_command
from hunter.valuation_methodology.repository import ValuationMethodologyIntegrityError, ValuationMethodologyRepository
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService


def _db_path(application_root: Path) -> Path:
    return application_root / "data" / "data_ops.sqlite"


class InputFixture:
    """Ingests evidence/rule/supply/market-fact inputs at exactly the canonical path
    `hunter valuation-authority` resolves (`<application_root>/data/data_ops.sqlite`),
    using only the existing, already-audited `value_capture`/`market_facts` services --
    the identical ingestion `hunter valuation-evidence acquire` performs. Does not touch
    methodology or estimate production; those go through the new CLI in each test."""

    def __init__(self, application_root: Path) -> None:
        self.application_root = application_root
        self.db_path = _db_path(application_root)
        self.market_fact = seed_observed_market_fact(self.db_path)
        verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
        self.vc_repository = SupplyAndValueCaptureRepository(self.db_path)
        self.vc_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((source(),)),
            repository=self.vc_repository,
            verification_keys=verification_keys,
        )
        self.provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)
        self.evidence = self.vc_service.ingest_evidence(self.provider, evidence_result(self.provider))
        self.supply = self.vc_service.ingest_supply(
            self.provider, supply_result(self.provider, self.evidence.record_id, self.market_fact)
        )
        self.rule = self.vc_service.ingest_rule(self.provider, rule_result(self.provider, self.evidence.record_id))


def _iso(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


def _methodology_manifest(tmp_path: Path, *, filename: str = "methodology-manifest.json", **overrides: object) -> Path:
    payload = {key: _iso(value) for key, value in methodology_payload(**overrides).items()}
    manifest = {"operation": "methodology", "payload": payload}
    path = tmp_path / filename
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _estimate_manifest(
    tmp_path: Path,
    *,
    filename: str = "estimate-manifest.json",
    effective_at: datetime = NOW,
    recorded_at: datetime | None = None,
    known_at: datetime | None = None,
    evidence_type: str = "official_disclosure",
    rule_type: str = "fee_distribution",
    supersedes_estimate_record_id: str | None = None,
    supersedes_assessment_record_id: str | None = None,
    correction_reason: str = "",
) -> Path:
    recorded_at = recorded_at if recorded_at is not None else NOW + timedelta(minutes=10)
    known_at = known_at if known_at is not None else NOW + timedelta(minutes=10)
    target = identity()
    manifest = {
        "operation": "estimate",
        "identity": {
            "entity_id": target.entity_id,
            "economic_claim_id": target.economic_claim_id,
            "asset_id": target.asset_id,
            "representation_id": target.representation_id,
            "token_id": target.token_id,
            "chain": target.chain,
            "contract_address": target.contract_address,
        },
        "evidence_type": evidence_type,
        "rule_type": rule_type,
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
        "supersedes_estimate_record_id": supersedes_estimate_record_id,
        "supersedes_assessment_record_id": supersedes_assessment_record_id,
        "correction_reason": correction_reason,
    }
    path = tmp_path / filename
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _run(
    monkeypatch: pytest.MonkeyPatch, application_root: Path, manifest_path: Path, capsys: pytest.CaptureFixture[str]
) -> dict:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(application_root))
    exit_code = valuation_authority_command.main(["run", str(manifest_path)])
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


# A. Methodology persistence through the production entry point ----------------------


def test_methodology_persists_through_production_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    output = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert output["operation"] == "methodology"
    repository = ValuationMethodologyRepository(_db_path(tmp_path))
    record = repository.get(output["record_id"])
    assert record is not None
    assert record.quality_state == "accepted"
    assert record.conflict_state == "none"


# B. End-to-end production-entry-point creation of all three record families ---------


def test_end_to_end_production_entry_point_creates_methodology_estimate_and_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    methodology_manifest = _methodology_manifest(tmp_path)
    _run(monkeypatch, tmp_path, methodology_manifest, capsys)

    estimate_manifest = _estimate_manifest(tmp_path)
    output = _run(monkeypatch, tmp_path, estimate_manifest, capsys)
    assert output["operation"] == "estimate"

    valuation_repository = CanonicalValuationRepository(_db_path(tmp_path))
    estimate = valuation_repository.get_fair_value_estimate(output["fair_value_estimate_record_id"])
    assessment = valuation_repository.get_valuation_assessment(output["valuation_assessment_record_id"])
    assert estimate is not None
    assert assessment is not None
    assert assessment.fair_value_estimate_record_id == estimate.record_id
    assert estimate.quality_state == "accepted"
    assert assessment.quality_state == "accepted"


# C. Insert-identical idempotency -----------------------------------------------------


def test_insert_identical_methodology_via_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    first = _run(monkeypatch, tmp_path, manifest_path, capsys)
    second = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert first["record_id"] == second["record_id"]


def test_insert_identical_estimate_via_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    estimate_manifest = _estimate_manifest(tmp_path)
    first = _run(monkeypatch, tmp_path, estimate_manifest, capsys)
    second = _run(monkeypatch, tmp_path, estimate_manifest, capsys)
    assert first["fair_value_estimate_record_id"] == second["fair_value_estimate_record_id"]
    assert first["valuation_assessment_record_id"] == second["valuation_assessment_record_id"]


# D. Divergent duplicate rejection ----------------------------------------------------


def test_divergent_methodology_duplicate_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_manifest = _methodology_manifest(tmp_path, filename="m1.json")
    _run(monkeypatch, tmp_path, first_manifest, capsys)
    second_manifest = _methodology_manifest(tmp_path, filename="m2.json", currency="eur")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValuationMethodologyIntegrityError, match="root methodology record already exists"):
        valuation_authority_command.main(["run", str(second_manifest)])


def test_divergent_estimate_duplicate_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path), capsys)
    # A second, independent root estimate for the same identity/methodology (no
    # supersedes_*_record_id) is a divergent duplicate, not idempotent retry, because
    # recorded_at differs from the first call.
    divergent_manifest = _estimate_manifest(
        tmp_path,
        filename="estimate-divergent.json",
        recorded_at=NOW + timedelta(minutes=20),
        known_at=NOW + timedelta(minutes=20),
    )
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(CanonicalValuationIntegrityError, match="root record already exists"):
        valuation_authority_command.main(["run", str(divergent_manifest)])


# E. Strict-known input resolution / F. No latest-or-current fallback ----------------


def test_strict_known_resolution_selects_correct_version_not_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proves strict-known replay is a genuine cutoff-bounded lookup, not a "latest"
    shortcut: a correction produced through the CLI is invisible to a read requested at
    a `known_by` strictly before the correction's own `known_at`, and visible to a read
    requested at a `known_by` at/after it -- exercised through the same
    `CanonicalValuationService`/`CanonicalValuationRepository` read primitives
    `hunter.historical.valuation_calibration` already relies on."""
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    original = _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path, filename="estimate-v1.json"), capsys)

    correction = _run(
        monkeypatch,
        tmp_path,
        _estimate_manifest(
            tmp_path,
            filename="estimate-correction.json",
            recorded_at=NOW + timedelta(minutes=30),
            known_at=NOW + timedelta(minutes=30),
            supersedes_estimate_record_id=original["fair_value_estimate_record_id"],
            supersedes_assessment_record_id=original["valuation_assessment_record_id"],
            correction_reason="strict-known resolution regression coverage",
        ),
        capsys,
    )
    assert correction["fair_value_estimate_record_id"] != original["fair_value_estimate_record_id"]

    repository = CanonicalValuationRepository(_db_path(tmp_path))
    original_record = repository.get_fair_value_estimate(original["fair_value_estimate_record_id"])
    assert original_record is not None

    before_correction = repository.strict_known_fair_value_estimate(
        effective_as_of=NOW, known_by=NOW + timedelta(minutes=20), logical_id=original_record.logical_id
    )
    assert before_correction is not None
    assert before_correction.record_id == original["fair_value_estimate_record_id"]

    at_or_after_correction = repository.strict_known_fair_value_estimate(
        effective_as_of=NOW, known_by=NOW + timedelta(minutes=30), logical_id=original_record.logical_id
    )
    assert at_or_after_correction is not None
    assert at_or_after_correction.record_id == correction["fair_value_estimate_record_id"]


# G. Missing-record rejection ----------------------------------------------------------


def test_missing_evidence_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    # No strict-known evidence exists for this evidence_type -- the ingested fixture
    # evidence is "official_disclosure", not "audited_financial_disclosure".
    with pytest.raises(CanonicalValuationAuthorityError, match="FundamentalEvidenceRecord"):
        valuation_authority_command.main(
            ["run", str(_estimate_manifest(tmp_path, evidence_type="audited_financial_disclosure"))]
        )


# H. Ambiguous/conflicted-record rejection ---------------------------------------------


def test_conflicted_supply_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = _db_path(tmp_path)
    market_fact = seed_observed_market_fact(db_path)
    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vc_service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((source(),)),
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)
    evidence = vc_service.ingest_evidence(provider, evidence_result(provider))
    conflicted_supply = vc_service.ingest_supply(
        provider, supply_result(provider, evidence.record_id, market_fact, conflict_state="open")
    )
    assert conflicted_supply.conflict_state == "open"
    vc_service.ingest_rule(provider, rule_result(provider, evidence.record_id))

    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(CanonicalValuationAuthorityError, match="SupplyBasisSnapshot"):
        valuation_authority_command.main(["run", str(_estimate_manifest(tmp_path))])


# I. Horizon/accounting-period incompatibility rejection -------------------------------


def test_accounting_period_incompatible_with_horizon_via_cli_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = _db_path(tmp_path)
    market_fact = seed_observed_market_fact(db_path)
    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    vc_service = SupplyAndValueCaptureService(
        registry=ValueCaptureSourceRegistry((source(),)),
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    provider = RegisteredValueCaptureProvider(source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY)
    evidence = vc_service.ingest_evidence(
        provider,
        evidence_result(provider, accounting_period_start=NOW - timedelta(days=30), accounting_period_end=NOW),
    )
    vc_service.ingest_supply(provider, supply_result(provider, evidence.record_id, market_fact))
    vc_service.ingest_rule(provider, rule_result(provider, evidence.record_id))

    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(CanonicalValuationAuthorityError, match="horizon"):
        valuation_authority_command.main(["run", str(_estimate_manifest(tmp_path))])


# J. Future-known/leakage rejection -----------------------------------------------------


def test_future_known_correction_does_not_leak_into_earlier_replay_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    baseline = _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path, filename="estimate-baseline.json"), capsys)

    # A correction recorded/known far in the future must not affect a replay requested
    # at an earlier cutoff.
    fixture.vc_service.ingest_evidence(
        fixture.provider,
        evidence_result(
            fixture.provider,
            acquisition_id="evidence-future-correction",
            acquired_at=NOW + timedelta(days=400),
            amount="9999999",
            supersedes_record_id=fixture.evidence.record_id,
            correction_reason="far-future correction must not leak backward",
        ),
    )
    replay_at_original_cutoff = _run(
        monkeypatch, tmp_path, _estimate_manifest(tmp_path, filename="estimate-replay.json"), capsys
    )
    assert replay_at_original_cutoff["fair_value_estimate_record_id"] == baseline["fair_value_estimate_record_id"]


# K. Provenance and correction-lineage preservation --------------------------------------


def test_correction_lineage_and_provenance_preserved_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    original = _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path, filename="estimate-original.json"), capsys)

    correction_manifest = _estimate_manifest(
        tmp_path,
        filename="estimate-correction.json",
        recorded_at=NOW + timedelta(minutes=20),
        known_at=NOW + timedelta(minutes=20),
        supersedes_estimate_record_id=original["fair_value_estimate_record_id"],
        supersedes_assessment_record_id=original["valuation_assessment_record_id"],
        correction_reason="test correction via production entry point",
    )
    correction = _run(monkeypatch, tmp_path, correction_manifest, capsys)
    assert correction["fair_value_estimate_record_id"] != original["fair_value_estimate_record_id"]

    valuation_repository = CanonicalValuationRepository(_db_path(tmp_path))
    corrected_estimate = valuation_repository.get_fair_value_estimate(correction["fair_value_estimate_record_id"])
    assert corrected_estimate is not None
    assert corrected_estimate.supersedes_record_id == original["fair_value_estimate_record_id"]
    assert corrected_estimate.evidence_record_id == fixture.evidence.record_id
    assert corrected_estimate.rule_record_id == fixture.rule.record_id
    assert corrected_estimate.supply_record_id == fixture.supply.record_id

    # A branching second successor claiming the same predecessor must be rejected.
    branching_manifest = _estimate_manifest(
        tmp_path,
        filename="estimate-branch.json",
        recorded_at=NOW + timedelta(minutes=25),
        known_at=NOW + timedelta(minutes=25),
        supersedes_estimate_record_id=original["fair_value_estimate_record_id"],
        supersedes_assessment_record_id=original["valuation_assessment_record_id"],
        correction_reason="branching attempt must be rejected",
    )
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(CanonicalValuationIntegrityError, match="branching"):
        valuation_authority_command.main(["run", str(branching_manifest)])


# L. Repository bypass remains impossible -------------------------------------------------


def test_repository_bypass_remains_impossible() -> None:
    for method_name in ("save", "insert", "apply", "write", "persist"):
        assert not hasattr(CanonicalValuationRepository, method_name)
        assert not hasattr(ValuationMethodologyRepository, method_name)


# M. Read-only status query ----------------------------------------------------------


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
    path = tmp_path / filename
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_status_reports_unavailable_before_any_methodology_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(tmp_path, target="methodology", known_by=NOW)
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output == {
        "operation": "status",
        "target": "methodology",
        "persistence_database": str(_db_path(tmp_path)),
        "available": False,
    }


def test_status_reports_persisted_methodology_matching_the_repository_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    written = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    manifest = _status_manifest(tmp_path, target="methodology", known_by=NOW + timedelta(days=1))
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output["available"] is True
    assert output["record"]["record_id"] == written["record_id"]
    assert output["record"]["logical_id"] == written["logical_id"]

    repository = ValuationMethodologyRepository(_db_path(tmp_path))
    direct = repository.strict_known_methodology(effective_as_of=NOW, known_by=NOW + timedelta(days=1))
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_unavailable_for_estimate_and_assessment_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    for target in ("fair_value_estimate", "valuation_assessment"):
        manifest = _status_manifest(
            tmp_path,
            filename=f"status-{target}-before.json",
            target=target,
            known_by=NOW + timedelta(minutes=10),
            logical_id="does-not-exist-yet",
        )
        output = _run(monkeypatch, tmp_path, manifest, capsys)
        assert output["available"] is False


def test_status_query_selects_strict_known_version_not_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proves the `status` operation reuses the same strict-known cutoff discipline as
    Test E above (`strict_known_fair_value_estimate`), rather than always returning the
    newest record: queried at a `known_by` before the correction, it reports the
    original estimate; queried at/after the correction's `known_at`, it reports the
    correction."""
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    original = _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path, filename="estimate-v1.json"), capsys)
    correction = _run(
        monkeypatch,
        tmp_path,
        _estimate_manifest(
            tmp_path,
            filename="estimate-correction.json",
            recorded_at=NOW + timedelta(minutes=30),
            known_at=NOW + timedelta(minutes=30),
            supersedes_estimate_record_id=original["fair_value_estimate_record_id"],
            supersedes_assessment_record_id=original["valuation_assessment_record_id"],
            correction_reason="status strict-known regression coverage",
        ),
        capsys,
    )

    original_record = CanonicalValuationRepository(_db_path(tmp_path)).get_fair_value_estimate(
        original["fair_value_estimate_record_id"]
    )
    assert original_record is not None
    logical_id = original_record.logical_id

    before_correction = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-before.json",
            target="fair_value_estimate",
            known_by=NOW + timedelta(minutes=20),
            logical_id=logical_id,
        ),
        capsys,
    )
    assert before_correction["record"]["record_id"] == original["fair_value_estimate_record_id"]

    at_or_after_correction = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            filename="status-after.json",
            target="fair_value_estimate",
            known_by=NOW + timedelta(minutes=30),
            logical_id=logical_id,
        ),
        capsys,
    )
    assert at_or_after_correction["record"]["record_id"] == correction["fair_value_estimate_record_id"]


def test_status_reports_persisted_valuation_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    InputFixture(tmp_path)
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    written = _run(monkeypatch, tmp_path, _estimate_manifest(tmp_path), capsys)
    assessment_record = CanonicalValuationRepository(_db_path(tmp_path)).get_valuation_assessment(
        written["valuation_assessment_record_id"]
    )
    assert assessment_record is not None
    logical_id = assessment_record.logical_id

    output = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            target="valuation_assessment",
            known_by=NOW + timedelta(minutes=10),
            logical_id=logical_id,
        ),
        capsys,
    )
    assert output["available"] is True
    assert output["record"]["record_id"] == written["valuation_assessment_record_id"]
    assert output["record"]["fair_value_estimate_record_id"] == written["fair_value_estimate_record_id"]


def test_status_rejects_unknown_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(tmp_path, target="not-a-real-target", known_by=NOW)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="status manifest requires target"):
        valuation_authority_command.main(["run", str(manifest)])


def test_status_does_not_persist_or_mutate_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A status query, run repeatedly, must never itself create, correct, or otherwise
    write a record -- it is a pure read over the two existing repositories."""
    _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    repository = ValuationMethodologyRepository(_db_path(tmp_path))
    before = repository.count("valuation_methodology_snapshots")
    for _ in range(3):
        _run(
            monkeypatch,
            tmp_path,
            _status_manifest(tmp_path, target="methodology", known_by=NOW + timedelta(days=1)),
            capsys,
        )
    after = repository.count("valuation_methodology_snapshots")
    assert after == before


# Dispatch smoke test -------------------------------------------------------------------


def test_dispatch_from_hunter_main_reaches_valuation_authority_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest_path = _methodology_manifest(tmp_path)
    exit_code = hunter_main.main(["valuation-authority", "run", str(manifest_path)])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "methodology"
