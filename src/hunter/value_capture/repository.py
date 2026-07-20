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
VALUE_CAPTURE_MIGRATION_ID = "supply-value-capture-v3.5.0-002"
Record = FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot

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
    parser_version TEXT NOT NULL,
    acquisition_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
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
    parser_version TEXT NOT NULL,
    acquisition_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
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
    acquisition_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT UNIQUE,
    payload_json TEXT NOT NULL,
    UNIQUE(logical_id, content_hash),
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
        self._initialize()

    def evidence(self, record_id: str) -> FundamentalEvidenceRecord | None:
        payload = self._payload("fundamental_evidence_records", record_id)
        return _evidence_from_payload(payload) if payload is not None else None

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
                SELECT current.payload_json
                FROM {table} AS current
                WHERE current.entity_id = ?
                  AND current.economic_claim_id = ?
                  AND current.representation_id = ?
                  AND current.{category_column} = ?
                  AND current.effective_at <= ?
                  AND current.recorded_at <= ?
                  AND current.known_at <= ?
                  AND current.quality_state = 'accepted'
                  AND current.conflict_state IN ('none','resolved')
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS successor
                      WHERE successor.supersedes_record_id = current.record_id
                        AND successor.recorded_at <= ?
                        AND successor.known_at <= ?
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
        with self._connect() as conn:
            row = conn.execute(f"SELECT payload_json FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
        return json.loads(str(row["payload_json"])) if row is not None else None

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


def _service_commit(
    repository: SupplyAndValueCaptureRepository, records: tuple[Record, ...], *, service_token: object
) -> None:
    from hunter.value_capture.service import _SERVICE_WRITE_TOKEN

    if service_token is not _SERVICE_WRITE_TOKEN:
        raise PermissionError("repository writes are service-authorized only")
    with repository._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                _insert(conn, _table_for(record), record)
        except Exception:
            conn.rollback()
            raise
        conn.commit()


def _insert(conn: sqlite3.Connection, table: str, record: Record) -> None:
    payload = _json_payload(record)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(f"SELECT payload_json FROM {table} WHERE record_id = ?", (record.record_id,)).fetchone()
    if existing is not None:
        if str(existing["payload_json"]) != canonical:
            raise ValueCaptureIntegrityError("record_id reused with divergent content")
        return
    predecessor = record.supersedes_record_id
    if predecessor is not None:
        row = conn.execute(
            f"SELECT logical_id,recorded_at,known_at FROM {table} WHERE record_id = ?", (predecessor,)
        ).fetchone()
        if row is None:
            raise ValueCaptureIntegrityError("superseded record does not exist")
        if str(row["logical_id"]) != record.logical_id:
            raise ValueCaptureIntegrityError("correction must preserve logical_id")
        if datetime.fromisoformat(str(row["recorded_at"])) >= record.recorded_at:
            raise ValueCaptureIntegrityError("correction recorded_at must follow predecessor")
        if datetime.fromisoformat(str(row["known_at"])) >= record.known_at:
            raise ValueCaptureIntegrityError("correction known_at must follow predecessor")
        successor = conn.execute(
            f"SELECT record_id FROM {table} WHERE supersedes_record_id = ?", (predecessor,)
        ).fetchone()
        if successor is not None:
            raise ValueCaptureIntegrityError("branching correction lineage is prohibited")
    category_column = None
    category_value = None
    if isinstance(record, SupplyBasisSnapshot):
        category_column, category_value = "supply_basis_type", record.supply_basis_type
    elif isinstance(record, ValueCaptureRuleSnapshot):
        category_column, category_value = "rule_type", record.rule_type
    columns = [
        "record_id",
        "logical_id",
        "entity_id",
        "economic_claim_id",
        "representation_id",
        "source_id",
        "parser_version",
        "acquisition_id",
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
        record.record_id,
        record.logical_id,
        record.identity.entity_id,
        record.identity.economic_claim_id,
        record.identity.representation_id,
        record.source_id,
        record.parser_version,
        record.acquisition_id,
        record.effective_at.isoformat(),
        record.recorded_at.isoformat(),
        record.known_at.isoformat(),
        record.quality_state,
        record.conflict_state,
        record.content_hash,
        predecessor,
        canonical,
    ]
    if category_column is not None:
        columns.insert(5, category_column)
        values.insert(5, category_value)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values),
    )


def _table_for(record: Record) -> str:
    if isinstance(record, FundamentalEvidenceRecord):
        return "fundamental_evidence_records"
    if isinstance(record, SupplyBasisSnapshot):
        return "supply_basis_snapshots"
    return "value_capture_rule_snapshots"


def _json_payload(record: Record) -> dict[str, Any]:
    payload = asdict(record)
    for name in ("effective_at", "recorded_at", "known_at"):
        payload[name] = _aware(payload[name]).isoformat()
    return payload


def _identity(payload: dict[str, Any]) -> EconomicClaimIdentity:
    return EconomicClaimIdentity(**payload)


def _base_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(result[name]).astimezone(UTC)
    return result


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
