from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


def _snapshot(now: datetime, project_id: str, signal: float) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot:committee-vote-e2e:{project_id}",
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


def _manifest(now: datetime, projects: tuple[str, ...]) -> dict[str, object]:
    return {
        "inputs": [
            {
                "project_id": project_id,
                "effective_at": now.isoformat(),
                "identity": {
                    "project_id": project_id,
                    "entity_id": f"entity:{project_id}",
                    "representation_id": f"ethereum:0x{project_id}",
                    "chain_id": "eip155:1",
                },
                "snapshot_ids": [f"snapshot:committee-vote-e2e:{project_id}"],
            }
            for project_id in projects
        ]
    }


def _installed_hunter() -> str:
    executable = shutil.which("hunter")
    assert executable is not None, "installed hunter console script is not available on PATH"
    return executable


def test_installed_cli_loads_run_votes_assessments_and_champion_from_generic_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    projects = ("alpha", "beta")
    root = tmp_path / "application-root"
    database = root / "data" / "data_ops.sqlite"
    database.parent.mkdir(parents=True)

    engine = create_sqlite_engine(database)
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        snapshots = RepositoryFactory(session).snapshots()
        for index, project_id in enumerate(projects, start=1):
            snapshots.save(_snapshot(now, project_id, 1.0 - (index * 0.1)))
        session.commit()
    finally:
        session.close()
        engine.dispose()

    manifest_path = tmp_path / "committee-manifest.json"
    manifest_path.write_text(json.dumps(_manifest(now, projects)), encoding="utf-8")
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(root.resolve()))

    completed = subprocess.run(
        [_installed_hunter(), "committee-authority", str(manifest_path)],
        cwd=unrelated_cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    engine = create_sqlite_engine(database)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        runs = repositories.pipeline_runs().query(QuerySpec(record_kind="pipeline-run"))
        votes = repositories.committee_votes().query(QuerySpec(record_kind="committee-vote"))
        assessments = repositories.investment_committee_assessments().query(
            QuerySpec(record_kind="investment-committee-assessment")
        )
        champions = repositories.cycle_champion_snapshots().query(QuerySpec(record_kind="cycle-champion-snapshot"))

        assert len(runs) == 1
        assert runs[0].status == "succeeded"
        assert len(assessments) == len(projects)
        assert len(champions) == 1
        assert votes

        assessment_ids = {assessment.id for assessment in assessments}
        assessment_projects = {assessment.id: assessment.project_id for assessment in assessments}
        vote_ids = {vote.id for vote in votes}
        assert len(vote_ids) == len(votes)
        assert {vote.assessment_id for vote in votes} == assessment_ids
        assert all(vote.project_id == assessment_projects[vote.assessment_id] for vote in votes)
        assert all(set(assessment.vote_ids) <= vote_ids for assessment in assessments)
    finally:
        session.close()
        engine.dispose()

    assert not (root / "data" / "committee" / "runtime" / "investment_committee.sqlite").exists()
    assert not (unrelated_cwd / "data" / "data_ops.sqlite").exists()
