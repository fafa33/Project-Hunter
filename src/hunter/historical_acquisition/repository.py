from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.historical_acquisition.models import (
    HistoricalAcquisitionRun,
    HistoricalEngineSnapshot,
    HistoricalEvidenceValidation,
    NormalizedHistoricalEvidence,
    RawHistoricalEvidence,
)


class HistoricalEvidenceRepository:
    def __init__(self, root: str | Path = "data/historical_acquisition") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_raw(self, rows: tuple[RawHistoricalEvidence, ...]) -> tuple[RawHistoricalEvidence, ...]:
        _write_jsonl(self.root / "raw.jsonl", (_payload(item) for item in rows), append=True)
        return rows

    def save_normalized(
        self, rows: tuple[NormalizedHistoricalEvidence, ...]
    ) -> tuple[NormalizedHistoricalEvidence, ...]:
        existing = {item.evidence_id for item in self.normalized()}
        new_rows = _unique_normalized(rows, existing)
        _write_jsonl(self.root / "normalized.jsonl", (_payload(item) for item in new_rows), append=True)
        return new_rows

    def save_validations(
        self, rows: tuple[HistoricalEvidenceValidation, ...]
    ) -> tuple[HistoricalEvidenceValidation, ...]:
        existing = {item.evidence_id for item in self.validations()}
        new_rows = _unique_validations(rows, existing)
        _write_jsonl(self.root / "validations.jsonl", (_payload(item) for item in new_rows), append=True)
        return new_rows

    def save_run(self, run: HistoricalAcquisitionRun) -> HistoricalAcquisitionRun:
        _write_jsonl(self.root / "runs.jsonl", (_payload(run),), append=True)
        return run

    def save_snapshots(self, rows: tuple[HistoricalEngineSnapshot, ...]) -> tuple[HistoricalEngineSnapshot, ...]:
        existing = {item.snapshot_id: item for item in self.snapshots()}
        new_rows = []
        for row in rows:
            current = existing.get(row.snapshot_id)
            if current is not None:
                continue
            new_rows.append(row)
        _write_jsonl(self.root / "snapshots.jsonl", (_payload(item) for item in new_rows), append=True)
        return tuple(new_rows)

    def raw(self) -> tuple[RawHistoricalEvidence, ...]:
        return tuple(_raw(item) for item in _read_jsonl(self.root / "raw.jsonl"))

    def normalized(self) -> tuple[NormalizedHistoricalEvidence, ...]:
        return tuple(_normalized(item) for item in _read_jsonl(self.root / "normalized.jsonl"))

    def validations(self) -> tuple[HistoricalEvidenceValidation, ...]:
        return tuple(_validation(item) for item in _read_jsonl(self.root / "validations.jsonl"))

    def runs(self) -> tuple[HistoricalAcquisitionRun, ...]:
        return tuple(_run(item) for item in _read_jsonl(self.root / "runs.jsonl"))

    def snapshots(
        self,
        *,
        project_id: str | None = None,
        engine: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        case_id: str | None = None,
    ) -> tuple[HistoricalEngineSnapshot, ...]:
        rows = tuple(_snapshot(item) for item in _read_jsonl(self.root / "snapshots.jsonl"))
        if project_id is not None:
            rows = tuple(item for item in rows if item.project_id == project_id)
        if engine is not None:
            rows = tuple(item for item in rows if item.engine == engine)
        if case_id is not None:
            rows = tuple(item for item in rows if item.case_id == case_id)
        if start is not None:
            rows = tuple(item for item in rows if item.effective_timestamp >= start)
        if end is not None:
            rows = tuple(item for item in rows if item.effective_timestamp <= end)
        return tuple(sorted(rows, key=lambda item: (item.case_id, item.engine, item.effective_timestamp)))


def _unique_normalized(
    rows: tuple[NormalizedHistoricalEvidence, ...],
    existing: set[str],
) -> tuple[NormalizedHistoricalEvidence, ...]:
    seen: set[str] = set()
    unique = []
    for item in rows:
        if item.evidence_id in existing or item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        unique.append(item)
    return tuple(unique)


def _unique_validations(
    rows: tuple[HistoricalEvidenceValidation, ...],
    existing: set[str],
) -> tuple[HistoricalEvidenceValidation, ...]:
    seen: set[str] = set()
    unique = []
    for item in rows:
        if item.evidence_id in existing or item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        unique.append(item)
    return tuple(unique)


def _payload(item: Any) -> dict[str, Any]:
    if not is_dataclass(item):
        msg = "historical acquisition payload must be a dataclass"
        raise TypeError(msg)
    payload = asdict(item)
    return {str(key): _jsonable(value) for key, value in payload.items()}


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _raw(payload: dict[str, Any]) -> RawHistoricalEvidence:
    return RawHistoricalEvidence(
        provider=str(payload["provider"]),
        collector=str(payload["collector"]),
        raw_source_id=str(payload["raw_source_id"]),
        case_id=str(payload["case_id"]),
        project_id=str(payload["project_id"]),
        metric=str(payload["metric"]),
        event_timestamp=_dt(payload["event_timestamp"]),
        publication_timestamp=_dt(payload["publication_timestamp"]),
        data_availability_timestamp=_dt(payload["data_availability_timestamp"]),
        retrieval_timestamp=_dt(payload["retrieval_timestamp"]),
        payload=dict(payload["payload"]),
        source_url=str(payload["source_url"]),
        repository_id=str(payload["repository_id"]),
    )


def _normalized(payload: dict[str, Any]) -> NormalizedHistoricalEvidence:
    return NormalizedHistoricalEvidence(
        evidence_id=str(payload["evidence_id"]),
        repository_id=str(payload["repository_id"]),
        provider=str(payload["provider"]),
        collector=str(payload["collector"]),
        raw_source_id=str(payload["raw_source_id"]),
        case_id=str(payload["case_id"]),
        project_id=str(payload["project_id"]),
        engine=str(payload["engine"]),
        metric=str(payload["metric"]),
        event_timestamp=_dt(payload["event_timestamp"]),
        publication_timestamp=_dt(payload["publication_timestamp"]),
        data_availability_timestamp=_dt(payload["data_availability_timestamp"]),
        retrieval_timestamp=_dt(payload["retrieval_timestamp"]),
        raw_metrics=dict(payload["raw_metrics"]),
        normalized_metrics={str(k): float(v) for k, v in dict(payload["normalized_metrics"]).items()},
        source_url=str(payload["source_url"]),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
    )


def _validation(payload: dict[str, Any]) -> HistoricalEvidenceValidation:
    return HistoricalEvidenceValidation(
        evidence_id=str(payload["evidence_id"]),
        status=str(payload["status"]),  # type: ignore[arg-type]
        validated_at=_dt(payload["validated_at"]),
        reason=str(payload.get("reason", "")),
    )


def _run(payload: dict[str, Any]) -> HistoricalAcquisitionRun:
    return HistoricalAcquisitionRun(
        run_id=str(payload["run_id"]),
        provider=str(payload["provider"]),
        started_at=_dt(payload["started_at"]),
        finished_at=_dt(payload["finished_at"]),
        raw_count=int(payload["raw_count"]),
        normalized_count=int(payload["normalized_count"]),
        valid_count=int(payload["valid_count"]),
        invalid_count=int(payload["invalid_count"]),
        duplicate_count=int(payload["duplicate_count"]),
    )


def _snapshot(payload: dict[str, Any]) -> HistoricalEngineSnapshot:
    return HistoricalEngineSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        acquisition_id=str(payload["acquisition_id"]),
        project_id=str(payload["project_id"]),
        case_id=str(payload["case_id"]),
        engine=str(payload["engine"]),
        acquisition_timestamp=_dt(payload["acquisition_timestamp"]),
        observation_timestamp=_dt(payload["observation_timestamp"]),
        effective_timestamp=_dt(payload["effective_timestamp"]),
        source_id=str(payload["source_id"]),
        provider=str(payload["provider"]),
        provider_version=str(payload["provider_version"]),
        historical_snapshot=dict(payload.get("historical_snapshot", {})),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
        quality=float(payload["quality"]),
        reconstruction_confidence=float(payload["reconstruction_confidence"]),
        missing_fields=tuple(payload.get("missing_fields", ())),
        status=str(payload["status"]),  # type: ignore[arg-type]
        validation_errors=tuple(payload.get("validation_errors", ())),
    )


def _dt(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _write_jsonl(path: Path, rows: Any, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
