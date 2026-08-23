from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hunter.evidence_assembly import command as evidence_assembly_command
from hunter.evidence_assembly.repository import AssembledEvidenceRepository


def _schema_snapshot(db_path: Path) -> tuple[tuple[str, str, str | None], ...]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    return tuple((str(row[0]), str(row[1]), None if row[2] is None else str(row[2])) for row in rows)


def _clear_production_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID",
        "HUNTER_VALUE_CAPTURE_SIGNING_KEY",
        "HUNTER_VALUE_CAPTURE_KEY_ID",
        "HUNTER_VALUE_CAPTURE_KEY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def _status_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "status.json"
    manifest_path.write_text(
        json.dumps(
            {
                "operation": "status",
                "logical_id": "assembled-evidence:not-initialized",
                "effective_as_of": "2026-03-01T00:00:00+00:00",
                "known_by": "2026-03-02T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _run_unavailable_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, object]:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    _clear_production_keys(monkeypatch)
    assert evidence_assembly_command.main(["run", str(_status_manifest(tmp_path))]) == 0
    return json.loads(capsys.readouterr().out)


def test_status_on_preexisting_shared_db_without_assembly_schema_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "data" / "data_ops.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated_authority (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO unrelated_authority(id, payload) VALUES (?, ?)", ("existing", "canonical"))

    before_bytes = db_path.read_bytes()
    before_schema = _schema_snapshot(db_path)

    output = _run_unavailable_status(tmp_path, monkeypatch, capsys)

    assert output == {
        "available": False,
        "operation": "status",
        "persistence_database": str(db_path),
    }
    assert db_path.read_bytes() == before_bytes
    assert _schema_snapshot(db_path) == before_schema
    assert all("evidence_assembly" not in name for _, name, _ in before_schema)
    assert all("assembled_fundamental_evidence_records" != name for _, name, _ in before_schema)


def test_status_on_partially_initialized_shared_db_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "data" / "data_ops.sqlite"
    AssembledEvidenceRepository(db_path)
    before_bytes = db_path.read_bytes()
    before_schema = _schema_snapshot(db_path)

    output = _run_unavailable_status(tmp_path, monkeypatch, capsys)

    assert output == {
        "available": False,
        "operation": "status",
        "persistence_database": str(db_path),
    }
    assert db_path.read_bytes() == before_bytes
    assert _schema_snapshot(db_path) == before_schema


def test_status_surfaces_corrupt_canonical_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "data" / "data_ops.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not-a-sqlite-database")
    before_bytes = db_path.read_bytes()

    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(tmp_path))
    _clear_production_keys(monkeypatch)

    with pytest.raises(sqlite3.DatabaseError):
        evidence_assembly_command.main(["run", str(_status_manifest(tmp_path))])

    assert capsys.readouterr().out == ""
    assert db_path.read_bytes() == before_bytes
