from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.value_capture.models import (
    EconomicClaimIdentity,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.providers import AcquisitionReceipt

DEFAULT_VALUE_CAPTURE_DB = Path("data/value_capture/runtime/value_capture.sqlite")
VALUE_CAPTURE_MIGRATION_ID = "supply-value-capture-v3.5.0-003"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS value_capture_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS value_capture_acquisition_receipts (
    acquisition_id TEXT PRIMARY KEY,
    receipt_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    capability TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_authority_tier TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    registry_fingerprint TEXT NOT NULL,
    signing_key_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fundamental_evidence_records (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    acquisition_id TEXT NOT NULL UNIQUE,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
    FOREIGN KEY(acquisition_id) REFERENCES value_capture_acquisition_receipts(acquisition_id),
    FOREIGN KEY(supersedes_record_id) REFERENCES fundamental_evidence_records(record_id)
);
CREATE TABLE IF NOT EXISTS supply_basis_snapshots (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    supply_basis_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    acquisition_id TEXT NOT NULL UNIQUE,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
    FOREIGN KEY(acquisition_id) REFERENCES value_capture_acquisition_receipts(acquisition_id),
    FOREIGN KEY(supersedes_record_id) REFERENCES supply_basis_snapshots(record_id)
);
CREATE INDEX IF NOT EXISTS supply_basis_strict_known_idx ON supply_basis_snapshots(
    entity_id,economic_claim_id,representation_id,supply_basis_type,effective_at,recorded_at,known_at
);
CREATE TABLE IF NOT EXISTS value_capture_rule_snapshots (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    acquisition_id TEXT NOT NULL UNIQUE,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
    FOREIGN KEY(acquisition_id) REFERENCES value_capture_acquisition_receipts(acquisition_id),
    FOREIGN KEY(supersedes_record_id) REFERENCES value_capture_rule_snapshots(record_id)
);
CREATE INDEX IF NOT EXISTS value_capture_rule_strict_known_idx ON value_capture_rule_snapshots(
    entity_id,economic_claim_id,representation_id,rule_type,effective_at,recorded_at,known_at
);
"""


class ValueCaptureIntegrityError(ValueError):
    pass


class SupplyAndValueCaptureRepository:
    def __init__(self, path: str | Path = DEFAULT_VALUE_CAPTURE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.__initialize()

    def __initialize(self) -> None:
        with self.__connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO value_capture_schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (VALUE_CAPTURE_MIGRATION_ID, datetime.now(UTC).isoformat()),
            )

    def __connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def evidence(self, record_id: str) -> FundamentalEvidenceRecord | None:
        payload = self._payload("fundamental_evidence_records", record_id)
        return _evidence_from_payload(payload) if payload is not None else None

    def supply(self, record_id: str) -> SupplyBasisSnapshot | None:
        payload = self._payload("supply_basis_snapshots", record_id)
        return _supply_from_payload(payload) if payload is not None else None

    def rule(self, record_id: str) -> ValueCaptureRuleSnapshot | None:
        payload = self._payload("value_capture_rule_snapshots", record_id)
        return _rule_from_payload(payload) if payload is not None else None

    def receipt(self, acquisition_id: str) -> AcquisitionReceipt | None:
        with self.__connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM value_capture_acquisition_receipts WHERE acquisition_id = ?",
                (acquisition_id,),
            ).fetchone()
        return _receipt_from_payload(json.loads(str(row["payload_json"]))) if row is not None else None

    def count(self, table: str) -> int:
        allowed = {
            "value_capture_acquisition_receipts",
            "fundamental_evidence_records",
            "supply_basis_snapshots",
            "value_capture_rule_snapshots",
            "value_capture_schema_migrations",
        }
        if table not in allowed:
            raise ValueError("unsupported value-capture table")
        with self.__connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def strict_known_supply(self, **kwargs: Any) -> SupplyBasisSnapshot | None:
        payload = self._strict_known(
            table="supply_basis_snapshots",
            category_column="supply_basis_type",
            **kwargs,
        )
        return _supply_from_payload(payload) if payload is not None else None

    def strict_known_rule(self, **kwargs: Any) -> ValueCaptureRuleSnapshot | None:
        payload = self._strict_known(
            table="value_capture_rule_snapshots",
            category_column="rule_type",
            **kwargs,
        )
        return _rule_from_payload(payload) if payload is not None else None

    def _strict_known(
        self,
        *,
        table: str,
        category_column: str,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        effective_as_of: datetime,
        known_by: datetime,
        **category: str,
    ) -> dict[str, Any] | None:
        category_value = category[category_column]
        effective = _aware(effective_as_of).isoformat()
        known = _aware(known_by).isoformat()
        with self.__connect() as conn:
            row = conn.execute(
                f"""
                SELECT current.payload_json FROM {table} AS current
                WHERE current.entity_id = ? AND current.economic_claim_id = ?
                  AND current.representation_id = ? AND current.{category_column} = ?
                  AND current.effective_at <= ? AND current.recorded_at <= ? AND current.known_at <= ?
                  AND current.quality_state = 'accepted' AND current.conflict_state IN ('none','resolved')
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS successor
                      WHERE successor.supersedes_record_id = current.record_id
                        AND successor.recorded_at <= ? AND successor.known_at <= ?
                  )
                ORDER BY current.effective_at DESC,current.recorded_at DESC,current.known_at DESC,current.record_id DESC
                LIMIT 1
                """,
                (
                    entity_id,
                    economic_claim_id,
                    representation_id,
                    category_value,
                    effective,
                    known,
                    known,
                    known,
                    known,
                ),
            ).fetchone()
        return json.loads(str(row["payload_json"])) if row is not None else None

    def _payload(self, table: str, record_id: str) -> dict[str, Any] | None:
        with self.__connect() as conn:
            row = conn.execute(f"SELECT payload_json FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
        return json.loads(str(row["payload_json"])) if row is not None else None


def _identity(payload: dict[str, Any]) -> EconomicClaimIdentity:
    return EconomicClaimIdentity(**payload)


def _base_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(result[name]).astimezone(UTC)
    return result


def _receipt_from_payload(payload: dict[str, Any]) -> AcquisitionReceipt:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    result["acquired_at"] = datetime.fromisoformat(result["acquired_at"]).astimezone(UTC)
    return AcquisitionReceipt(**result)


def _evidence_from_payload(payload: dict[str, Any]) -> FundamentalEvidenceRecord:
    return FundamentalEvidenceRecord(**_base_payload(payload))


def _supply_from_payload(payload: dict[str, Any]) -> SupplyBasisSnapshot:
    result = _base_payload(payload)
    result["evidence_record_ids"] = tuple(result["evidence_record_ids"])
    return SupplyBasisSnapshot(**result)


def _rule_from_payload(payload: dict[str, Any]) -> ValueCaptureRuleSnapshot:
    result = _base_payload(payload)
    result["evidence_record_ids"] = tuple(result["evidence_record_ids"])
    return ValueCaptureRuleSnapshot(**result)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
