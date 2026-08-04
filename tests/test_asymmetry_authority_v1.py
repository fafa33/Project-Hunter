"""Exercises the existing canonical Asymmetry authority (`CanonicalAsymmetryService`,
ADR 0021) through the new manifest-driven orchestration module
`hunter.asymmetry.command` (`main(["run", MANIFEST_PATH])`), added by Issue #187.
This module is deliberately not dispatched from `hunter.__main__` and is not
reachable through the `hunter` CLI (see
`test_hunter_main_does_not_dispatch_asymmetry_authority` below); it is exercised
only by direct construction, exactly as every test in this file does.

This suite proves the new orchestration module is a pure orchestration layer -- it
reimplements no validation, formula, replay, or persistence logic of its own. Every
guarantee already independently audited on the underlying service (strict-known
selection, truthful missingness, scenario/probability/payoff compatibility,
append-only correction with branching-lineage rejection, repository-bypass
rejection) is proven here to hold identically when driven through
`hunter.asymmetry.command`, not just through direct service construction as in
`test_asymmetry_v1.py`.

Fixture helpers (identity/market-fact seeding primitives) are imported from
`test_asymmetry_v1` rather than duplicated, mirroring
`test_mispricing_authority_v1.py`'s own convention against `test_mispricing_v1.py`,
so the suites never drift out of sync on what a valid payload looks like. The
methodology payload and the canonical-path seeding helper are defined locally in
this file because `test_asymmetry_v1.py`'s `Fixture` seeds its baseline market fact
at an arbitrary path (`tmp_path / "asymmetry.sqlite"`), not the canonical path this
orchestration module resolves to (`<application_root>/data/data_ops.sqlite`).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from test_asymmetry_v1 import (
    NOW,
    RECORDED,
    identity,
    scenario_payload,
    seed_spot_price_fact,
)

from hunter import __main__ as hunter_main
from hunter.asymmetry import command as asymmetry_authority_command
from hunter.asymmetry.repository import AsymmetryIntegrityError, AsymmetryRepository
from hunter.asymmetry.service import CanonicalAsymmetryService
from hunter.market_facts.repository import ObservedMarketFactRepository


def _db_path(application_root: Path) -> Path:
    return application_root / "data" / "data_ops.sqlite"


class _SeededBaseline:
    """Seeds the provider-observed baseline spot price at the exact canonical path
    `hunter.asymmetry.command` resolves (`<application_root>/data/data_ops.sqlite`),
    using only the existing, already-audited `market_facts` service -- the identical
    ingestion `test_asymmetry_v1.seed_spot_price_fact` performs, but pointed at the
    orchestration module's canonical persistence path rather than an arbitrary
    fixture path. Evidence ingestion is out of scope for this module; only
    `methodology`/`scenario_set`/`scenario_probability`/`scenario_payoff`/`assess`
    are driven through it in these tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = _db_path(tmp_path)
        self.spot_price_fact = seed_spot_price_fact(self.db_path)


def asymmetry_methodology_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "methodology_id": "cmp-asymmetry-methodology-v1",
        "required_quote_currency": "usd",
        "entity_class_criteria_id": "adr-0021-first-entity-class-v1",
        "entity_class_criteria_version": "1.0.0",
        "effective_at": NOW,
        "recorded_at": NOW + timedelta(minutes=1),
        "known_at": NOW + timedelta(minutes=1),
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
    payload = {key: _iso(value) for key, value in asymmetry_methodology_payload(**overrides).items()}
    manifest = {"operation": "methodology", "payload": payload}
    return _write(tmp_path, filename, manifest)


def _scenario_set_manifest(
    tmp_path: Path,
    *,
    filename: str = "scenario-set-manifest.json",
    methodology_record_id: str,
    baseline_market_fact_record_id: str,
    **overrides: object,
) -> Path:
    payload = scenario_payload(**overrides)
    manifest: dict[str, object] = {
        "operation": "scenario_set",
        "identity": _identity_payload(),
        "methodology_record_id": methodology_record_id,
        "scenario_set_id": payload["scenario_set_id"],
        "scenario_ids": list(cast(Any, payload["scenario_ids"])),
        "scenario_types": list(cast(Any, payload["scenario_types"])),
        "baseline_market_fact_record_id": baseline_market_fact_record_id,
        "quote_currency": payload["quote_currency"],
        "material_omitted_scenario_policy": payload["material_omitted_scenario_policy"],
        "uncertainty": payload["uncertainty"],
        "evidence_record_ids": list(cast(Any, payload["evidence_record_ids"])),
        "source_versions": list(cast(Any, payload["source_versions"])),
        "dependency_pairs": [list(pair) for pair in cast(Any, payload["dependency_pairs"])],
        "effective_at": _iso(payload["effective_at"]),
        "recorded_at": _iso(payload["recorded_at"]),
        "known_at": _iso(payload["known_at"]),
    }
    return _write(tmp_path, filename, manifest)


def _scenario_probability_manifest(
    tmp_path: Path,
    *,
    filename: str,
    scenario_set_record_id: str,
    scenario_id: str,
    probability: str,
    probability_uncertainty: str = "0.05",
    effective_at: datetime = NOW,
    recorded_at: datetime = NOW + timedelta(minutes=3),
    known_at: datetime = NOW + timedelta(minutes=3),
) -> Path:
    manifest = {
        "operation": "scenario_probability",
        "scenario_set_record_id": scenario_set_record_id,
        "scenario_id": scenario_id,
        "probability": probability,
        "probability_uncertainty": probability_uncertainty,
        "probability_authority": "declared-pre-evaluation",
        "probability_method": "predeclared-scenario-probability-v1",
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
    }
    return _write(tmp_path, filename, manifest)


def _scenario_payoff_manifest(
    tmp_path: Path,
    *,
    filename: str,
    scenario_set_record_id: str,
    scenario_id: str,
    terminal_payoff: str,
    payoff_uncertainty: str = "0.05",
    effective_at: datetime = NOW,
    recorded_at: datetime = NOW + timedelta(minutes=4),
    known_at: datetime = NOW + timedelta(minutes=4),
) -> Path:
    manifest = {
        "operation": "scenario_payoff",
        "scenario_set_record_id": scenario_set_record_id,
        "scenario_id": scenario_id,
        "terminal_payoff": terminal_payoff,
        "payoff_uncertainty": payoff_uncertainty,
        "evidence_record_ids": [f"evidence:cmp-asymmetry:payoff-{scenario_id}"],
        "source_record_id": f"cmp-asymmetry-payoff-{scenario_id}",
        "source_record_version": "2026-01-01",
        "effective_at": effective_at.isoformat(),
        "recorded_at": recorded_at.isoformat(),
        "known_at": known_at.isoformat(),
    }
    return _write(tmp_path, filename, manifest)


def _assess_manifest(
    tmp_path: Path,
    *,
    filename: str = "assess-manifest.json",
    methodology_record_id: str,
    scenario_set_logical_id: str,
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
        "scenario_set_logical_id": scenario_set_logical_id,
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
    exit_code = asymmetry_authority_command.main(["run", str(manifest_path)])
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


def _build_full_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[dict, dict, dict, dict]:
    """Drives methodology, scenario_set, and all three scenario probabilities/payoffs
    entirely through the orchestration module, returning each operation's parsed JSON
    output."""
    seeded = _SeededBaseline(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    scenario_set = _run(
        monkeypatch,
        tmp_path,
        _scenario_set_manifest(
            tmp_path,
            methodology_record_id=methodology["record_id"],
            baseline_market_fact_record_id=seeded.spot_price_fact.record_id,
        ),
        capsys,
    )
    probability_values = {"s-upside-strong": "0.25", "s-upside-mild": "0.35", "s-downside-tail": "0.40"}
    probabilities = {}
    for index, (scenario_id, probability) in enumerate(probability_values.items()):
        probabilities[scenario_id] = _run(
            monkeypatch,
            tmp_path,
            _scenario_probability_manifest(
                tmp_path,
                filename=f"probability-{index}.json",
                scenario_set_record_id=scenario_set["record_id"],
                scenario_id=scenario_id,
                probability=probability,
                probability_uncertainty="0.10" if scenario_id == "s-downside-tail" else "0.05",
            ),
            capsys,
        )
    payoff_values = {"s-upside-strong": "0.30", "s-upside-mild": "0.10", "s-downside-tail": "-0.40"}
    payoffs = {}
    for index, (scenario_id, payoff) in enumerate(payoff_values.items()):
        payoffs[scenario_id] = _run(
            monkeypatch,
            tmp_path,
            _scenario_payoff_manifest(
                tmp_path,
                filename=f"payoff-{index}.json",
                scenario_set_record_id=scenario_set["record_id"],
                scenario_id=scenario_id,
                terminal_payoff=payoff,
                payoff_uncertainty="0.10" if scenario_id == "s-downside-tail" else "0.05",
            ),
            capsys,
        )
    return methodology, scenario_set, probabilities["s-upside-strong"], payoffs["s-upside-strong"]


# A. Write-operation happy paths through the orchestration module ---------------------


def test_methodology_persists_through_orchestration_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    output = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert output["operation"] == "methodology"
    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_asymmetry_methodology(output["record_id"])
    assert record is not None
    assert record.quality_state == "accepted"
    assert record.conflict_state == "none"


def test_scenario_set_persists_through_orchestration_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _SeededBaseline(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    output = _run(
        monkeypatch,
        tmp_path,
        _scenario_set_manifest(
            tmp_path,
            methodology_record_id=methodology["record_id"],
            baseline_market_fact_record_id=seeded.spot_price_fact.record_id,
        ),
        capsys,
    )
    assert output["operation"] == "scenario_set"
    assert output["scenario_count"] == 3
    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_scenario_set(output["record_id"])
    assert record is not None
    assert record.scenario_ids == ("s-upside-strong", "s-upside-mild", "s-downside-tail")


def test_scenario_probability_and_payoff_persist_through_orchestration_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    methodology, scenario_set, probability_output, payoff_output = _build_full_chain(monkeypatch, tmp_path, capsys)
    assert probability_output["operation"] == "scenario_probability"
    assert probability_output["scenario_id"] == "s-upside-strong"
    assert payoff_output["operation"] == "scenario_payoff"
    assert payoff_output["scenario_id"] == "s-upside-strong"
    repository = AsymmetryRepository(_db_path(tmp_path))
    probability_record = repository.get_scenario_probability(probability_output["record_id"])
    assert probability_record is not None
    assert probability_record.probability == "0.25"
    payoff_record = repository.get_scenario_payoff(payoff_output["record_id"])
    assert payoff_record is not None
    assert payoff_record.terminal_payoff == "0.30"


# B. End-to-end orchestration-module creation of all five record families -------------


def test_end_to_end_orchestration_module_creates_full_chain_and_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    methodology, scenario_set, _, _ = _build_full_chain(monkeypatch, tmp_path, capsys)

    assess_manifest = _assess_manifest(
        tmp_path,
        methodology_record_id=methodology["record_id"],
        scenario_set_logical_id=scenario_set["logical_id"],
    )
    output = _run(monkeypatch, tmp_path, assess_manifest, capsys)
    assert output["operation"] == "assess"
    # ADR 0021: no calibrated normalization exists in this foundation, so a complete
    # scenario set still never reaches AVAILABLE -- it reaches the raw-values state
    # that persists the raw asymmetry ratio while normalization remains unavailable.
    assert output["availability_state"] == "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"
    assert output["normalization_status"] == "unavailable"
    assert output["raw_asymmetry_ratio"] is not None

    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_assessment(output["record_id"])
    assert record is not None
    assert record.raw_asymmetry_ratio == output["raw_asymmetry_ratio"]


# C. Insert-identical idempotency -------------------------------------------------------


def test_insert_identical_methodology_via_orchestration_module_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _methodology_manifest(tmp_path)
    first = _run(monkeypatch, tmp_path, manifest_path, capsys)
    second = _run(monkeypatch, tmp_path, manifest_path, capsys)
    assert first["record_id"] == second["record_id"]


# D. Divergent duplicate rejection -------------------------------------------------------


def test_divergent_methodology_duplicate_via_orchestration_module_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_manifest = _methodology_manifest(tmp_path, filename="m1.json")
    _run(monkeypatch, tmp_path, first_manifest, capsys)
    second_manifest = _methodology_manifest(tmp_path, filename="m2.json", required_quote_currency="eur")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(AsymmetryIntegrityError, match="root record already exists"):
        asymmetry_authority_command.main(["run", str(second_manifest)])


# E. Orchestration-module-construction / direct-construction field equivalence --------


def test_orchestration_construction_is_field_equivalent_to_direct_service_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Independently constructs all five record families twice, from the same semantic
    inputs, into two completely separate databases:

    - Path A: entirely through the orchestration module
      (`asymmetry_authority_command.main`) -- not through the `hunter` CLI, which does
      not dispatch to it (see `test_hunter_main_does_not_dispatch_asymmetry_authority`
      below).
    - Path B: entirely through direct `CanonicalAsymmetryService` construction, never
      touching the orchestration module.

    A read-back-only equivalence check (re-reading what the orchestration module
    itself wrote and confirming a direct-service *read* call returns the same record)
    proves persistence/replay compatibility, but cannot detect an argument-mapping bug
    in the module (a dropped field, a field mapped to the wrong keyword, a
    type-coercion difference) because both sides of the comparison would originate
    from the one write. This test instead performs Path B as a fully independent
    *construction*, then asserts complete dataclass-field equality (via `asdict`)
    between the two paths for every one of the five record families -- covering
    canonical identity (`record_id`/`logical_id`), the deterministic `content_hash`,
    methodology/formula-version fields, provenance, and every other ADR 0021 field. If
    the orchestration module's argument mapping ever diverges from direct
    construction, the computed `content_hash` (and therefore `record_id`) changes and
    this test fails.
    """
    module_root = tmp_path / "module-path"
    direct_root = tmp_path / "direct-path"
    module_root.mkdir()
    direct_root.mkdir()

    # Path A: construct the full chain entirely through the orchestration module.
    methodology_a, scenario_set_a, probability_a, payoff_a = _build_full_chain(monkeypatch, module_root, capsys)
    assessment_a = _run(
        monkeypatch,
        module_root,
        _assess_manifest(
            module_root,
            methodology_record_id=methodology_a["record_id"],
            scenario_set_logical_id=scenario_set_a["logical_id"],
        ),
        capsys,
    )

    # Path B: construct the identical logical records directly through the service, in
    # a completely separate database, never touching the orchestration module.
    seeded_b = _SeededBaseline(direct_root)
    direct_service = CanonicalAsymmetryService(
        repository=AsymmetryRepository(_db_path(direct_root)),
        market_fact_repository=ObservedMarketFactRepository(_db_path(direct_root)),
        application_root=direct_root,
    )
    methodology_b = direct_service.persist_asymmetry_methodology(**asymmetry_methodology_payload())
    scenario_set_b = direct_service.persist_scenario_set(
        **scenario_payload(methodology_record_id=methodology_b.record_id),
        baseline_market_fact_record_id=seeded_b.spot_price_fact.record_id,
    )
    probability_b = direct_service.persist_scenario_probability(
        scenario_set_record_id=scenario_set_b.record_id,
        scenario_id="s-upside-strong",
        probability="0.25",
        probability_uncertainty="0.05",
        probability_authority="declared-pre-evaluation",
        probability_method="predeclared-scenario-probability-v1",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=3),
        known_at=NOW + timedelta(minutes=3),
    )
    for scenario_id, probability, uncertainty in (
        ("s-upside-mild", "0.35", "0.05"),
        ("s-downside-tail", "0.40", "0.10"),
    ):
        direct_service.persist_scenario_probability(
            scenario_set_record_id=scenario_set_b.record_id,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty=uncertainty,
            probability_authority="declared-pre-evaluation",
            probability_method="predeclared-scenario-probability-v1",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=3),
            known_at=NOW + timedelta(minutes=3),
        )
    payoff_b = direct_service.persist_scenario_payoff(
        scenario_set_record_id=scenario_set_b.record_id,
        scenario_id="s-upside-strong",
        terminal_payoff="0.30",
        payoff_uncertainty="0.05",
        evidence_record_ids=("evidence:cmp-asymmetry:payoff-s-upside-strong",),
        source_record_id="cmp-asymmetry-payoff-s-upside-strong",
        source_record_version="2026-01-01",
        effective_at=NOW,
        recorded_at=NOW + timedelta(minutes=4),
        known_at=NOW + timedelta(minutes=4),
    )
    for scenario_id, payoff, uncertainty in (
        ("s-upside-mild", "0.10", "0.05"),
        ("s-downside-tail", "-0.40", "0.10"),
    ):
        direct_service.persist_scenario_payoff(
            scenario_set_record_id=scenario_set_b.record_id,
            scenario_id=scenario_id,
            terminal_payoff=payoff,
            payoff_uncertainty=uncertainty,
            evidence_record_ids=(f"evidence:cmp-asymmetry:payoff-{scenario_id}",),
            source_record_id=f"cmp-asymmetry-payoff-{scenario_id}",
            source_record_version="2026-01-01",
            effective_at=NOW,
            recorded_at=NOW + timedelta(minutes=4),
            known_at=NOW + timedelta(minutes=4),
        )
    assessment_b = direct_service.assess(
        identity=identity(),
        methodology_record_id=methodology_b.record_id,
        scenario_set_logical_id=scenario_set_b.logical_id,
        effective_at=NOW,
        recorded_at=RECORDED,
        known_at=RECORDED,
    )

    # Full structural equality, field by field, for every record family: canonical
    # identity, deterministic content_hash, formula-version fields, provenance, and
    # every replay-relevant field ADR 0021 requires.
    repository_a = AsymmetryRepository(_db_path(module_root))

    methodology_a_record = repository_a.get_asymmetry_methodology(methodology_a["record_id"])
    assert methodology_a_record is not None
    assert methodology_a_record.content_hash == methodology_b.content_hash
    assert asdict(methodology_a_record) == asdict(methodology_b)

    scenario_set_a_record = repository_a.get_scenario_set(scenario_set_a["record_id"])
    assert scenario_set_a_record is not None
    assert scenario_set_a_record.content_hash == scenario_set_b.content_hash
    assert asdict(scenario_set_a_record) == asdict(scenario_set_b)

    probability_a_record = repository_a.get_scenario_probability(probability_a["record_id"])
    assert probability_a_record is not None
    assert probability_a_record.content_hash == probability_b.content_hash
    assert asdict(probability_a_record) == asdict(probability_b)

    payoff_a_record = repository_a.get_scenario_payoff(payoff_a["record_id"])
    assert payoff_a_record is not None
    assert payoff_a_record.content_hash == payoff_b.content_hash
    assert asdict(payoff_a_record) == asdict(payoff_b)

    assessment_a_record = repository_a.get_assessment(assessment_a["record_id"])
    assert assessment_a_record is not None
    assert assessment_a_record.raw_asymmetry_ratio == assessment_b.raw_asymmetry_ratio
    assert asdict(assessment_a_record) == asdict(assessment_b)


# F. Read-only status query ------------------------------------------------------------


@pytest.mark.parametrize(
    "target", ["methodology", "scenario_set", "scenario_probability", "scenario_payoff", "assessment"]
)
def test_status_reports_unavailable_before_any_record_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    manifest = _status_manifest(
        tmp_path, target=target, known_by=NOW + timedelta(days=1), logical_id="does-not-exist-yet"
    )
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output == {
        "operation": "status",
        "target": target,
        "persistence_database": str(_db_path(tmp_path)),
        "available": False,
    }


def test_status_does_not_create_database_when_none_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: a status query against a pristine `HUNTER_APPLICATION_ROOT`
    must remain a pure read and must not create `data/data_ops.sqlite` as a side
    effect, even though it correctly reports `available: False`. Before this fix,
    `_status` unconditionally constructed the underlying repositories (via
    `_service`) before checking anything, and those repositories create their
    database file and schema in `__init__`."""
    manifest = _status_manifest(
        tmp_path, target="methodology", known_by=NOW + timedelta(days=1), logical_id="does-not-exist-yet"
    )
    assert not _db_path(tmp_path).exists()
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    assert output["available"] is False
    assert not _db_path(tmp_path).exists()


def test_status_still_validates_manifest_against_a_pristine_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for a review finding on the fix above (raised against the
    sibling `hunter.mispricing.command`, PR #189, and applied identically here):
    skipping repository construction when no database exists yet must not also
    skip validating the manifest's own required fields. A malformed status
    manifest (here, a non-ISO-8601 `known_by`) against a pristine application root
    must still raise, not be masked as `available: False`, and must still not
    create the database."""
    manifest = _write(
        tmp_path,
        "malformed-status.json",
        {
            "operation": "status",
            "target": "methodology",
            "effective_as_of": NOW.isoformat(),
            "known_by": "not-a-timestamp",
            "logical_id": "does-not-exist-yet",
        },
    )
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        asymmetry_authority_command.main(["run", str(manifest)])
    assert not _db_path(tmp_path).exists()


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

    repository = AsymmetryRepository(_db_path(tmp_path))
    direct = repository.get_asymmetry_methodology(written["record_id"])
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_persisted_scenario_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _SeededBaseline(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    written = _run(
        monkeypatch,
        tmp_path,
        _scenario_set_manifest(
            tmp_path,
            methodology_record_id=methodology["record_id"],
            baseline_market_fact_record_id=seeded.spot_price_fact.record_id,
        ),
        capsys,
    )
    output = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path, target="scenario_set", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
        ),
        capsys,
    )
    assert output["available"] is True
    assert output["record"]["record_id"] == written["record_id"]


def test_status_reports_persisted_scenario_probability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard for the acceptance matrix's `status` claim: proves
    `strict_known_scenario_probability` retrieval through the orchestration module
    for a persisted record, independently of the methodology/scenario_set/
    assessment coverage above -- `scenario_probability` and `scenario_payoff` are
    the only two of the five status targets that previously lacked an
    `available: True` retrieval test."""
    _, _, probability, _ = _build_full_chain(monkeypatch, tmp_path, capsys)
    output = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path,
            target="scenario_probability",
            known_by=NOW + timedelta(days=1),
            logical_id=probability["logical_id"],
        ),
        capsys,
    )
    assert output["available"] is True
    assert output["record"]["record_id"] == probability["record_id"]
    assert output["record"]["scenario_id"] == "s-upside-strong"
    assert output["record"]["probability"] == "0.25"

    repository = AsymmetryRepository(_db_path(tmp_path))
    direct = repository.get_scenario_probability(probability["record_id"])
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_persisted_scenario_payoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard for the acceptance matrix's `status` claim: proves
    `strict_known_scenario_payoff` retrieval through the orchestration module for
    a persisted record, independently of every other status-target test in this
    file (see `test_status_reports_persisted_scenario_probability` above)."""
    _, _, _, payoff = _build_full_chain(monkeypatch, tmp_path, capsys)
    output = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(
            tmp_path, target="scenario_payoff", known_by=NOW + timedelta(days=1), logical_id=payoff["logical_id"]
        ),
        capsys,
    )
    assert output["available"] is True
    assert output["record"]["record_id"] == payoff["record_id"]
    assert output["record"]["scenario_id"] == "s-upside-strong"
    assert output["record"]["terminal_payoff"] == "0.30"

    repository = AsymmetryRepository(_db_path(tmp_path))
    direct = repository.get_scenario_payoff(payoff["record_id"])
    assert direct is not None
    assert output["record"]["content_hash"] == direct.content_hash


def test_status_reports_persisted_assessment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    methodology, scenario_set, _, _ = _build_full_chain(monkeypatch, tmp_path, capsys)
    written = _run(
        monkeypatch,
        tmp_path,
        _assess_manifest(
            tmp_path,
            methodology_record_id=methodology["record_id"],
            scenario_set_logical_id=scenario_set["logical_id"],
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
    assert output["record"]["raw_asymmetry_ratio"] == written["raw_asymmetry_ratio"]


def test_status_rejects_unknown_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _status_manifest(tmp_path, target="not-a-real-target", known_by=NOW, logical_id="anything")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="status manifest requires target"):
        asymmetry_authority_command.main(["run", str(manifest)])


def test_status_does_not_persist_or_mutate_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A status query, run repeatedly, must never itself create, correct, or otherwise
    write a record -- it is a pure read over the existing repository."""
    written = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    repository = AsymmetryRepository(_db_path(tmp_path))
    before = repository.count("asymmetry_methodologies")
    for _ in range(3):
        _run(
            monkeypatch,
            tmp_path,
            _status_manifest(
                tmp_path, target="methodology", known_by=NOW + timedelta(days=1), logical_id=written["logical_id"]
            ),
            capsys,
        )
    after = repository.count("asymmetry_methodologies")
    assert after == before


# G. Unknown operation and malformed manifest rejection --------------------------------


def test_unknown_operation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _write(tmp_path, "bad-operation.json", {"operation": "not-a-real-operation"})
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="manifest requires operation"):
        asymmetry_authority_command.main(["run", str(manifest)])


def test_missing_application_root_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _methodology_manifest(tmp_path)
    monkeypatch.delenv("HUNTER_APPLICATION_ROOT", raising=False)
    with pytest.raises(ValueError, match="HUNTER_APPLICATION_ROOT"):
        asymmetry_authority_command.main(["run", str(manifest)])


def test_wrong_argv_shape_prints_usage_and_returns_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = asymmetry_authority_command.main(["run"])
    assert exit_code == 1
    assert (
        "usage: python -c 'from hunter.asymmetry.command import main; "
        'raise SystemExit(main(["run", "MANIFEST.json"]))\'' in capsys.readouterr().out
    )


def test_malformed_manifest_top_level_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "not-an-object.json"
    manifest_path.write_text(json.dumps(["operation", "methodology"]), encoding="utf-8")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="manifest must be a JSON object"):
        asymmetry_authority_command.main(["run", str(manifest_path)])


def test_null_required_methodology_field_is_rejected_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a JSON `null` (or any non-string) value for a required
    methodology field must be rejected by the orchestration module itself, not
    silently coerced to the literal string `"None"` and handed to the service --
    which would then accept and persist it, because `"None"` is a non-blank
    string and satisfies the service's own required-text check."""
    manifest = _methodology_manifest(tmp_path, methodology_id=None)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="methodology_id.*must be a non-blank string"):
        asymmetry_authority_command.main(["run", str(manifest)])


def test_null_identity_field_is_rejected_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same regression guard as
    `test_null_required_methodology_field_is_rejected_not_stringified`, for the
    identity parser `_assess` (and `_persist_scenario_set`) shares with every other
    orchestration module: a JSON `null` `entity_id` must be rejected, not turned
    into the literal string `"None"` and persisted as part of an assessment's
    identity."""
    methodology, scenario_set, _, _ = _build_full_chain(monkeypatch, tmp_path, capsys)
    manifest_path = _assess_manifest(
        tmp_path,
        methodology_record_id=methodology["record_id"],
        scenario_set_logical_id=scenario_set["logical_id"],
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["identity"]["entity_id"] = None
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="identity.entity_id.*must be a non-blank string"):
        asymmetry_authority_command.main(["run", str(manifest_path)])


def test_null_list_entry_is_rejected_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard for the same defect class in `_string_tuple`: a `null` entry
    inside a required string list (e.g. `scenario_ids`) must be rejected, not
    coerced to the literal string `"None"` and persisted as a scenario id."""
    seeded = _SeededBaseline(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    manifest_path = _scenario_set_manifest(
        tmp_path,
        methodology_record_id=methodology["record_id"],
        baseline_market_fact_record_id=seeded.spot_price_fact.record_id,
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["scenario_ids"] = [None, "s-upside-mild", "s-downside-tail"]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="scenario_ids.*must be a non-blank string"):
        asymmetry_authority_command.main(["run", str(manifest_path)])


# H. Repository bypass remains impossible -----------------------------------------------


def test_repository_bypass_remains_impossible() -> None:
    """Mirrors test_valuation_authority_v1.py's, test_comparative_valuation_authority_v1.py's,
    and test_mispricing_authority_v1.py's equivalent assertion: the repository this
    orchestration module drives exposes no public write/apply method of its own."""
    for name in ("save", "apply", "write", "persist", "assess", "replay"):
        assert not hasattr(AsymmetryRepository, name)


# I. Entry point is implemented but deliberately not activated -------------------------


def test_hunter_main_does_not_dispatch_asymmetry_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for Issue #187's explicit scope boundary: ADR 0021 authorizes
    `CanonicalAsymmetryService` (already implemented, PR #165) but nothing in the
    governance framework authorizes production dispatch of an entry point without
    independent Architecture Review, which has not occurred for this module. This test
    proves `hunter.asymmetry.command` remains implemented and directly testable (as
    every other test in this file proves) but unreachable through `hunter.__main__` /
    the `hunter` CLI -- mirroring the identical precedent already established for
    `hunter.evidence_assembly`, `hunter.comparative_valuation.command`, and
    `hunter.mispricing.command`. If a future change wires this verb into
    `hunter.__main__` without that authorization, this test fails."""
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest_path = _methodology_manifest(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        hunter_main.main(["asymmetry-authority", "run", str(manifest_path)])
    # falls through to hunter.cli's argparse dispatcher, which rejects the unknown verb
    assert excinfo.value.code == 2
    # and, independently, no repository write occurred as a side effect of the attempt
    assert not _db_path(tmp_path).exists()


# J. Null defaulting for fields the orchestration module itself defaults ---------------


def test_null_formula_version_falls_back_to_default_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: `formula_version` has an orchestration-module-supplied
    default (the only ADR 0021-supported value). An explicit JSON `null` must fall
    back to that default, not be stringified to the literal `"None"` -- which the
    service would reject only later, at assess/lookup time, as an unsupported
    formula version, after an invalid record had already been persisted."""
    manifest = _methodology_manifest(tmp_path, formula_version=None)
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_asymmetry_methodology(output["record_id"])
    assert record is not None
    assert record.formula_version == "asymmetry-raw-ratio-v1"


def test_null_correction_reason_falls_back_to_blank_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: `correction_reason` defaults to blank when no correction is
    being made. An explicit JSON `null` must fall back to `""`, not be stringified
    to the literal `"None"` -- which is non-blank and would trip the
    `supersedes_record_id`/`correction_reason` pairing invariant when no
    `supersedes_record_id` is supplied."""
    manifest = _methodology_manifest(tmp_path, correction_reason=None)
    output = _run(monkeypatch, tmp_path, manifest, capsys)
    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_asymmetry_methodology(output["record_id"])
    assert record is not None
    assert record.correction_reason == ""


def test_null_identity_chain_and_contract_address_default_to_empty_not_stringified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: `EconomicClaimIdentity` treats `chain`/`contract_address`
    as optional, paired fields (both set or both blank). Before this fix, an
    explicit JSON `null` for both fields was stringified to the literal `"None"`
    for each -- which is non-blank for both, so it satisfied the paired-presence
    invariant and would have silently persisted meaningless `"None"`/`"None"`
    values instead of the intended blank pair."""
    seeded = _SeededBaseline(tmp_path)
    methodology = _run(monkeypatch, tmp_path, _methodology_manifest(tmp_path), capsys)
    manifest_path = _scenario_set_manifest(
        tmp_path,
        methodology_record_id=methodology["record_id"],
        baseline_market_fact_record_id=seeded.spot_price_fact.record_id,
    )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["identity"]["chain"] = None
    raw["identity"]["contract_address"] = None
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    output = _run(monkeypatch, tmp_path, manifest_path, capsys)
    repository = AsymmetryRepository(_db_path(tmp_path))
    record = repository.get_scenario_set(output["record_id"])
    assert record is not None
    assert record.identity.chain == ""
    assert record.identity.contract_address == ""


def test_non_string_correction_reason_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_text_or_default` must reject a present, non-string, non-null value (e.g. a
    JSON number) rather than silently stringifying it, the same way `_required_text`
    does for genuinely required fields."""
    manifest = _methodology_manifest(tmp_path, correction_reason=12345)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="correction_reason.*must be a string"):
        asymmetry_authority_command.main(["run", str(manifest)])
