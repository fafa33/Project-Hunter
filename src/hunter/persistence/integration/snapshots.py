from __future__ import annotations

from datetime import datetime

from hunter.execution.hashing import stable_identifier
from hunter.execution.run import PipelineRun
from hunter.persistence.records import SnapshotRecord


def snapshot_for_run(
    run: PipelineRun,
    *,
    artifact_ids: tuple[str, ...],
    created_at: datetime,
    snapshot_type: str = "pipeline-run",
) -> SnapshotRecord:
    record_ids = tuple(sorted(set(artifact_ids)))
    payload = {
        "pipeline_run_id": run.run_id,
        "target_id": run.target_id,
        "target_type": run.target_type,
        "artifact_ids": record_ids,
        "artifact_count": len(record_ids),
    }
    snapshot_id = stable_identifier(
        "snapshot",
        {
            "snapshot_type": snapshot_type,
            "pipeline_run_id": run.run_id,
            "target_id": run.target_id,
            "effective_at": run.effective_at,
            "artifact_ids": record_ids,
        },
        schema_version="snapshot-v1",
    )
    return SnapshotRecord(
        id=snapshot_id,
        created_at=created_at,
        effective_at=run.effective_at,
        snapshot_type=snapshot_type,
        target_id=run.target_id,
        record_ids=record_ids,
        payload=payload,
        metadata={"pipeline_run_id": run.run_id},
    )
