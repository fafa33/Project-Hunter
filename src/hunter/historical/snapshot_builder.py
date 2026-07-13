from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.execution.identity import identity
from hunter.historical.cutoff import evidence_is_cutoff_eligible
from hunter.historical.models import HistoricalEvidenceRecord, HistoricalEvidenceSnapshot, HistoricalValidationCase
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository

REQUIRED_EVIDENCE_ENGINES: tuple[str, ...] = (
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
    "developer",
    "protocol",
    "news",
    "social",
    "narrative",
    "whale_intelligence",
    "macro_intelligence",
    "future_demand",
    "opportunity_timing",
    "probability",
    "pattern_matching",
    "technology_necessity",
    "capital_rotation",
    "necessity_gap",
    "risk",
    "validation_health",
    "committee",
)

PROVIDER_ENGINE_MAP = {
    "coingecko": ("valuation", "comparative_valuation", "mispricing", "asymmetry", "risk"),
    "github": ("developer",),
    "defillama": ("protocol", "risk"),
    "narrative": ("news", "social", "narrative", "future_demand", "macro_intelligence"),
}


class HistoricalSnapshotBuilder:
    def __init__(
        self,
        acquisition_repository: FileAcquisitionRepository | None = None,
        historical_repository: HistoricalEvidenceRepository | None = None,
        include_live_acquisition: bool = True,
    ) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()
        self.historical_repository = historical_repository or HistoricalEvidenceRepository()
        self.include_live_acquisition = include_live_acquisition

    def build(
        self,
        case: HistoricalValidationCase,
        *,
        version: int = 1,
        previous_snapshot_id: str | None = None,
        correction_reason: str | None = None,
        changed_fields: tuple[str, ...] = (),
    ) -> HistoricalEvidenceSnapshot:
        records = tuple(self._records(case))
        eligible = tuple(record for record in records if evidence_is_cutoff_eligible(record))
        available_engines = {record.engine for record in eligible}
        missing = tuple(engine for engine in REQUIRED_EVIDENCE_ENGINES if engine not in available_engines)
        snapshot_id = identity(
            "historical-evidence-snapshot",
            {
                "case_id": case.case_id,
                "version": version,
                "evidence_ids": tuple(evidence_id for record in eligible for evidence_id in record.evidence_ids),
            },
        )
        return HistoricalEvidenceSnapshot(
            snapshot_id=snapshot_id,
            case_id=case.case_id,
            version=version,
            finalized=True,
            created_at=datetime.now(tz=UTC),
            previous_snapshot_id=previous_snapshot_id,
            correction_reason=correction_reason,
            correction_timestamp=datetime.now(tz=UTC) if previous_snapshot_id else None,
            changed_fields=changed_fields,
            evidence=eligible,
            missing_evidence=missing,
            unavailable_engines=missing,
            stale_engines=tuple(record.engine for record in eligible if record.freshness < 0.5),
            validation_warnings=(),
        )

    def _records(self, case: HistoricalValidationCase) -> tuple[HistoricalEvidenceRecord, ...]:
        rows: list[HistoricalEvidenceRecord] = []
        if not self.include_live_acquisition:
            return self._historical_records(case)
        for evidence in self.acquisition_repository.normalized.values():
            if evidence.target_id != case.project_id:
                continue
            validation = self.acquisition_repository.validations.get(evidence.evidence_id)
            if validation is None or validation.status != "valid":
                continue
            engines = PROVIDER_ENGINE_MAP.get(evidence.provider, ())
            publication = _timestamp(evidence.raw_metrics.get("publication_timestamp"), evidence.retrieved_at)
            event = _timestamp(evidence.raw_metrics.get("event_timestamp"), publication)
            for engine in engines:
                rows.append(
                    HistoricalEvidenceRecord(
                        source_provider=evidence.provider,
                        source_record_ids=(evidence.raw_evidence_id or evidence.raw_source_id,),
                        evidence_ids=(evidence.evidence_id,),
                        repository_ids=(evidence.repository_id,),
                        event_timestamp=event,
                        publication_timestamp=publication,
                        ingestion_timestamp=evidence.retrieved_at,
                        evaluation_cutoff_timestamp=case.historical_cutoff_timestamp,
                        confidence=min(evidence.confidence, validation.confidence),
                        freshness=min(evidence.freshness, validation.freshness),
                        validation_status=validation.status.upper(),
                        engine=engine,
                        raw_metrics=dict(evidence.raw_metrics),
                        normalized_metrics=dict(evidence.normalized_metrics),
                    )
                )
        rows.extend(self._historical_records(case))
        return tuple(rows)

    def _historical_records(self, case: HistoricalValidationCase) -> tuple[HistoricalEvidenceRecord, ...]:
        rows = []
        valid_ids = {item.evidence_id for item in self.historical_repository.validations() if item.status == "valid"}
        snapshots_by_engine = {
            item.engine: item
            for item in self.historical_repository.snapshots(case_id=case.case_id)
            if item.status == "AVAILABLE"
        }
        for evidence in self.historical_repository.normalized():
            if evidence.case_id != case.case_id or evidence.evidence_id not in valid_ids:
                continue
            snapshot = snapshots_by_engine.get(evidence.engine)
            source_record_ids = (evidence.raw_source_id,)
            repository_ids = (evidence.repository_id,)
            if snapshot is not None:
                source_record_ids = (evidence.raw_source_id, snapshot.snapshot_id, snapshot.acquisition_id)
                repository_ids = (evidence.repository_id, snapshot.snapshot_id)
            rows.append(
                HistoricalEvidenceRecord(
                    source_provider=evidence.provider,
                    source_record_ids=source_record_ids,
                    evidence_ids=(evidence.evidence_id,),
                    repository_ids=repository_ids,
                    event_timestamp=evidence.event_timestamp,
                    publication_timestamp=evidence.publication_timestamp,
                    ingestion_timestamp=evidence.retrieval_timestamp,
                    evaluation_cutoff_timestamp=case.historical_cutoff_timestamp,
                    confidence=evidence.confidence,
                    freshness=evidence.freshness,
                    validation_status="VALID",
                    engine=evidence.engine,
                    raw_metrics=dict(evidence.raw_metrics),
                    normalized_metrics=dict(evidence.normalized_metrics),
                    data_availability_timestamp=evidence.data_availability_timestamp,
                )
            )
        return tuple(rows)


def _timestamp(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value))
    else:
        parsed = fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
