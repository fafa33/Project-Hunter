from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.market_facts.models import (
    MarketFactAvailabilityEvent,
    MarketFactIdentity,
    ObservedMarketFactRecord,
)

DEFAULT_MARKET_FACTS_DB = Path("data/market_facts/runtime/market_facts.sqlite")
MARKET_FACTS_MIGRATION_ID = "market-facts-v3.4.0-001"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS market_fact_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observed_market_facts (
    record_id TEXT PRIMARY KEY,
    logical_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    provider_listing_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    value TEXT NOT NULL,
    unit TEXT NOT NULL,
    quote_currency TEXT,
    venue_scope TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    supersedes_record_id TEXT,
    correction_reason TEXT NOT NULL,
    UNIQUE (logical_id, content_hash),
    FOREIGN KEY (supersedes_record_id) REFERENCES observed_market_facts(record_id)
);

CREATE INDEX IF NOT EXISTS observed_market_facts_strict_known_idx
ON observed_market_facts(
    entity_id,
    representation_id,
    fact_type,
    quote_currency,
    effective_at,
    recorded_at,
    known_at
);

CREATE INDEX IF NOT EXISTS observed_market_facts_logical_lineage_idx
ON observed_market_facts(logical_id, recorded_at, known_at, record_id);

CREATE TABLE IF NOT EXISTS market_fact_availability_events (
    event_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    source_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    representation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    failure_reason TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS market_fact_availability_events_source_time_idx
ON market_fact_availability_events(source_id, representation_id, requested_at, recorded_at);
"""


class MarketFactIntegrityError(ValueError):
    """Raised when immutable market-fact identity is reused with divergent content."""


class RepositoryAuthorizationError(PermissionError):
    """Raised when a caller attempts an unauthorized authoritative mutation."""


class _RepositoryAuthority:
    pass


class MarketFactWritePlan:
    __slots__ = ("records", "availability_events", "_authority")

    def __init__(
        self,
        *,
        records: tuple[ObservedMarketFactRecord, ...] = (),
        availability_events: tuple[MarketFactAvailabilityEvent, ...] = (),
        authority: object,
    ) -> None:
        self.records = records
        self.availability_events = availability_events
        self._authority = authority


class ObservedMarketFactRepository:
    def __init__(self, path: str | Path = DEFAULT_MARKET_FACTS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._authority = _RepositoryAuthority()
        self._initialize()

    def apply(self, plan: MarketFactWritePlan) -> None:
        if plan._authority is not self._authority:
            raise RepositoryAuthorizationError("market fact write plan was not authorized for this repository")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for record in plan.records:
                    self._insert_record(conn, record)
                for event in plan.availability_events:
                    self._insert_availability_event(conn, event)
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    def record(self, record_id: str) -> ObservedMarketFactRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM observed_market_facts WHERE record_id = ?", (record_id,)).fetchone()
        return _record_from_row(row) if row is not None else None

    def lineage(self, logical_id: str) -> tuple[ObservedMarketFactRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observed_market_facts
                WHERE logical_id = ?
                ORDER BY effective_at, recorded_at, known_at, record_id
                """,
                (logical_id,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def strict_known_fact(
        self,
        *,
        entity_id: str,
        representation_id: str,
        fact_type: str,
        effective_as_of: datetime,
        known_by: datetime,
        quote_currency: str | None = None,
    ) -> ObservedMarketFactRecord | None:
        effective_as_of = _aware("effective_as_of", effective_as_of)
        known_by = _aware("known_by", known_by)
        filters = [
            "entity_id = ?",
            "representation_id = ?",
            "fact_type = ?",
            "effective_at <= ?",
            "recorded_at <= ?",
            "known_at <= ?",
            "quality_state = 'accepted'",
            "conflict_state IN ('none', 'resolved')",
        ]
        params: list[object] = [
            entity_id,
            representation_id,
            fact_type,
            _serialize(effective_as_of),
            _serialize(known_by),
            _serialize(known_by),
        ]
        if quote_currency is None:
            filters.append("quote_currency IS NULL")
        else:
            filters.append("quote_currency = ?")
            params.append(quote_currency.lower())
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM observed_market_facts
                WHERE {' AND '.join(filters)}
                ORDER BY effective_at DESC, recorded_at DESC, known_at DESC, record_id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def availability_events(self, *, source_id: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            if source_id is None:
                rows = conn.execute(
                    "SELECT * FROM market_fact_availability_events ORDER BY requested_at, event_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM market_fact_availability_events
                    WHERE source_id = ?
                    ORDER BY requested_at, event_id
                    """,
                    (source_id,),
                ).fetchall()
        return tuple(dict(row) for row in rows)

    def migration_ids(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT migration_id FROM market_fact_schema_migrations ORDER BY migration_id"
            ).fetchall()
        return tuple(str(row["migration_id"]) for row in rows)

    def count(self, table: str) -> int:
        if table not in {
            "market_fact_schema_migrations",
            "observed_market_facts",
            "market_fact_availability_events",
        }:
            raise ValueError("unsupported market fact table")
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _insert_record(self, conn: sqlite3.Connection, record: ObservedMarketFactRecord) -> None:
        payload = _record_payload(record)
        existing = conn.execute(
            "SELECT * FROM observed_market_facts WHERE record_id = ?", (record.record_id,)
        ).fetchone()
        if existing is not None:
            if dict(existing) != payload:
                raise MarketFactIntegrityError("record_id reused with divergent observed market fact content")
            return
        if record.supersedes_record_id is not None:
            predecessor = conn.execute(
                "SELECT logical_id FROM observed_market_facts WHERE record_id = ?",
                (record.supersedes_record_id,),
            ).fetchone()
            if predecessor is None:
                raise MarketFactIntegrityError("superseded market fact record does not exist")
            if str(predecessor["logical_id"]) != record.logical_id:
                raise MarketFactIntegrityError("correction must preserve logical_id")
        columns = tuple(payload)
        conn.execute(
            f"INSERT INTO observed_market_facts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(payload[column] for column in columns),
        )

    def _insert_availability_event(self, conn: sqlite3.Connection, event: MarketFactAvailabilityEvent) -> None:
        payload = _availability_payload(event)
        existing = conn.execute(
            "SELECT * FROM market_fact_availability_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        if existing is not None:
            if dict(existing) != payload:
                raise MarketFactIntegrityError("availability event identity reused with divergent content")
            return
        columns = tuple(payload)
        conn.execute(
            f"INSERT INTO market_fact_availability_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(payload[column] for column in columns),
        )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO market_fact_schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (MARKET_FACTS_MIGRATION_ID, datetime.now(UTC).isoformat()),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _record_payload(record: ObservedMarketFactRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "schema_version": record.schema_version,
        "semantic_version": record.semantic_version,
        "entity_id": record.identity.entity_id,
        "asset_id": record.identity.asset_id,
        "representation_id": record.identity.representation_id,
        "chain": record.identity.chain,
        "contract_address": record.identity.contract_address,
        "provider_listing_id": record.identity.provider_listing_id,
        "source_id": record.source_id,
        "provider_id": record.provider_id,
        "endpoint": record.endpoint,
        "parser_version": record.parser_version,
        "fact_type": record.fact_type,
        "value": record.value,
        "unit": record.unit,
        "quote_currency": record.quote_currency,
        "venue_scope": record.venue_scope,
        "effective_at": _serialize(record.effective_at),
        "observed_at": _serialize(record.observed_at),
        "recorded_at": _serialize(record.recorded_at),
        "known_at": _serialize(record.known_at),
        "raw_payload_hash": record.raw_payload_hash,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
        "content_hash": record.content_hash,
        "supersedes_record_id": record.supersedes_record_id,
        "correction_reason": record.correction_reason,
    }


def _availability_payload(event: MarketFactAvailabilityEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "source_id": event.source_id,
        "provider_id": event.provider_id,
        "entity_id": event.entity_id,
        "representation_id": event.representation_id,
        "status": event.status,
        "requested_at": _serialize(event.requested_at),
        "recorded_at": _serialize(event.recorded_at),
        "known_at": _serialize(event.known_at),
        "endpoint": event.endpoint,
        "parser_version": event.parser_version,
        "raw_payload_hash": event.raw_payload_hash,
        "failure_reason": event.failure_reason,
    }


def _record_from_row(row: sqlite3.Row) -> ObservedMarketFactRecord:
    return ObservedMarketFactRecord(
        record_id=str(row["record_id"]),
        logical_id=str(row["logical_id"]),
        schema_version=str(row["schema_version"]),
        semantic_version=str(row["semantic_version"]),
        identity=MarketFactIdentity(
            entity_id=str(row["entity_id"]),
            asset_id=str(row["asset_id"]),
            representation_id=str(row["representation_id"]),
            chain=str(row["chain"]),
            contract_address=str(row["contract_address"]),
            provider_listing_id=str(row["provider_listing_id"]),
        ),
        source_id=str(row["source_id"]),
        provider_id=str(row["provider_id"]),
        endpoint=str(row["endpoint"]),
        parser_version=str(row["parser_version"]),
        fact_type=str(row["fact_type"]),  # type: ignore[arg-type]
        value=str(row["value"]),
        unit=str(row["unit"]),
        quote_currency=None if row["quote_currency"] is None else str(row["quote_currency"]),
        venue_scope=str(row["venue_scope"]),
        effective_at=_parse(str(row["effective_at"])),
        observed_at=_parse(str(row["observed_at"])),
        recorded_at=_parse(str(row["recorded_at"])),
        known_at=_parse(str(row["known_at"])),
        raw_payload_hash=str(row["raw_payload_hash"]),
        quality_state=str(row["quality_state"]),  # type: ignore[arg-type]
        conflict_state=str(row["conflict_state"]),  # type: ignore[arg-type]
        content_hash=str(row["content_hash"]),
        supersedes_record_id=None
        if row["supersedes_record_id"] is None
        else str(row["supersedes_record_id"]),
        correction_reason=str(row["correction_reason"]),
    )


def _serialize(value: datetime) -> str:
    return _aware("datetime", value).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
