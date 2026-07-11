from __future__ import annotations

from datetime import datetime

from hunter.automation.exceptions import AutomationReplaySafetyError
from hunter.automation.models import AutomationJob

SUPPORTED_JOB_KINDS = {
    "ingest_project_data",
    "current_state_pipeline",
    "selected_intelligence",
    "fusion",
    "opportunity_timing",
    "generate_reports",
    "evaluate_alerts",
    "historical_replay",
    "backtest",
}


def validate_job_for_execution(job: AutomationJob) -> None:
    if job.job_kind not in SUPPORTED_JOB_KINDS:
        msg = f"Unsupported automation job kind: {job.job_kind}"
        raise ValueError(msg)
    if job.run_type in {"replay", "backtest"} or job.job_kind in {"historical_replay", "backtest"}:
        if job.as_of_policy.as_of is None:
            msg = "Replay/backtest automation jobs require explicit timezone-aware as_of"
            raise AutomationReplaySafetyError(msg)


def job_as_of(job: AutomationJob, *, scheduled_for: datetime) -> datetime | None:
    if job.as_of_policy.mode in {"replay", "backtest"}:
        return job.as_of_policy.as_of
    if job.as_of_policy.mode == "scheduled_for":
        return scheduled_for
    return None
