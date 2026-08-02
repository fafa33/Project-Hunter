from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


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


def _operational_corpus_root() -> Path:
    application_root = os.environ.get("HUNTER_APPLICATION_ROOT")
    if application_root:
        return Path(application_root) / "data" / "operational_corpus"
    test_runtime_root = os.environ.get("HUNTER_TEST_RUNTIME_ROOT")
    if test_runtime_root:
        return Path(test_runtime_root) / "operational_corpus"
    return Path("data/operational_corpus")


@dataclass(frozen=True)
class OperationalCorpusSettings:
    enabled: bool = True
    root: Path = field(default_factory=_operational_corpus_root)
    filename: str = "executions.jsonl"
    prediction_filename: str = "predictions.jsonl"
    prediction_closure_filename: str = "prediction_closures.jsonl"
    prediction_state_filename: str = "prediction_state.json"
    outcome_filename: str = "outcomes.jsonl"
    validation_sample_filename: str = "validation_samples.jsonl"
    opportunity_events_filename: str = "opportunity_events.jsonl"
    opportunity_state_filename: str = "opportunities.json"
    readiness_filename: str = "readiness.json"


@dataclass(frozen=True)
class PipelinePersistenceSettings:
    enabled: bool = False
    backend: str = "sqlite"
    policy: PersistencePolicy = PersistencePolicy.ATOMIC
    artifacts: ArtifactPersistenceSettings = ArtifactPersistenceSettings()
    snapshots: SnapshotSettings = SnapshotSettings()
    history: HistorySettings = HistorySettings()
    operational_corpus: OperationalCorpusSettings = field(default_factory=OperationalCorpusSettings)
    strict_identity_validation: bool = True
    enforce_engine_manifest: bool = True
