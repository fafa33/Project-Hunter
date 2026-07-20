from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.value_capture.models import (
    EconomicClaimIdentity,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)

DEFAULT_VALUE_CAPTURE_DB = Path("data/value_capture/runtime/value_capture.sqlite")
VALUE_CAPTURE_MIGRATION_ID = "supply-value-capture-v3.5.0-001"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS value_capture_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamental_evidence_records (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
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
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
    FOREIGN KEY(supersedes_record_id) REFERENCES supply_basis_snapshots(record_id)
);

CREATE INDEX IF NOT EXISTS supply_basis_strict_known_idx
ON supply_basis_snapshots(
    entity_id,
    economic_claim_id,
    representation_id,
    supply_basis_type,
    effective_at,
    recorded_at,
    known_at
);

CREATE TABLE IF NOT EXISTS value_capture_rule_snapshots (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
    FOREIGN KEY(supersedes_record_id) REFERENCES value_capture_rule_snapshots(record_id)
);

CREATE INDEX IF NOT EXISTS value_capture_rule_strict_known_idx
ON value_capture_rule_snapshots(
    entity_id,
    economic_claim_id,
    representation_id,
    rule_type,
    effective_at,
    recorded_at,
    known_at
);
"""


class ValueCaptureIntegrityError(ValueError):
    pass


class ValueCaptureAuthorizationError(PermissionError):
    pass


class _RepositoryAuthority:
    pass


class ValueCaptureWritePlan:
    __slots__ = ("evidence", "supply", "rules", "_authority")

    def __init__(
        self,
        *,
        evidence: tuple[FundamentalEvidenceRecord, ...] = (),
        supply: tuple[SupplyBasisSnapshot, ...] = (),
        rules: tuple[ValueCaptureRuleSnapshot, ...] = (),
        authority: object,
    ) -> None:
        self.evidence = evidence
        self.supply = supply
        self.rules = rules
        self._authority = authority


class SupplyAndValueCaptureRepository:
    def __init__(self, path: str | Path = DEFAULT_VALUE_CAPTURE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._authority = _RepositoryAuthority()
        self._initialize()

    def apply(self, plan: ValueCaptureWritePlan) -> None:
        if plan._authority is not self._authority:
            raise ValueCaptureAuthorizationError("write plan was not authorized for this repository")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for record in plan.evidence:
                    self._insert(conn, "fundamental_evidence_records", record)
                for record in plan.supply:
                    self._insert(conn, "supply_basis_snapshots", record)
                for record in plan.rules:
                    self._insert(conn, "value_capture_rule_snapshots", record)
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    def evidence(self, record_id: str) -> FundamentalEvidenceRecord | None:
        payload = self._payload("fundamental_evidence_records", record_id)
        return _evidence_from_payload(payload) if payload is not None else None

    def strict_known_supply(
        self,
        *,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        supply_basis_type: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> SupplyBasisSnapshot | None:
        payload = self._strict_known(
            table="supply_basis_snapshots",
            category_column="supply_basis_type",
            category_value=supply_basis_type,
            entity_id=entity_id,
            economic_claim_id=economic_claim_id,
            representation_id=representation_id,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )
        return _supply_from_payload(payload) if payload is not None else None

    def strict_known_rule(
        self,
        *,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        rule_type: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> ValueCaptureRuleSnapshot | None:
        payload = self._strict_known(
            table="value_capture_rule_snapshots",
            category_column="rule_type",
            category_value=rule_type,
            entity_id=entity_id,
            economic_claim_id=economic_claim_id,
            representation_id=representation_id,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )
        return _rule_from_payload(payload) if payload is not None else None

    def count(self, table: str) -> int:
        allowed = {
            "fundamental_evidence_records",
            "supply_basis_snapshots",
            "value_capture_rule_snapshots",
            "value_capture_schema_migrations",
        }
        if table not in allowed:
            raise ValueError("unsupported value-capture table")
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _strict_known(
        self,
        *,
        table: str,
        category_column: str,
        category_value: str,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> dict[str, Any] | None:
        effective = _aware(effective_as_of).isoformat()
        known = _aware(known_by).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT payload_json FROM {table}
                WHERE entity_id = ?
                  AND economic_claim_id = ?
                  AND representation_id = ?
                  AND {category_column} = ?
                  AND effective_at <= ?
                  AND recorded_at <= ?
                  AND known_at <= ?
                  AND quality_state = 'accepted'
                  AND conflict_state IN ('none', 'resolved')
                ORDER BY effective_at DESC, recorded_at DESC, known_at DESC, record_id DESC
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
                ),
            ).fetchone()
        return json.loads(str(row["payload_json"])) if row is not None else None

    def _payload(self, table: str, record_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT payload_json FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
        return json.loads(str(row["payload_json"])) if row is not None else None

    def _insert(self, conn: sqlite3.Connection, table: str, record: object) -> None:
        payload = _json_payload(record)
        record_id = str(payload["record_id"])
        existing = conn.execute(f"SELECT payload_json FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if existing is not None:
            if str(existing["payload_json"]) != canonical:
                raise ValueCaptureIntegrityError("record_id reused with divergent content")
            return
        predecessor = payload.get("supersedes_record_id")
        if predecessor is not None:
            row = conn.execute(f"SELECT logical_id FROM {table} WHERE record_id = ?", (predecessor,)).fetchone()
            if row is None:
                raise ValueCaptureIntegrityError("superseded record does not exist")
            if str(row["logical_id"]) != str(payload["logical_id"]):
                raise ValueCaptureIntegrityError("correction must preserve logical_id")
        category_column = ""
        category_value = ""
        if table == "supply_basis_snapshots":
            category_column = "supply_basis_type"
            category_value = str(payload[category_column])
        elif table == "value_capture_rule_snapshots":
            category_column = "rule_type"
            category_value = str(payload[category_column])
        columns = [
            "record_id",
            "logical_id",
            "entity_id",
            "economic_claim_id",
            "representation_id",
            "source_id",
            "effective_at",
            "recorded_at",
            "known_at",
            "quality_state",
            "conflict_state",
            "content_hash",
            "supersedes_record_id",
            "payload_json",
        ]
        values: list[object] = [
            record_id,
            payload["logical_id"],
            payload["identity"]["entity_id"],
            payload["identity"]["economic_claim_id"],
            payload["identity"]["representation_id"],
            payload["source_id"],
            payload["effective_at"],
            payload["recorded_at"],
            payload["known_at"],
            payload["quality_state"],
            payload["conflict_state"],
            payload["content_hash"],
            predecessor,
            canonical,
        ]
        if category_column:
            columns.insert(5, category_column)
            values.insert(5, category_value)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(values),
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO value_capture_schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (VALUE_CAPTURE_MIGRATION_ID, datetime.now(UTC).isoformat()),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _json_payload(record: object) -> dict[str, Any]:
    payload = asdict(record)
    for name in ("effective_at", "recorded_at", "known_at"):
        payload[name] = _aware(payload[name]).isoformat()
    return payload


def _identity(payload: dict[str, Any]) -> EconomicClaimIdentity:
    return EconomicClaimIdentity(**payload)


def _evidence_from_payload(payload: dict[str, Any]) -> FundamentalEvidenceRecord:
    payload = dict(payload)
    payload["identity"] = _identity(payload["identity"])
    for name in ("effective_at", "recorded_at", "known_at"):
        payload[name] = datetime.fromisoformat(payload[name]).astimezone(UTC)
    return FundamentalEvidenceRecord(**payload)


def _supply_from_payload(payload: dict[str, Any]) -> SupplyBasisSnapshot:
    payload = dict(payload)
    payload["identity"] = _identity(payload["identity"])
    payload["evidence_record_ids"] = tuple(payload["evidence_record_ids"])
    for name in ("effective_at", "recorded_at", "known_at"):
        payload[name] = datetime.fromisoformat(payload[name]).astimezone(UTC)
    return SupplyBasisSnapshot(**payload)


def _rule_from_payload(payload: dict[str, Any]) -> ValueCaptureRuleSnapshot:
    payload = dict(payload)
    payload["identity"] = _identity(payload["identity"])
    payload["evidence_record_ids"] = tuple(payload["evidence_record_ids"])
    for name in ("effective_at", "recorded_at", "known_at"):
        payload[name] = datetime.fromisoformat(payload[name]).astimezone(UTC)
    return ValueCaptureRuleSnapshot(**payload)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
