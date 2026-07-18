from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from hunter.jsonl_contract import JsonlRecord, JsonlWritePlan, envelope, read_records, strict_known
from hunter.whale.models import WhaleEvidence, WhaleMetric, WhaleProviderFailure, WhaleSnapshot

WHALE_JSONL_SCHEMA = "hunter-whale-jsonl-v1"


class WhaleRepository:
    def __init__(self, root: str | Path = "data/whale") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.root / "raw.jsonl"
        self.normalized_path = self.root / "normalized.jsonl"
        self.validation_path = self.root / "validations.jsonl"
        self.snapshot_path = self.root / "snapshots.jsonl"
        self.run_path = self.root / "runs.jsonl"
        self.failure_path = self.root / "failures.jsonl"

    def save_evidence(
        self, evidence: tuple[WhaleEvidence, ...], *, write_plan: JsonlWritePlan | None = None
    ) -> tuple[WhaleEvidence, ...]:
        existing = {item.evidence_id for item in self.evidence()}
        validations = {row["evidence_id"]: row for row in self._read(self.validation_path)}
        for item in evidence:
            if item.evidence_id in existing:
                current = validations.get(item.evidence_id, {})
                if (
                    current.get("status") != item.validation_status
                    or tuple(current.get("errors", ())) != item.validation_errors
                ):
                    self._append(self.validation_path, _validation_payload(item), write_plan=write_plan)
                continue
            self._append(self.raw_path, _metric_payload(item), write_plan=write_plan)
            self._append(
                self.normalized_path,
                {
                    "evidence_id": item.evidence_id,
                    "repository_id": item.repository_id,
                    "metric": item.metric.name,
                    "asset": item.metric.asset,
                    "normalized_value": item.normalized_value,
                },
                write_plan=write_plan,
            )
            self._append(self.validation_path, _validation_payload(item), write_plan=write_plan)
        return evidence

    def save_snapshot(self, snapshot: WhaleSnapshot, *, write_plan: JsonlWritePlan | None = None) -> WhaleSnapshot:
        if snapshot.snapshot_id not in {item.snapshot_id for item in self.snapshots()}:
            self._append(self.snapshot_path, _snapshot_payload(snapshot), write_plan=write_plan)
            self._append(
                self.run_path,
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "generated_at": snapshot.generated_at.isoformat(),
                    "metrics": len(snapshot.evidence),
                    "valid": sum(1 for item in snapshot.evidence if item.validation_status == "VALID"),
                },
                write_plan=write_plan,
            )
        return snapshot

    def save_failures(
        self, failures: tuple[WhaleProviderFailure, ...], *, write_plan: JsonlWritePlan | None = None
    ) -> tuple[WhaleProviderFailure, ...]:
        for failure in failures:
            self._append(
                self.failure_path,
                {
                    "provider": failure.provider,
                    "metric": failure.metric,
                    "reason": failure.reason,
                    "message": failure.message,
                    "source_url": failure.source_url,
                    "occurred_at": failure.occurred_at.isoformat(),
                },
                write_plan=write_plan,
            )
        return failures

    def evidence(self) -> tuple[WhaleEvidence, ...]:
        rows = []
        validations = {row["evidence_id"]: row for row in self._read(self.validation_path)}
        normalized = {row["evidence_id"]: row for row in self._read(self.normalized_path)}
        for row in self._read(self.raw_path):
            evidence_id = str(row["evidence_id"])
            validation = validations.get(evidence_id, {})
            norm = normalized.get(evidence_id, {})
            rows.append(
                WhaleEvidence(
                    evidence_id=evidence_id,
                    repository_id=str(row["repository_id"]),
                    metric=WhaleMetric(
                        name=str(row["metric"]),
                        provider=str(row["provider"]),
                        source_url=str(row["source_url"]),
                        asset=str(row["asset"]),
                        timestamp=datetime.fromisoformat(str(row["timestamp"])),
                        retrieval_time=datetime.fromisoformat(str(row["retrieval_time"])),
                        value=float(row["value"]),
                        raw_payload=dict(row.get("raw_payload", {})),
                        wallet_label=row.get("wallet_label"),
                        confidence=float(row.get("confidence", 1.0)),
                        freshness=float(row.get("freshness", 1.0)),
                    ),
                    normalized_value=float(norm.get("normalized_value", 0.0)),
                    validation_status=str(validation.get("status", "INVALID")),
                    validation_errors=tuple(str(item) for item in validation.get("errors", ())),
                )
            )
        return tuple(rows)

    def snapshots(self) -> tuple[WhaleSnapshot, ...]:
        rows = []
        evidence_by_id = {item.evidence_id: item for item in self.evidence()}
        for row in self._read(self.snapshot_path):
            evidence = tuple(evidence_by_id[eid] for eid in row.get("evidence_ids", ()) if eid in evidence_by_id)
            rows.append(
                WhaleSnapshot(
                    snapshot_id=str(row["snapshot_id"]),
                    generated_at=datetime.fromisoformat(str(row["generated_at"])),
                    evidence=evidence,
                    whale_score=float(row["whale_score"]),
                    accumulation_score=float(row["accumulation_score"]),
                    distribution_score=float(row["distribution_score"]),
                    exchange_pressure=float(row["exchange_pressure"]),
                    smart_money_score=float(row["smart_money_score"]),
                    stablecoin_pressure=float(row["stablecoin_pressure"]),
                    institutional_score=float(row["institutional_score"]),
                    market_participation=float(row["market_participation"]),
                    confidence=float(row["confidence"]),
                    freshness=float(row["freshness"]),
                    evidence_quality=float(row["evidence_quality"]),
                    raw_metrics=dict(row.get("raw_metrics", {})),
                    normalized_metrics=dict(row.get("normalized_metrics", {})),
                )
            )
        return tuple(rows)

    def latest_snapshot(self) -> WhaleSnapshot | None:
        snapshots = self.snapshots()
        return snapshots[-1] if snapshots else None

    def failures(self) -> tuple[WhaleProviderFailure, ...]:
        return tuple(
            WhaleProviderFailure(
                provider=str(row["provider"]),
                metric=str(row["metric"]),
                reason=str(row["reason"]),
                message=str(row["message"]),
                source_url=str(row["source_url"]),
                occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            )
            for row in self._read(self.failure_path)
        )

    def records(self, path: Path) -> tuple[JsonlRecord, ...]:
        return read_records(path, supported_schema=WHALE_JSONL_SCHEMA)

    def strict_known_records(self, path: Path, *, as_of: datetime) -> tuple[JsonlRecord, ...]:
        return strict_known(self.records(path), as_of=as_of)

    def _append(self, path: Path, payload: dict[str, Any], *, write_plan: JsonlWritePlan | None = None) -> None:
        if write_plan is not None:
            payload = envelope(payload, write_plan)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")

    def _read(self, path: Path) -> tuple[dict[str, Any], ...]:
        return tuple(record.payload for record in self.records(path))


def _metric_payload(evidence: WhaleEvidence) -> dict[str, Any]:
    metric = evidence.metric
    return {
        "evidence_id": evidence.evidence_id,
        "repository_id": evidence.repository_id,
        "metric": metric.name,
        "provider": metric.provider,
        "source_url": metric.source_url,
        "asset": metric.asset,
        "timestamp": metric.timestamp.isoformat(),
        "retrieval_time": metric.retrieval_time.isoformat(),
        "value": metric.value,
        "wallet_label": metric.wallet_label,
        "raw_payload": dict(metric.raw_payload),
        "confidence": metric.confidence,
        "freshness": metric.freshness,
    }


def _validation_payload(evidence: WhaleEvidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "status": evidence.validation_status,
        "errors": evidence.validation_errors,
    }


def _snapshot_payload(snapshot: WhaleSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "evidence_ids": tuple(item.evidence_id for item in snapshot.evidence),
        "whale_score": snapshot.whale_score,
        "accumulation_score": snapshot.accumulation_score,
        "distribution_score": snapshot.distribution_score,
        "exchange_pressure": snapshot.exchange_pressure,
        "smart_money_score": snapshot.smart_money_score,
        "stablecoin_pressure": snapshot.stablecoin_pressure,
        "institutional_score": snapshot.institutional_score,
        "market_participation": snapshot.market_participation,
        "confidence": snapshot.confidence,
        "freshness": snapshot.freshness,
        "evidence_quality": snapshot.evidence_quality,
        "raw_metrics": dict(snapshot.raw_metrics),
        "normalized_metrics": dict(snapshot.normalized_metrics),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return dict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value
