from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

from hunter.persistence.models import HistorySpec, QuerySpec, SnapshotSpec
from hunter.persistence.records import (
    AutomationJobRecord,
    AutomationRunRecord,
    CommitteeVoteRecord,
    ConfigurationRecord,
    CycleChampionSnapshotRecord,
    EngineManifestRecord,
    EvidenceRecord,
    FusedIntelligenceRecord,
    InsightRecord,
    IntelligenceRecord,
    InvestmentCommitteeAssessmentRecord,
    MarketValidationProjectResultRecord,
    MarketValidationRunRecord,
    ObservationRecord,
    OperationalAttemptRecord,
    OpportunityTimingAssessmentRecord,
    OpportunityTimingSnapshotRecord,
    PersistenceRecord,
    PipelineRunRecord,
    SignalRecord,
    SnapshotRecord,
)

RecordT = TypeVar("RecordT", bound=PersistenceRecord)


@runtime_checkable
class Repository(Protocol, Generic[RecordT]):
    def save(self, record: RecordT) -> RecordT:
        raise NotImplementedError

    def save_many(self, records: tuple[RecordT, ...]) -> tuple[RecordT, ...]:
        raise NotImplementedError

    def load(self, identity: str) -> RecordT | None:
        raise NotImplementedError

    def load_many(self, identities: tuple[str, ...]) -> tuple[RecordT, ...]:
        raise NotImplementedError

    def exists(self, identity: str) -> bool:
        raise NotImplementedError

    def delete(self, identity: str) -> None:
        raise NotImplementedError

    def query(self, spec: QuerySpec) -> tuple[RecordT, ...]:
        raise NotImplementedError

    def latest(self, spec: QuerySpec) -> RecordT | None:
        raise NotImplementedError

    def history(self, spec: HistorySpec) -> tuple[RecordT, ...]:
        raise NotImplementedError

    def snapshot(self, spec: SnapshotSpec) -> SnapshotRecord:
        raise NotImplementedError


class PipelineRunRepository(Repository[PipelineRunRecord], Protocol):
    pass


class OperationalAttemptRepository(Repository[OperationalAttemptRecord], Protocol):
    pass


class AutomationJobRepository(Repository[AutomationJobRecord], Protocol):
    pass


class AutomationRunRepository(Repository[AutomationRunRecord], Protocol):
    pass


class CommitteeVoteRepository(Repository[CommitteeVoteRecord], Protocol):
    pass


class InvestmentCommitteeAssessmentRepository(Repository[InvestmentCommitteeAssessmentRecord], Protocol):
    pass


class CycleChampionSnapshotRepository(Repository[CycleChampionSnapshotRecord], Protocol):
    pass


class MarketValidationRunRepository(Repository[MarketValidationRunRecord], Protocol):
    pass


class MarketValidationProjectResultRepository(Repository[MarketValidationProjectResultRecord], Protocol):
    pass


class EvidenceRepository(Repository[EvidenceRecord], Protocol):
    pass


class SignalRepository(Repository[SignalRecord], Protocol):
    pass


class ObservationRepository(Repository[ObservationRecord], Protocol):
    pass


class InsightRepository(Repository[InsightRecord], Protocol):
    pass


class IntelligenceRepository(Repository[IntelligenceRecord], Protocol):
    pass


class FusedIntelligenceRepository(Repository[FusedIntelligenceRecord], Protocol):
    pass


class OpportunityTimingAssessmentRepository(Repository[OpportunityTimingAssessmentRecord], Protocol):
    pass


class OpportunityTimingSnapshotRepository(Repository[OpportunityTimingSnapshotRecord], Protocol):
    pass


class SnapshotRepository(Repository[SnapshotRecord], Protocol):
    pass


class ConfigurationRepository(Repository[ConfigurationRecord], Protocol):
    pass


class EngineManifestRepository(Repository[EngineManifestRecord], Protocol):
    pass
