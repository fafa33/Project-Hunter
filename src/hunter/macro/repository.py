from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from hunter.macro.models import MacroEvidence, MacroMetric, MacroProviderFailure, MacroSnapshot


class MacroRepository:
    def __init__(self, root: str | Path = "data/macro") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.root / "raw.jsonl"
        self.normalized_path = self.root / "normalized.jsonl"
        self.validation_path = self.root / "validations.jsonl"
        self.snapshot_path = self.root / "snapshots.jsonl"
        self.run_path = self.root / "runs.jsonl"
        self.failure_path = self.root / "failures.jsonl"

    def save_evidence(self, evidence: tuple[MacroEvidence, ...]) -> tuple[MacroEvidence, ...]:
        existing = {item.evidence_id for item in self.evidence()}
        validations = {row["evidence_id"]: row for row in self._read(self.validation_path)}
        for item in evidence:
            if item.evidence_id in existing:
                current = validations.get(item.evidence_id, {})
                if (
                    current.get("status") != item.validation_status
                    or tuple(current.get("errors", ())) != item.validation_errors
                ):
                    self._append(
                        self.validation_path,
                        {
                            "evidence_id": item.evidence_id,
                            "status": item.validation_status,
                            "errors": item.validation_errors,
                        },
                    )
                continue
            self._append(self.raw_path, _metric_payload(item))
            self._append(
                self.normalized_path,
                {
                    "evidence_id": item.evidence_id,
                    "repository_id": item.repository_id,
                    "metric": item.metric.name,
                    "normalized_value": item.normalized_value,
                },
            )
            self._append(
                self.validation_path,
                {
                    "evidence_id": item.evidence_id,
                    "status": item.validation_status,
                    "errors": item.validation_errors,
                },
            )
        return evidence

    def save_snapshot(self, snapshot: MacroSnapshot) -> MacroSnapshot:
        if snapshot.snapshot_id not in {item.snapshot_id for item in self.snapshots()}:
            self._append(self.snapshot_path, _snapshot_payload(snapshot))
            self._append(
                self.run_path,
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "generated_at": snapshot.generated_at.isoformat(),
                    "metrics": len(snapshot.evidence),
                    "valid": sum(1 for item in snapshot.evidence if item.validation_status == "VALID"),
                },
            )
        return snapshot

    def save_failures(self, failures: tuple[MacroProviderFailure, ...]) -> tuple[MacroProviderFailure, ...]:
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
            )
        return failures

    def evidence(self) -> tuple[MacroEvidence, ...]:
        rows = []
        validations = {row["evidence_id"]: row for row in self._read(self.validation_path)}
        normalized = {row["evidence_id"]: row for row in self._read(self.normalized_path)}
        for row in self._read(self.raw_path):
            evidence_id = str(row["evidence_id"])
            validation = validations.get(evidence_id, {})
            norm = normalized.get(evidence_id, {})
            rows.append(
                MacroEvidence(
                    evidence_id=evidence_id,
                    repository_id=str(row["repository_id"]),
                    metric=MacroMetric(
                        name=str(row["metric"]),
                        provider=str(row["provider"]),
                        source_url=str(row["source_url"]),
                        timestamp=datetime.fromisoformat(str(row["timestamp"])),
                        value=float(row["value"]),
                        raw_payload=dict(row.get("raw_payload", {})),
                        confidence=float(row.get("confidence", 1.0)),
                        freshness=float(row.get("freshness", 1.0)),
                    ),
                    normalized_value=float(norm.get("normalized_value", 0.0)),
                    validation_status=str(validation.get("status", "INVALID")),
                    validation_errors=tuple(str(item) for item in validation.get("errors", ())),
                )
            )
        return tuple(rows)

    def snapshots(self) -> tuple[MacroSnapshot, ...]:
        rows = []
        evidence_by_id = {item.evidence_id: item for item in self.evidence()}
        for row in self._read(self.snapshot_path):
            evidence = tuple(evidence_by_id[eid] for eid in row.get("evidence_ids", ()) if eid in evidence_by_id)
            rows.append(
                MacroSnapshot(
                    snapshot_id=str(row["snapshot_id"]),
                    generated_at=datetime.fromisoformat(str(row["generated_at"])),
                    evidence=evidence,
                    liquidity_score=float(row["liquidity_score"]),
                    inflation_score=float(row["inflation_score"]),
                    monetary_policy_score=float(row["monetary_policy_score"]),
                    recession_probability=float(row["recession_probability"]),
                    risk_on_score=float(row["risk_on_score"]),
                    risk_off_score=float(row["risk_off_score"]),
                    crypto_liquidity_score=float(row["crypto_liquidity_score"]),
                    macro_confidence=float(row["macro_confidence"]),
                    freshness=float(row["freshness"]),
                    evidence_quality=float(row["evidence_quality"]),
                    raw_metrics=dict(row.get("raw_metrics", {})),
                    normalized_metrics=dict(row.get("normalized_metrics", {})),
                )
            )
        return tuple(rows)

    def latest_snapshot(self) -> MacroSnapshot | None:
        snapshots = self.snapshots()
        return snapshots[-1] if snapshots else None

    def failures(self) -> tuple[MacroProviderFailure, ...]:
        return tuple(
            MacroProviderFailure(
                provider=str(row["provider"]),
                metric=str(row["metric"]),
                reason=str(row["reason"]),
                message=str(row["message"]),
                source_url=str(row["source_url"]),
                occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            )
            for row in self._read(self.failure_path)
        )

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")

    def _read(self, path: Path) -> tuple[dict[str, Any], ...]:
        if not path.exists():
            return ()
        return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _metric_payload(evidence: MacroEvidence) -> dict[str, Any]:
    metric = evidence.metric
    return {
        "evidence_id": evidence.evidence_id,
        "repository_id": evidence.repository_id,
        "metric": metric.name,
        "provider": metric.provider,
        "source_url": metric.source_url,
        "timestamp": metric.timestamp.isoformat(),
        "value": metric.value,
        "raw_payload": dict(metric.raw_payload),
        "confidence": metric.confidence,
        "freshness": metric.freshness,
    }


def _snapshot_payload(snapshot: MacroSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "evidence_ids": tuple(item.evidence_id for item in snapshot.evidence),
        "liquidity_score": snapshot.liquidity_score,
        "inflation_score": snapshot.inflation_score,
        "monetary_policy_score": snapshot.monetary_policy_score,
        "recession_probability": snapshot.recession_probability,
        "risk_on_score": snapshot.risk_on_score,
        "risk_off_score": snapshot.risk_off_score,
        "crypto_liquidity_score": snapshot.crypto_liquidity_score,
        "macro_confidence": snapshot.macro_confidence,
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
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value
