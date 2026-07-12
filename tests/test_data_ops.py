from __future__ import annotations

from pathlib import Path

import yaml

from hunter.automation import load_automation_config
from hunter.data_ops import DATA_OPS_JOB_IDS, install_data_ops_jobs


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
