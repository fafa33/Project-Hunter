from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PersistencePolicy(StrEnum):
    ATOMIC = "atomic"
    RUN_DURABLE = "run_durable"


@dataclass(frozen=True)
class ArtifactPersistenceSettings:
    persist_evidence: bool = True
    persist_signals: bool = True
    persist_observations: bool = True
    persist_insights: bool = True
    persist_intelligence: bool = True


@dataclass(frozen=True)
class SnapshotSettings:
    create_on_success: bool = True
    create_on_partial: bool = True
    snapshot_type: str = "pipeline-run"


@dataclass(frozen=True)
class HistorySettings:
    enabled: bool = True


@dataclass(frozen=True)
class PipelinePersistenceSettings:
    enabled: bool = False
    backend: str = "sqlite"
    policy: PersistencePolicy = PersistencePolicy.ATOMIC
    artifacts: ArtifactPersistenceSettings = ArtifactPersistenceSettings()
    snapshots: SnapshotSettings = SnapshotSettings()
    history: HistorySettings = HistorySettings()
    strict_identity_validation: bool = True
    enforce_engine_manifest: bool = True
