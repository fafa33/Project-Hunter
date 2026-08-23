"""Issue #190: undispatched orchestration for Canonical Evidence Assembly.

These tests exercise ``hunter.evidence_assembly.command`` only through direct Python
invocation. They prove the module remains a manifest-to-service adapter: canonical
native records are re-hydrated from production persistence, all assembly/replay
semantics remain owned by ``CanonicalEvidenceAssemblyService``, pristine read-only
status is side-effect free, and no ``hunter.__main__`` dispatch is added.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from test_evidence_assembly_production_constructibility import _dt, _seed_production_environment

from hunter import __main__ as hunter_main
from hunter.evidence_assembly import command as evidence_assembly_command
from hunter.evidence_assembly.composition import build_production_evidence_assembly_service
from hunter.evidence_assembly.models import AssemblyConstituent
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.evidence_assembly.service import CanonicalEvidenceAssemblyError


def _db_path(application_root: Path) -> Path:
    return application_root / "data" / "data_ops.sqlite"


def _configure_production_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "production-key")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", (b"k" * 32).hex())


def _seed(monkeypatch: pytest.MonkeyPatch, application_root: Path) -> dict:
    application_root.mkdir(parents=True, exist_ok=True)
    seeded = _seed_production_environment(
        monkeypatch=monkeypatch,
        db_path=_db_path(application_root),
        app_root=application_root,
    )
    _configure_production_key(monkeypatch)
    return seeded


def _write(application_root: Path, filename: str, manifest: dict[str, object]) -> Path:
    path = application_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _constituent(record_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "record_id": record_id,
        "shape_id": "monthly-revenue-shape",
        "currency": "USD",
        "raw_unit": "USD",
        "accounting_meaning": "period_specific",
        "supply_basis_id": "supply-basis-test",
        "pathway_id": "pathway-test",
    }
    payload.update(overrides)
    return payload


def _assemble_manifest(seeded: dict, **overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "operation": "assemble",
        "constituents": [
            _constituent(seeded["ev1"].record_id),
            _constituent(seeded["ev2"].record_id),
        ],
        "accounting_window_start": _dt(2026, 1, 1).isoformat(),
        "accounting_window_end": _dt(2026, 3, 1).isoformat(),
        "recorded_at": _dt(2026, 3, 2).isoformat(),
        "replay_cutoff": _dt(2026, 3, 2).isoformat(),
        "methodology_contract_id": "contract-test-1",
        "methodology_contract_version": "1.0.0",
        "evidence_shape_registry_version": "1.0.0",
    }
    manifest.update(overrides)
    return manifest


def _manifest_constituents(manifest: dict[str, object]) -> list[dict[str, object]]:
    value = manifest["constituents"]
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return [dict(item) for item in value]


def _status_manifest(logical_id: object, *, known_by=None) -> dict[str, object]:
    if known_by is None:
        known_by = _dt(2026, 3, 2)
    return {
        "operation": "status",
        "logical_id": logical_id,
        "effective_as_of": _dt(2026, 3, 1).isoformat(),
        "known_by": known_by.isoformat(),
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    application_root: Path,
    manifest: dict[str, object],
    capsys: pytest.CaptureFixture[str],
    *,
    filename: str = "manifest.json",
) -> dict:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(application_root))
    path = _write(application_root, filename, manifest)
    assert evidence_assembly_command.main(["run", str(path)]) == 0
    return json.loads(capsys.readouterr().out)


def _assembly_constituents(seeded: dict) -> tuple[AssemblyConstituent, AssemblyConstituent]:
    return (
        AssemblyConstituent(
            record=seeded["ev1"],
            shape_id="monthly-revenue-shape",
            currency="USD",
            raw_unit="USD",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis-test",
            pathway_id="pathway-test",
        ),
        AssemblyConstituent(
            record=seeded["ev2"],
            shape_id="monthly-revenue-shape",
            currency="USD",
            raw_unit="USD",
            accounting_meaning="period_specific",
            supply_basis_id="supply-basis-test",
            pathway_id="pathway-test",
        ),
    )


def test_assemble_write_and_strict_known_status_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed(monkeypatch, tmp_path)

    assembled_output = _run(monkeypatch, tmp_path, _assemble_manifest(seeded), capsys, filename="assemble.json")
    assert assembled_output["operation"] == "assemble"
    assert assembled_output["quality_state"] == "accepted"
    assert assembled_output["conflict_state"] == "none"

    persisted = AssembledEvidenceRepository(_db_path(tmp_path)).get(assembled_output["record_id"])
    assert persisted is not None
    assert persisted.logical_id == assembled_output["logical_id"]
    assert persisted.content_hash == assembled_output["content_hash"]

    available = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(persisted.logical_id),
        capsys,
        filename="status-available.json",
    )
    assert available["available"] is True
    assert available["record"]["record_id"] == persisted.record_id
    assert available["record"]["content_hash"] == persisted.content_hash

    unavailable = _run(
        monkeypatch,
        tmp_path,
        _status_manifest(persisted.logical_id, known_by=_dt(2026, 3, 1)),
        capsys,
        filename="status-unavailable.json",
    )
    assert unavailable == {
        "available": False,
        "operation": "status",
        "persistence_database": str(_db_path(tmp_path)),
    }


def test_status_on_pristine_root_is_side_effect_free_and_does_not_require_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    for name in (
        "HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID",
        "HUNTER_VALUE_CAPTURE_SIGNING_KEY",
        "HUNTER_VALUE_CAPTURE_KEY_ID",
        "HUNTER_VALUE_CAPTURE_KEY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    output = _run(monkeypatch, tmp_path, _status_manifest("assembled-evidence:missing"), capsys)

    assert output["available"] is False
    assert not _db_path(tmp_path).exists()
    assert not (tmp_path / "data").exists()


def test_status_validates_required_fields_before_unavailable_short_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest = _status_manifest(None)
    path = _write(tmp_path, "malformed-status.json", manifest)

    with pytest.raises(ValueError, match="logical_id.*non-blank string"):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""
    assert not _db_path(tmp_path).exists()
    assert not (tmp_path / "data").exists()


def test_explicit_null_optional_text_is_not_coerced_to_none_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest = {
        "operation": "assemble",
        "constituents": [
            _constituent("record-1"),
            _constituent("record-2"),
        ],
        "accounting_window_start": _dt(2026, 1, 1).isoformat(),
        "accounting_window_end": _dt(2026, 3, 1).isoformat(),
        "recorded_at": _dt(2026, 3, 2).isoformat(),
        "replay_cutoff": _dt(2026, 3, 2).isoformat(),
        "methodology_contract_id": "contract-test-1",
        "methodology_contract_version": "1.0.0",
        "evidence_shape_registry_version": "1.0.0",
        "correction_reason": None,
    }
    path = _write(tmp_path, "null-optional.json", manifest)

    with pytest.raises(ValueError, match="correction_reason.*string"):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""
    assert not _db_path(tmp_path).exists()


def test_nullable_constituent_metadata_is_rejected_before_persistence_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    manifest = {
        "operation": "assemble",
        "constituents": [
            _constituent("record-1", shape_id=None),
            _constituent("record-2"),
        ],
        "accounting_window_start": _dt(2026, 1, 1).isoformat(),
        "accounting_window_end": _dt(2026, 3, 1).isoformat(),
        "recorded_at": _dt(2026, 3, 2).isoformat(),
        "replay_cutoff": _dt(2026, 3, 2).isoformat(),
        "methodology_contract_id": "contract-test-1",
        "methodology_contract_version": "1.0.0",
        "evidence_shape_registry_version": "1.0.0",
    }
    path = _write(tmp_path, "null-constituent.json", manifest)

    with pytest.raises(ValueError, match="shape_id.*non-blank string"):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""
    assert not _db_path(tmp_path).exists()


def test_unknown_operation_fails_before_persistence_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    path = _write(tmp_path, "unknown.json", {"operation": "invent-authority"})

    with pytest.raises(ValueError, match="requires operation: assemble or status"):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""
    assert not _db_path(tmp_path).exists()


def test_manifest_cannot_override_authoritative_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed(monkeypatch, tmp_path)
    manifest = _assemble_manifest(seeded)
    constituents = _manifest_constituents(manifest)
    second = dict(constituents[1])
    second["currency"] = "EUR"
    constituents[1] = second
    manifest["constituents"] = constituents
    path = _write(tmp_path, "semantic-override.json", manifest)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))

    with pytest.raises(
        CanonicalEvidenceAssemblyError,
        match="constituent metadata does not match authoritative strict-known evidence semantics",
    ):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""


def test_unknown_native_record_id_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed(monkeypatch, tmp_path)
    manifest = _assemble_manifest(seeded)
    constituents = _manifest_constituents(manifest)
    first = dict(constituents[0])
    first["record_id"] = "forged-record-id"
    constituents[0] = first
    manifest["constituents"] = constituents
    path = _write(tmp_path, "forged-record.json", manifest)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="not canonical native evidence: forged-record-id"):
        evidence_assembly_command.main(["run", str(path)])

    assert capsys.readouterr().out == ""


def test_orchestration_and_direct_service_are_complete_record_equivalent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module_root = tmp_path / "module"
    direct_root = tmp_path / "direct"
    module_seeded = _seed(monkeypatch, module_root)
    direct_seeded = _seed(monkeypatch, direct_root)

    module_output = _run(monkeypatch, module_root, _assemble_manifest(module_seeded), capsys)
    module_record = AssembledEvidenceRepository(_db_path(module_root)).get(module_output["record_id"])
    assert module_record is not None

    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(direct_root))
    _configure_production_key(monkeypatch)
    direct_service = build_production_evidence_assembly_service(
        db_path=_db_path(direct_root),
        application_root=direct_root,
    )
    direct_record = direct_service.assemble(
        constituents=_assembly_constituents(direct_seeded),
        accounting_window_start=_dt(2026, 1, 1),
        accounting_window_end=_dt(2026, 3, 1),
        recorded_at=_dt(2026, 3, 2),
        replay_cutoff=_dt(2026, 3, 2),
        methodology_contract_id="contract-test-1",
        methodology_contract_version="1.0.0",
        evidence_shape_registry_version="1.0.0",
    )

    assert _db_path(module_root) != _db_path(direct_root)
    assert asdict(module_record) == asdict(direct_record)


def test_status_on_existing_database_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = _seed(monkeypatch, tmp_path)
    assembled = _run(monkeypatch, tmp_path, _assemble_manifest(seeded), capsys)
    db_path = _db_path(tmp_path)
    before = db_path.read_bytes()

    output = _run(monkeypatch, tmp_path, _status_manifest(assembled["logical_id"]), capsys, filename="status.json")

    assert output["available"] is True
    assert db_path.read_bytes() == before


def test_help_advertises_only_real_direct_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    assert evidence_assembly_command.main([]) == 1
    output = capsys.readouterr().out
    assert "from hunter.evidence_assembly.command import main" in output
    assert "python -c" in output
    assert "hunter evidence-assembly-authority" not in output


def test_hunter_main_does_not_dispatch_evidence_assembly_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[str] = []

    def fake_cli_main(arguments: list[str]) -> int:
        observed.extend(arguments)
        return 73

    monkeypatch.setattr(hunter_main, "cli_main", fake_cli_main)

    arguments = ["evidence-assembly-authority", "run", "manifest.json"]
    assert hunter_main.main(arguments) == 73
    assert observed == arguments
