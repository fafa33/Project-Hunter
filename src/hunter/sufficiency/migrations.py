from __future__ import annotations

import sqlite3

from hunter.sufficiency.repository import SCHEMA_SQL

SUFFICIENCY_MIGRATION_ID = "data-sufficiency-schema-v1-phase4-cross-source-validation"


def migrate_data_sufficiency_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _add_column_if_missing(conn, "data_requirement_source_types", "effective_at", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "data_requirement_source_types", "recorded_at", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "data_requirement_proxy_types", "effective_at", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "data_requirement_proxy_types", "recorded_at", "TEXT NOT NULL DEFAULT ''")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
