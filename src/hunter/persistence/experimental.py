from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from sqlalchemy.orm import Session

from hunter.committee.models import InvestmentCommitteeAssessment
from hunter.execution.hashing import stable_identifier
from hunter.necessity.models import TechnologyNecessityAssessment
from hunter.patterns.models import PatternMatchingAssessment
from hunter.persistence.models import AuthorizedAnalyticalWrite
from hunter.persistence.records import AnalyticalRecord
from hunter.persistence.sql import SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import AnalyticalCorrectionConflictError, AnalyticalWriteAuthorizationError
from hunter.persistence.sql.repositories.records import SQLAnalyticalRecordRepository
from hunter.probability.models import ProbabilityAssessment

ExperimentalAssessment = (
    ProbabilityAssessment | PatternMatchingAssessment | TechnologyNecessityAssessment | InvestmentCommitteeAssessment
)
ExperimentalSemanticType = Literal[
    "experimental.probability-assessment",
    "experimental.pattern-assessment",
    "experimental.technology-necessity-assessment",
    "experimental.standalone-committee-assessment",
    "experimental.opportunity-metric-snapshot",
    "experimental.opportunity-assessment",
]

EXPERIMENTAL_SCHEMA_VERSION = "experimental-derived-reasoning-envelope-v1"
EXPERIMENTAL_RECORD_PREFIX = "experimental-derived"
DEFAULT_EXPERIMENTAL_CONFIG = Path("configs/experimental_persistence.yaml")
DERIVED_REASONING_SEMANTIC_TYPES: frozenset[str] = frozenset(
    {
        "experimental.probability-assessment",
        "experimental.pattern-assessment",
        "experimental.technology-necessity-assessment",
        "experimental.standalone-committee-assessment",
    }
)
ALLOWED_SEMANTIC_TYPES: frozenset[str] = frozenset(
    {
        *DERIVED_REASONING_SEMANTIC_TYPES,
        "experimental.opportunity-metric-snapshot",
        "experimental.opportunity-assessment",
    }
)


@dataclass(frozen=True, slots=True)
class ExperimentalPersistenceConfig:
    enabled: bool
    database_path: Path


@dataclass(frozen=True, slots=True)
class ExperimentalAuthorizationContext:
    recorded_at: datetime
    known_at: datetime | None
    known_time_limitation: str | None
    model_version: str
    configuration_version: str
    methodology_fingerprint: str
    source_versions: tuple[str, ...]
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))
        if self.known_at is not None:
            if self.known_at.tzinfo is None:
                raise ValueError("known_at must be timezone-aware")
            object.__setattr__(self, "known_at", self.known_at.astimezone(UTC))
        for name in ("model_version", "configuration_version", "methodology_fingerprint"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "source_versions", tuple(self.source_versions))
        object.__setattr__(self, "evidence_references", tuple(self.evidence_references))


class ExperimentalDerivedReasoningAuthorizationService:
    """Authorizes experimental envelopes without changing native engine behavior."""

    def authorize(
        self,
        assessment: ExperimentalAssessment,
        context: ExperimentalAuthorizationContext,
        *,
        predecessor: AnalyticalRecord | None = None,
        correction_reason: str | None = None,
    ) -> AuthorizedAnalyticalWrite:
        semantic_type, native_id, target_id, effective_at, source_ids, confidence, missing = _domain_fields(assessment)
        if len(source_ids) != len(context.source_versions):
            raise ValueError("source_versions must correspond one-to-one with native source_record_ids")
        logical_identity = f"{semantic_type}:{target_id}"
        if predecessor is not None:
            if predecessor.semantic_type != semantic_type or predecessor.logical_identity != logical_identity:
                raise AnalyticalCorrectionConflictError("correction predecessor belongs to another experimental output")
            if not correction_reason or not correction_reason.strip():
                raise ValueError("correction_reason is required")
        elif correction_reason is not None:
            raise ValueError("correction_reason requires an explicit predecessor")

        native_payload = cast(dict[str, Any], _plain(assessment))
        payload = {
            "authority_classification": "experimental",
            "configuration_version": context.configuration_version,
            "native_assessment": native_payload,
            "native_assessment_id": native_id,
            "target_id": target_id,
        }
        record_id = stable_identifier(
            EXPERIMENTAL_RECORD_PREFIX,
            {
                "semantic_type": semantic_type,
                "logical_identity": logical_identity,
                "native_assessment_id": native_id,
                "recorded_at": context.recorded_at,
                "payload": payload,
                "supersedes_id": predecessor.id if predecessor else None,
            },
            schema_version=EXPERIMENTAL_SCHEMA_VERSION,
        )
        record = AnalyticalRecord(
            id=record_id,
            schema_version=EXPERIMENTAL_SCHEMA_VERSION,
            created_at=context.recorded_at,
            effective_at=effective_at,
            logical_identity=logical_identity,
            semantic_type=semantic_type,
            known_at=context.known_at,
            known_time_limitation=context.known_time_limitation,
            model_version=context.model_version,
            methodology_fingerprint=context.methodology_fingerprint,
            source_record_ids=source_ids,
            source_versions=context.source_versions,
            evidence_references=context.evidence_references,
            confidence=confidence,
            missing_evidence=missing,
            supersedes_id=predecessor.id if predecessor else None,
            correction_reason=correction_reason,
            payload=payload,
        )
        return AuthorizedAnalyticalWrite(record, "correct" if predecessor else "create")


class ExperimentalAnalyticalRecordRepository(SQLAnalyticalRecordRepository):
    """Repository restricted to the isolated experimental semantic namespace."""

    def persist(self, plan: AuthorizedAnalyticalWrite) -> AnalyticalRecord:
        self._require_experimental(plan.record)
        return super().persist(plan)

    def load(self, identity: str) -> AnalyticalRecord | None:
        record = super().load(identity)
        if record is None:
            return None
        self._require_experimental(record)
        return record

    def lineage(self, logical_identity: str) -> tuple[AnalyticalRecord, ...]:
        records = super().lineage(logical_identity)
        for record in records:
            self._require_experimental(record)
        return records

    def by_semantic_type(self, semantic_type: ExperimentalSemanticType) -> tuple[AnalyticalRecord, ...]:
        if semantic_type not in ALLOWED_SEMANTIC_TYPES:
            raise ValueError(f"unsupported experimental semantic type: {semantic_type}")
        records = [record for record in self._all_records() if record.semantic_type == semantic_type]
        records.sort(key=lambda record: (record.effective_at, record.recorded_at, record.id))
        return tuple(records)

    @staticmethod
    def _require_experimental(record: AnalyticalRecord) -> None:
        if record.semantic_type not in ALLOWED_SEMANTIC_TYPES:
            raise AnalyticalWriteAuthorizationError("experimental store rejects non-experimental semantic types")
        if not record.id.startswith(f"{EXPERIMENTAL_RECORD_PREFIX}:"):
            raise AnalyticalWriteAuthorizationError("experimental record identity is outside its namespace")
        if record.payload.get("authority_classification") != "experimental":
            raise AnalyticalWriteAuthorizationError("experimental authority classification is required")
        native = record.payload.get("native_assessment")
        if record.semantic_type in {
            "experimental.opportunity-metric-snapshot",
            "experimental.opportunity-assessment",
        }:
            native = record.payload.get("opportunity_snapshot") or record.payload.get("opportunity_assessment")
        if not isinstance(native, dict):
            raise AnalyticalWriteAuthorizationError("native experimental payload is required")
        if record.semantic_type == "experimental.standalone-committee-assessment" and (
            "committee_decision" in native or "hunter_score" in native
        ):
            raise AnalyticalWriteAuthorizationError(
                "standalone Committee cannot persist canonical Market Validation committee fields"
            )


class ExperimentalDerivedReasoningStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError("experimental store must be explicitly bootstrapped before use")
        self._sessions = SessionFactory(create_sqlite_engine(self.path))

    @classmethod
    def from_config(cls, config: ExperimentalPersistenceConfig) -> ExperimentalDerivedReasoningStore:
        if not config.enabled:
            raise RuntimeError("experimental persistence is disabled")
        return cls(config.database_path)

    @contextmanager
    def repository(self) -> Iterator[ExperimentalAnalyticalRecordRepository]:
        session: Session = self._sessions.create()
        try:
            yield ExperimentalAnalyticalRecordRepository(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def bootstrap_experimental_store(path: str | Path) -> Path:
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(store_path)
    create_schema(engine)
    engine.dispose()
    return store_path


def load_experimental_persistence_config(
    path: str | Path = DEFAULT_EXPERIMENTAL_CONFIG,
) -> ExperimentalPersistenceConfig:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text()) or {}
    section = payload.get("experimental_persistence", {})
    if not isinstance(section, dict):
        raise ValueError("experimental_persistence must be a mapping")
    database_path = section.get("database_path")
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError("experimental_persistence.database_path is required")
    return ExperimentalPersistenceConfig(
        enabled=bool(section.get("enabled", False)),
        database_path=Path(database_path),
    )


def _domain_fields(
    assessment: ExperimentalAssessment,
) -> tuple[ExperimentalSemanticType, str, str, datetime, tuple[str, ...], float, tuple[str, ...]]:
    if isinstance(assessment, ProbabilityAssessment):
        return (
            "experimental.probability-assessment",
            assessment.assessment_id,
            assessment.target_id,
            assessment.effective_at,
            assessment.source_record_ids,
            assessment.decision_confidence,
            assessment.missing_evidence,
        )
    if isinstance(assessment, PatternMatchingAssessment):
        return (
            "experimental.pattern-assessment",
            assessment.assessment_id,
            assessment.target_id,
            assessment.effective_at,
            assessment.source_record_ids,
            assessment.historical_confidence,
            assessment.missing_evidence,
        )
    if isinstance(assessment, TechnologyNecessityAssessment):
        return (
            "experimental.technology-necessity-assessment",
            assessment.assessment_id,
            assessment.technology_id,
            assessment.effective_at,
            assessment.source_record_ids,
            assessment.confidence,
            assessment.missing_evidence,
        )
    missing = tuple(
        sorted({field for vote in assessment.votes for field in vote.missing_fields} | set(assessment.abstentions))
    )
    return (
        "experimental.standalone-committee-assessment",
        assessment.id,
        assessment.project_id,
        assessment.created_at,
        assessment.source_record_ids,
        assessment.committee_confidence,
        missing,
    )


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("native payload datetimes must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported native payload value: {type(value).__name__}")
