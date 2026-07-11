from __future__ import annotations

from datetime import datetime

from hunter.automation.exceptions import AutomationReplaySafetyError
from hunter.automation.models import AutomationJob, AutomationPipelinePlan, AutomationReplayContext
from hunter.intelligence.fusion.models import FusionTarget

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


def pipeline_plan_for_job(job: AutomationJob, *, scheduled_for: datetime) -> AutomationPipelinePlan:
    as_of = job_as_of(job, scheduled_for=scheduled_for)
    replay = AutomationReplayContext(mode=job.as_of_policy.mode, as_of=as_of)
    return AutomationPipelinePlan(
        job_id=job.job_id,
        job_kind=job.job_kind,
        target=FusionTarget(
            target_type=job.target.target_type,  # type: ignore[arg-type]
            target_id=job.target.target_id,
            metadata={"automation_job_id": job.job_id, "run_type": job.run_type},
        ),
        run_type=job.run_type,
        selected_engines=job.pipeline_options.selected_engines,
        run_intelligence=job.pipeline_options.run_intelligence,
        run_fusion=job.pipeline_options.run_fusion,
        run_opportunity_timing=job.pipeline_options.run_opportunity_timing,
        run_investment_committee=job.pipeline_options.run_investment_committee,
        persistence_policy=job.persistence_policy,
        replay=replay,
    )
