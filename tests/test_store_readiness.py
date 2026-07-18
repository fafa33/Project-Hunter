from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hunter.cli import main
from hunter.store_readiness import StoreName, bootstrap_store, inspect_store

STORES: tuple[StoreName, ...] = ("tokenomics", "evidence_intelligence", "sufficiency")


@pytest.mark.parametrize("store", STORES)
def test_unconfigured_and_absent_stores_are_truthful(tmp_path: Path, store: StoreName) -> None:
    assert inspect_store(store, None).state == "unavailable"
    result = inspect_store(store, tmp_path / f"{store}.sqlite")
    assert result.state == "absent"
    assert result.analytical_record_count is None


@pytest.mark.parametrize("store", STORES)
def test_bootstrap_is_structural_empty_and_idempotent(tmp_path: Path, store: StoreName) -> None:
    path = tmp_path / store / "runtime.sqlite"

    first = bootstrap_store(store, path)
    second = bootstrap_store(store, path)

    assert first.state == second.state == "schema_only"
    assert first.schema_status == second.schema_status == "current"
    assert first.analytical_record_count == second.analytical_record_count == 0
    assert first.table_counts == second.table_counts
    assert all(count == 0 for count in first.table_counts.values())


@pytest.mark.parametrize("store", STORES)
def test_valid_stored_record_reports_populated_without_interpretation(tmp_path: Path, store: StoreName) -> None:
    path = tmp_path / f"{store}.sqlite"
    assert bootstrap_store(store, path).state == "schema_only"
    with sqlite3.connect(path) as conn:
        if store == "tokenomics":
            conn.execute("INSERT INTO token_asset_identities(asset_id) VALUES ('asset-1')")
        elif store == "evidence_intelligence":
            conn.execute(
                "INSERT INTO knowledge_versions VALUES (?, ?, ?, ?, ?)",
                ("version-1", "record-1", "claim", "1", "2026-01-01T00:00:00+00:00"),
            )
        else:
            conn.execute(
                "INSERT INTO data_sufficiency_checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                ("checkpoint-1", "processor", "target", "cursor", "2026-01-01T00:00:00+00:00", "1"),
            )

    result = inspect_store(store, path)
    assert result.state == "populated"
    assert result.analytical_record_count == 1
    assert "does not interpret" in result.reason


@pytest.mark.parametrize("store", STORES)
def test_schema_mismatch_requires_migration(tmp_path: Path, store: StoreName) -> None:
    path = tmp_path / f"{store}.sqlite"
    result = bootstrap_store(store, path)
    table = next(iter(result.table_counts))
    with sqlite3.connect(path) as conn:
        conn.execute(f'DROP TABLE "{table}"')

    result = inspect_store(store, path)
    assert result.state == "migration_required"
    assert result.schema_status == "mismatch"


def test_invalid_and_corrupt_paths_report_deterministic_failures(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    assert inspect_store("tokenomics", directory).state == "unreachable"

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    first = inspect_store("evidence_intelligence", corrupt)
    second = inspect_store("evidence_intelligence", corrupt)
    assert first.state == second.state == "failed"
    assert first.reason == second.reason


def test_bootstrap_preserves_existing_records_and_does_not_use_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tokenomics.sqlite"
    bootstrap_store("tokenomics", path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO token_asset_identities(asset_id) VALUES ('asset-1')")

    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("network call attempted"))
    result = bootstrap_store("tokenomics", path)

    assert result.state == "populated"
    assert result.table_counts["token_asset_identities"] == 1


def test_dedicated_cli_is_additive_and_requires_explicit_bootstrap_path(tmp_path: Path, capsys) -> None:
    path = tmp_path / "evidence.sqlite"

    assert main(["analytical-store", "status", "evidence-intelligence"]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "unavailable"
    assert main(["analytical-store", "bootstrap", "evidence-intelligence", "--path", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "schema_only"
    assert main(["analytical-store", "status", "evidence-intelligence", "--path", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["analytical_record_count"] == 0
