from __future__ import annotations

from pathlib import Path

import yaml

from hunter.automation import load_automation_config
from hunter.data_ops import DATA_OPS_JOB_IDS, data_ops_status, install_data_ops_jobs, run_data_ops_now


def _empty_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "enabled": False,
                "timezone": "UTC",
                "polling_interval_seconds": 60,
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )


def test_data_ops_run_creates_sqlite_parent_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "run" / "configs" / "automation.yaml"
    _empty_config(config_path)

    assert run_data_ops_now(config_path) == ()
    assert (config_path.parent / "data" / "data_ops.sqlite").is_file()


def test_data_ops_status_creates_sqlite_parent_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "status" / "configs" / "automation.yaml"
    _empty_config(config_path)

    assert data_ops_status(config_path)["runs"] == 0
    assert (config_path.parent / "data" / "data_ops.sqlite").is_file()


def test_data_ops_install_is_idempotent_and_preserves_existing_jobs(tmp_path: Path) -> None:
    config_path = tmp_path / "automation.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "enabled": False,
                "timezone": "UTC",
                "polling_interval_seconds": 60,
                "jobs": [
                    {
                        "job_id": "existing-job",
                        "name": "Existing Job",
                        "enabled": True,
                        "job_kind": "current_state_pipeline",
                        "schedule": {"type": "daily"},
                        "timezone": "UTC",
                        "target": {"type": "project", "id": "project-a"},
                        "run_type": "scheduled",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first = install_data_ops_jobs(config_path)
    second = install_data_ops_jobs(config_path)
    config = load_automation_config(config_path)
    job_ids = tuple(job.job_id for job in config.jobs)

    assert first == DATA_OPS_JOB_IDS
    assert second == DATA_OPS_JOB_IDS
    assert job_ids.count("existing-job") == 1
    assert all(job_id in job_ids for job_id in DATA_OPS_JOB_IDS)
    assert len(job_ids) == len(set(job_ids))
    assert config.enabled


def test_data_ops_dependency_order_and_schedules(tmp_path: Path) -> None:
    config_path = tmp_path / "automation.yaml"
    install_data_ops_jobs(config_path)
    config = load_automation_config(config_path)
    by_id = {job.job_id: job for job in config.jobs}

    assert by_id["dataops-coingecko-market-sync"].schedule.schedule_type == "every_6_hours"
    assert by_id["dataops-defillama-protocol-sync"].schedule.schedule_type == "cron"
    assert by_id["dataops-defillama-protocol-sync"].schedule.expression == "0 */12 * * *"
    assert by_id["dataops-github-developer-sync"].schedule.schedule_type == "daily"
    assert by_id["dataops-market-validation-run"].metadata["depends_on"] == "dataops-scenario-refresh"
    assert by_id["dataops-committee-evaluation"].metadata["depends_on"] == "dataops-market-validation-run"
    assert tuple(job.job_id for job in config.jobs if job.job_id in DATA_OPS_JOB_IDS) == DATA_OPS_JOB_IDS


def test_data_ops_installer_satisfies_discovery_startup_guard(tmp_path: Path, monkeypatch) -> None:
    from hunter.automation import AutomationScheduler
    from hunter.cli import main
    from hunter.discovery.automation import DISCOVERY_JOB_IDS, require_discovery_automation

    path = tmp_path / "automation.yaml"
    install_data_ops_jobs(path)
    assert require_discovery_automation(path) == DISCOVERY_JOB_IDS
    calls = []
    monkeypatch.setattr(AutomationScheduler, "run_loop", lambda self, **kwargs: calls.append(kwargs))
    assert main(["--config", str(path), "automation", "start", "--max-iterations", "1"]) == 0
    assert calls == [{"max_iterations": 1}]
