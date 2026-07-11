from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from hunter.automation.exceptions import AutomationLifecycleError
from hunter.automation.models import AutomationRun, AutomationRunStatus

TRANSITIONS: dict[AutomationRunStatus, set[AutomationRunStatus]] = {
    "scheduled": {"claimed", "skipped", "cancelled", "blocked"},
    "claimed": {"running", "cancelled", "blocked"},
    "running": {"succeeded", "partial", "failed", "cancelled"},
    "succeeded": set(),
    "partial": set(),
    "failed": set(),
    "cancelled": set(),
    "skipped": set(),
    "blocked": set(),
}


def transition(run: AutomationRun, status: AutomationRunStatus, *, at: datetime, error: str | None = None, warning: str | None = None) -> AutomationRun:
    if status not in TRANSITIONS[run.status]:
        msg = f"Invalid automation transition: {run.status} -> {status}"
        raise AutomationLifecycleError(msg)
    started_at = at if status == "running" and run.started_at is None else run.started_at
    finished_at = at if status in {"succeeded", "partial", "failed", "cancelled", "skipped", "blocked"} else run.finished_at
    return replace(
        run,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        error_summary=error or run.error_summary,
        warning_summary=warning or run.warning_summary,
    )
