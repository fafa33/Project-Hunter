from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.acquisition.models import (
    AcquisitionCheckpoint,
    AcquisitionRun,
    EvidenceValidation,
    NormalizedEvidence,
    RawEvidence,
    ValidationIssue,
)
from hunter.jsonl_contract import JsonlRecord, JsonlWritePlan, envelope, read_records, strict_known

ACQUISITION_JSONL_SCHEMA = "hunter-acquisition-jsonl-v1"


class InMemoryAcquisitionRepository:
    def __init__(self) -> None:
        self.raw: dict[str, RawEvidence] = {}
        self.normalized: dict[str, NormalizedEvidence] = {}
        self.validations: dict[str, EvidenceValidation] = {}
        self.runs: dict[str, AcquisitionRun] = {}
        self._run_history: list[AcquisitionRun] = []
        self.checkpoints: dict[tuple[str, str, str], AcquisitionCheckpoint] = {}

    def save_raw(self, raw: tuple[RawEvidence, ...]) -> tuple[RawEvidence, ...]:
        for item in raw:
            self.raw[_raw_key(item)] = item
        return raw

    def save_normalized(self, evidence: tuple[NormalizedEvidence, ...]) -> tuple[NormalizedEvidence, ...]:
        for item in evidence:
            self.normalized[item.evidence_id] = item
        return evidence

    def save_validations(self, validations: tuple[EvidenceValidation, ...]) -> tuple[EvidenceValidation, ...]:
        for item in validations:
            self.validations[item.evidence_id] = item
        return validations

    def save_run(self, run: AcquisitionRun) -> AcquisitionRun:
        self.runs[run.run_id] = run
        self._run_history.append(run)
        return run

    def save_checkpoint(self, checkpoint: AcquisitionCheckpoint) -> AcquisitionCheckpoint:
        self.checkpoints[(checkpoint.provider, checkpoint.domain, checkpoint.target_id)] = checkpoint
        return checkpoint

    def latest_checkpoint(self, provider: str, domain: str, target_id: str) -> AcquisitionCheckpoint | None:
        return self.checkpoints.get((provider, domain, target_id))

    def history(self) -> tuple[AcquisitionRun, ...]:
        return tuple(sorted(self._run_history, key=lambda item: (item.started_at, item.run_id)))


def _raw_key(item: RawEvidence) -> str:
    return f"{item.provider}:{item.collector}:{item.domain}:{item.metric}:{item.target_id}:{item.raw_source_id}"


class FileAcquisitionRepository(InMemoryAcquisitionRepository):
    def __init__(self, root: str | Path = "data/acquisition") -> None:
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def save_raw(
        self, raw: tuple[RawEvidence, ...], *, write_plan: JsonlWritePlan | None = None
    ) -> tuple[RawEvidence, ...]:
        saved = super().save_raw(raw)
        self._append("raw.jsonl", (_raw_payload(item) for item in raw), write_plan=write_plan)
        return saved

    def save_normalized(
        self, evidence: tuple[NormalizedEvidence, ...], *, write_plan: JsonlWritePlan | None = None
    ) -> tuple[NormalizedEvidence, ...]:
        saved = super().save_normalized(evidence)
        self._append("normalized.jsonl", (_normalized_payload(item) for item in evidence), write_plan=write_plan)
        return saved

    def save_validations(
        self, validations: tuple[EvidenceValidation, ...], *, write_plan: JsonlWritePlan | None = None
    ) -> tuple[EvidenceValidation, ...]:
        saved = super().save_validations(validations)
        self._append("validations.jsonl", (_validation_payload(item) for item in validations), write_plan=write_plan)
        return saved

    def save_run(self, run: AcquisitionRun, *, write_plan: JsonlWritePlan | None = None) -> AcquisitionRun:
        saved = super().save_run(run)
        self._append("runs.jsonl", (_run_payload(run),), write_plan=write_plan)
        return saved

    def save_checkpoint(
        self, checkpoint: AcquisitionCheckpoint, *, write_plan: JsonlWritePlan | None = None
    ) -> AcquisitionCheckpoint:
        saved = super().save_checkpoint(checkpoint)
        self._append("checkpoints.jsonl", (_checkpoint_payload(checkpoint),), write_plan=write_plan)
        return saved

    def records(self, name: str) -> tuple[JsonlRecord, ...]:
        return read_records(self.root / name, supported_schema=ACQUISITION_JSONL_SCHEMA)

    def strict_known_records(self, name: str, *, as_of: datetime) -> tuple[JsonlRecord, ...]:
        return strict_known(self.records(name), as_of=as_of)

    def _append(self, name: str, rows: object, *, write_plan: JsonlWritePlan | None = None) -> None:
        path = self.root / name
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:  # type: ignore[union-attr]
                if write_plan is not None:
                    row = envelope(row, write_plan)
                handle.write(json.dumps(row, sort_keys=True, default=_json_default))
                handle.write("\n")

    def _load_existing(self) -> None:
        for payload in _read_jsonl(self.root / "raw.jsonl"):
            raw = RawEvidence(
                provider=str(payload["provider"]),
                collector=str(payload["collector"]),
                raw_source_id=str(payload["raw_source_id"]),
                domain=str(payload["domain"]),
                metric=str(payload["metric"]),
                target_id=str(payload["target_id"]),
                retrieved_at=_datetime(str(payload["retrieved_at"])),
                payload=dict(payload["payload"]),
                source_url=str(payload.get("source_url", "")),
                repository_id=str(payload.get("repository_id", "")),
            )
            self.raw[_raw_key(raw)] = raw
        for payload in _read_jsonl(self.root / "normalized.jsonl"):
            normalized = NormalizedEvidence(
                evidence_id=str(payload["evidence_id"]),
                repository_id=str(payload["repository_id"]),
                provider=str(payload["provider"]),
                collector=str(payload["collector"]),
                raw_source_id=str(payload["raw_source_id"]),
                domain=str(payload["domain"]),
                metric=str(payload["metric"]),
                target_id=str(payload["target_id"]),
                value=payload["value"],
                raw_metrics=dict(payload["raw_metrics"]),
                normalized_metrics={str(k): float(v) for k, v in dict(payload["normalized_metrics"]).items()},
                source_url=str(payload["source_url"]),
                retrieved_at=_datetime(str(payload["retrieved_at"])),
                normalized_at=_datetime(str(payload["normalized_at"])),
                confidence=float(payload["confidence"]),
                freshness=float(payload["freshness"]),
                raw_evidence_id=str(payload.get("raw_evidence_id", "")),
            )
            self.normalized[normalized.evidence_id] = normalized
        for payload in _read_jsonl(self.root / "validations.jsonl"):
            validation = EvidenceValidation(
                evidence_id=str(payload["evidence_id"]),
                status=str(payload["status"]),  # type: ignore[arg-type]
                validated_at=_datetime(str(payload["validated_at"])),
                confidence=float(payload["confidence"]),
                freshness=float(payload["freshness"]),
                issues=_validation_issues(payload.get("issues", ())),
            )
            self.validations[validation.evidence_id] = validation
        for payload in _read_jsonl(self.root / "checkpoints.jsonl"):
            checkpoint = AcquisitionCheckpoint(
                provider=str(payload["provider"]),
                domain=str(payload["domain"]),
                target_id=str(payload["target_id"]),
                cursor=str(payload["cursor"]),
                updated_at=_datetime(str(payload["updated_at"])),
            )
            self.checkpoints[(checkpoint.provider, checkpoint.domain, checkpoint.target_id)] = checkpoint


def _raw_payload(item: RawEvidence) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "collector": item.collector,
        "raw_source_id": item.raw_source_id,
        "domain": item.domain,
        "metric": item.metric,
        "target_id": item.target_id,
        "retrieved_at": item.retrieved_at,
        "payload": dict(item.payload),
        "source_url": item.source_url,
        "repository_id": item.repository_id,
    }


def _normalized_payload(item: NormalizedEvidence) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "repository_id": item.repository_id,
        "provider": item.provider,
        "collector": item.collector,
        "raw_source_id": item.raw_source_id,
        "domain": item.domain,
        "metric": item.metric,
        "target_id": item.target_id,
        "value": item.value,
        "raw_metrics": dict(item.raw_metrics),
        "normalized_metrics": dict(item.normalized_metrics),
        "source_url": item.source_url,
        "retrieved_at": item.retrieved_at,
        "normalized_at": item.normalized_at,
        "confidence": item.confidence,
        "freshness": item.freshness,
        "raw_evidence_id": item.raw_evidence_id,
    }


def _validation_payload(item: EvidenceValidation) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "status": item.status,
        "validated_at": item.validated_at,
        "confidence": item.confidence,
        "freshness": item.freshness,
        "issues": tuple(asdict(issue) for issue in item.issues),
    }


def _run_payload(run: AcquisitionRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "provider": run.provider,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "raw_count": run.raw_count,
        "normalized_count": run.normalized_count,
        "valid_count": run.valid_count,
        "duplicate_count": run.duplicate_count,
        "stale_count": run.stale_count,
        "invalid_count": run.invalid_count,
    }


def _checkpoint_payload(item: AcquisitionCheckpoint) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "domain": item.domain,
        "target_id": item.target_id,
        "cursor": item.cursor,
        "updated_at": item.updated_at,
    }


def _validation_issues(raw: object) -> tuple[ValidationIssue, ...]:
    if not isinstance(raw, list | tuple):
        return ()
    issues = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        issues.append(
            ValidationIssue(
                code=str(item.get("code", "")),
                field=str(item.get("field", "")),
                message=str(item.get("message", "")),
            )
        )
    return tuple(issues)


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                payload.pop("_record_metadata", None)
            rows.append(payload)
    return tuple(row for row in rows if isinstance(row, dict))


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
