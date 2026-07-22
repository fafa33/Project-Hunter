from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hunter.committee.composition import build_authoritative_committee_service
from hunter.committee.models import CommitteeInputSet, CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


@dataclass(frozen=True)
class ApprovedCommitteeRuntimePaths:
    persistence_database: Path
    committee_database: Path

    def __post_init__(self) -> None:
        if not self.persistence_database.is_absolute() or not self.committee_database.is_absolute():
            raise ValueError("approved committee runtime paths must be absolute")
        if self.persistence_database == self.committee_database:
            raise ValueError("input persistence and committee output databases must be distinct")


class ProductionCommitteeRuntime:
    """Hunter-owned production entry point for authoritative committee execution."""

    def __init__(self, paths: ApprovedCommitteeRuntimePaths) -> None:
        self.paths = paths

    def evaluate_cycle(
        self,
        inputs: tuple[CommitteeInputSet, ...],
    ) -> tuple[CycleChampionSnapshot, tuple[InvestmentCommitteeAssessment, ...]]:
        engine = create_sqlite_engine(self.paths.persistence_database)
        create_schema(engine)
        session = SessionFactory(engine).create()
        try:
            service = build_authoritative_committee_service(
                output_repository=InvestmentCommitteeRepository(self.paths.committee_database),
                persistence_repositories=RepositoryFactory(session),
            )
            return service.evaluate_cycle(inputs)
        finally:
            session.close()
            engine.dispose()
