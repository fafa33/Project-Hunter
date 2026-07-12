from __future__ import annotations

from datetime import datetime

from hunter.historical.models import HistoricalEvidenceRecord


def evidence_is_cutoff_eligible(record: HistoricalEvidenceRecord) -> bool:
    cutoff = record.evaluation_cutoff_timestamp
    availability = record.data_availability_timestamp or record.ingestion_timestamp
    return record.publication_timestamp <= cutoff and availability <= cutoff and record.event_timestamp <= cutoff


def reject_future_evidence(records: tuple[HistoricalEvidenceRecord, ...]) -> tuple[str, ...]:
    violations = []
    for record in records:
        if record.publication_timestamp > record.evaluation_cutoff_timestamp:
            violations.append(f"{record.engine}:publication_after_cutoff")
        availability = record.data_availability_timestamp or record.ingestion_timestamp
        if availability > record.evaluation_cutoff_timestamp:
            violations.append(f"{record.engine}:ingestion_after_cutoff")
        if record.event_timestamp > record.evaluation_cutoff_timestamp:
            violations.append(f"{record.engine}:event_after_cutoff")
    return tuple(sorted(violations))


def timestamp_valid_at(timestamp: datetime, cutoff: datetime) -> bool:
    return timestamp <= cutoff
