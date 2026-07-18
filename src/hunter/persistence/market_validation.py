from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hunter.market_validation.persistence import AuthorizedMarketValidationWrite
from hunter.persistence.records import MarketValidationProjectResultRecord, MarketValidationRunRecord
from hunter.persistence.sql import SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import AnalyticalCorrectionConflictError, AnalyticalWriteAuthorizationError
from hunter.persistence.sql.repositories.records import (
    SQLMarketValidationProjectResultRepository,
    SQLMarketValidationRunRepository,
)

DEFAULT_MARKET_VALIDATION_PERSISTENCE_CONFIG = Path("configs/market_validation_persistence.yaml")


@dataclass(frozen=True, slots=True)
class CanonicalMarketValidationPersistenceConfig:
    enabled: bool
    database_path: Path


class CanonicalMarketValidationRepository:
    """Mechanical storage scoped to canonical Market Validation record types."""

    def __init__(
        self,
        runs: SQLMarketValidationRunRepository,
        projects: SQLMarketValidationProjectResultRepository,
    ) -> None:
        self._runs = runs
        self._projects = projects

    def persist(self, plan: AuthorizedMarketValidationWrite) -> MarketValidationRunRecord:
        if not isinstance(plan, AuthorizedMarketValidationWrite):
            raise AnalyticalWriteAuthorizationError("canonical Market Validation requires an authorized write plan")
        self._validate_plan(plan)
        for project in plan.project_records:
            self._projects.save(project)
        return self._runs.save(plan.run_record)

    def run(self, identity: str) -> MarketValidationRunRecord | None:
        return self._runs.load(identity)

    def project(self, identity: str) -> MarketValidationProjectResultRecord | None:
        return self._projects.load(identity)

    def run_history(self, validation_run_id: str) -> tuple[MarketValidationRunRecord, ...]:
        records = [record for record in self._runs._all_records() if record.validation_run_id == validation_run_id]
        records.sort(key=lambda record: (record.created_at, record.id))
        return tuple(records)

    def project_history(
        self, validation_run_id: str, project_id: str
    ) -> tuple[MarketValidationProjectResultRecord, ...]:
        records = [
            record
            for record in self._projects._all_records()
            if record.validation_run_id == validation_run_id and record.project_id == project_id
        ]
        records.sort(key=lambda record: (record.created_at, record.id))
        return tuple(records)

    def strict_known_run(
        self,
        validation_run_id: str,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MarketValidationRunRecord | None:
        return _strict_known(self.run_history(validation_run_id), effective_as_of, known_by)

    def strict_known_project(
        self,
        validation_run_id: str,
        project_id: str,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MarketValidationProjectResultRecord | None:
        return _strict_known(self.project_history(validation_run_id, project_id), effective_as_of, known_by)

    def projects_for_run(self, run_record_id: str) -> tuple[MarketValidationProjectResultRecord, ...]:
        run = self.run(run_record_id)
        if run is None:
            return ()
        records = self._projects.load_many(run.project_result_ids)
        by_id = {record.id: record for record in records}
        return tuple(by_id[identity] for identity in run.project_result_ids if identity in by_id)

    def _validate_plan(self, plan: AuthorizedMarketValidationWrite) -> None:
        records = (plan.run_record, *plan.project_records)
        if any(record.authorized_payload.get("authority_classification") != "production" for record in records):
            raise AnalyticalWriteAuthorizationError("canonical records require production authority classification")
        if plan.operation == "correct":
            predecessor = self.run(plan.run_record.supersedes_id or "")
            if predecessor is None:
                raise AnalyticalCorrectionConflictError("canonical run correction predecessor does not exist")
            if predecessor.validation_run_id != plan.run_record.validation_run_id:
                raise AnalyticalCorrectionConflictError("canonical run correction must preserve validation_run_id")
            for project in plan.project_records:
                project_predecessor = self.project(project.supersedes_id or "")
                if project_predecessor is None:
                    raise AnalyticalCorrectionConflictError("canonical project correction predecessor does not exist")
                if (
                    project_predecessor.validation_run_id != project.validation_run_id
                    or project_predecessor.project_id != project.project_id
                ):
                    raise AnalyticalCorrectionConflictError("canonical project correction must preserve identity")


class CanonicalMarketValidationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError("canonical Market Validation store must be explicitly bootstrapped")
        self._sessions = SessionFactory(create_sqlite_engine(self.path))

    @classmethod
    def from_config(cls, config: CanonicalMarketValidationPersistenceConfig) -> CanonicalMarketValidationStore:
        if not config.enabled:
            raise RuntimeError("canonical Market Validation persistence is disabled")
        return cls(config.database_path)

    def persist(self, plan: AuthorizedMarketValidationWrite) -> MarketValidationRunRecord:
        with UnitOfWork(self._sessions) as uow:
            if uow.repositories is None:
                raise RuntimeError("Market Validation UnitOfWork did not initialize repositories")
            repository = CanonicalMarketValidationRepository(
                uow.repositories.market_validation_runs(),
                uow.repositories.market_validation_project_results(),
            )
            return repository.persist(plan)

    def repository(self, uow: UnitOfWork) -> CanonicalMarketValidationRepository:
        if uow.repositories is None:
            raise RuntimeError("UnitOfWork must be entered before repository access")
        return CanonicalMarketValidationRepository(
            uow.repositories.market_validation_runs(),
            uow.repositories.market_validation_project_results(),
        )

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._sessions)


def bootstrap_canonical_market_validation_store(path: str | Path) -> Path:
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(store_path)
    create_schema(engine)
    engine.dispose()
    return store_path


def load_canonical_market_validation_persistence_config(
    path: str | Path = DEFAULT_MARKET_VALIDATION_PERSISTENCE_CONFIG,
) -> CanonicalMarketValidationPersistenceConfig:
    payload = yaml.safe_load(Path(path).read_text()) or {}
    section = payload.get("market_validation_persistence", {})
    if not isinstance(section, dict):
        raise ValueError("market_validation_persistence must be a mapping")
    enabled = section.get("enabled", False)
    database_path = section.get("database_path")
    if not isinstance(enabled, bool):
        raise ValueError("market_validation_persistence.enabled must be boolean")
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError("market_validation_persistence.database_path is required")
    return CanonicalMarketValidationPersistenceConfig(enabled, Path(database_path))


def _strict_known(records, effective_as_of: datetime, known_by: datetime):
    for name, value in (("effective_as_of", effective_as_of), ("known_by", known_by)):
        if value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
    effective_cutoff = effective_as_of.astimezone(UTC)
    known_cutoff = known_by.astimezone(UTC)
    eligible = [
        record
        for record in records
        if record.effective_at <= effective_cutoff
        and record.created_at <= known_cutoff
        and record.known_at is not None
        and record.known_time_limitation is None
        and record.known_at <= known_cutoff
    ]
    if not eligible:
        return None
    superseded = {record.supersedes_id for record in eligible if record.supersedes_id is not None}
    current = [record for record in eligible if record.id not in superseded]
    current.sort(key=lambda record: (record.created_at, record.id), reverse=True)
    return current[0] if current else None
