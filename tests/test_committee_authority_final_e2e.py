from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.committee import command as committee_command
from hunter.committee.models import CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.committee.sql_output import GenericSQLCommitteeOutput
from hunter.dashboard.data import DashboardDataProvider
from hunter.execution.hashing import stable_digest
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


def _snapshot(now: datetime, project_id: str, signal: float) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot:committee-e2e:{project_id}",
        created_at=now - timedelta(minutes=30),
        effective_at=now - timedelta(minutes=30),
        snapshot_type="committee-input",
        target_id=project_id,
        record_ids=(f"evidence:{project_id}",),
        payload={"signal": signal},
        metadata={
            "authority_class": "production-authoritative",
            "project_id": project_id,
            "entity_id": f"entity:{project_id}",
            "representation_id": f"ethereum:0x{project_id}",
            "chain_id": "eip155:1",
            "lineage_id": f"lineage:{project_id}",
            "revision_id": "revision:1",
            "lifecycle_state": "active",
        },
    )


def _manifest(now: datetime, projects: tuple[str, ...] = ("alpha",), *, duplicate: bool = False) -> dict[str, object]:
    items = [
        {
            "project_id": project_id,
            "effective_at": now.isoformat(),
            "identity": {
                "project_id": project_id,
                "entity_id": f"entity:{project_id}",
                "representation_id": f"ethereum:0x{project_id}",
                "chain_id": "eip155:1",
            },
            "snapshot_ids": [f"snapshot:committee-e2e:{project_id}"],
        }
        for project_id in projects
    ]
    if duplicate:
        items.append(dict(items[0]))
    return {"inputs": items}


def _prepare_runtime(root: Path, now: datetime, projects: tuple[str, ...] = ("alpha", "beta")) -> Path:
    database = root / "data" / "data_ops.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database)
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        snapshots = RepositoryFactory(session).snapshots()
        for index, project_id in enumerate(projects, start=1):
            snapshots.save(_snapshot(now, project_id, signal=1.0 - (index * 0.1)))
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


def test_installed_cli_persists_ranked_output_consumed_read_only_by_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    projects = ("alpha", "beta")
    root = tmp_path / "application-root"
    database = _prepare_runtime(root, now, projects)
    manifest_path = tmp_path / "committee-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(now, projects)), encoding="utf-8")
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
        assert len(assessments) == 2
        assert len(champions) == 1
        assert tuple(sorted(item.rank for item in assessments)) == (1, 2)
        assert any(record.status == "succeeded" for record in runs)

        before = _record_count(database)
        dashboard = DashboardDataProvider(repositories).build(generated_at=now)
        after = _record_count(database)
        assert after == before

        committee = next(panel for panel in dashboard.panels if panel.panel_id == "investment-committee")
        assert len(committee.rows) == 2
        rows_by_id = {row.row_id: row for row in committee.rows}
        assert set(rows_by_id) == {assessment.id for assessment in assessments}
        for assessment in assessments:
            row = rows_by_id[assessment.id]
            assert row.values["project"] == assessment.project_id
            assert row.values["decision"] == assessment.decision
            assert row.values["confidence"] == assessment.committee_confidence
            assert row.values["rank"] == assessment.rank
            assert row.values["source_record_ids"] == ",".join(assessment.source_record_ids)
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
    database = _prepare_runtime(root, now, ("alpha",))
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


def test_post_evaluation_persistence_failure_rolls_back_staged_output_and_persists_failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    projects = ("alpha", "beta")
    root = tmp_path / "application-root"
    database = _prepare_runtime(root, now, projects)
    manifest = _manifest(now, projects)
    manifest_path = tmp_path / "persistence-failure-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(root.resolve()))

    original_persist_cycle = GenericSQLCommitteeOutput.persist_cycle

    def persist_then_fail(
        self: GenericSQLCommitteeOutput,
        champion: CycleChampionSnapshot,
        assessments: tuple[InvestmentCommitteeAssessment, ...],
    ) -> None:
        original_persist_cycle(self, champion, assessments)
        raise RuntimeError("forced post-evaluation persistence failure")

    monkeypatch.setattr(GenericSQLCommitteeOutput, "persist_cycle", persist_then_fail)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="forced post-evaluation persistence failure"):
            committee_command.main([str(manifest_path)])

    manifest_fingerprint = stable_digest("committee-authority-manifest", manifest, schema_version="v1")
    input_ids = sorted(f"snapshot:committee-e2e:{project_id}" for project_id in projects)
    input_fingerprint = stable_digest("committee-authority-inputs", input_ids, schema_version="v1")
    attempted_run_id = f"pipeline-run:committee-authority:{manifest_fingerprint}"

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
        failed = runs[0]
        assert failed.id == f"{attempted_run_id}:failed"
        assert failed.status == "failed"
        assert failed.parent_run_id == attempted_run_id
        assert failed.configuration_fingerprint == manifest_fingerprint
        assert failed.input_fingerprint == input_fingerprint
        assert failed.metadata["manifest_fingerprint"] == manifest_fingerprint
        assert "forced post-evaluation persistence failure" in str(failed.metadata["error_summary"])
    finally:
        session.close()
        engine.dispose()
