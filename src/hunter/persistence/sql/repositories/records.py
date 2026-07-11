from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

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


class SQLAutomationJobRepository(SQLRecordRepository[AutomationJobRecord]):
    record_type = "automation-job"
    record_class = AutomationJobRecord

    def _canonical_hash_payload(self, record: AutomationJobRecord) -> str:
        definition_time = datetime(1970, 1, 1, tzinfo=UTC)
        definition = replace(record, created_at=definition_time, effective_at=definition_time)
        return record_to_json(definition)


class SQLAutomationRunRepository(SQLRecordRepository[AutomationRunRecord]):
    record_type = "automation-run"
    record_class = AutomationRunRecord

    def _canonical_hash_payload(self, record: AutomationRunRecord) -> str:
        state = replace(
            record,
            created_at=record.scheduled_for,
            effective_at=record.scheduled_for,
            started_at=None,
            finished_at=None,
        )
        return record_to_json(state)


class SQLCommitteeVoteRepository(SQLRecordRepository[CommitteeVoteRecord]):
    record_type = "committee-vote"
    record_class = CommitteeVoteRecord

    def _canonical_hash_payload(self, record: CommitteeVoteRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLInvestmentCommitteeAssessmentRepository(SQLRecordRepository[InvestmentCommitteeAssessmentRecord]):
    record_type = "investment-committee-assessment"
    record_class = InvestmentCommitteeAssessmentRecord

    def _canonical_hash_payload(self, record: InvestmentCommitteeAssessmentRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLCycleChampionSnapshotRepository(SQLRecordRepository[CycleChampionSnapshotRecord]):
    record_type = "cycle-champion-snapshot"
    record_class = CycleChampionSnapshotRecord

    def _canonical_hash_payload(self, record: CycleChampionSnapshotRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLMarketValidationRunRepository(SQLRecordRepository[MarketValidationRunRecord]):
    record_type = "market-validation-run"
    record_class = MarketValidationRunRecord

    def _canonical_hash_payload(self, record: MarketValidationRunRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLMarketValidationProjectResultRepository(SQLRecordRepository[MarketValidationProjectResultRecord]):
    record_type = "market-validation-project-result"
    record_class = MarketValidationProjectResultRecord

    def _canonical_hash_payload(self, record: MarketValidationProjectResultRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


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


class SQLFusedIntelligenceRepository(SQLRecordRepository[FusedIntelligenceRecord]):
    record_type = "fused-intelligence"
    record_class = FusedIntelligenceRecord

    def _canonical_hash_payload(self, record: FusedIntelligenceRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLOpportunityTimingAssessmentRepository(SQLRecordRepository[OpportunityTimingAssessmentRecord]):
    record_type = "opportunity-timing-assessment"
    record_class = OpportunityTimingAssessmentRecord

    def _canonical_hash_payload(self, record: OpportunityTimingAssessmentRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLOpportunityTimingSnapshotRepository(SQLRecordRepository[OpportunityTimingSnapshotRecord]):
    record_type = "opportunity-timing-snapshot"
    record_class = OpportunityTimingSnapshotRecord

    def _canonical_hash_payload(self, record: OpportunityTimingSnapshotRecord) -> str:
        analytical = replace(record, created_at=record.effective_at)
        return record_to_json(analytical)


class SQLConfigurationRepository(SQLRecordRepository[ConfigurationRecord]):
    record_type = "configuration"
    record_class = ConfigurationRecord


class SQLEngineManifestRepository(SQLRecordRepository[EngineManifestRecord]):
    record_type = "engine-manifest"
    record_class = EngineManifestRecord


class SQLSnapshotRepository(BaseSQLSnapshotRepository):
    pass
