from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from hunter.evidence_intelligence.repository import (
    EVIDENCE_INTELLIGENCE_TABLES,
)
from hunter.evidence_intelligence.repository import (
    SCHEMA_SQL as EVIDENCE_SCHEMA_SQL,
)
from hunter.sufficiency.migrations import SUFFICIENCY_MIGRATION_ID, migrate_data_sufficiency_schema
from hunter.sufficiency.repository import SUFFICIENCY_TABLES
from hunter.tokenomics.migrations import TOKENOMICS_MIGRATION_ID, migrate_tokenomics_schema
from hunter.tokenomics.repository import TABLES as TOKENOMICS_TABLES

StoreName = Literal["tokenomics", "evidence_intelligence", "sufficiency"]
ReadinessState = Literal[
    "unavailable",
    "absent",
    "schema_only",
    "populated",
    "migration_required",
    "unreachable",
    "failed",
]

EVIDENCE_INTELLIGENCE_SCHEMA_ID = "evidence-intelligence-schema-current"
CLI_STORE_NAMES = ("tokenomics", "evidence-intelligence", "sufficiency")


@dataclass(frozen=True, slots=True)
class AnalyticalStoreReadiness:
    store: StoreName
    state: ReadinessState
    reason: str
    path: str | None
    schema_id: str
    schema_status: Literal["unavailable", "absent", "current", "mismatch", "unreadable"]
    analytical_record_count: int | None
    table_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _StoreDefinition:
    tables: frozenset[str]
    schema_id: str


_STORES: dict[StoreName, _StoreDefinition] = {
    "tokenomics": _StoreDefinition(TOKENOMICS_TABLES, TOKENOMICS_MIGRATION_ID),
    "evidence_intelligence": _StoreDefinition(
        EVIDENCE_INTELLIGENCE_TABLES,
        EVIDENCE_INTELLIGENCE_SCHEMA_ID,
    ),
    "sufficiency": _StoreDefinition(SUFFICIENCY_TABLES, SUFFICIENCY_MIGRATION_ID),
}


def inspect_store(store: StoreName, path: str | Path | None) -> AnalyticalStoreReadiness:
    definition = _STORES[store]
    if path is None:
        return _result(store, "unavailable", "no store path was supplied", None, definition, "unavailable")

    store_path = Path(path)
    if not store_path.exists():
        return _result(store, "absent", "store file does not exist", store_path, definition, "absent")
    if not store_path.is_file():
        return _result(store, "unreachable", "configured path is not a file", store_path, definition, "unreadable")

    try:
        with _read_only_connection(store_path) as conn:
            integrity = str(conn.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                return _result(
                    store,
                    "failed",
                    f"SQLite integrity check failed: {integrity}",
                    store_path,
                    definition,
                    "unreadable",
                )
            present = {
                str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            missing = sorted(definition.tables - present)
            if missing:
                return _result(
                    store,
                    "migration_required",
                    f"required schema objects are missing: {', '.join(missing)}",
                    store_path,
                    definition,
                    "mismatch",
                )
            counts = {
                table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in sorted(definition.tables)
            }
    except sqlite3.DatabaseError as exc:
        return _result(
            store,
            "failed",
            f"store is not a valid readable SQLite database: {exc}",
            store_path,
            definition,
            "unreadable",
        )
    except OSError as exc:
        return _result(store, "unreachable", f"store cannot be reached: {exc}", store_path, definition, "unreadable")

    total = sum(counts.values())
    if total == 0:
        return _result(
            store,
            "schema_only",
            "schema is current and all data-bearing tables are empty",
            store_path,
            definition,
            "current",
            counts,
        )
    return _result(
        store,
        "populated",
        "schema is current and stored records are present; readiness does not interpret them",
        store_path,
        definition,
        "current",
        counts,
    )


def bootstrap_store(store: StoreName, path: str | Path) -> AnalyticalStoreReadiness:
    store_path = Path(path)
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(store_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            if store == "tokenomics":
                migrate_tokenomics_schema(conn)
            elif store == "evidence_intelligence":
                conn.executescript(EVIDENCE_SCHEMA_SQL)
            else:
                migrate_data_sufficiency_schema(conn)
            conn.commit()
    except sqlite3.DatabaseError as exc:
        definition = _STORES[store]
        return _result(store, "failed", f"bootstrap failed: {exc}", store_path, definition, "unreadable")
    except OSError as exc:
        definition = _STORES[store]
        return _result(
            store, "unreachable", f"bootstrap path cannot be reached: {exc}", store_path, definition, "unreadable"
        )
    return inspect_store(store, store_path)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _result(
    store: StoreName,
    state: ReadinessState,
    reason: str,
    path: Path | None,
    definition: _StoreDefinition,
    schema_status: Literal["unavailable", "absent", "current", "mismatch", "unreadable"],
    counts: dict[str, int] | None = None,
) -> AnalyticalStoreReadiness:
    table_counts = counts or {}
    return AnalyticalStoreReadiness(
        store=store,
        state=state,
        reason=reason,
        path=str(path) if path is not None else None,
        schema_id=definition.schema_id,
        schema_status=schema_status,
        analytical_record_count=sum(table_counts.values()) if counts is not None else None,
        table_counts=table_counts,
    )
