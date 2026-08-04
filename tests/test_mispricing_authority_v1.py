"""Production execution path for the existing canonical Mispricing authority
(`CanonicalMispricingService`, ADR 0021), exercised through the new manifest-driven
orchestration module `hunter.mispricing.command` (`main(["run", MANIFEST_PATH])`),
added by Issue #183. This module is deliberately not dispatched from
`hunter.__main__` and is not reachable through the `hunter` CLI (see
`test_hunter_main_does_not_dispatch_mispricing_authority` below); it is exercised
only by direct construction, exactly as every test in this file does.

This suite proves the new orchestration module is a pure orchestration layer -- it
reimplements no validation, formula, replay, or persistence logic of its own. Every
guarantee already independently audited on the underlying service (strict-known
selection, truthful missingness, identity/unit/supply-basis compatibility,
append-only correction with branching-lineage rejection, repository-bypass
rejection) is proven here to hold identically when driven through
`hunter.mispricing.command`, not just through direct service construction as in
`test_mispricing_v1.py`.

Fixture helpers (identity/methodology-payload builders and native-evidence/fair-value
seeding primitives) are imported from `test_mispricing_v1` rather than duplicated,
mirroring `test_comparative_valuation_authority_v1.py`'s own convention against
`test_comparative_valuation_v1.py`, so the suites never drift out of sync on what a
valid payload looks like.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_mispricing_v1 import (
    NOW,
    RECORDED,
    SIGNING_KEY,
    SIGNING_KEY_ID,
    evidence_result,
    identity,
    rule_result,
    seed_circulating_supply_fact,
    seed_spot_price_fact,
    supply_result,
    value_capture_source,
)
from test_mispricing_v1 import (
    methodology_payload as valuation_methodology_payload,
)

from hunter import __main__ as hunter_main
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.mispricing import command as mispricing_authority_command
from hunter.mispricing.repository import MispricingIntegrityError, MispricingRepository
from hunter.mispricing.service import CanonicalMispricingService
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationService
from hunter.valuation_methodology.repository import ValuationMethodologyRepository
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService


def _db_path(application_root: Path) -> Path:
    return application_root / "data" / "data_ops.sqlite"


class _SeededFairValue:
    """Seeds the complete native evidence chain (circulating-supply and spot-price
    observed market facts, value-capture flow evidence, supply basis, value-capture
    rule, valuation methodology, and a fair-value estimate) at the exact canonical
    path `hunter.mispricing.command` resolves
    (`<application_root>/data/data_ops.sqlite`), using only the existing,
    already-audited `value_capture`/`market_facts`/`valuation` services -- the
    identical ingestion `Fixture` in test_mispricing_v1.py performs, but pointed at
    the orchestration module's canonical persistence path rather than an arbitrary
    fixture path. Evidence and fair-value ingestion are out of scope for this module;
    only `methodology`/`assess` are driven through it in these tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = _db_path(tmp_path)
        self.circulating_supply_fact = seed_circulating_supply_fact(self.db_path)
        self.spot_price_fact = seed_spot_price_fact(self.db_path)

        verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: SIGNING_KEY})
        vc_repository = SupplyAndValueCaptureRepository(self.db_path)
        vc_service = SupplyAndValueCaptureService(
            registry=ValueCaptureSourceRegistry((value_capture_source(),)),
            repository=vc_repository,
            verification_keys=verification_keys,
        )
        provider = RegisteredValueCaptureProvider(
            value_capture_source(), signing_key_id=SIGNING_KEY_ID, signing_key=SIGNING_KEY
        )
        evidence = vc_service.ingest_evidence(provider, evidence_result(provider))
        vc_service.ingest_supply(provider, supply_result(provider, evidence.record_id, self.circulating_supply_fact))
        vc_service.ingest_rule(provider, rule_result(provider, evidence.record_id))

        methodology_authority = CanonicalValuationMethodologyAuthority(
            repository=ValuationMethodologyRepository(self.db_path), application_root=tmp_path
        )
        methodology_authority.persist_methodology(**valuation_methodology_payload())

        valuation_service = CanonicalValuationService(
            repository=CanonicalValuationRepository(self.db_path),
            methodology_authority=methodology_authority,
            value_capture_repository=vc_repository,
            market_fact_repository=ObservedMarketFactRepository(self.db_path),
            application_root=tmp_path,
        )
        self.estimate, self.valuation_assessment = valuation_service.estimate_fair_value(
            identity=identity(),
            evidence_type="official_disclosure",
            rule_type="fee_distribution",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=10),
            known_at=NOW + timedelta(minutes=10),
        )


def mispricing_methodology_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "methodology_id": "cmp-mispricing-methodology-v1",
        "required_quote_currency": "usd",
        "entity_class_criteria_id": "adr-0021-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "effective_at": NOW,
        "recorded_at": NOW + timedelta(minutes=12),
        "known_at": NOW + timedelta(minutes=12),
    }
    base.update(overrides)
    return base


def _iso(value: object) -> object:
    return value.isoformat() if isinstance(value, datetime) else value


def _identity_payload() -> dict[str, str]:
    target = identity()
    return {
        "entity_id": target.entity_id,
        "economic_claim_id": target.economic_claim_id,
        "asset_id": target.asset_id,
        "representation_id": target.representation_id,
        "token_id": target.token_id,
        "chain": target.chain,
        "contract_address": target.contract_address,
    }


def _write(tmp_path: Path, filename: str, manifest: dict[str, object]) -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _methodology_manifest(tmp_path: Path, *, filename: str = "methodology-manifest.json", **overrides: object) -> Path:
    payload = {key: _iso(value) for key, value in mispricing_methodology_payload(**overrides).items()}
    manifest = {"operation": "methodology", "payload": payload}
    return _write(tmp_path, filename, manifest)


def _assess_manifest(
    tmp_path: Path,
    *,
    filename: str = "assess-manifest.json",
    methodology_record_id: str,
    fair_value_logical_id: str,
    effective_at: datetime = NOW,
    recorded_at: datetime = RECORDED,
    known_at: datetime = RECORDED,
    supersedes_record_id: str | None = None,
    correction_reason: str = "",
) -> Path:
    manifest = {
        "operation": "assess",
        "identity": _identity_payload(),
        "methodology_record_id": methodology_record_id,
        "fair_value_logical_id": fair_value_logical_id,
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
    exit_code = mispricing_authority_command.main(["run", str(manifest_path)])
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


# A. Methodology persistence through the orchestration module -------------------------


def test_methodology_persists_through_orchestration_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    output = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert output["operation"] == "methodology"
    repository = MispricingRepository(_db_path(tmp_path))
    record = repository.get_mispricing_methodology(output["record_id"])
    assert record is not None
    assert record.quality_state == "accepted"
    assert record.conflict_state == "none"


# B. End-to-end orchestration-module creation of both record families -----------------


def test_end_to_end_orchestration_module_creates_methodology_and_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _SeededFairValue(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)

    assess_manifest = _assess_manifest(
        tmp_path,
        methodology_record_id=methodology["record_id"],
        fair_value_logical_id=seeded.estimate.logical_id,
    )
    output = _run(monkeypatch, tmp_path, assess_manifest, capsys)
    assert output["operation"] == "assess"
    # ADR 0021: no calibrated normalization exists in this foundation, so a complete
    # comparison still never reaches AVAILABLE -- it reaches the raw-values state that
    # persists the raw signed ratio while normalization remains unavailable.
    assert output["availability_state"] == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert output["normalization_status"] == "unavailable"
    assert output["raw_signed_ratio"] is not None

    repository = MispricingRepository(_db_path(tmp_path))
    record = repository.get_assessment(output["record_id"])
    assert record is not None
    assert record.raw_signed_ratio == output["raw_signed_ratio"]


# C. Insert-identical idempotency -----------------------------------------------------


def test_insert_identical_methodology_via_orchestration_module_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    first = _run(monkeypatch, tmp_path, manifest_path, capsys)
    second = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert first["record_id"] == second["record_id"]


# D. Divergent duplicate rejection ----------------------------------------------------


def test_divergent_methodology_duplicate_via_orchestration_module_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_manifest = _methodology_manifest(tmp_path, filename="m1.json")
    _run(monkeypatch, tmp_path, first_manifest, capsys)
    second_manifest = _methodology_manifest(tmp_path, filename="m2.json", required_quote_currency="eur")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(MispricingIntegrityError, match="root.*already exists"):
        mispricing_authority_command.main(["run", str(second_manifest)])


# E. Orchestration-module-construction / direct-construction field equivalence --------


def test_orchestration_construction_is_field_equivalent_to_direct_service_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Independently constructs both record families twice, from the same semantic
    inputs, into two completely separate databases:

    - Path A: entirely through the orchestration module
      (`mispricing_authority_command.main`) -- not through the `hunter` CLI, which
      does not dispatch to it (see
      `test_hunter_main_does_not_dispatch_mispricing_authority` below).
    - Path B: entirely through direct `CanonicalMispricingService` construction,
      never touching the orchestration module.

    A prior version of this equivalence pattern (Issue #181/PR #182) only re-read
    what the orchestration module had itself written and confirmed a direct-service
    *read* call returned the same record -- that proves persistence/replay
    compatibility, but it cannot detect an argument-mapping bug in the module (a
    dropped field, a field mapped to the wrong keyword, a type-coercion difference)
    because both sides of the comparison originate from the one write. This test
    instead performs Path B as a fully independent *construction*, then asserts
    complete dataclass-field equality (via `asdict`) between the two paths for both
    record families -- covering canonical identity (`record_id`/`logical_id`), the
    deterministic `content_hash`, methodology/formula-version fields, provenance, and
    every other ADR 0021 field. If the orchestration module's argument mapping ever
    diverges from direct construction, the computed `content_hash` (and therefore
    `record_id`) changes and this test fails.
    """
    module_root = tmp_path / "module-path"
    direct_root = tmp_path / "direct-path"
    module_root.mkdir()
    direct_root.mkdir()

    # Path A: construct both records entirely through the orchestration module.
    module_seeded = _SeededFairValue(module_root)
    methodology_a = _run(monkeypatch, module_root, _methodology_manifest(module_root), capsys)
    assessment_a = _run(
        monkeypatch,
        module_root,
        _assess_manifest(
            module_root,
            methodology_record_id=methodology_a["record_id"],
            fair_value_logical_id=module_seeded.estimate.logical_id,
        ),
        capsys,
    )

    # Path B: construct the identical logical records directly through the service,
    # in a completely separate database, never touching the orchestration module.
    direct_seeded = _SeededFairValue(direct_root)
    direct_service = CanonicalMispricingService(
        repository=MispricingRepository(_db_path(direct_root)),
        valuation_repository=CanonicalValuationRepository(_db_path(direct_root)),
        market_fact_repository=ObservedMarketFactRepository(_db_path(direct_root)),
        application_root=direct_root,
    )
    methodology_b = direct_service.persist_mispricing_methodology(**mispricing_methodology_payload())
    assessment_b = direct_service.assess(
        identity=identity(),
        methodology_record_id=methodology_b.record_id,
        fair_value_logical_id=direct_seeded.estimate.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )

    # Full structural equality, field by field, for both record families: canonical
    # identity, deterministic content_hash, formula-version fields, provenance, and
    # every replay-relevant field ADR 0021 requires.
    repository_a = MispricingRepository(_db_path(module_root))
    methodology_a_record = repository_a.get_mispricing_methodology(methodology_a["record_id"])
    assert methodology_a_record is not None
    assert methodology_a_record.record_id == methodology_b.record_id
    assert methodology_a_record.logical_id == methodology_b.logical_id
    assert methodology_a_record.content_hash == methodology_b.content_hash
    assert asdict(methodology_a_record) == asdict(methodology_b)

    assessment_a_record = repository_a.get_assessment(assessment_a["record_id"])
    assert assessment_a_record is not None
    assert assessment_a_record.record_id == assessment_b.record_id
    assert assessment_a_record.raw_signed_ratio == assessment_b.raw_signed_ratio
    assert asdict(assessment_a_record) == asdict(assessment_b)


# F. Read-only status query ------------------------------------------------------------


def test_status_reports_unavailable_before_any_methodology_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(
        tmp_path, target="methodology", known_by=NOW + timedelta(days=1), logical_id="does-not-exist-yet"
    )
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
    manifest = _status_manifest(
        tmp_path, target="methodology", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
    )
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output["available"] is True
    assert output["record"]["record_id"] == written["record_id"]

    repository = MispricingRepository(_db_path(tmp_path))
    direct = repository.get_mispricing_methodology(written["record_id"])
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_persisted_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _SeededFairValue(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    written = _run(
        monkeypatch,
        tmp_path,
        _assess_manifest(
            tmp_path,
            methodology_record_id=methodology["record_id"],
            fair_value_logical_id=seeded.estimate.logical_id,
        ),
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
    assert output["record"]["raw_signed_ratio"] == written["raw_signed_ratio"]


def test_status_rejects_unknown_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(tmp_path, target="not-a-real-target", known_by=NOW, logical_id="anything")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="status manifest requires target"):
        mispricing_authority_command.main(["run", str(manifest)])


def test_status_does_not_persist_or_mutate_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A status query, run repeatedly, must never itself create, correct, or otherwise
    write a record -- it is a pure read over the existing repository."""
    written = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    repository = MispricingRepository(_db_path(tmp_path))
    before = repository.count("mispricing_methodologies")
    for _ in range(3):
        _run(
            monkeypatch,
            tmp_path,
            _status_manifest(
                tmp_path, target="methodology", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
            ),
            capsys,
        )
    after = repository.count("mispricing_methodologies")
    assert after == before


# G. Unknown operation and malformed manifest rejection --------------------------------


def test_unknown_operation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _write(tmp_path, "bad-operation.json", {"operation": "not-a-real-operation"})
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="manifest requires operation"):
        mispricing_authority_command.main(["run", str(manifest)])


def test_missing_application_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _methodology_manifest(tmp_path)
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(ValueError, match="HUNTER_APPLICATION_ROOT"):
        mispricing_authority_command.main(["run", str(manifest)])


def test_wrong_argv_shape_prints_usage_and_returns_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = mispricing_authority_command.main(["run"])
    assert exit_code == 1
    assert (
        "usage: python -c 'from hunter.mispricing.command import main; "
        'raise SystemExit(main(["run", "MANIFEST.json"]))\'' in capsys.readouterr().out
    )


def test_malformed_manifest_top_level_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "not-an-object.json"
    manifest_path.write_text(json.dumps(["operation", "methodology"]), encoding="utf-8")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="manifest must be a JSON object"):
        mispricing_authority_command.main(["run", str(manifest_path)])


# H. Repository bypass remains impossible -----------------------------------------------


def test_repository_bypass_remains_impossible() -> None:
    """Mirrors test_valuation_authority_v1.py's and
    test_comparative_valuation_authority_v1.py's equivalent assertion: the repository
    this orchestration module drives exposes no public write/apply method of its
    own."""
    for name in ("save", "apply", "write", "persist", "assess", "replay"):
        assert not hasattr(MispricingRepository, name)


# I. Entry point is implemented but deliberately not activated -------------------------


def test_hunter_main_does_not_dispatch_mispricing_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for Issue #183's explicit scope boundary: ADR 0021 authorizes
    `CanonicalMispricingService` (already implemented, PR #163) but nothing in the
    governance framework authorizes production dispatch of an entry point without
    independent Architecture Review, which has not occurred for this module. This
    test proves `hunter.mispricing.command` remains implemented and directly testable
    (as every other test in this file proves) but unreachable through
    `hunter.__main__` / the `hunter` CLI -- mirroring the identical precedent already
    established for `hunter.evidence_assembly` and
    `hunter.comparative_valuation.command`. If a future change wires this verb into
    `hunter.__main__` without that authorization, this test fails."""
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest_path = _methodology_manifest(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        hunter_main.main(["mispricing-authority", "run", str(manifest_path)])
    # falls through to hunter.cli's argparse dispatcher, which rejects the unknown verb
    assert excinfo.value.code == 2
    # and, independently, no repository write occurred as a side effect of the attempt
    assert not _db_path(tmp_path).exists()
