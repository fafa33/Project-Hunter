from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.discovery.canonical_history import CanonicalEntityRecord, digest_payload, replay_canonical_entities
from hunter.discovery.models import (
    MARKET_SELECTION_METADATA_KEYS,
    QUEUE_STATUS_ELIGIBLE,
    QUEUE_STATUS_NOT_QUEUED,
    QUEUE_STATUS_QUEUED,
    REFRESHABLE_DISCOVERY_METADATA_KEYS,
    SCREENING_STATUS_NOT_SCREENED,
    CandidateAlias,
    CandidateIdentifier,
    CandidateIdentity,
    CandidateLifecycleTransition,
    CandidateQueueEntry,
    CandidateRecord,
    CandidateRegistryStats,
    CandidateScreeningResult,
    CandidateSource,
    DiscoveryConflict,
    DiscoveryRun,
    normalize_identity_disagreements,
)
from hunter.execution.identity import identity

DEFAULT_DISCOVERY_DB = Path("data/discovery/runtime/candidates.sqlite")


class CandidateRegistryRepository:
    def __init__(self, path: str | Path = DEFAULT_DISCOVERY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append_canonical_entity(
        self,
        record: CanonicalEntityRecord,
        *,
        repository: str,
        clock: Callable[[], datetime],
        verify_attestation: Callable[[dict[str, Any]], bool],
    ) -> str:
        """Registry-only immutable evidence write; no implicit identity promotion."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT digest FROM canonical_entity_history ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = str(row[0]) if row else f"GENESIS:{repository}"
            payload = record.payload(repository=repository, ingested_at=clock(), previous_digest=previous)
            prior = conn.execute("SELECT content FROM canonical_entity_history ORDER BY sequence DESC").fetchall()
            for historical in prior:
                previous_record = json.loads(historical[0])
                if previous_record["repository"] != repository:
                    raise ValueError("canonical identity repository mismatch")
                if previous_record["candidate_id"] == record.candidate_id and (
                    payload["known_at"] <= previous_record["known_at"]
                    or payload["ingested_at"] < previous_record["ingested_at"]
                ):
                    raise ValueError("canonical identity supersession must advance trusted time")
            if not verify_attestation(payload):
                raise ValueError("canonical identity attestation unverified")
            digest = digest_payload(payload)
            conn.execute(
                "INSERT INTO canonical_entity_history (digest, content) VALUES (?, ?)",
                (digest, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            return digest

    def append_signed_canonical_entity(
        self,
        record: CanonicalEntityRecord,
        *,
        repository: str,
        clock: Callable[[], datetime],
        sign_digest: Callable[[str], str],
        verify_attestation: Callable[[dict[str, Any]], bool],
    ) -> str:
        """Atomically bind trusted ingress time and chain head to an external signature.

        The signer is injected by an independently authorized authority; this
        repository never creates or loads signing keys. Verification is mandatory
        before commit, and any signer failure rolls back the transaction.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT digest FROM canonical_entity_history ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = str(last[0]) if last else f"GENESIS:{repository}"
            import hashlib
            from dataclasses import replace

            from hunter.market_validation.trusted_membership import canonical_bytes

            unsigned_record = replace(record, upstream_attestation="PENDING")
            payload = unsigned_record.payload(repository=repository, ingested_at=clock(), previous_digest=previous)
            for historical in conn.execute("SELECT content FROM canonical_entity_history ORDER BY sequence DESC"):
                earlier = json.loads(historical[0])
                if earlier["repository"] != repository:
                    raise ValueError("canonical identity repository mismatch")
                if earlier["candidate_id"] == record.candidate_id and (
                    payload["known_at"] <= earlier["known_at"] or payload["ingested_at"] < earlier["ingested_at"]
                ):
                    raise ValueError("canonical identity supersession must advance trusted time")
            unsigned = {key: value for key, value in payload.items() if key != "upstream_attestation"}
            payload["upstream_attestation"] = sign_digest(hashlib.sha256(canonical_bytes(unsigned)).hexdigest())
            if not verify_attestation(payload):
                raise ValueError("canonical identity attestation unverified")
            digest = digest_payload(payload)
            conn.execute(
                "INSERT INTO canonical_entity_history (digest, content) VALUES (?, ?)",
                (digest, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            return digest

    def canonical_entity_history(self) -> list[tuple[str, dict[str, Any]]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT digest, content FROM canonical_entity_history ORDER BY sequence").fetchall()
        return [(str(row[0]), json.loads(row[1])) for row in rows]

    def canonical_entities_as_of(
        self, *, repository: str, cutoff: datetime, verify_attestation: Callable[[dict[str, Any]], bool]
    ) -> dict[str, str]:
        return replay_canonical_entities(
            self.canonical_entity_history(), repository=repository, cutoff=cutoff, verify_attestation=verify_attestation
        )

    def upsert_candidate(self, candidate: CandidateRecord) -> str:
        with self._connect() as conn:
            return self._upsert_candidate(conn, candidate)

    def upsert_many(self, candidates: Iterable[CandidateRecord]) -> tuple[int, int]:
        created = 0
        updated = 0
        with self._connect() as conn:
            conn.execute("BEGIN")
            for candidate in candidates:
                exists = self._candidate_exists(conn, candidate.candidate_id, candidate.slug)
                self._upsert_candidate(conn, candidate)
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
        limit = max(1, min(limit, 100_000))
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

    def iter_candidates(self, *, batch_size: int = 1000) -> Iterable[tuple[CandidateRecord, ...]]:
        offset = 0
        size = max(1, min(batch_size, 10_000))
        while True:
            batch = self.list_candidates(limit=size, offset=offset)
            if not batch:
                break
            yield batch
            offset += len(batch)

    def candidates_by_ids(self, candidate_ids: tuple[str, ...]) -> tuple[CandidateRecord, ...]:
        unique = tuple(dict.fromkeys(candidate_ids))
        if not unique:
            return ()
        records: list[CandidateRecord] = []
        with self._connect() as conn:
            for start in range(0, len(unique), 500):
                chunk = unique[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM candidates WHERE candidate_id IN ({placeholders}) ORDER BY candidate_id",
                    chunk,
                ).fetchall()
                records.extend(self._record_from_row(conn, row) for row in rows)
        return tuple(records)

    def fair_candidates(self, purpose: str, limit: int = 500) -> tuple[CandidateRecord, ...]:
        """Bounded round-robin batch driven by a durable candidate_id cursor.

        Candidate slugs are mutable and can share long prefixes, so slug ordering
        lets one cohort starve every later candidate. The immutable candidate_id
        cursor rotates through the full registry and survives restarts.
        """
        size = max(1, min(limit, 10_000))
        key = f"processing:{purpose}"
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM checkpoints WHERE provider = ?", (key,)).fetchone()
            raw_cursor = str(row["cursor"]) if row else ""
            after = raw_cursor.removeprefix("id:") if raw_cursor.startswith("id:") else ""
            rows = list(
                conn.execute(
                    "SELECT * FROM candidates WHERE candidate_id > ? ORDER BY candidate_id LIMIT ?",
                    (after, size),
                ).fetchall()
            )
            seen = {str(item["candidate_id"]) for item in rows}
            if len(rows) < size:
                for wrap in conn.execute("SELECT * FROM candidates ORDER BY candidate_id LIMIT ?", (size,)).fetchall():
                    candidate_id = str(wrap["candidate_id"])
                    if candidate_id in seen:
                        continue
                    rows.append(wrap)
                    seen.add(candidate_id)
                    if len(rows) == size:
                        break
            if rows:
                conn.execute(
                    """
                    INSERT INTO checkpoints (provider, cursor, updated_at, status)
                    VALUES (?, ?, ?, 'active')
                    ON CONFLICT(provider) DO UPDATE SET
                        cursor=excluded.cursor,
                        updated_at=excluded.updated_at,
                        status=excluded.status
                    """,
                    (key, f"id:{rows[-1]['candidate_id']}", _dt(datetime.now(tz=UTC))),
                )
            return tuple(self._record_from_row(conn, item) for item in rows)

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
            screenable = int(conn.execute("""
                    SELECT COUNT(*) FROM candidates
                    WHERE lifecycle_status IN ('screenable', 'analyzable', 'ranked', 'deep_research')
                    """).fetchone()[0])
            identity_ready = int(conn.execute("""
                    SELECT COUNT(*) FROM (
                        SELECT candidate_id FROM identifiers
                        GROUP BY candidate_id HAVING COUNT(*) >= 1
                    )
                    """).fetchone()[0])
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
            identity_rows = conn.execute("PRAGMA index_list(identity_results)").fetchall()
            return tuple(str(row["name"]) for row in (*rows, *identifier_rows, *identity_rows))

    def identifier_groups(self, *, namespaces: tuple[str, ...] | None = None) -> dict[tuple[str, str], tuple[str, ...]]:
        with self._connect() as conn:
            params: tuple[str, ...] = ()
            where = ""
            if namespaces:
                placeholders = ",".join("?" for _ in namespaces)
                where = f"WHERE namespace IN ({placeholders})"
                params = namespaces
            rows = conn.execute(
                f"""
                SELECT namespace, value, candidate_id FROM identifiers
                {where}
                ORDER BY namespace, value, candidate_id
                """,
                params,
            ).fetchall()
        groups: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            key = (str(row["namespace"]), str(row["value"]))
            groups.setdefault(key, []).append(str(row["candidate_id"]))
        return {key: tuple(value) for key, value in groups.items()}

    def alias_groups(self, *, alias_type: str) -> dict[str, tuple[str, ...]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT alias, candidate_id FROM aliases
                WHERE alias_type = ?
                ORDER BY alias, candidate_id
                """,
                (alias_type,),
            ).fetchall()
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(str(row["alias"]).upper(), []).append(str(row["candidate_id"]))
        return {key: tuple(value) for key, value in groups.items()}

    def save_identity_results(self, results: tuple[CandidateIdentity, ...]) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            for result in results:
                conn.execute(
                    """
                    INSERT INTO identity_results (
                        candidate_id, outcome, confidence, evidence_ids, source_candidate_ids,
                        source_ids, reason, missing_evidence, conflicts, related_candidate_ids,
                        evaluated_at, policy_version, evidence_fingerprint
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        outcome=excluded.outcome,
                        confidence=excluded.confidence,
                        evidence_ids=excluded.evidence_ids,
                        source_candidate_ids=excluded.source_candidate_ids,
                        source_ids=excluded.source_ids,
                        reason=excluded.reason,
                        missing_evidence=excluded.missing_evidence,
                        conflicts=excluded.conflicts,
                        related_candidate_ids=excluded.related_candidate_ids,
                        evaluated_at=excluded.evaluated_at,
                        policy_version=excluded.policy_version,
                        evidence_fingerprint=excluded.evidence_fingerprint
                    """,
                    (
                        result.candidate_id,
                        result.outcome,
                        result.confidence,
                        _json(result.evidence_ids),
                        _json(result.source_candidate_ids),
                        _json(result.source_ids),
                        result.reason,
                        _json(result.missing_evidence),
                        _json(result.conflicts),
                        _json(result.related_candidate_ids),
                        _dt(result.evaluated_at),
                        result.policy_version,
                        result.evidence_fingerprint,
                    ),
                )
                conn.execute(
                    "UPDATE candidates SET identity_resolution_status = ? WHERE candidate_id = ?",
                    (result.outcome, result.candidate_id),
                )
                if result.outcome in {"exact", "probable"}:
                    current = conn.execute(
                        "SELECT lifecycle_status FROM candidates WHERE candidate_id = ?",
                        (result.candidate_id,),
                    ).fetchone()
                    previous = str(current["lifecycle_status"]) if current else ""
                    if previous in {"discovered", "screenable", "evidence_pending"} and _valid_transition(
                        previous, "identified"
                    ):
                        transition_id = identity(
                            "candidate-lifecycle-transition",
                            {
                                "candidate": result.candidate_id,
                                "from": previous,
                                "to": "identified",
                                "reason": result.reason,
                            },
                        )
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO lifecycle_transitions (
                                transition_id, candidate_id, previous_state, new_state, transitioned_at,
                                reason, supporting_evidence_ids, discovery_run_id
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                transition_id,
                                result.candidate_id,
                                previous,
                                "identified",
                                _dt(result.evaluated_at),
                                f"identity accepted: {result.reason}",
                                _json(result.evidence_ids),
                                "identity-resolution",
                            ),
                        )
                        conn.execute(
                            "UPDATE candidates SET lifecycle_status = 'identified' WHERE candidate_id = ?",
                            (result.candidate_id,),
                        )

    def identity_result_for(self, candidate_id: str) -> CandidateIdentity | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM identity_results WHERE candidate_id = ?", (candidate_id,)).fetchone()
            return _identity_from_row(row) if row else None

    def identity_results(self) -> tuple[CandidateIdentity, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM identity_results ORDER BY outcome, candidate_id").fetchall()
            return tuple(_identity_from_row(row) for row in rows)

    def latest_identity_by_candidate(self) -> dict[str, CandidateIdentity]:
        return {result.candidate_id: result for result in self.identity_results()}

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
            conn.execute(
                "UPDATE candidates SET screening_status = ? WHERE candidate_id = ?",
                (result.status, result.candidate_id),
            )

    def latest_screening_results(self) -> tuple[CandidateScreeningResult, ...]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT sr.* FROM screening_results sr
                WHERE sr.screening_id = (
                    SELECT latest.screening_id FROM screening_results latest
                    WHERE latest.candidate_id = sr.candidate_id
                    ORDER BY latest.screened_at DESC, latest.screening_id DESC
                    LIMIT 1
                )
                ORDER BY sr.score DESC, sr.candidate_id
                """).fetchall()
            return tuple(_screening_from_row(row) for row in rows)

    def latest_screening_by_candidate(self) -> dict[str, CandidateScreeningResult]:
        return {result.candidate_id: result for result in self.latest_screening_results()}

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
                conn.execute(
                    "UPDATE candidates SET queue_status = ? WHERE candidate_id = ?",
                    (
                        QUEUE_STATUS_ELIGIBLE if entry.eligible_for_deep_analysis else QUEUE_STATUS_QUEUED,
                        entry.candidate_id,
                    ),
                )

    def queue_entries(self, limit: int = 25) -> tuple[CandidateQueueEntry, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queue_entries ORDER BY priority_score DESC, candidate_id LIMIT ?",
                (max(1, min(limit, 1000)),),
            ).fetchall()
            return tuple(_queue_entry_from_row(row) for row in rows)

    def all_queue_entries(self) -> tuple[CandidateQueueEntry, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM queue_entries ORDER BY candidate_id").fetchall()
            return tuple(_queue_entry_from_row(row) for row in rows)

    def candidate_lifecycle_states(self) -> dict[str, str]:
        with self._connect() as conn:
            return {
                str(row["candidate_id"]): str(row["lifecycle_status"])
                for row in conn.execute("SELECT candidate_id, lifecycle_status FROM candidates").fetchall()
            }

    def set_queue_eligibility(self, updates: tuple[tuple[str, bool], ...]) -> int:
        if not updates:
            return 0
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.executemany(
                "UPDATE queue_entries SET eligible_for_deep_analysis = ? WHERE candidate_id = ?",
                tuple((int(eligible), candidate_id) for candidate_id, eligible in updates),
            )
            for candidate_id, eligible in updates:
                conn.execute(
                    "UPDATE candidates SET queue_status = ? WHERE candidate_id = ?",
                    (QUEUE_STATUS_ELIGIBLE if eligible else QUEUE_STATUS_QUEUED, candidate_id),
                )
            return len(updates)

    def reconcile_candidate_status(self) -> dict[str, int]:
        """Make candidate status columns agree with authoritative result tables."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            changes: dict[str, int] = {}
            changes["identity_resolution_status"] = conn.execute("""
                UPDATE candidates SET identity_resolution_status = 'not_resolved'
                WHERE identity_resolution_status != 'not_resolved'
                AND NOT EXISTS (
                    SELECT 1 FROM identity_results ir WHERE ir.candidate_id = candidates.candidate_id
                )
                """).rowcount
            changes["identity_resolution_status"] += conn.execute("""
                UPDATE candidates SET identity_resolution_status = (
                    SELECT ir.outcome FROM identity_results ir
                    WHERE ir.candidate_id = candidates.candidate_id
                )
                WHERE EXISTS (
                    SELECT 1 FROM identity_results ir
                    WHERE ir.candidate_id = candidates.candidate_id
                    AND ir.outcome != candidates.identity_resolution_status
                )
                """).rowcount
            changes["screening_status"] = conn.execute(f"""
                UPDATE candidates SET screening_status = '{SCREENING_STATUS_NOT_SCREENED}'
                WHERE screening_status != '{SCREENING_STATUS_NOT_SCREENED}'
                AND NOT EXISTS (
                    SELECT 1 FROM screening_results sr WHERE sr.candidate_id = candidates.candidate_id
                )
                """).rowcount
            changes["screening_status"] += conn.execute("""
                UPDATE candidates SET screening_status = (
                    SELECT sr.status FROM screening_results sr
                    WHERE sr.candidate_id = candidates.candidate_id
                    ORDER BY sr.screened_at DESC, sr.screening_id DESC LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM screening_results sr
                    WHERE sr.candidate_id = candidates.candidate_id
                    AND sr.status != candidates.screening_status
                )
                """).rowcount
            changes[QUEUE_STATUS_NOT_QUEUED] = conn.execute(f"""
                UPDATE candidates SET queue_status = '{QUEUE_STATUS_NOT_QUEUED}'
                WHERE queue_status != '{QUEUE_STATUS_NOT_QUEUED}'
                AND NOT EXISTS (
                    SELECT 1 FROM queue_entries qe WHERE qe.candidate_id = candidates.candidate_id
                )
                """).rowcount
            changes[QUEUE_STATUS_QUEUED] = conn.execute(f"""
                UPDATE candidates SET queue_status = '{QUEUE_STATUS_QUEUED}'
                WHERE queue_status != '{QUEUE_STATUS_QUEUED}'
                AND NOT EXISTS (
                    SELECT 1 FROM queue_entries qe
                    WHERE qe.candidate_id = candidates.candidate_id AND qe.eligible_for_deep_analysis = 1
                )
                AND EXISTS (SELECT 1 FROM queue_entries qe WHERE qe.candidate_id = candidates.candidate_id)
                """).rowcount
            changes[QUEUE_STATUS_ELIGIBLE] = conn.execute(f"""
                UPDATE candidates SET queue_status = '{QUEUE_STATUS_ELIGIBLE}'
                WHERE queue_status != '{QUEUE_STATUS_ELIGIBLE}'
                AND EXISTS (
                    SELECT 1 FROM queue_entries qe
                    WHERE qe.candidate_id = candidates.candidate_id AND qe.eligible_for_deep_analysis = 1
                )
                """).rowcount
            return changes

    def discovery_page_cursor(self, provider: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM checkpoints WHERE provider = ?", (provider,)).fetchone()
        value = str(row["cursor"]) if row else ""
        return int(value[5:]) if value.startswith("page:") and value[5:].isdigit() else 0

    def save_checkpoint(self, provider: str, cursor: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (provider, cursor, updated_at, status)
                VALUES (?, ?, ?, ?)
                """,
                (provider, cursor, _dt(datetime.now(tz=UTC)), status),
            )

    def save_scan_state(self, provider: str, state: dict[str, Any], status: str) -> None:
        cursor = "scan:" + json.dumps(state, sort_keys=True, separators=(",", ":"))
        self.save_checkpoint(provider, cursor, status)

    def scan_state(self, provider: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM checkpoints WHERE provider = ?", (provider,)).fetchone()
        value = str(row["cursor"]) if row else ""
        if not value.startswith("scan:"):
            return None
        try:
            state = json.loads(value[5:])
        except json.JSONDecodeError:
            return None
        return state if isinstance(state, dict) else None

    def identifier_cursor(self, provider: str) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM checkpoints WHERE provider = ?", (provider,)).fetchone()
        value = str(row["cursor"]) if row else ""
        if not value.startswith("identifier:"):
            return None
        try:
            parsed = json.loads(value[len("identifier:") :])
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list) and len(parsed) == 2 and all(isinstance(item, str) for item in parsed):
            return str(parsed[0]), str(parsed[1])
        return None

    def save_identifier_cursor(self, provider: str, namespace: str, value: str) -> None:
        cursor = "identifier:" + json.dumps([namespace, value], sort_keys=True, separators=(",", ":"))
        self.save_checkpoint(provider, cursor, "cursor")

    def save_universe_assets(self, provider: str, asset_ids: tuple[str, ...] | list[str], observed_at: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM universe_assets WHERE provider = ?", (provider,))
            conn.executemany(
                "INSERT INTO universe_assets (provider, asset_id, observed_at) VALUES (?, ?, ?)",
                ((provider, str(asset_id), observed_at) for asset_id in dict.fromkeys(asset_ids)),
            )

    def universe_assets(self, provider: str) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT asset_id FROM universe_assets WHERE provider = ? ORDER BY asset_id", (provider,)
            ).fetchall()
        return tuple(str(row["asset_id"]) for row in rows)

    def universe_observed_at(self, provider: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(observed_at) AS observed_at FROM universe_assets WHERE provider = ?", (provider,)
            ).fetchone()
        return str(row["observed_at"]) if row and row["observed_at"] else None

    def identifier_values(self, namespace: str) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT value FROM identifiers WHERE namespace = ? ORDER BY value", (namespace,)
            ).fetchall()
        return tuple(str(row["value"]) for row in rows)

    def has_identifier(self, namespace: str, value: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM identifiers WHERE namespace = ? AND value = ? LIMIT 1", (namespace, value)
            ).fetchone()
        return row is not None

    def save_network_observations(self, observations: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> int:
        created = 0
        with self._connect() as conn:
            conn.execute("BEGIN")
            for item in observations:
                network_id = str(item.get("network_id") or "").strip()
                if not network_id:
                    continue
                observed_at = str(item.get("observed_at") or _dt(datetime.now(tz=UTC)))
                existing = conn.execute(
                    "SELECT 1 FROM network_observations WHERE network_id = ?", (network_id,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE network_observations SET name = ?, source_url = ?, last_seen_at = ? WHERE network_id = ?",
                        (item.get("name"), item.get("source_url"), observed_at, network_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO network_observations
                            (network_id, name, source_url, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (network_id, item.get("name"), item.get("source_url"), observed_at, observed_at),
                    )
                    created += 1
        return created

    def network_observations(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM network_observations ORDER BY network_id").fetchall()
        return tuple(dict(row) for row in rows)

    def save_pool_observations(self, observations: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> int:
        created = 0
        with self._connect() as conn:
            conn.execute("BEGIN")
            for item in observations:
                network = str(item.get("network") or "").strip()
                address = str(item.get("pool_address") or "").strip()
                if not network or not address:
                    continue
                observed_at = str(item.get("observed_at") or _dt(datetime.now(tz=UTC)))
                contracts = json.dumps(sorted({str(value) for value in item.get("contract_addresses", ())}))
                existing = conn.execute(
                    "SELECT 1 FROM pool_observations WHERE network = ? AND pool_address = ?", (network, address)
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE pool_observations
                        SET contract_addresses = ?, source_url = ?, last_seen_at = ?
                        WHERE network = ? AND pool_address = ?
                        """,
                        (contracts, item.get("source_url"), observed_at, network, address),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO pool_observations
                            (network, pool_address, contract_addresses, source_url, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (network, address, contracts, item.get("source_url"), observed_at, observed_at),
                    )
                    created += 1
        return created

    def pool_observations(self, *, network: str | None = None) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            if network:
                rows = conn.execute(
                    "SELECT * FROM pool_observations WHERE network = ? ORDER BY network, pool_address", (network,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM pool_observations ORDER BY network, pool_address").fetchall()
        return tuple(dict(row) for row in rows)

    def try_reserve_capacity(
        self,
        *,
        provider: str,
        endpoint: str,
        quota_key: str,
        fingerprint: str,
        dedup_key: str | None,
        attempted_at: str,
        window_start: str,
        dedupe_until: str,
        rate_limit_per_minute: int | None,
        retry: bool,
        batch_size: int | None,
        reservation_id: str,
    ) -> tuple[str | None, float, bool]:
        """Atomically reserve one request slot; returns (id, wait_seconds, duplicate)."""
        effective_quota = quota_key.strip().lower() or f"{provider}:{endpoint}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if dedup_key is not None and not retry:
                duplicate = conn.execute(
                    """
                    SELECT 1 FROM capacity_requests
                    WHERE provider = ? AND endpoint = ? AND dedup_key = ? AND dedupe_until > ?
                      AND (state <> 'completed' OR outcome = 'success')
                    LIMIT 1
                    """,
                    (provider, endpoint, dedup_key, attempted_at),
                ).fetchone()
                if duplicate is not None:
                    return None, 0.0, True
            if rate_limit_per_minute is not None and rate_limit_per_minute > 0:
                rows = conn.execute(
                    """
                    SELECT attempted_at FROM capacity_requests
                    WHERE quota_key = ? AND attempted_at > ?
                    ORDER BY attempted_at
                    LIMIT ?
                    """,
                    (effective_quota, window_start, rate_limit_per_minute),
                ).fetchall()
                if len(rows) >= rate_limit_per_minute:
                    oldest = _parse_dt(str(rows[0]["attempted_at"]))
                    wait = (oldest - _parse_dt(window_start)).total_seconds()
                    return None, max(0.0, wait), False
            conn.execute(
                """
                INSERT INTO capacity_requests (
                    reservation_id, provider, endpoint, quota_key, fingerprint, dedup_key, attempted_at,
                    window_start, dedupe_until, state, retry, batch_size
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_flight', ?, ?)
                """,
                (
                    reservation_id,
                    provider,
                    endpoint,
                    effective_quota,
                    fingerprint,
                    dedup_key,
                    attempted_at,
                    window_start,
                    dedupe_until,
                    int(retry),
                    batch_size,
                ),
            )
            return reservation_id, 0.0, False

    def complete_capacity_request(
        self,
        reservation_id: str,
        *,
        completed_at: str,
        status_code: int | None,
        outcome: str,
        retry_after_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE capacity_requests
                SET state = ?, completed_at = ?, status_code = ?, outcome = ?,
                    retry_after_seconds = ?, error = ?
                WHERE reservation_id = ?
                """,
                ("completed", completed_at, status_code, outcome, retry_after_seconds, error, reservation_id),
            )

    def capacity_cooldown_until(self, provider: str, endpoint: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM capacity_cooldowns WHERE provider = ? AND endpoint = ?",
                (provider, endpoint),
            ).fetchone()
        return str(row["cooldown_until"]) if row and row["cooldown_until"] else None

    def set_capacity_cooldown(self, provider: str, endpoint: str, *, until: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO capacity_cooldowns (provider, endpoint, cooldown_until, reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, endpoint) DO UPDATE SET
                    cooldown_until = excluded.cooldown_until,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (provider, endpoint, until, reason, _dt(datetime.now(tz=UTC))),
            )
            conn.execute(
                """
                INSERT INTO capacity_cooldown_events (provider, endpoint, cooldown_until, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (provider, endpoint, until, reason, _dt(datetime.now(tz=UTC))),
            )

    def capacity_quota_recent(self, *, window_start: str) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT quota_key, COUNT(*) AS recent
                FROM capacity_requests
                WHERE attempted_at > ?
                GROUP BY quota_key
                """,
                (window_start,),
            ).fetchall()
        return {str(row["quota_key"]): int(row["recent"]) for row in rows}

    def capacity_summary(self, *, window_start: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    requests.provider AS provider,
                    requests.endpoint AS endpoint,
                    COUNT(*) AS attempts,
                    COALESCE(SUM(CASE WHEN requests.retry = 1 THEN 1 ELSE 0 END), 0) AS retries,
                    COALESCE(SUM(CASE WHEN requests.outcome = 'throttled' THEN 1 ELSE 0 END), 0) AS throttles,
                    COALESCE(SUM(CASE WHEN requests.outcome = 'failure' THEN 1 ELSE 0 END), 0) AS failures,
                    COALESCE(SUM(CASE WHEN requests.outcome = 'success' THEN 1 ELSE 0 END), 0) AS successes,
                    COALESCE(SUM(CASE WHEN requests.attempted_at > ? THEN 1 ELSE 0 END), 0) AS recent_attempts,
                    MIN(requests.attempted_at) AS first_attempt_at,
                    MAX(requests.attempted_at) AS last_attempt_at,
                    MAX(requests.batch_size) AS max_batch_size,
                    COALESCE(cooldowns.cooldowns, 0) AS cooldowns,
                    MAX(COALESCE(cooldowns.cooldown_until, '')) AS cooldown_until
                FROM capacity_requests AS requests
                LEFT JOIN (
                    SELECT provider, endpoint, COUNT(*) AS cooldowns, MAX(cooldown_until) AS cooldown_until
                    FROM capacity_cooldown_events GROUP BY provider, endpoint
                ) AS cooldowns
                    ON cooldowns.provider = requests.provider AND cooldowns.endpoint = requests.endpoint
                GROUP BY requests.provider, requests.endpoint
                ORDER BY requests.provider, requests.endpoint
                """,
                (window_start,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
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
                CREATE TABLE IF NOT EXISTS universe_assets (
                    provider TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, asset_id)
                );
                CREATE TABLE IF NOT EXISTS network_observations (
                    network_id TEXT NOT NULL,
                    name TEXT,
                    source_url TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (network_id)
                );
                CREATE TABLE IF NOT EXISTS pool_observations (
                    network TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    contract_addresses TEXT NOT NULL,
                    source_url TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (network, pool_address)
                );
                CREATE INDEX IF NOT EXISTS universe_assets_provider_idx
                    ON universe_assets (provider);
                CREATE INDEX IF NOT EXISTS pool_observations_network_idx
                    ON pool_observations (network);
                CREATE TABLE IF NOT EXISTS capacity_requests (
                    reservation_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    quota_key TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL,
                    dedup_key TEXT,
                    attempted_at TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    dedupe_until TEXT NOT NULL,
                    state TEXT NOT NULL,
                    retry INTEGER NOT NULL,
                    batch_size INTEGER,
                    status_code INTEGER,
                    outcome TEXT,
                    retry_after_seconds REAL,
                    error TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS capacity_requests_window_idx
                    ON capacity_requests (provider, endpoint, attempted_at);
                CREATE INDEX IF NOT EXISTS capacity_requests_dedupe_idx
                    ON capacity_requests (provider, endpoint, dedup_key, dedupe_until);
                CREATE TABLE IF NOT EXISTS capacity_cooldowns (
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    cooldown_until TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, endpoint)
                );
                CREATE TABLE IF NOT EXISTS capacity_cooldown_events (
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    cooldown_until TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS canonical_entity_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    digest TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS canonical_entity_history_no_update
                    BEFORE UPDATE ON canonical_entity_history BEGIN SELECT RAISE(ABORT, 'immutable canonical identity'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_entity_history_no_delete
                    BEFORE DELETE ON canonical_entity_history BEGIN SELECT RAISE(ABORT, 'immutable canonical identity'); END;
                CREATE TABLE IF NOT EXISTS identity_results (
                    candidate_id TEXT PRIMARY KEY,
                    outcome TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    source_candidate_ids TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    missing_evidence TEXT NOT NULL,
                    conflicts TEXT NOT NULL,
                    related_candidate_ids TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL DEFAULT 'identity-policy-v1',
                    evidence_fingerprint TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
                );
                CREATE INDEX IF NOT EXISTS candidates_status_idx ON candidates(lifecycle_status);
                CREATE INDEX IF NOT EXISTS candidates_source_idx ON candidates(discovery_source);
                CREATE INDEX IF NOT EXISTS candidates_symbol_idx ON candidates(symbol);
                CREATE INDEX IF NOT EXISTS identifiers_candidate_idx ON identifiers(candidate_id);
                CREATE INDEX IF NOT EXISTS identifiers_namespace_value_idx ON identifiers(namespace, value);
                CREATE INDEX IF NOT EXISTS sources_candidate_idx ON sources(candidate_id);
                CREATE INDEX IF NOT EXISTS runs_finished_idx ON runs(finished_at);
                CREATE INDEX IF NOT EXISTS screening_candidate_idx ON screening_results(candidate_id);
                CREATE INDEX IF NOT EXISTS queue_priority_idx ON queue_entries(priority_score);
                CREATE INDEX IF NOT EXISTS conflicts_candidate_idx ON conflicts(candidate_id);
                CREATE INDEX IF NOT EXISTS identity_outcome_idx ON identity_results(outcome);
                """)
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(identity_results)").fetchall()}
            if "policy_version" not in columns:
                conn.execute(
                    "ALTER TABLE identity_results ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'identity-policy-v1'"
                )
            if "evidence_fingerprint" not in columns:
                conn.execute("ALTER TABLE identity_results ADD COLUMN evidence_fingerprint TEXT NOT NULL DEFAULT ''")
            capacity_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(capacity_requests)").fetchall()
            }
            if "quota_key" not in capacity_columns:
                conn.execute("ALTER TABLE capacity_requests ADD COLUMN quota_key TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    "UPDATE capacity_requests SET quota_key = provider || ':' || endpoint WHERE quota_key = ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS capacity_requests_quota_idx ON capacity_requests (quota_key, attempted_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _candidate_exists(self, conn: sqlite3.Connection, candidate_id: str, slug: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ? OR slug = ? LIMIT 1",
                (candidate_id, slug),
            ).fetchone()
            is not None
        )

    def _upsert_candidate(self, conn: sqlite3.Connection, candidate: CandidateRecord) -> str:
        existing = conn.execute(
            "SELECT candidate_id FROM candidates WHERE slug = ? LIMIT 1",
            (candidate.slug,),
        ).fetchone()
        candidate_id = (
            str(existing["candidate_id"])
            if existing is not None and str(existing["candidate_id"]) != candidate.candidate_id
            else candidate.candidate_id
        )
        candidate = _with_candidate_id(candidate, candidate_id)
        previous = conn.execute(
            """
            SELECT slug, name, first_seen_at, evidence_ids, source_ids, metadata
            FROM candidates WHERE candidate_id = ? LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        write_slug = str(previous["slug"]) if previous else candidate.slug
        write_name = str(previous["name"]) if previous else candidate.name
        first_seen = _parse_dt(str(previous["first_seen_at"])) if previous else candidate.first_seen_at
        previous_evidence = tuple(_loads(previous["evidence_ids"])) if previous else ()
        previous_sources = tuple(_loads(previous["source_ids"])) if previous else ()
        evidence_ids = tuple(sorted({*previous_evidence, *candidate.evidence_ids}))
        source_ids = tuple(sorted({*previous_sources, *candidate.source_ids}))
        previous_metadata = dict(_loads(previous["metadata"])) if previous else {}
        metadata = _merge_metadata(previous_metadata, candidate.metadata)
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
                symbol=COALESCE(candidates.symbol, excluded.symbol),
                sector=COALESCE(candidates.sector, excluded.sector),
                primary_chain=COALESCE(candidates.primary_chain, excluded.primary_chain),
                candidate_type=CASE
                    WHEN candidates.candidate_type != 'unknown' THEN candidates.candidate_type
                    ELSE excluded.candidate_type
                END,
                lifecycle_status=CASE
                    WHEN candidates.lifecycle_status IN ('analyzable', 'ranked', 'deep_research')
                    THEN candidates.lifecycle_status
                    ELSE excluded.lifecycle_status
                END,
                discovery_source=CASE
                    WHEN candidates.discovery_source = 'seed' THEN candidates.discovery_source
                    ELSE excluded.discovery_source
                END,
                first_seen_at=MIN(candidates.first_seen_at, excluded.first_seen_at),
                last_seen_at=MAX(candidates.last_seen_at, excluded.last_seen_at),
                confidence=MAX(candidates.confidence, excluded.confidence),
                evidence_ids=excluded.evidence_ids,
                source_ids=excluded.source_ids,
                alias_count=excluded.alias_count,
                identifier_count=excluded.identifier_count,
                identity_resolution_status=CASE
                    WHEN excluded.identity_resolution_status = 'not_resolved'
                    AND candidates.identity_resolution_status != 'not_resolved'
                    THEN candidates.identity_resolution_status
                    ELSE excluded.identity_resolution_status
                END,
                queue_status=CASE
                    WHEN excluded.queue_status = 'not_queued' AND candidates.queue_status != 'not_queued'
                    THEN candidates.queue_status
                    ELSE excluded.queue_status
                END,
                screening_status=CASE
                    WHEN excluded.screening_status = 'not_screened'
                    AND candidates.screening_status != 'not_screened'
                    THEN candidates.screening_status
                    ELSE excluded.screening_status
                END,
                intrinsic_value_status=excluded.intrinsic_value_status,
                competition_status=excluded.competition_status,
                network_effect_status=excluded.network_effect_status,
                metadata=excluded.metadata
            """,
            (
                candidate_id,
                write_slug,
                write_name,
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
                _json(metadata),
            ),
        )
        self._upsert_identifiers(conn, candidate.identifiers)
        self._upsert_aliases(conn, candidate.aliases)
        self._upsert_sources(conn, candidate.sources)
        self._refresh_counts(conn, candidate_id)
        return candidate_id

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


def _merge_metadata(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = normalize_identity_disagreements(previous)
    refreshed = normalize_identity_disagreements(current)
    if "market_evidence_provider" in refreshed:
        for key in MARKET_SELECTION_METADATA_KEYS:
            if key not in refreshed:
                merged.pop(key, None)
    for key, value in refreshed.items():
        if value in (None, "", (), []):
            continue
        if key in REFRESHABLE_DISCOVERY_METADATA_KEYS:
            merged[key] = value
            continue
        if key not in merged or merged[key] in (None, "", (), []):
            merged[key] = value
        elif merged[key] != value:
            conflicts = dict(merged.get("provider_disagreements", {}))
            values = tuple(sorted({str(merged[key]), str(value)}))
            conflicts[str(key)] = values
            merged["provider_disagreements"] = conflicts
    return merged


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


def _identity_from_row(row: sqlite3.Row) -> CandidateIdentity:
    columns = dict(zip(row.keys(), tuple(row), strict=True))
    return CandidateIdentity(
        candidate_id=str(row["candidate_id"]),
        outcome=str(row["outcome"]),  # type: ignore[arg-type]
        confidence=float(row["confidence"]),
        evidence_ids=tuple(_loads(row["evidence_ids"])),
        source_candidate_ids=tuple(_loads(row["source_candidate_ids"])),
        source_ids=tuple(_loads(row["source_ids"])),
        reason=str(row["reason"]),
        missing_evidence=tuple(_loads(row["missing_evidence"])),
        conflicts=tuple(_loads(row["conflicts"])),
        related_candidate_ids=tuple(_loads(row["related_candidate_ids"])),
        evaluated_at=_parse_dt(str(row["evaluated_at"])),
        policy_version=str(columns.get("policy_version", "identity-policy-v1")),
        evidence_fingerprint=str(columns.get("evidence_fingerprint", "")),
    )


def _valid_transition(previous: str, new: str) -> bool:
    if previous == new:
        return True
    allowed = {
        "discovered": {"identified", "evidence_pending", "rejected", "archived"},
        "identified": {"screenable", "evidence_pending", "rejected", "archived"},
        "evidence_pending": {"identified", "screenable", "rejected", "archived"},
        "screenable": {"identified", "analyzable", "ranked", "rejected", "archived"},
        "analyzable": {"ranked", "deep_research", "archived"},
        "ranked": {"deep_research", "analyzable", "archived"},
        "deep_research": {"ranked", "archived"},
        "rejected": {"archived"},
        "archived": set(),
    }
    return new in allowed.get(previous, set())
