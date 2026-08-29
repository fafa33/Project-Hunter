from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from hunter.cli import main
from hunter.operational_status import CommandResult, build_status, exit_code, render_json, render_text

NOW = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def test_status_reports_healthy_runtime(tmp_path: Path) -> None:
    _runtime(tmp_path)

    status = build_status(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert status["health"] == "HEALTHY"
    assert status["scheduler"]["running"] is True
    assert status["automation"]["jobs"][0]["state"] == "enabled"
    assert exit_code(status) == 0
    assert "HEALTHY" in render_text(status)


def test_status_reports_stopped_scheduler_as_degraded(tmp_path: Path) -> None:
    _runtime(tmp_path)

    status = build_status(root=tmp_path, now=NOW, command_runner=_runner("stopped"))

    assert status["health"] == "DEGRADED"
    assert status["scheduler"]["running"] is False
    assert exit_code(status) == 1


def test_status_reports_missing_database_as_degraded(tmp_path: Path) -> None:
    _runtime(tmp_path, database=False)

    status = build_status(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert status["health"] == "DEGRADED"
    assert status["database"]["operational_database_reachable"] is False
    assert exit_code(status) == 1


def test_status_reports_failed_job_as_failed(tmp_path: Path) -> None:
    _runtime(tmp_path, failed_job=True)

    status = build_status(root=tmp_path, now=NOW, command_runner=_runner("running"))

    assert status["health"] == "FAILED"
    assert status["automation"]["jobs"][0]["last_result"] == "failed"
    assert status["automation"]["jobs"][0]["failure_count"] == 1
    assert exit_code(status) == 2


def test_status_json_schema_is_deterministic(tmp_path: Path) -> None:
    _runtime(tmp_path)
    status = build_status(root=tmp_path, now=NOW, command_runner=_runner("running"))

    first = render_json(status)
    second = render_json(status)
    payload = json.loads(first)

    assert first == second
    assert tuple(payload) == (
        "analytical_stores",
        "automation",
        "corpus",
        "database",
        "discovery",
        "general",
        "health",
        "logs",
        "providers",
        "scheduler",
        "validation",
    )
    assert payload["analytical_stores"]["projection_version"] == "analytical-store-readiness.v1"
    assert len(payload["analytical_stores"]["stores"]) == 7


def test_status_cli_exit_codes_and_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    _runtime(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hunter.operational_status._run_command", _runner("running"))

    assert main(["status", "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["health"] == "HEALTHY"


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
