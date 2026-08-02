from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.evidence_assembly.models import AssembledFundamentalEvidenceRecord, AssemblyConflictRecord
from hunter.value_capture.models import EconomicClaimIdentity

EVIDENCE_ASSEMBLY_MIGRATION_ID = "canonical-evidence-assembly-v1"

_MIGRATION = """
CREATE TABLE IF NOT EXISTS evidence_assembly_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assembled_fundamental_evidence_records (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    supersedes_record_id TEXT,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assembled_evidence_logical_history
ON assembled_fundamental_evidence_records(logical_id, recorded_at, known_at, record_id);
CREATE INDEX IF NOT EXISTS ix_assembled_evidence_strict_known
ON assembled_fundamental_evidence_records(logical_id, effective_at, known_at, recorded_at, record_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_assembled_evidence_successor
ON assembled_fundamental_evidence_records(supersedes_record_id)
WHERE supersedes_record_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS evidence_assembly_conflicts (
    record_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    economic_claim_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_evidence_assembly_conflicts
ON evidence_assembly_conflicts(entity_id, economic_claim_id, known_at, record_id);
"""


class EvidenceAssemblyPersistenceError(ValueError):
    pass


class AssembledEvidenceRepository:
    """Mechanical append-only persistence and deterministic raw-history boundary."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_MIGRATION)
            connection.execute(
                "INSERT OR IGNORE INTO evidence_assembly_schema_migrations(migration_id, applied_at) VALUES (?, ?)",
                (EVIDENCE_ASSEMBLY_MIGRATION_ID, datetime.now().astimezone().isoformat()),
            )

    def migration_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT migration_id FROM evidence_assembly_schema_migrations ORDER BY migration_id"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _insert_authorized(self, record: AssembledFundamentalEvidenceRecord) -> AssembledFundamentalEvidenceRecord:
        """Persist a record already authorized by CanonicalEvidenceAssemblyService."""
        payload = _canonical_payload(record)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM assembled_fundamental_evidence_records WHERE record_id = ?",
                (record.record_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) == payload:
                    return record
                raise EvidenceAssemblyPersistenceError("divergent duplicate record identity")
            try:
                connection.execute(
                    """
                    INSERT INTO assembled_fundamental_evidence_records(
                        record_id, logical_id, semantic_version, effective_at, recorded_at, known_at,
                        conflict_state, supersedes_record_id, content_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.record_id,
                        record.logical_id,
                        record.semantic_version,
                        record.effective_at.isoformat(),
                        record.recorded_at.isoformat(),
                        record.known_at.isoformat(),
                        record.conflict_state,
                        record.supersedes_record_id,
                        record.content_hash,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise EvidenceAssemblyPersistenceError("append-only identity or lineage conflict") from exc
        return record

    def get(self, record_id: str) -> AssembledFundamentalEvidenceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM assembled_fundamental_evidence_records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return _from_payload(str(row[0])) if row is not None else None

    def history(self, logical_id: str) -> tuple[AssembledFundamentalEvidenceRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM assembled_fundamental_evidence_records
                WHERE logical_id = ? ORDER BY recorded_at, known_at, record_id
                """,
                (logical_id,),
            ).fetchall()
        return tuple(_from_payload(str(row[0])) for row in rows)

    def successor(self, predecessor_id: str) -> AssembledFundamentalEvidenceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM assembled_fundamental_evidence_records WHERE supersedes_record_id = ?",
                (predecessor_id,),
            ).fetchone()
        return _from_payload(str(row[0])) if row is not None else None

    def assemblies_for_constituent(self, record_id: str) -> tuple[AssembledFundamentalEvidenceRecord, ...]:
        """Return provenance-complete assemblies containing an exact constituent ID."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM assembled_fundamental_evidence_records
                ORDER BY effective_at, recorded_at, known_at, record_id
                """
            ).fetchall()
        records = (_from_payload(str(row[0])) for row in rows)
        return tuple(record for record in records if record_id in record.constituent_record_ids)

    def _insert_conflict_authorized(self, record: AssemblyConflictRecord) -> AssemblyConflictRecord:
        payload = _canonical_conflict_payload(record)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM evidence_assembly_conflicts WHERE record_id = ?", (record.record_id,)
            ).fetchone()
            if existing is not None:
                if str(existing[0]) == payload:
                    return record
                raise EvidenceAssemblyPersistenceError("divergent duplicate conflict identity")
            connection.execute(
                """
                INSERT INTO evidence_assembly_conflicts(
                    record_id, entity_id, economic_claim_id, recorded_at, known_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.entity_id,
                    record.economic_claim_id,
                    record.recorded_at.isoformat(),
                    record.known_at.isoformat(),
                    payload,
                ),
            )
        return record

    def conflict_records(self) -> tuple[AssemblyConflictRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM evidence_assembly_conflicts ORDER BY known_at, record_id"
            ).fetchall()
        return tuple(_conflict_from_payload(str(row[0])) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _canonical_payload(record: AssembledFundamentalEvidenceRecord) -> str:
    payload = asdict(record)
    payload["identity"] = asdict(record.identity)
    for name in (
        "accounting_window_start",
        "accounting_window_end",
        "effective_at",
        "recorded_at",
        "known_at",
    ):
        payload[name] = getattr(record, name).isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _from_payload(payload_json: str) -> AssembledFundamentalEvidenceRecord:
    payload: dict[str, Any] = json.loads(payload_json)
    payload["identity"] = EconomicClaimIdentity(**payload["identity"])
    for name in (
        "accounting_window_start",
        "accounting_window_end",
        "effective_at",
        "recorded_at",
        "known_at",
    ):
        payload[name] = datetime.fromisoformat(payload[name])
    for name in (
        "constituent_record_ids",
        "constituent_logical_ids",
        "constituent_versions",
        "constituent_content_hashes",
        "constituent_source_ids",
        "constituent_source_references",
        "constituent_shape_ids",
    ):
        payload[name] = tuple(payload[name])
    return AssembledFundamentalEvidenceRecord(**payload)


def _canonical_conflict_payload(record: AssemblyConflictRecord) -> str:
    payload = asdict(record)
    for name in ("accounting_window_start", "accounting_window_end", "recorded_at", "known_at"):
        payload[name] = getattr(record, name).isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _conflict_from_payload(payload_json: str) -> AssemblyConflictRecord:
    payload: dict[str, Any] = json.loads(payload_json)
    for name in ("accounting_window_start", "accounting_window_end", "recorded_at", "known_at"):
        payload[name] = datetime.fromisoformat(payload[name])
    payload["candidate_record_ids"] = tuple(payload["candidate_record_ids"])
    return AssemblyConflictRecord(**payload)
