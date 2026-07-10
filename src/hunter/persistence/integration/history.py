from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hunter.persistence.models import QueryFilter, QuerySpec
from hunter.persistence.records import IntelligenceRecord, OperationalAttemptRecord, PipelineRunRecord, SnapshotRecord


class HistoryRepositories(Protocol):
    def pipeline_runs(self): ...

    def operational_attempts(self): ...

    def intelligence(self): ...

    def snapshots(self): ...


@dataclass(frozen=True)
class PipelineHistory:
    repositories: HistoryRepositories

    def run_history(self, pipeline_run_id: str) -> tuple[PipelineRunRecord, ...]:
        return self.repositories.pipeline_runs().query(
            QuerySpec(record_kind="pipeline-run", filters=(QueryFilter("id", pipeline_run_id),))
        )

    def attempt_history(self, pipeline_run_id: str) -> tuple[OperationalAttemptRecord, ...]:
        records = self.repositories.operational_attempts().query(
            QuerySpec(record_kind="operational-attempt", filters=(QueryFilter("run_id", pipeline_run_id),))
        )
        return tuple(sorted(records, key=lambda item: (item.attempt_number, _state_order(item.status))))

    def target_history(self, target_id: str) -> tuple[PipelineRunRecord, ...]:
        return self.repositories.pipeline_runs().query(
            QuerySpec(record_kind="pipeline-run", filters=(QueryFilter("target_id", target_id),))
        )

    def engine_history(self, engine_id: str) -> tuple[IntelligenceRecord, ...]:
        return self.repositories.intelligence().query(
            QuerySpec(record_kind="intelligence", filters=(QueryFilter("engine_id", engine_id),))
        )

    def effective_time_history(self, *, target_id: str, as_of: datetime) -> tuple[PipelineRunRecord, ...]:
        return tuple(record for record in self.target_history(target_id) if record.effective_at <= as_of)

    def artifact_history(self, pipeline_run_id: str) -> tuple[IntelligenceRecord, ...]:
        return self.repositories.intelligence().query(
            QuerySpec(record_kind="intelligence", filters=(QueryFilter("pipeline_run_id", pipeline_run_id),))
        )

    def snapshot_history(self, target_id: str) -> tuple[SnapshotRecord, ...]:
        return self.repositories.snapshots().query(
            QuerySpec(record_kind="snapshot", filters=(QueryFilter("target_id", target_id),))
        )


def _state_order(status: str) -> int:
    return {
        "pending": 0,
        "running": 1,
        "failed": 2,
        "partial": 2,
        "cancelled": 2,
        "succeeded": 2,
    }.get(status, 99)
