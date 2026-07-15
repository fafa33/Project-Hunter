from __future__ import annotations

import sqlite3

from hunter.tokenomics.repository import SCHEMA_SQL

TOKENOMICS_MIGRATION_ID = "tokenomics-schema-v3.3.0-phase-a"


def migrate_tokenomics_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
