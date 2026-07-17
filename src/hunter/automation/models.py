from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

from hunter.execution.canonicalization import normalize
from hunter.intelligence.fusion.models import FusionTarget

AutomationScheduleType = Literal["hourly", "every_6_hours", "daily", "weekly", "cron", "once"]
AutomationRunStatus = Literal[
    "scheduled", "claimed", "running", "succeeded", "partial", "failed", "cancelled", "skipped", "blocked"
]
AutomationJobKind = Literal[
    "ingest_project_data",
    "current_state_pipeline",
    "selected_intelligence",
    "fusion",
    "opportunity_timing",
    "generate_reports",
    "evaluate_alerts",
    "historical_replay",
    "backtest",
    "real_market_validation",
]


@dataclass(frozen=True)
class AutomationSchedule:
    schedule_type: AutomationScheduleType
    expression: str | None = None
    run_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.schedule_type == "cron" and not self.expression:
            msg = "cron schedules require an expression"
            raise ValueError(msg)
        if self.schedule_type == "once":
            if self.run_at is None:
                msg = "one-time schedules require run_at"
                raise ValueError(msg)
            _aware("run_at", self.run_at)


@dataclass(frozen=True)
class TargetSelection:
    target_type: str
    target_id: str

    def __post_init__(self) -> None:
        _text("target_type", self.target_type)
        _text("target_id", self.target_id)


@dataclass(frozen=True)
class PipelineOptions:
    run_intelligence: bool = True
    run_fusion: bool = False
    run_opportunity_timing: bool = False
    run_investment_committee: bool = False
    selected_engines: tuple[str, ...] = ()
    generate_reports: bool = False
    evaluate_alerts: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_engines", tuple(str(item) for item in self.selected_engines))


@dataclass(frozen=True)
class AsOfPolicy:
    mode: str = "current"
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _aware("as_of", self.as_of))
        if self.mode in {"replay", "backtest"} and self.as_of is None:
            msg = "replay and backtest jobs require explicit as_of"
            raise ValueError(msg)


@dataclass(frozen=True)
class ConcurrencyPolicy:
    prevent_overlapping: bool = True
    scope: str = "job_target"


@dataclass(frozen=True)
class AutomationJob:
    job_id: str
    name: str
    enabled: bool
    schedule: AutomationSchedule
    timezone: str
    target: TargetSelection
    run_type: str
    pipeline_options: PipelineOptions = field(default_factory=PipelineOptions)
    persistence_policy: str = "atomic"
    as_of_policy: AsOfPolicy = field(default_factory=AsOfPolicy)
    timeout_seconds: int | None = None
    concurrency_policy: ConcurrencyPolicy = field(default_factory=ConcurrencyPolicy)
    job_kind: AutomationJobKind = "current_state_pipeline"
    metadata: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("job_id", "name", "timezone", "run_type", "persistence_policy", "job_kind"):
            _text(name, getattr(self, name))
        ZoneInfo(self.timezone)
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        normalize(dict(self.metadata))
        object.__setattr__(
            self, "metadata", MappingProxyType({str(key): value for key, value in self.metadata.items()})
        )

    def lock_key(self) -> str:
        return f"{self.job_id}:{self.target.target_type}:{self.target.target_id}"


@dataclass(frozen=True)
class AutomationEvent:
    event_type: str
    job_id: str
    automation_run_id: str | None
    at: datetime
    detail: str = ""

    def __post_init__(self) -> None:
        _text("event_type", self.event_type)
        _text("job_id", self.job_id)
        object.__setattr__(self, "at", _aware("at", self.at))


@dataclass(frozen=True)
class AutomationRun:
    automation_run_id: str
    job_id: str
    scheduled_for: datetime
    status: AutomationRunStatus
    pipeline_run_id: str | None = None
    operational_attempt_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    warning_summary: str | None = None
    metadata: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("automation_run_id", self.automation_run_id)
        _text("job_id", self.job_id)
        object.__setattr__(self, "scheduled_for", _aware("scheduled_for", self.scheduled_for))
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware(name, value))
        normalize(dict(self.metadata))
        object.__setattr__(
            self, "metadata", MappingProxyType({str(key): value for key, value in self.metadata.items()})
        )


@dataclass(frozen=True)
class AutomationReplayContext:
    mode: str
    as_of: datetime | None

    def __post_init__(self) -> None:
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _aware("as_of", self.as_of))
        if self.mode in {"replay", "backtest"} and self.as_of is None:
            msg = "replay and backtest execution requires explicit as_of"
            raise ValueError(msg)


@dataclass(frozen=True)
class AutomationPipelinePlan:
    job_id: str
    job_kind: AutomationJobKind
    target: FusionTarget
    run_type: str
    selected_engines: tuple[str, ...]
    run_intelligence: bool
    run_fusion: bool
    run_opportunity_timing: bool
    run_investment_committee: bool
    persistence_policy: str
    replay: AutomationReplayContext

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_engines", tuple(sorted(str(item) for item in self.selected_engines)))


def next_scheduled_at(schedule: AutomationSchedule, *, after: datetime, timezone: str) -> datetime:
    local = _aware("after", after).astimezone(ZoneInfo(timezone))
    if schedule.schedule_type == "once":
        assert schedule.run_at is not None
        return schedule.run_at.astimezone(ZoneInfo(timezone))
    if schedule.schedule_type == "hourly":
        return _ceil(local + timedelta(hours=1), "hour")
    if schedule.schedule_type == "every_6_hours":
        candidate = _ceil(local + timedelta(hours=1), "hour")
        while candidate.hour % 6 != 0:
            candidate += timedelta(hours=1)
        return candidate
    if schedule.schedule_type == "daily":
        return _ceil(local + timedelta(days=1), "day")
    if schedule.schedule_type == "weekly":
        return _ceil(local + timedelta(days=7), "day")
    assert schedule.expression is not None
    return _next_cron(schedule.expression, local)


def is_due(schedule: AutomationSchedule, *, at: datetime, timezone: str) -> bool:
    local = _aware("at", at).astimezone(ZoneInfo(timezone))
    if schedule.schedule_type == "once":
        assert schedule.run_at is not None
        return schedule.run_at <= at
    if schedule.schedule_type == "hourly":
        return local.minute == 0
    if schedule.schedule_type == "every_6_hours":
        return local.minute == 0 and local.hour % 6 == 0
    if schedule.schedule_type == "daily":
        return local.hour == 0 and local.minute == 0
    if schedule.schedule_type == "weekly":
        return local.weekday() == 0 and local.hour == 0 and local.minute == 0
    assert schedule.expression is not None
    parts = schedule.expression.split()
    if len(parts) != 5:
        msg = "cron expression must contain five fields"
        raise ValueError(msg)
    minute, hour, day, month, weekday = parts
    return (
        _cron_match(local.minute, minute, 0, 59)
        and _cron_match(local.hour, hour, 0, 23)
        and _cron_match(local.day, day, 1, 31)
        and _cron_match(local.month, month, 1, 12)
        and _cron_match(local.weekday(), weekday, 0, 6)
    )


def _next_cron(expression: str, after: datetime) -> datetime:
    parts = expression.split()
    if len(parts) != 5:
        msg = "cron expression must contain five fields"
        raise ValueError(msg)
    minute, hour, day, month, weekday = parts
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            _cron_match(candidate.minute, minute, 0, 59)
            and _cron_match(candidate.hour, hour, 0, 23)
            and _cron_match(candidate.day, day, 1, 31)
            and _cron_match(candidate.month, month, 1, 12)
            and _cron_match(candidate.weekday(), weekday, 0, 6)
        ):
            return candidate
        candidate += timedelta(minutes=1)
    msg = "cron expression did not match within one year"
    raise ValueError(msg)


def _cron_match(value: int, expression: str, minimum: int, maximum: int) -> bool:
    if expression == "*":
        return True
    if expression.startswith("*/"):
        step = int(expression[2:])
        return (value - minimum) % step == 0
    return value == int(expression) and minimum <= value <= maximum


def _ceil(value: datetime, unit: str) -> datetime:
    if unit == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)
