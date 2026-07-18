from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hunter.cli import main
from hunter.dashboard_api import SCHEMA_VERSION, build_dashboard_api, render_dashboard_api
from hunter.operational_status import CommandResult, build_status

NOW = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def test_dashboard_api_output_is_deterministic(tmp_path: Path) -> None:
    _runtime(tmp_path)

    first = render_dashboard_api(build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running")))
    second = render_dashboard_api(build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running")))

    assert first == second


def test_dashboard_api_schema_is_stable(tmp_path: Path) -> None:
    _runtime(tmp_path)

    payload = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert tuple(payload) == (
        "schema_version",
        "generated_at",
        "system",
        "scheduler",
        "automation",
        "jobs",
        "providers",
        "discovery",
        "validation",
        "predictions",
        "corpus",
        "database",
        "logs",
        "health",
    )
    assert payload["schema_version"] == SCHEMA_VERSION
    assert tuple(payload["system"]) == (
        "branch",
        "commit",
        "platform",
        "python_version",
        "repository_path",
        "runtime_path",
        "version",
    )
    assert tuple(payload["corpus"]) == ("last_update", "operational", "validation")
    assert tuple(payload["corpus"]["operational"]) == (
        "authority_classification",
        "error",
        "last_update",
        "path",
        "read_only",
        "record_count",
        "status",
    )
    assert tuple(payload["predictions"]) == ("closed", "due", "open", "canonical_evaluation")
    assert payload["predictions"]["canonical_evaluation"]["projection_version"] == (
        "canonical-prediction-evaluation-dashboard.v1"
    )
    assert payload["health"]["analytical_stores"]["projection_version"] == "analytical-store-readiness.v1"
    assert len(payload["health"]["analytical_stores"]["stores"]) == 7


def test_dashboard_api_missing_runtime_is_degraded(tmp_path: Path) -> None:
    payload = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("stopped"))

    assert payload["health"]["state"] == "DEGRADED"
    assert "scheduler is not running" in payload["health"]["explanation"]
    assert "operational database is unreachable" in payload["health"]["explanation"]
    assert payload["corpus"]["operational"] == {
        "authority_classification": "operational-only",
        "error": None,
        "last_update": None,
        "path": str(tmp_path / "data" / "data_ops" / "runs.jsonl"),
        "read_only": True,
        "record_count": None,
        "status": "unavailable",
    }


def test_dashboard_api_degraded_runtime(tmp_path: Path) -> None:
    _runtime(tmp_path, database=False)

    payload = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert payload["health"]["state"] == "DEGRADED"
    assert payload["database"]["repositories"]["operational"] is False


def test_dashboard_api_healthy_runtime(tmp_path: Path) -> None:
    _runtime(tmp_path)

    payload = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert payload["health"]["state"] == "HEALTHY"
    assert payload["jobs"][0]["enabled"] is True
    assert payload["corpus"]["operational"]["status"] == "available"
    assert payload["corpus"]["operational"]["authority_classification"] == "operational-only"
    assert payload["corpus"]["operational"]["read_only"] is True


def test_dashboard_api_distinguishes_empty_and_failed_corpus_sources(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path)
    (tmp_path / "data" / "operational_corpus" / "executions.jsonl").write_text("", encoding="utf-8")

    empty = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert empty["corpus"]["operational"]["status"] == "empty"
    assert empty["corpus"]["operational"]["record_count"] == 0
    assert empty["corpus"]["operational"]["error"] is None

    failed_status = build_status(root=tmp_path, now=NOW, command_runner=_runner("running"))
    failed_status["corpus"]["operational_corpus_records"] = None
    monkeypatch.setattr("hunter.dashboard_api.build_status", lambda **kwargs: failed_status)

    failed = build_dashboard_api(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert failed["corpus"]["operational"]["status"] == "error"
    assert failed["corpus"]["operational"]["record_count"] is None
    assert failed["corpus"]["operational"]["error"] == "source is present but its record count is unavailable"


def test_dashboard_api_cli_pretty(tmp_path: Path, monkeypatch, capsys) -> None:
    _runtime(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hunter.operational_status._run_command", _runner("running"))

    assert main(["dashboard-api", "--pretty"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert "\n  " in output


def _runner(state: str):
    def run(command, cwd: Path) -> CommandResult:
        if command[:2] == ("git", "rev-parse") and command[-1] == "--abbrev-ref":
            return CommandResult(0, "main\n", "")
        if command[:2] == ("git", "rev-parse"):
            return CommandResult(0, "abc123\n", "")
        if command[:2] == ("launchctl", "print"):
            if state == "running":
                return CommandResult(0, "state = running\npid = 123\n", "")
            return CommandResult(0, "state = not running\n", "")
        if command[:2] == ("ps", "-p"):
            return CommandResult(0, "01:02 python hunter automation start\n", "")
        return CommandResult(1, "", "")

    return run


def _runtime(tmp_path: Path, *, database: bool = True, failed_job: bool = False) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "automation.yaml").write_text(
        """
enabled: true
timezone: UTC
polling_interval_seconds: 60
jobs:
- job_id: job-a
  name: Job A
  enabled: true
  job_kind: current_state_pipeline
  schedule:
    type: daily
  timezone: UTC
  target:
    type: project
    id: project-a
  run_type: scheduled
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "data" / "historical_validation").mkdir(parents=True)
    (tmp_path / "data" / "historical_validation" / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "data" / "acquisition").mkdir(parents=True)
    (tmp_path / "data" / "acquisition" / "checkpoints.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "data" / "operational_corpus").mkdir(parents=True)
    (tmp_path / "data" / "operational_corpus" / "executions.jsonl").write_text(
        json.dumps({"execution_status": "succeeded", "finished_at": NOW.isoformat(), "target_id": "project-a"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "operational_corpus" / "predictions.jsonl").write_text(
        json.dumps(
            {
                "prediction_id": "prediction-a",
                "status": "open",
                "evaluation_horizon_at": "2026-01-04T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "operational_corpus" / "validation_samples.jsonl").write_text("", encoding="utf-8")
    if database:
        _database(tmp_path / "data" / "data_ops.sqlite", failed_job=failed_job)


def _database(path: Path, *, failed_job: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            create table persistence_records (
                id text primary key,
                record_type text,
                schema_version text,
                created_at text,
                effective_at text,
                canonical_hash text,
                payload text,
                deleted_at text
            )
            """
        )
        if failed_job:
            connection.execute(
                "insert into persistence_records values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "run-a",
                    "automation-run",
                    "v1",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    "hash",
                    json.dumps(
                        {
                            "job_id": "job-a",
                            "status": "failed",
                            "scheduled_for": NOW.isoformat(),
                            "finished_at": NOW.isoformat(),
                        }
                    ),
                    None,
                ),
            )
