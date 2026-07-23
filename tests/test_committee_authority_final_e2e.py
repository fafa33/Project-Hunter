from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.dashboard.data import DashboardDataProvider
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


def _snapshot(now: datetime) -> SnapshotRecord:
    return SnapshotRecord(
        id="snapshot:committee-e2e:alpha",
        created_at=now - timedelta(minutes=30),
        effective_at=now - timedelta(minutes=30),
        snapshot_type="committee-input",
        target_id="alpha",
        record_ids=("evidence:alpha",),
        payload={"signal": 0.9},
        metadata={
            "authority_class": "production-authoritative",
            "project_id": "alpha",
            "entity_id": "entity:alpha",
            "representation_id": "ethereum:0xalpha",
            "chain_id": "eip155:1",
            "lineage_id": "lineage:alpha",
            "revision_id": "revision:1",
            "lifecycle_state": "active",
        },
    )


def _manifest(now: datetime, *, duplicate: bool = False) -> dict[str, object]:
    item = {
        "project_id": "alpha",
        "effective_at": now.isoformat(),
        "identity": {
            "project_id": "alpha",
            "entity_id": "entity:alpha",
            "representation_id": "ethereum:0xalpha",
            "chain_id": "eip155:1",
        },
        "snapshot_ids": ["snapshot:committee-e2e:alpha"],
    }
    return {"inputs": [item, dict(item)] if duplicate else [item]}


def _prepare_runtime(root: Path, now: datetime) -> Path:
    database = root / "data" / "data_ops.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database)
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        RepositoryFactory(session).snapshots().save(_snapshot(now))
        session.commit()
    finally:
        session.close()
        engine.dispose()
    return database


def _record_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT COUNT(*) FROM persistence_records").fetchone()
    assert row is not None
    return int(row[0])


def _installed_hunter() -> str:
    executable = shutil.which("hunter")
    assert executable is not None, "installed hunter console script is not available on PATH"
    return executable


def _run_hunter(manifest_path: Path, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_installed_hunter(), "committee-authority", str(manifest_path)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_installed_cli_persists_canonical_output_consumed_read_only_by_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "application-root"
    database = _prepare_runtime(root, now)
    manifest_path = tmp_path / "committee-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(now)), encoding="utf-8")
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(root.resolve()))

    completed = _run_hunter(manifest_path, cwd=unrelated_cwd)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["persistence_database"] == str(database.resolve())

    engine = create_sqlite_engine(database)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        assessments = repositories.investment_committee_assessments().query(
            QuerySpec(record_kind="investment-committee-assessment")
        )
        champions = repositories.cycle_champion_snapshots().query(QuerySpec(record_kind="cycle-champion-snapshot"))
        runs = repositories.pipeline_runs().query(QuerySpec(record_kind="pipeline-run"))
        assert len(assessments) == 1
        assert len(champions) == 1
        assert assessments[0].source_record_ids == ("snapshot:committee-e2e:alpha",)
        assert any(record.status == "succeeded" for record in runs)

        before = _record_count(database)
        dashboard = DashboardDataProvider(repositories).build(generated_at=now)
        after = _record_count(database)
        assert after == before

        committee = next(panel for panel in dashboard.panels if panel.panel_id == "investment-committee")
        assert len(committee.rows) == 1
        assert committee.rows[0].row_id == assessments[0].id
        assert committee.rows[0].values["project"] == "alpha"
        assert committee.rows[0].values["decision"] == assessments[0].decision
        assert committee.rows[0].values["confidence"] == assessments[0].committee_confidence
        assert committee.rows[0].values["source_record_ids"] == "snapshot:committee-e2e:alpha"
    finally:
        session.close()
        engine.dispose()

    assert not (root / "data" / "committee" / "runtime" / "investment_committee.sqlite").exists()
    assert not (unrelated_cwd / "data" / "data_ops.sqlite").exists()


def test_failed_cli_retries_preserve_original_error_and_single_durable_failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    root = tmp_path / "application-root"
    database = _prepare_runtime(root, now)
    manifest_path = tmp_path / "duplicate-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(now, duplicate=True)), encoding="utf-8")
    unrelated_cwd = tmp_path / "unrelated-failure-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(root.resolve()))

    for _ in range(2):
        completed = _run_hunter(manifest_path, cwd=unrelated_cwd)
        assert completed.returncode != 0
        assert "duplicate project_id" in completed.stderr
        assert "PersistenceIdentityConflictError" not in completed.stderr

    engine = create_sqlite_engine(database)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        assert repositories.committee_votes().query(QuerySpec(record_kind="committee-vote")) == ()
        assert (
            repositories.investment_committee_assessments().query(
                QuerySpec(record_kind="investment-committee-assessment")
            )
            == ()
        )
        assert repositories.cycle_champion_snapshots().query(QuerySpec(record_kind="cycle-champion-snapshot")) == ()
        runs = repositories.pipeline_runs().query(QuerySpec(record_kind="pipeline-run"))
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].parent_run_id is not None
        assert "duplicate project_id" in str(runs[0].metadata["error_summary"])
    finally:
        session.close()
        engine.dispose()

    assert not (unrelated_cwd / "data" / "data_ops.sqlite").exists()
