from __future__ import annotations

from dataclasses import replace

from hunter.persistence.records import (
    ConfigurationRecord,
    EngineManifestRecord,
    EvidenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    OperationalAttemptRecord,
    PipelineRunRecord,
    SignalRecord,
)
from hunter.persistence.serialization import record_to_json
from hunter.persistence.sql.repositories.base import SQLRecordRepository
from hunter.persistence.sql.repositories.base import SQLSnapshotRepository as BaseSQLSnapshotRepository


class SQLPipelineRunRepository(SQLRecordRepository[PipelineRunRecord]):
    record_type = "pipeline-run"
    record_class = PipelineRunRecord

    def _canonical_hash_payload(self, record: PipelineRunRecord) -> str:
        analytical = replace(
            record,
            status="analytical",
            requested_at=None,
            started_at=None,
            finished_at=None,
        )
        return record_to_json(analytical)


class SQLOperationalAttemptRepository(SQLRecordRepository[OperationalAttemptRecord]):
    record_type = "operational-attempt"
    record_class = OperationalAttemptRecord


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
