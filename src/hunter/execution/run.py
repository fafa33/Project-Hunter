from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from hunter.execution.canonicalization import normalize
from hunter.execution.clock import Clock, SystemClock
from hunter.execution.hashing import stable_identifier

RunType = Literal["live", "scheduled", "manual", "replay", "backtest", "test"]
RunStatus = Literal["created", "running", "succeeded", "failed"]

PIPELINE_RUN_IDENTITY_VERSION = "pipeline-run-v1"


@dataclass(frozen=True)
class PipelineRun:
    run_id: str
    run_type: RunType
    target_id: str
    target_type: str
    configuration_fingerprint: str
    input_fingerprint: str
    engine_manifest_fingerprint: str
    requested_at: datetime
    effective_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: RunStatus = "created"
    parent_run_id: str | None = None
    replay_of_run_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        run_type: RunType,
        target_id: str,
        target_type: str,
        configuration_fingerprint: str,
        input_fingerprint: str,
        engine_manifest_fingerprint: str,
        effective_at: datetime,
        requested_at: datetime | None = None,
        parent_run_id: str | None = None,
        replay_of_run_id: str | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        status: RunStatus = "created",
        clock: Clock | None = None,
        unique_operational_run: bool = False,
    ) -> PipelineRun:
        selected_clock = clock or SystemClock()
        requested = requested_at or selected_clock.now()
        identity_payload = {
            "run_type": run_type,
            "target_id": target_id,
            "target_type": target_type,
            "configuration_fingerprint": configuration_fingerprint,
            "input_fingerprint": input_fingerprint,
            "engine_manifest_fingerprint": engine_manifest_fingerprint,
            "effective_at": effective_at,
            "parent_run_id": parent_run_id,
            "replay_of_run_id": replay_of_run_id,
            "metadata": metadata or {},
        }
        if unique_operational_run:
            identity_payload["requested_at"] = requested
        return cls(
            run_id=stable_identifier(
                "pipeline-run",
                identity_payload,
                schema_version=PIPELINE_RUN_IDENTITY_VERSION,
            ),
            run_type=run_type,
            target_id=target_id,
            target_type=target_type,
            configuration_fingerprint=configuration_fingerprint,
            input_fingerprint=input_fingerprint,
            engine_manifest_fingerprint=engine_manifest_fingerprint,
            requested_at=requested,
            effective_at=effective_at,
            status=status,
            parent_run_id=parent_run_id,
            replay_of_run_id=replay_of_run_id,
            metadata=dict(metadata or {}),
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "run_type": self.run_type,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "configuration_fingerprint": self.configuration_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "engine_manifest_fingerprint": self.engine_manifest_fingerprint,
            "effective_at": normalize(self.effective_at),
            "parent_run_id": self.parent_run_id,
            "replay_of_run_id": self.replay_of_run_id,
            "metadata": self.metadata,
        }
