from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.historical_acquisition.models import (
    HistoricalAcquisitionRun,
    HistoricalEvidenceValidation,
    NormalizedHistoricalEvidence,
    RawHistoricalEvidence,
)
from hunter.historical_acquisition.providers import HistoricalProvider
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository

ENGINE_BY_METRIC = {
    "historical_market": ("valuation", "comparative_valuation", "mispricing", "asymmetry"),
    "historical_protocol": ("protocol",),
    "historical_developer": ("developer",),
    "historical_narrative": ("news", "narrative", "future_demand"),
    "historical_governance": ("governance", "tokenomics"),
    "historical_archive_presence": ("narrative", "evidence_provenance"),
}


class HistoricalAcquisitionPipeline:
    def __init__(self, repository: HistoricalEvidenceRepository | None = None) -> None:
        self.repository = repository or HistoricalEvidenceRepository()

    def sync(self, provider: HistoricalProvider, cases) -> HistoricalAcquisitionRun:
        started = datetime.now(tz=UTC)
        raw = provider.collect(tuple(cases))
        normalized = normalize_historical(raw)
        validations = validate_historical(
            normalized, existing_ids={item.evidence_id for item in self.repository.normalized()}
        )
        valid_ids = {item.evidence_id for item in validations if item.status == "valid"}
        saved_normalized = self.repository.save_normalized(
            tuple(item for item in normalized if item.evidence_id in valid_ids)
        )
        saved_validations = self.repository.save_validations(validations)
        self.repository.save_raw(raw)
        finished = datetime.now(tz=UTC)
        run = HistoricalAcquisitionRun(
            run_id=identity("historical-acquisition-run", {"provider": provider.metadata.name, "started": started}),
            provider=provider.metadata.name,
            started_at=started,
            finished_at=finished,
            raw_count=len(raw),
            normalized_count=len(saved_normalized),
            valid_count=sum(1 for item in saved_validations if item.status == "valid"),
            invalid_count=sum(1 for item in saved_validations if item.status in {"invalid", "future", "corrupted"}),
            duplicate_count=sum(1 for item in validations if item.status == "duplicate"),
        )
        self.repository.save_run(run)
        return run


def normalize_historical(raw_rows: tuple[RawHistoricalEvidence, ...]) -> tuple[NormalizedHistoricalEvidence, ...]:
    rows = []
    for raw in raw_rows:
        engines = ENGINE_BY_METRIC.get(raw.metric, ())
        for engine in engines:
            normalized = {
                key: _normalize(value)
                for key, value in raw.payload.items()
                if isinstance(value, int | float) and value is not None
            }
            rows.append(
                NormalizedHistoricalEvidence(
                    evidence_id=identity(
                        "historical-evidence",
                        {"raw": raw.raw_source_id, "engine": engine, "metric": raw.metric},
                    ),
                    repository_id=raw.repository_id,
                    provider=raw.provider,
                    collector=raw.collector,
                    raw_source_id=raw.raw_source_id,
                    case_id=raw.case_id,
                    project_id=raw.project_id,
                    engine=engine,
                    metric=raw.metric,
                    event_timestamp=raw.event_timestamp,
                    publication_timestamp=raw.publication_timestamp,
                    data_availability_timestamp=raw.data_availability_timestamp,
                    retrieval_timestamp=raw.retrieval_timestamp,
                    raw_metrics=dict(raw.payload),
                    normalized_metrics=normalized or {"available": 1.0},
                    source_url=raw.source_url,
                    confidence=1.0,
                    freshness=1.0,
                )
            )
    return tuple(rows)


def validate_historical(
    rows: tuple[NormalizedHistoricalEvidence, ...],
    *,
    existing_ids: set[str],
) -> tuple[HistoricalEvidenceValidation, ...]:
    seen: set[str] = set()
    validations = []
    now = datetime.now(tz=UTC)
    for row in rows:
        status = "valid"
        reason = ""
        if row.evidence_id in existing_ids or row.evidence_id in seen:
            status = "duplicate"
            reason = "duplicate historical evidence"
        elif not row.project_id or not row.case_id:
            status = "invalid"
            reason = "missing project mapping"
        elif row.publication_timestamp > now or row.event_timestamp > now:
            status = "future"
            reason = "future timestamp"
        elif (
            row.event_timestamp > row.publication_timestamp
            or row.publication_timestamp > row.data_availability_timestamp
        ):
            status = "invalid"
            reason = "invalid chronology"
        elif not row.raw_metrics:
            status = "corrupted"
            reason = "corrupted record"
        seen.add(row.evidence_id)
        validations.append(
            HistoricalEvidenceValidation(
                evidence_id=row.evidence_id,
                status=status,  # type: ignore[arg-type]
                validated_at=now,
                reason=reason,
            )
        )
    return tuple(validations)


def _normalize(value: int | float) -> float:
    if value <= 0:
        return 0.0
    return round(min(1.0, float(value) / (float(value) + 1_000_000_000.0)), 6)
