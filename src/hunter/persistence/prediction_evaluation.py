from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hunter.persistence.models import AnalyticalReplaySpec, AuthorizedAnalyticalWrite
from hunter.persistence.records import AnalyticalRecord
from hunter.persistence.sql import SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.base import PersistenceRecordModel
from hunter.persistence.sql.exceptions import AnalyticalWriteAuthorizationError
from hunter.persistence.sql.repositories.records import SQLAnalyticalRecordRepository

POLICY_TYPE = "canonical.prediction-evaluation-policy"
PUBLICATION_TYPE = "canonical.prediction-publication"
EVALUATION_TYPE = "canonical.prediction-evaluation"
ACCURACY_TYPE = "canonical.prediction-accuracy-snapshot"
CALIBRATION_TYPE = "canonical.prediction-calibration-snapshot"
PredictionSemanticType = Literal[
    "canonical.prediction-evaluation-policy",
    "canonical.prediction-publication",
    "canonical.prediction-evaluation",
    "canonical.prediction-accuracy-snapshot",
    "canonical.prediction-calibration-snapshot",
]
SEMANTIC_TYPES = frozenset({POLICY_TYPE, PUBLICATION_TYPE, EVALUATION_TYPE, ACCURACY_TYPE, CALIBRATION_TYPE})
RECORD_PREFIX = "prediction-evaluation"
SCHEMA_VERSION = "canonical-prediction-evaluation-v1"
DEFAULT_CONFIG = Path("configs/prediction_evaluation_persistence.yaml")


@dataclass(frozen=True, slots=True)
class PredictionEvaluationPersistenceConfig:
    enabled: bool
    database_path: Path


class PredictionEvaluationRepository(SQLAnalyticalRecordRepository):
    def persist(self, plan: AuthorizedAnalyticalWrite) -> AnalyticalRecord:
        self._require_canonical(plan.record)
        return super().persist(plan)

    def persist_many(self, plans: tuple[AuthorizedAnalyticalWrite, ...]) -> tuple[AnalyticalRecord, ...]:
        return tuple(self.persist(plan) for plan in plans)

    def load(self, identity: str) -> AnalyticalRecord | None:
        record = super().load(identity)
        if record is not None:
            self._require_canonical(record)
        return record

    def by_semantic_type(self, semantic_type: PredictionSemanticType) -> tuple[AnalyticalRecord, ...]:
        if semantic_type not in SEMANTIC_TYPES:
            raise ValueError("unsupported prediction-evaluation semantic type")
        records = tuple(record for record in self._all_records() if record.semantic_type == semantic_type)
        return tuple(sorted(records, key=lambda item: (item.effective_at, item.recorded_at, item.id)))

    def target_history(
        self, semantic_type: PredictionSemanticType, target_identity: str
    ) -> tuple[AnalyticalRecord, ...]:
        return tuple(
            record
            for record in self.by_semantic_type(semantic_type)
            if record.payload.get("target_identity") == target_identity
        )

    def current(self, semantic_type: PredictionSemanticType, target_identity: str) -> AnalyticalRecord | None:
        records = self.target_history(semantic_type, target_identity)
        superseded = {record.supersedes_id for record in records if record.supersedes_id}
        current = [record for record in records if record.id not in superseded]
        current.sort(key=lambda item: (item.recorded_at, item.id), reverse=True)
        return current[0] if current else None

    def strict_known_target(
        self,
        semantic_type: PredictionSemanticType,
        target_identity: str,
        *,
        effective_as_of,
        known_by,
    ) -> AnalyticalRecord | None:
        logical = f"{semantic_type}:{target_identity}"
        return super().strict_known(AnalyticalReplaySpec(logical, effective_as_of, known_by))

    @staticmethod
    def _require_canonical(record: AnalyticalRecord) -> None:
        if record.semantic_type not in SEMANTIC_TYPES:
            raise AnalyticalWriteAuthorizationError("prediction-evaluation store rejects foreign semantic types")
        if not record.id.startswith(f"{RECORD_PREFIX}:"):
            raise AnalyticalWriteAuthorizationError("prediction-evaluation identity namespace is required")
        if record.payload.get("authority_classification") != "canonical-evaluation":
            raise AnalyticalWriteAuthorizationError("canonical evaluation classification is required")


class PredictionEvaluationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError("prediction-evaluation store must be explicitly bootstrapped")
        self._sessions = SessionFactory(create_sqlite_engine(self.path))

    @classmethod
    def from_config(cls, config: PredictionEvaluationPersistenceConfig) -> PredictionEvaluationStore:
        if not config.enabled:
            raise RuntimeError("prediction-evaluation persistence is disabled")
        return cls(config.database_path)

    @contextmanager
    def repository(self) -> Iterator[PredictionEvaluationRepository]:
        session: Session = self._sessions.create()
        try:
            yield PredictionEvaluationRepository(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read_repository(self) -> Iterator[PredictionEvaluationRepository]:
        """Expose canonical queries without creating a write transaction."""
        session: Session = self._sessions.create()
        try:
            yield PredictionEvaluationRepository(session)
        finally:
            session.rollback()
            session.close()


def bootstrap_prediction_evaluation_store(path: str | Path) -> Path:
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(store_path)
    create_schema(engine)
    engine.dispose()
    return store_path


def prediction_evaluation_store_status(path: str | Path) -> str:
    store_path = Path(path)
    if not store_path.exists():
        return "absent"
    try:
        engine = create_sqlite_engine(store_path)
        with Session(engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(PersistenceRecordModel)
                .where(PersistenceRecordModel.record_type == "analytical-record")
            )
        engine.dispose()
    except Exception:
        return "unreachable"
    return "populated" if count else "schema-only"


def load_prediction_evaluation_config(
    path: str | Path = DEFAULT_CONFIG,
) -> PredictionEvaluationPersistenceConfig:
    payload = yaml.safe_load(Path(path).read_text()) or {}
    section = payload.get("prediction_evaluation_persistence", {})
    if not isinstance(section, dict):
        raise ValueError("prediction_evaluation_persistence must be a mapping")
    enabled = section.get("enabled", False)
    database_path = section.get("database_path")
    if not isinstance(enabled, bool):
        raise ValueError("prediction_evaluation_persistence.enabled must be boolean")
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError("prediction_evaluation_persistence.database_path is required")
    return PredictionEvaluationPersistenceConfig(enabled, Path(database_path))
