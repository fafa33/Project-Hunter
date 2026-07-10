from __future__ import annotations

from hunter.persistence.records import (
    ConfigurationRecord,
    EngineManifestRecord,
    EvidenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    PipelineRunRecord,
    SignalRecord,
)
from hunter.persistence.sql.repositories.base import SQLRecordRepository
from hunter.persistence.sql.repositories.base import SQLSnapshotRepository as BaseSQLSnapshotRepository


class SQLPipelineRunRepository(SQLRecordRepository[PipelineRunRecord]):
    record_type = "pipeline-run"
    record_class = PipelineRunRecord


class SQLEvidenceRepository(SQLRecordRepository[EvidenceRecord]):
    record_type = "evidence"
    record_class = EvidenceRecord


class SQLSignalRepository(SQLRecordRepository[SignalRecord]):
    record_type = "signal"
    record_class = SignalRecord


class SQLObservationRepository(SQLRecordRepository[ObservationRecord]):
    record_type = "observation"
    record_class = ObservationRecord


class SQLInsightRepository(SQLRecordRepository[InsightRecord]):
    record_type = "insight"
    record_class = InsightRecord


class SQLIntelligenceRepository(SQLRecordRepository[IntelligenceRecord]):
    record_type = "intelligence"
    record_class = IntelligenceRecord


class SQLConfigurationRepository(SQLRecordRepository[ConfigurationRecord]):
    record_type = "configuration"
    record_class = ConfigurationRecord


class SQLEngineManifestRepository(SQLRecordRepository[EngineManifestRecord]):
    record_type = "engine-manifest"
    record_class = EngineManifestRecord


class SQLSnapshotRepository(BaseSQLSnapshotRepository):
    pass
