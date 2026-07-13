from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.historical_acquisition.models import (
    HistoricalAcquisitionRun,
    HistoricalEngineSnapshot,
    HistoricalEvidenceValidation,
    NormalizedHistoricalEvidence,
    RawHistoricalEvidence,
)
from hunter.historical_acquisition.providers import HistoricalProvider
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository

ENGINE_BY_METRIC = {
    "historical_market": ("market", "valuation", "comparative_valuation", "mispricing", "asymmetry"),
    "historical_protocol": ("protocol",),
    "historical_developer": ("developer",),
    "historical_narrative": ("news", "narrative", "future_demand"),
    "historical_governance": ("governance", "tokenomics"),
    "historical_archive_presence": ("narrative", "evidence_provenance"),
    "historical_macro": ("macro_intelligence",),
    "historical_whale": ("whale_intelligence",),
    "historical_technology_graph": ("technology_graph",),
    "historical_economic_graph": ("economic_graph",),
    "historical_scenario": ("scenario",),
}

HISTORICAL_ENGINE_SCOPE: tuple[str, ...] = (
    "protocol",
    "developer",
    "news",
    "narrative",
    "future_demand",
    "macro_intelligence",
    "whale_intelligence",
    "technology_graph",
    "economic_graph",
    "scenario",
    "market",
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
)

PROVIDER_ENGINE_SCOPE: dict[str, tuple[str, ...]] = {
    "coingecko-historical": ("market", "valuation", "comparative_valuation", "mispricing", "asymmetry"),
    "defillama-historical": ("protocol",),
    "github-historical": ("developer",),
    "historical-rss-announcements": ("news", "narrative", "future_demand"),
    "governance-archive-snapshot": ("narrative",),
    "internet-archive-historical": ("narrative",),
    "reconstructed-historical-evidence": (
        "macro_intelligence",
        "whale_intelligence",
        "technology_graph",
        "economic_graph",
        "scenario",
    ),
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
        self.repository.save_snapshots(_engine_snapshots(tuple(cases), run.run_id, provider.metadata.name, normalized))
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


def _engine_snapshots(
    cases,
    acquisition_id: str,
    provider: str,
    rows: tuple[NormalizedHistoricalEvidence, ...],
) -> tuple[HistoricalEngineSnapshot, ...]:
    snapshots = []
    now = datetime.now(tz=UTC)
    engine_scope = PROVIDER_ENGINE_SCOPE.get(provider, HISTORICAL_ENGINE_SCOPE)
    for case in cases:
        for engine in engine_scope:
            engine_rows = tuple(
                row
                for row in rows
                if row.case_id == case.case_id
                and row.project_id == case.project_id
                and row.engine == engine
                and row.publication_timestamp <= case.historical_cutoff_timestamp
                and row.data_availability_timestamp <= case.historical_cutoff_timestamp
                and row.event_timestamp <= case.historical_cutoff_timestamp
            )
            if engine_rows:
                snapshots.append(_available_snapshot(case, engine, acquisition_id, provider, engine_rows, now))
            else:
                snapshots.append(_unavailable_snapshot(case, engine, acquisition_id, provider, now))
    return tuple(snapshots)


def _available_snapshot(
    case,
    engine: str,
    acquisition_id: str,
    provider: str,
    rows: tuple[NormalizedHistoricalEvidence, ...],
    acquired_at: datetime,
) -> HistoricalEngineSnapshot:
    latest = max(rows, key=lambda item: item.publication_timestamp)
    evidence_ids = tuple(sorted(row.evidence_id for row in rows))
    confidence = round(sum(row.confidence for row in rows) / len(rows), 4)
    freshness = round(sum(row.freshness for row in rows) / len(rows), 4)
    return HistoricalEngineSnapshot(
        snapshot_id=identity(
            "historical-acquisition-snapshot",
            {"case": case.case_id, "engine": engine, "provider": provider, "evidence_ids": evidence_ids},
        ),
        acquisition_id=acquisition_id,
        project_id=case.project_id,
        case_id=case.case_id,
        engine=engine,
        acquisition_timestamp=acquired_at,
        observation_timestamp=latest.event_timestamp,
        effective_timestamp=case.historical_cutoff_timestamp,
        source_id=latest.raw_source_id,
        provider=provider,
        provider_version="v1",
        historical_snapshot={
            "evidence_ids": evidence_ids,
            "repository_ids": tuple(sorted(row.repository_id for row in rows)),
            "metrics": tuple(sorted(row.metric for row in rows)),
        },
        confidence=confidence,
        freshness=freshness,
        quality=confidence,
        reconstruction_confidence=confidence,
        missing_fields=(),
        status="AVAILABLE",
    )


def _unavailable_snapshot(
    case,
    engine: str,
    acquisition_id: str,
    provider: str,
    acquired_at: datetime,
) -> HistoricalEngineSnapshot:
    return HistoricalEngineSnapshot(
        snapshot_id=identity(
            "historical-acquisition-snapshot",
            {"case": case.case_id, "engine": engine, "provider": provider, "status": "UNAVAILABLE"},
        ),
        acquisition_id=acquisition_id,
        project_id=case.project_id,
        case_id=case.case_id,
        engine=engine,
        acquisition_timestamp=acquired_at,
        observation_timestamp=case.historical_cutoff_timestamp,
        effective_timestamp=case.historical_cutoff_timestamp,
        source_id=f"unavailable:{provider}:{case.case_id}:{engine}",
        provider=provider,
        provider_version="v1",
        historical_snapshot={"status": "UNAVAILABLE"},
        confidence=0.0,
        freshness=0.0,
        quality=0.0,
        reconstruction_confidence=0.0,
        missing_fields=(engine,),
        status="UNAVAILABLE",
    )
