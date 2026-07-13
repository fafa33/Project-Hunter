from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.discovery.models import (
    CandidateAlias,
    CandidateIdentifier,
    CandidateLifecycleTransition,
    CandidateQueueEntry,
    CandidateRecord,
    CandidateRegistryStats,
    CandidateScreeningResult,
    CandidateSource,
    DiscoveryConflict,
    DiscoveryRun,
)

DEFAULT_DISCOVERY_DB = Path("data/discovery/runtime/candidates.sqlite")


class CandidateRegistryRepository:
    def __init__(self, path: str | Path = DEFAULT_DISCOVERY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert_candidate(self, candidate: CandidateRecord) -> str:
        with self._connect() as conn:
            existing = self.get_by_slug(candidate.slug, connection=conn)
            candidate_id = (
                existing.candidate_id
                if existing and existing.candidate_id != candidate.candidate_id
                else candidate.candidate_id
            )
            candidate = _with_candidate_id(candidate, candidate_id)
            previous = self.get(candidate_id, connection=conn)
            first_seen = previous.first_seen_at if previous else candidate.first_seen_at
            evidence_ids = tuple(sorted({*(previous.evidence_ids if previous else ()), *candidate.evidence_ids}))
            source_ids = tuple(sorted({*(previous.source_ids if previous else ()), *candidate.source_ids}))
            conn.execute(
                """
                INSERT INTO candidates (
                    candidate_id, slug, name, symbol, sector, primary_chain, candidate_type,
                    lifecycle_status, discovery_source, first_seen_at, last_seen_at, confidence,
                    evidence_ids, source_ids, alias_count, identifier_count,
                    identity_resolution_status, queue_status, screening_status,
                    intrinsic_value_status, competition_status, network_effect_status, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    slug=excluded.slug,
                    name=excluded.name,
                    symbol=COALESCE(excluded.symbol, candidates.symbol),
                    sector=COALESCE(excluded.sector, candidates.sector),
                    primary_chain=COALESCE(excluded.primary_chain, candidates.primary_chain),
                    candidate_type=excluded.candidate_type,
                    lifecycle_status=excluded.lifecycle_status,
                    discovery_source=excluded.discovery_source,
                    first_seen_at=MIN(candidates.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(candidates.last_seen_at, excluded.last_seen_at),
                    confidence=MAX(candidates.confidence, excluded.confidence),
                    evidence_ids=excluded.evidence_ids,
                    source_ids=excluded.source_ids,
                    alias_count=excluded.alias_count,
                    identifier_count=excluded.identifier_count,
                    identity_resolution_status=excluded.identity_resolution_status,
                    queue_status=excluded.queue_status,
                    screening_status=excluded.screening_status,
                    intrinsic_value_status=excluded.intrinsic_value_status,
                    competition_status=excluded.competition_status,
                    network_effect_status=excluded.network_effect_status,
                    metadata=excluded.metadata
                """,
                (
                    candidate_id,
                    candidate.slug,
                    candidate.name,
                    candidate.symbol,
                    candidate.sector,
                    candidate.primary_chain,
                    candidate.candidate_type,
                    candidate.lifecycle_status,
                    candidate.discovery_source,
                    _dt(first_seen),
                    _dt(candidate.last_seen_at),
                    candidate.confidence,
                    _json(evidence_ids),
                    _json(source_ids),
                    len(candidate.aliases),
                    len(candidate.identifiers),
                    candidate.identity_resolution_status,
                    candidate.queue_status,
                    candidate.screening_status,
                    candidate.intrinsic_value_status,
                    candidate.competition_status,
                    candidate.network_effect_status,
                    _json(candidate.metadata),
                ),
            )
            self._upsert_identifiers(conn, candidate.identifiers)
            self._upsert_aliases(conn, candidate.aliases)
            self._upsert_sources(conn, candidate.sources)
            self._refresh_counts(conn, candidate_id)
            return candidate_id

    def upsert_many(self, candidates: Iterable[CandidateRecord]) -> tuple[int, int]:
        created = 0
        updated = 0
        for candidate in candidates:
            exists = self.get(candidate.candidate_id) is not None or self.get_by_slug(candidate.slug) is not None
            self.upsert_candidate(candidate)
            if exists:
                updated += 1
            else:
                created += 1
        return created, updated

    def get(self, candidate_id: str, *, connection: sqlite3.Connection | None = None) -> CandidateRecord | None:
        conn = connection or self._connect()
        try:
            row = conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
            return self._record_from_row(conn, row) if row else None
        finally:
            if connection is None:
                conn.close()

    def get_by_slug(self, slug: str, *, connection: sqlite3.Connection | None = None) -> CandidateRecord | None:
        conn = connection or self._connect()
        try:
            row = conn.execute("SELECT * FROM candidates WHERE slug = ?", (slug,)).fetchone()
            return self._record_from_row(conn, row) if row else None
        finally:
            if connection is None:
                conn.close()

    def find_by_identifier(self, namespace: str, value: str) -> CandidateRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT candidate_id FROM identifiers WHERE namespace = ? AND value = ?",
                (namespace, value),
            ).fetchone()
            if row is None:
                return None
            return self.get(str(row["candidate_id"]), connection=conn)

    def list_candidates(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CandidateRecord, ...]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM candidates WHERE lifecycle_status = ? ORDER BY slug LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM candidates ORDER BY slug LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
            return tuple(self._record_from_row(conn, row) for row in rows)

    def stats(self) -> CandidateRegistryStats:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
            configured = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT candidate_id) FROM identifiers WHERE namespace = 'hunter_project'"
                ).fetchone()[0]
            )
            identifier_count = int(conn.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0])
            alias_count = int(conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
            source_count = int(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
            by_status = dict(
                Counter(
                    {
                        str(row["lifecycle_status"]): int(row["count"])
                        for row in conn.execute(
                            "SELECT lifecycle_status, COUNT(*) AS count FROM candidates GROUP BY lifecycle_status"
                        ).fetchall()
                    }
                )
            )
            by_source = {
                str(row["discovery_source"]): int(row["count"])
                for row in conn.execute(
                    "SELECT discovery_source, COUNT(*) AS count FROM candidates GROUP BY discovery_source"
                ).fetchall()
            }
            screenable = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM candidates
                    WHERE lifecycle_status IN ('screenable', 'analyzable', 'ranked', 'deep_research')
                    """
                ).fetchone()[0]
            )
            identity_ready = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT candidate_id FROM identifiers
                        GROUP BY candidate_id HAVING COUNT(*) >= 1
                    )
                    """
                ).fetchone()[0]
            )
            last_run = conn.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0]
            return CandidateRegistryStats(
                total_candidates=total,
                configured_candidates=configured,
                by_status=by_status,
                by_source=by_source,
                identifier_count=identifier_count,
                alias_count=alias_count,
                source_count=source_count,
                screenable_candidates=screenable,
                future_identity_ready_candidates=identity_ready,
                last_run_at=_parse_dt(last_run) if last_run else None,
            )

    def save_run(self, run: DiscoveryRun) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, provider, started_at, finished_at, candidates_seen,
                    candidates_created, candidates_updated, status, message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.provider,
                    _dt(run.started_at),
                    _dt(run.finished_at),
                    run.candidates_seen,
                    run.candidates_created,
                    run.candidates_updated,
                    run.status,
                    run.message,
                ),
            )

    def runs(self, limit: int = 20) -> tuple[DiscoveryRun, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY finished_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            return tuple(
                DiscoveryRun(
                    run_id=str(row["run_id"]),
                    provider=str(row["provider"]),
                    started_at=_parse_dt(str(row["started_at"])),
                    finished_at=_parse_dt(str(row["finished_at"])),
                    candidates_seen=int(row["candidates_seen"]),
                    candidates_created=int(row["candidates_created"]),
                    candidates_updated=int(row["candidates_updated"]),
                    status=str(row["status"]),
                    message=str(row["message"] or ""),
                )
                for row in rows
            )

    def index_names(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("PRAGMA index_list(candidates)").fetchall()
            identifier_rows = conn.execute("PRAGMA index_list(identifiers)").fetchall()
            return tuple(str(row["name"]) for row in (*rows, *identifier_rows))

    def transition_lifecycle(self, transition: CandidateLifecycleTransition) -> None:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT lifecycle_status FROM candidates WHERE candidate_id = ?",
                (transition.candidate_id,),
            ).fetchone()
            if current is None:
                msg = f"candidate not found: {transition.candidate_id}"
                raise KeyError(msg)
            previous = str(current["lifecycle_status"])
            if transition.previous_state is not None and transition.previous_state != previous:
                msg = f"transition expected {transition.previous_state}, found {previous}"
                raise ValueError(msg)
            if not _valid_transition(previous, transition.new_state):
                msg = f"invalid lifecycle transition: {previous} -> {transition.new_state}"
                raise ValueError(msg)
            conn.execute(
                """
                INSERT OR REPLACE INTO lifecycle_transitions (
                    transition_id, candidate_id, previous_state, new_state, transitioned_at,
                    reason, supporting_evidence_ids, discovery_run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.transition_id,
                    transition.candidate_id,
                    previous,
                    transition.new_state,
                    _dt(transition.transitioned_at),
                    transition.reason,
                    _json(transition.supporting_evidence_ids),
                    transition.discovery_run_id,
                ),
            )
            conn.execute(
                "UPDATE candidates SET lifecycle_status = ?, last_seen_at = MAX(last_seen_at, ?) WHERE candidate_id = ?",
                (transition.new_state, _dt(transition.transitioned_at), transition.candidate_id),
            )

    def save_conflict(self, conflict: DiscoveryConflict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conflicts (
                    conflict_id, candidate_id, conflict_type, description, detected_at, source_ids, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.conflict_id,
                    conflict.candidate_id,
                    conflict.conflict_type,
                    conflict.description,
                    _dt(conflict.detected_at),
                    _json(conflict.source_ids),
                    conflict.status,
                ),
            )

    def conflicts(self) -> tuple[DiscoveryConflict, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM conflicts ORDER BY detected_at DESC").fetchall()
            return tuple(
                DiscoveryConflict(
                    conflict_id=str(row["conflict_id"]),
                    candidate_id=str(row["candidate_id"]),
                    conflict_type=str(row["conflict_type"]),
                    description=str(row["description"]),
                    detected_at=_parse_dt(str(row["detected_at"])),
                    source_ids=tuple(_loads(row["source_ids"])),
                    status=str(row["status"]),
                )
                for row in rows
            )

    def save_screening_result(self, result: CandidateScreeningResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO screening_results (
                    screening_id, candidate_id, screened_at, status, score, advanced,
                    reasons, missing_evidence, confidence, coverage
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.screening_id,
                    result.candidate_id,
                    _dt(result.screened_at),
                    result.status,
                    result.score,
                    int(result.advanced),
                    _json(result.reasons),
                    _json(result.missing_evidence),
                    result.confidence,
                    result.coverage,
                ),
            )

    def latest_screening_results(self) -> tuple[CandidateScreeningResult, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sr.* FROM screening_results sr
                JOIN (
                    SELECT candidate_id, MAX(screened_at) AS screened_at
                    FROM screening_results GROUP BY candidate_id
                ) latest
                ON sr.candidate_id = latest.candidate_id AND sr.screened_at = latest.screened_at
                ORDER BY sr.score DESC, sr.candidate_id
                """
            ).fetchall()
            return tuple(_screening_from_row(row) for row in rows)

    def save_queue_entries(self, entries: tuple[CandidateQueueEntry, ...]) -> None:
        with self._connect() as conn:
            for entry in entries:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO queue_entries (
                        queue_entry_id, candidate_id, priority_score, priority, priority_reasons,
                        missing_evidence, lifecycle_state, created_at, updated_at,
                        source_run_id, eligible_for_deep_analysis
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.queue_entry_id,
                        entry.candidate_id,
                        entry.priority_score,
                        entry.priority,
                        _json(entry.priority_reasons),
                        _json(entry.missing_evidence),
                        entry.lifecycle_state,
                        _dt(entry.created_at),
                        _dt(entry.updated_at),
                        entry.source_run_id,
                        int(entry.eligible_for_deep_analysis),
                    ),
                )

    def queue_entries(self, limit: int = 25) -> tuple[CandidateQueueEntry, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queue_entries ORDER BY priority_score DESC, candidate_id LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
            return tuple(_queue_entry_from_row(row) for row in rows)

    def save_checkpoint(self, provider: str, cursor: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (provider, cursor, updated_at, status)
                VALUES (?, ?, ?, ?)
                """,
                (provider, cursor, _dt(datetime.now(tz=UTC)), status),
            )

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    symbol TEXT,
                    sector TEXT,
                    primary_chain TEXT,
                    candidate_type TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    discovery_source TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    alias_count INTEGER NOT NULL,
                    identifier_count INTEGER NOT NULL,
                    identity_resolution_status TEXT NOT NULL,
                    queue_status TEXT NOT NULL,
                    screening_status TEXT NOT NULL,
                    intrinsic_value_status TEXT NOT NULL,
                    competition_status TEXT NOT NULL,
                    network_effect_status TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identifiers (
                    namespace TEXT NOT NULL,
                    value TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, value),
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS aliases (
                    candidate_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    alias_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    PRIMARY KEY(candidate_id, alias, alias_type),
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT,
                    source_ref TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    candidates_seen INTEGER NOT NULL,
                    candidates_created INTEGER NOT NULL,
                    candidates_updated INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_transitions (
                    transition_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    previous_state TEXT,
                    new_state TEXT NOT NULL,
                    transitioned_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    supporting_evidence_ids TEXT NOT NULL,
                    discovery_run_id TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS screening_results (
                    screening_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    screened_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL NOT NULL,
                    advanced INTEGER NOT NULL,
                    reasons TEXT NOT NULL,
                    missing_evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    coverage REAL NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS queue_entries (
                    queue_entry_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    priority_score REAL NOT NULL,
                    priority TEXT NOT NULL,
                    priority_reasons TEXT NOT NULL,
                    missing_evidence TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    eligible_for_deep_analysis INTEGER NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    provider TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS candidates_status_idx ON candidates(lifecycle_status);
                CREATE INDEX IF NOT EXISTS candidates_source_idx ON candidates(discovery_source);
                CREATE INDEX IF NOT EXISTS candidates_symbol_idx ON candidates(symbol);
                CREATE INDEX IF NOT EXISTS identifiers_candidate_idx ON identifiers(candidate_id);
                CREATE INDEX IF NOT EXISTS sources_candidate_idx ON sources(candidate_id);
                CREATE INDEX IF NOT EXISTS runs_finished_idx ON runs(finished_at);
                CREATE INDEX IF NOT EXISTS screening_candidate_idx ON screening_results(candidate_id);
                CREATE INDEX IF NOT EXISTS queue_priority_idx ON queue_entries(priority_score);
                CREATE INDEX IF NOT EXISTS conflicts_candidate_idx ON conflicts(candidate_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _upsert_identifiers(self, conn: sqlite3.Connection, identifiers: tuple[CandidateIdentifier, ...]) -> None:
        for identifier in identifiers:
            conn.execute(
                """
                INSERT INTO identifiers (
                    namespace, value, candidate_id, source, confidence, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, value) DO UPDATE SET
                    candidate_id=excluded.candidate_id,
                    source=excluded.source,
                    confidence=MAX(identifiers.confidence, excluded.confidence),
                    first_seen_at=MIN(identifiers.first_seen_at, excluded.first_seen_at),
                    last_seen_at=MAX(identifiers.last_seen_at, excluded.last_seen_at)
                """,
                (
                    identifier.namespace,
                    identifier.value,
                    identifier.candidate_id,
                    identifier.source,
                    identifier.confidence,
                    _dt(identifier.first_seen_at),
                    _dt(identifier.last_seen_at),
                ),
            )

    def _upsert_aliases(self, conn: sqlite3.Connection, aliases: tuple[CandidateAlias, ...]) -> None:
        for alias in aliases:
            conn.execute(
                """
                INSERT OR REPLACE INTO aliases (candidate_id, alias, alias_type, source, confidence)
                VALUES (?, ?, ?, ?, ?)
                """,
                (alias.candidate_id, alias.alias, alias.alias_type, alias.source, alias.confidence),
            )

    def _upsert_sources(self, conn: sqlite3.Connection, sources: tuple[CandidateSource, ...]) -> None:
        for source in sources:
            conn.execute(
                """
                INSERT OR REPLACE INTO sources (
                    source_id, candidate_id, provider, source_type, source_url, source_ref, observed_at, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.candidate_id,
                    source.provider,
                    source.source_type,
                    source.source_url,
                    source.source_ref,
                    _dt(source.observed_at),
                    source.confidence,
                ),
            )

    def _refresh_counts(self, conn: sqlite3.Connection, candidate_id: str) -> None:
        aliases = int(
            conn.execute("SELECT COUNT(*) FROM aliases WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        )
        identifiers = int(
            conn.execute("SELECT COUNT(*) FROM identifiers WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        )
        source_ids = tuple(
            str(row["source_id"])
            for row in conn.execute(
                "SELECT source_id FROM sources WHERE candidate_id = ? ORDER BY source_id", (candidate_id,)
            )
        )
        conn.execute(
            "UPDATE candidates SET alias_count = ?, identifier_count = ?, source_ids = ? WHERE candidate_id = ?",
            (aliases, identifiers, _json(source_ids), candidate_id),
        )

    def _record_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CandidateRecord:
        candidate_id = str(row["candidate_id"])
        identifiers = tuple(
            CandidateIdentifier(
                candidate_id=candidate_id,
                namespace=str(item["namespace"]),
                value=str(item["value"]),
                source=str(item["source"]),
                confidence=float(item["confidence"]),
                first_seen_at=_parse_dt(str(item["first_seen_at"])),
                last_seen_at=_parse_dt(str(item["last_seen_at"])),
            )
            for item in conn.execute(
                "SELECT * FROM identifiers WHERE candidate_id = ? ORDER BY namespace, value", (candidate_id,)
            )
        )
        aliases = tuple(
            CandidateAlias(
                candidate_id=candidate_id,
                alias=str(item["alias"]),
                alias_type=str(item["alias_type"]),
                source=str(item["source"]),
                confidence=float(item["confidence"]),
            )
            for item in conn.execute(
                "SELECT * FROM aliases WHERE candidate_id = ? ORDER BY alias, alias_type", (candidate_id,)
            )
        )
        sources = tuple(
            CandidateSource(
                source_id=str(item["source_id"]),
                candidate_id=candidate_id,
                provider=str(item["provider"]),
                source_type=str(item["source_type"]),
                source_url=str(item["source_url"]) if item["source_url"] else None,
                source_ref=str(item["source_ref"]),
                observed_at=_parse_dt(str(item["observed_at"])),
                confidence=float(item["confidence"]),
            )
            for item in conn.execute("SELECT * FROM sources WHERE candidate_id = ? ORDER BY source_id", (candidate_id,))
        )
        return CandidateRecord(
            candidate_id=candidate_id,
            slug=str(row["slug"]),
            name=str(row["name"]),
            symbol=str(row["symbol"]) if row["symbol"] else None,
            sector=str(row["sector"]) if row["sector"] else None,
            primary_chain=str(row["primary_chain"]) if row["primary_chain"] else None,
            candidate_type=str(row["candidate_type"]),  # type: ignore[arg-type]
            lifecycle_status=str(row["lifecycle_status"]),  # type: ignore[arg-type]
            discovery_source=str(row["discovery_source"]),
            first_seen_at=_parse_dt(str(row["first_seen_at"])),
            last_seen_at=_parse_dt(str(row["last_seen_at"])),
            confidence=float(row["confidence"]),
            identifiers=identifiers,
            aliases=aliases,
            sources=sources,
            evidence_ids=tuple(_loads(row["evidence_ids"])),
            source_ids=tuple(_loads(row["source_ids"])),
            identity_resolution_status=str(row["identity_resolution_status"]),
            queue_status=str(row["queue_status"]),
            screening_status=str(row["screening_status"]),
            intrinsic_value_status=str(row["intrinsic_value_status"]),
            competition_status=str(row["competition_status"]),
            network_effect_status=str(row["network_effect_status"]),
            metadata=dict(_loads(row["metadata"])),
        )


def _with_candidate_id(candidate: CandidateRecord, candidate_id: str) -> CandidateRecord:
    if candidate.candidate_id == candidate_id:
        return candidate
    identifiers = tuple(
        CandidateIdentifier(**{**item.__dict__, "candidate_id": candidate_id}) for item in candidate.identifiers
    )
    aliases = tuple(CandidateAlias(**{**item.__dict__, "candidate_id": candidate_id}) for item in candidate.aliases)
    sources = tuple(CandidateSource(**{**item.__dict__, "candidate_id": candidate_id}) for item in candidate.sources)
    return CandidateRecord(
        **{
            **candidate.__dict__,
            "candidate_id": candidate_id,
            "identifiers": identifiers,
            "aliases": aliases,
            "sources": sources,
        }
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _loads(value: object) -> Any:
    return json.loads(str(value or "null"))


def _dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _screening_from_row(row: sqlite3.Row) -> CandidateScreeningResult:
    return CandidateScreeningResult(
        screening_id=str(row["screening_id"]),
        candidate_id=str(row["candidate_id"]),
        screened_at=_parse_dt(str(row["screened_at"])),
        status=str(row["status"]),
        score=float(row["score"]),
        advanced=bool(row["advanced"]),
        reasons=tuple(_loads(row["reasons"])),
        missing_evidence=tuple(_loads(row["missing_evidence"])),
        confidence=float(row["confidence"]),
        coverage=float(row["coverage"]),
    )


def _queue_entry_from_row(row: sqlite3.Row) -> CandidateQueueEntry:
    return CandidateQueueEntry(
        queue_entry_id=str(row["queue_entry_id"]),
        candidate_id=str(row["candidate_id"]),
        priority_score=float(row["priority_score"]),
        priority=str(row["priority"]),  # type: ignore[arg-type]
        priority_reasons=tuple(_loads(row["priority_reasons"])),
        missing_evidence=tuple(_loads(row["missing_evidence"])),
        lifecycle_state=str(row["lifecycle_state"]),  # type: ignore[arg-type]
        created_at=_parse_dt(str(row["created_at"])),
        updated_at=_parse_dt(str(row["updated_at"])),
        source_run_id=str(row["source_run_id"]),
        eligible_for_deep_analysis=bool(row["eligible_for_deep_analysis"]),
    )


def _valid_transition(previous: str, new: str) -> bool:
    if previous == new:
        return True
    allowed = {
        "discovered": {"identified", "evidence_pending", "rejected", "archived"},
        "identified": {"screenable", "evidence_pending", "rejected", "archived"},
        "evidence_pending": {"identified", "screenable", "rejected", "archived"},
        "screenable": {"analyzable", "ranked", "rejected", "archived"},
        "analyzable": {"ranked", "deep_research", "archived"},
        "ranked": {"deep_research", "analyzable", "archived"},
        "deep_research": {"ranked", "archived"},
        "rejected": {"archived"},
        "archived": set(),
    }
    return new in allowed.get(previous, set())
