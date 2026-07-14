from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.competitive.models import (
    AlgorithmicPeerRelationship,
    ComparisonDimension,
    CompetitiveAssessment,
    CompetitiveCheckpoint,
    CompetitiveConflictLink,
    CompetitiveProcessingRun,
    CompetitiveRelationship,
    CompetitiveRelationshipEvidenceLink,
    CompetitiveRelationshipSpanLink,
    PeerSet,
    PeerSetEvidenceLink,
    PeerSetMember,
    PeerSetSpanLink,
)

DEFAULT_COMPETITIVE_DB = Path("data/competitive/runtime/competitive.sqlite")


class CompetitiveRepository:
    def __init__(self, path: str | Path = DEFAULT_COMPETITIVE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_competitive_relationship(self, relationship: CompetitiveRelationship) -> None:
        self._upsert("competitive_relationships", _payload(relationship), key=_versioned_key("relationship_id"))

    def save_algorithmic_peer_relationship(self, relationship: AlgorithmicPeerRelationship) -> None:
        self._upsert("algorithmic_peer_relationships", _payload(relationship), key=_versioned_key("relationship_id"))

    def save_peer_set(self, peer_set: PeerSet) -> None:
        self._upsert("competitive_peer_sets", _payload(peer_set), key=_versioned_key("peer_set_id"))

    def save_peer_set_member(self, member: PeerSetMember) -> None:
        self._upsert("competitive_peer_set_members", _payload(member), key=("member_id",))

    def save_comparison_dimension(self, dimension: ComparisonDimension) -> None:
        self._upsert("competitive_comparison_dimensions", _payload(dimension), key=_versioned_key("dimension_id"))

    def save_assessment(self, assessment: CompetitiveAssessment) -> None:
        self._upsert("competitive_assessments", _payload(assessment), key=("assessment_id",))

    def save_relationship_evidence_links(self, links: Iterable[CompetitiveRelationshipEvidenceLink]) -> None:
        for link in links:
            self._upsert("competitive_relationship_evidence_links", _payload(link), key=("link_id",))

    def save_relationship_span_links(self, links: Iterable[CompetitiveRelationshipSpanLink]) -> None:
        for link in links:
            self._upsert("competitive_relationship_span_links", _payload(link), key=("link_id",))

    def save_peer_set_evidence_links(self, links: Iterable[PeerSetEvidenceLink]) -> None:
        for link in links:
            self._upsert("peer_set_evidence_links", _payload(link), key=("link_id",))

    def save_peer_set_span_links(self, links: Iterable[PeerSetSpanLink]) -> None:
        for link in links:
            self._upsert("peer_set_span_links", _payload(link), key=("link_id",))

    def save_conflict_links(self, links: Iterable[CompetitiveConflictLink]) -> None:
        for link in links:
            self._upsert("competitive_conflict_links", _payload(link), key=("link_id",))

    def save_processing_run(self, run: CompetitiveProcessingRun) -> None:
        self._upsert("competitive_processing_runs", _payload(run), key=("run_id",))

    def save_checkpoint(self, checkpoint: CompetitiveCheckpoint) -> None:
        self._upsert("competitive_checkpoints", _payload(checkpoint), key=("checkpoint_id",))

    def save_relationship_with_lineage(
        self,
        relationship: CompetitiveRelationship,
        *,
        evidence_links: Iterable[CompetitiveRelationshipEvidenceLink] = (),
        span_links: Iterable[CompetitiveRelationshipSpanLink] = (),
        conflict_links: Iterable[CompetitiveConflictLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(
                conn,
                "competitive_relationships",
                _payload(relationship),
                key=_versioned_key("relationship_id"),
            )
            for link in evidence_links:
                _upsert_payload(conn, "competitive_relationship_evidence_links", _payload(link), key=("link_id",))
            for link in span_links:
                _upsert_payload(conn, "competitive_relationship_span_links", _payload(link), key=("link_id",))
            for link in conflict_links:
                _upsert_payload(conn, "competitive_conflict_links", _payload(link), key=("link_id",))

    def save_peer_set_with_lineage(
        self,
        peer_set: PeerSet,
        *,
        members: Iterable[PeerSetMember] = (),
        evidence_links: Iterable[PeerSetEvidenceLink] = (),
        span_links: Iterable[PeerSetSpanLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "competitive_peer_sets", _payload(peer_set), key=_versioned_key("peer_set_id"))
            for member in members:
                _upsert_payload(conn, "competitive_peer_set_members", _payload(member), key=("member_id",))
            for link in evidence_links:
                _upsert_payload(conn, "peer_set_evidence_links", _payload(link), key=("link_id",))
            for link in span_links:
                _upsert_payload(conn, "peer_set_span_links", _payload(link), key=("link_id",))

    def relationship_lineage(self, relationship_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
        with self._connect() as conn:
            return {
                "relationship": tuple(
                    _rows(
                        conn,
                        "SELECT * FROM competitive_relationships WHERE relationship_id = ?",
                        (relationship_id,),
                    )
                ),
                "source_evidence": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_relationship_evidence_links
                        WHERE relationship_id = ?
                        ORDER BY role, position, link_id
                        """,
                        (relationship_id,),
                    )
                ),
                "spans": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_relationship_span_links
                        WHERE relationship_id = ?
                        ORDER BY role, position, link_id
                        """,
                        (relationship_id,),
                    )
                ),
                "conflicts": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_conflict_links
                        WHERE relationship_id = ?
                        ORDER BY role, conflict_id
                        """,
                        (relationship_id,),
                    )
                ),
            }

    def peer_set_lineage(self, peer_set_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
        with self._connect() as conn:
            return {
                "peer_set": tuple(
                    _rows(conn, "SELECT * FROM competitive_peer_sets WHERE peer_set_id = ?", (peer_set_id,))
                ),
                "members": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_peer_set_members
                        WHERE peer_set_id = ?
                        ORDER BY position, member_id
                        """,
                        (peer_set_id,),
                    )
                ),
                "source_evidence": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM peer_set_evidence_links
                        WHERE peer_set_id = ?
                        ORDER BY role, position, link_id
                        """,
                        (peer_set_id,),
                    )
                ),
                "spans": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM peer_set_span_links
                        WHERE peer_set_id = ?
                        ORDER BY role, position, link_id
                        """,
                        (peer_set_id,),
                    )
                ),
            }

    def competitive_relationship_at(
        self,
        relationship_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_relationships
                WHERE relationship_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, relationship_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (relationship_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_relationships
                WHERE relationship_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, relationship_id DESC
                LIMIT 1
            """
            params = (relationship_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return None if row is None else dict(row)

    def peer_set_at(
        self,
        peer_set_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_peer_sets
                WHERE peer_set_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, peer_set_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (peer_set_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_peer_sets
                WHERE peer_set_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, peer_set_id DESC
                LIMIT 1
            """
            params = (peer_set_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return None if row is None else dict(row)

    def peer_sets(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM competitive_peer_sets AS current
                    WHERE NOT EXISTS (
                        SELECT 1 FROM competitive_peer_sets AS newer
                        WHERE newer.peer_set_id = current.peer_set_id
                          AND _version_tuple(newer.effective_at, newer.recorded_at)
                              > _version_tuple(current.effective_at, current.recorded_at)
                    )
                    ORDER BY subject_candidate_id, scope
                    """,
                )
            )

    def peer_sets_at(
        self,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_peer_sets AS current
                WHERE effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_peer_sets AS newer
                      WHERE newer.peer_set_id = current.peer_set_id
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY subject_candidate_id, scope, peer_set_version, peer_set_id
            """
            params: tuple[object, ...] = (cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_peer_sets AS current
                WHERE effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_peer_sets AS newer
                      WHERE newer.peer_set_id = current.peer_set_id
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY subject_candidate_id, scope, peer_set_version, peer_set_id
            """
            params = (cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def peer_sets_for_subject(
        self,
        subject_candidate_id: str,
        *,
        status: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            if status is None:
                return tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_peer_sets AS current
                        WHERE subject_candidate_id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM competitive_peer_sets AS newer
                              WHERE newer.peer_set_id = current.peer_set_id
                                AND _version_tuple(newer.effective_at, newer.recorded_at)
                                    > _version_tuple(current.effective_at, current.recorded_at)
                          )
                        ORDER BY scope, peer_set_version, peer_set_id
                        """,
                        (subject_candidate_id,),
                    )
                )
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM competitive_peer_sets AS current
                    WHERE subject_candidate_id = ? AND status = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM competitive_peer_sets AS newer
                          WHERE newer.peer_set_id = current.peer_set_id
                            AND _version_tuple(newer.effective_at, newer.recorded_at)
                                > _version_tuple(current.effective_at, current.recorded_at)
                      )
                    ORDER BY scope, peer_set_version, peer_set_id
                    """,
                    (subject_candidate_id, status),
                )
            )

    def peer_sets_for_subject_at(
        self,
        subject_candidate_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_peer_sets AS current
                WHERE subject_candidate_id = ? AND effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_peer_sets AS newer
                      WHERE newer.peer_set_id = current.peer_set_id
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY scope, peer_set_version, peer_set_id
            """
            params: tuple[object, ...] = (subject_candidate_id, cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_peer_sets AS current
                WHERE subject_candidate_id = ? AND effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_peer_sets AS newer
                      WHERE newer.peer_set_id = current.peer_set_id
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY scope, peer_set_version, peer_set_id
            """
            params = (subject_candidate_id, cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def competitive_relationships_for_subject(
        self,
        subject_candidate_id: str,
        *,
        status: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            if status is None:
                return tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM competitive_relationships AS current
                        WHERE subject_candidate_id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM competitive_relationships AS newer
                              WHERE newer.relationship_id = current.relationship_id
                                AND _version_tuple(newer.effective_at, newer.recorded_at)
                                    > _version_tuple(current.effective_at, current.recorded_at)
                          )
                        ORDER BY peer_candidate_id, relationship_type, relationship_id
                        """,
                        (subject_candidate_id,),
                    )
                )
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM competitive_relationships AS current
                    WHERE subject_candidate_id = ? AND status = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM competitive_relationships AS newer
                          WHERE newer.relationship_id = current.relationship_id
                            AND _version_tuple(newer.effective_at, newer.recorded_at)
                                > _version_tuple(current.effective_at, current.recorded_at)
                      )
                    ORDER BY peer_candidate_id, relationship_type, relationship_id
                    """,
                    (subject_candidate_id, status),
                )
            )

    def competitive_relationships_for_subject_at(
        self,
        subject_candidate_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_relationships AS current
                WHERE subject_candidate_id = ? AND effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_relationships AS newer
                      WHERE newer.relationship_id = current.relationship_id
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY peer_candidate_id, relationship_type, relationship_id
            """
            params: tuple[object, ...] = (subject_candidate_id, cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_relationships AS current
                WHERE subject_candidate_id = ? AND effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_relationships AS newer
                      WHERE newer.relationship_id = current.relationship_id
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY peer_candidate_id, relationship_type, relationship_id
            """
            params = (subject_candidate_id, cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def algorithmic_relationships_for_subject(
        self,
        subject_candidate_id: str,
        *,
        status: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            if status is None:
                return tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM algorithmic_peer_relationships AS current
                        WHERE subject_candidate_id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM algorithmic_peer_relationships AS newer
                              WHERE newer.relationship_id = current.relationship_id
                                AND _version_tuple(newer.effective_at, newer.recorded_at)
                                    > _version_tuple(current.effective_at, current.recorded_at)
                          )
                        ORDER BY peer_candidate_id, relationship_type, relationship_id
                        """,
                        (subject_candidate_id,),
                    )
                )
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM algorithmic_peer_relationships AS current
                    WHERE subject_candidate_id = ? AND status = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM algorithmic_peer_relationships AS newer
                          WHERE newer.relationship_id = current.relationship_id
                            AND _version_tuple(newer.effective_at, newer.recorded_at)
                                > _version_tuple(current.effective_at, current.recorded_at)
                      )
                    ORDER BY peer_candidate_id, relationship_type, relationship_id
                    """,
                    (subject_candidate_id, status),
                )
            )

    def algorithmic_relationships_for_subject_at(
        self,
        subject_candidate_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM algorithmic_peer_relationships AS current
                WHERE subject_candidate_id = ? AND effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM algorithmic_peer_relationships AS newer
                      WHERE newer.relationship_id = current.relationship_id
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY peer_candidate_id, relationship_type, relationship_id
            """
            params: tuple[object, ...] = (subject_candidate_id, cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM algorithmic_peer_relationships AS current
                WHERE subject_candidate_id = ? AND effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM algorithmic_peer_relationships AS newer
                      WHERE newer.relationship_id = current.relationship_id
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY peer_candidate_id, relationship_type, relationship_id
            """
            params = (subject_candidate_id, cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def comparison_dimensions_for_relationship(self, relationship_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM competitive_comparison_dimensions AS current
                    WHERE relationship_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM competitive_comparison_dimensions AS newer
                          WHERE newer.dimension_id = current.dimension_id
                            AND _version_tuple(newer.effective_at, newer.recorded_at)
                                > _version_tuple(current.effective_at, current.recorded_at)
                      )
                    ORDER BY dimension_type, dimension_id
                    """,
                    (relationship_id,),
                )
            )

    def comparison_dimensions_for_relationship_at(
        self,
        relationship_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM competitive_comparison_dimensions AS current
                WHERE relationship_id = ? AND effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_comparison_dimensions AS newer
                      WHERE newer.dimension_id = current.dimension_id
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY dimension_type, dimension_id
            """
            params: tuple[object, ...] = (relationship_id, cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM competitive_comparison_dimensions AS current
                WHERE relationship_id = ? AND effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competitive_comparison_dimensions AS newer
                      WHERE newer.dimension_id = current.dimension_id
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY dimension_type, dimension_id
            """
            params = (relationship_id, cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def conflict_links_for_relationship(
        self,
        relationship_id: str,
        *,
        cutoff: datetime | None = None,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        if cutoff is None:
            sql = """
                SELECT * FROM competitive_conflict_links
                WHERE relationship_id = ?
                ORDER BY created_at, conflict_id, role
            """
            params: tuple[object, ...] = (relationship_id,)
        else:
            cutoff_value = _serialize(cutoff)
            sql = """
                SELECT * FROM competitive_conflict_links
                WHERE relationship_id = ? AND created_at <= ?
                ORDER BY created_at, conflict_id, role
            """
            params = (relationship_id, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def conflict_links(
        self,
        *,
        cutoff: datetime | None = None,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        if cutoff is None:
            sql = "SELECT * FROM competitive_conflict_links ORDER BY created_at, conflict_id, relationship_id"
            params: tuple[object, ...] = ()
        else:
            sql = """
                SELECT * FROM competitive_conflict_links
                WHERE created_at <= ?
                ORDER BY created_at, conflict_id, relationship_id
            """
            params = (_serialize(cutoff),)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def count(self, table: str) -> int:
        _ensure_table(table)
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def table_names(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
            return tuple(str(row["name"]) for row in rows)

    def index_names(self) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name").fetchall()
            return tuple(str(row["name"]) for row in rows if not str(row["name"]).startswith("sqlite_autoindex"))

    def _upsert(self, table: str, payload: dict[str, Any], *, key: tuple[str, ...]) -> None:
        _ensure_table(table)
        with self._connect() as conn:
            _upsert_payload(conn, table, payload, key=key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.create_function("_version_tuple", 2, lambda effective_at, recorded_at: f"{effective_at}|{recorded_at}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)


COMPETITIVE_TABLES = frozenset(
    {
        "competitive_peer_sets",
        "competitive_peer_set_members",
        "competitive_relationships",
        "algorithmic_peer_relationships",
        "competitive_comparison_dimensions",
        "competitive_assessments",
        "competitive_relationship_evidence_links",
        "competitive_relationship_span_links",
        "peer_set_evidence_links",
        "peer_set_span_links",
        "competitive_conflict_links",
        "competitive_processing_runs",
        "competitive_checkpoints",
    }
)


def _upsert_payload(conn: sqlite3.Connection, table: str, payload: dict[str, Any], *, key: tuple[str, ...]) -> None:
    normalized = {name: _serialize(value) for name, value in payload.items()}
    columns = tuple(normalized)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column not in key)
    conflict = ", ".join(key)
    sql = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT({conflict}) DO UPDATE SET {updates}
    """
    conn.execute(sql, tuple(normalized[column] for column in columns))


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in conn.execute(sql, params).fetchall())


def _payload(value: object) -> dict[str, Any]:
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


def _ensure_table(table: str) -> None:
    if table not in COMPETITIVE_TABLES:
        msg = f"unsupported competitive table: {table}"
        raise ValueError(msg)


def _versioned_key(identity_column: str) -> tuple[str, str, str, str]:
    return (identity_column, "effective_at", "recorded_at", "schema_version")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS competitive_peer_sets (
    peer_set_id TEXT NOT NULL,
    subject_candidate_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    peer_set_version TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    evidence_backed_count INTEGER NOT NULL,
    algorithmic_peer_count INTEGER NOT NULL,
    confidence REAL NOT NULL,
    coverage REAL NOT NULL,
    freshness REAL NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    conflict_status TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(peer_set_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_peer_set_members (
    member_id TEXT PRIMARY KEY,
    peer_set_id TEXT NOT NULL,
    peer_candidate_id TEXT NOT NULL,
    member_role TEXT NOT NULL,
    relationship_kind TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    freshness REAL NOT NULL,
    position INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(peer_set_id, peer_candidate_id, relationship_kind, relationship_id, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_relationships (
    relationship_id TEXT NOT NULL,
    subject_candidate_id TEXT NOT NULL,
    peer_candidate_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    status TEXT NOT NULL,
    predicate_id TEXT NOT NULL,
    predicate_schema_version TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    peer_entity_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    modality TEXT NOT NULL,
    polarity TEXT NOT NULL,
    confidence REAL NOT NULL,
    freshness REAL NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    projection_id TEXT,
    qualifier TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    conflict_status TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(relationship_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS algorithmic_peer_relationships (
    relationship_id TEXT NOT NULL,
    subject_candidate_id TEXT NOT NULL,
    peer_candidate_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    status TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    scope TEXT NOT NULL,
    compared_dimension_count INTEGER NOT NULL,
    matched_dimension_count INTEGER NOT NULL,
    missing_dimension_count INTEGER NOT NULL,
    similarity REAL NOT NULL,
    confidence REAL NOT NULL,
    freshness REAL NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(relationship_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_comparison_dimensions (
    dimension_id TEXT NOT NULL,
    subject_candidate_id TEXT NOT NULL,
    peer_candidate_id TEXT NOT NULL,
    dimension_type TEXT NOT NULL,
    subject_value TEXT NOT NULL,
    peer_value TEXT NOT NULL,
    match_status TEXT NOT NULL,
    relationship_kind TEXT NOT NULL,
    relationship_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    confidence REAL NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(dimension_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_assessments (
    assessment_id TEXT PRIMARY KEY,
    subject_candidate_id TEXT NOT NULL,
    peer_set_id TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_backed_competitors INTEGER NOT NULL,
    algorithmic_peers INTEGER NOT NULL,
    missing_evidence_count INTEGER NOT NULL,
    conflict_count INTEGER NOT NULL,
    confidence REAL NOT NULL,
    coverage REAL NOT NULL,
    freshness REAL NOT NULL,
    mode TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitive_relationship_evidence_links (
    link_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(relationship_id, source_evidence_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_relationship_span_links (
    link_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(relationship_id, span_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS peer_set_evidence_links (
    link_id TEXT PRIMARY KEY,
    peer_set_id TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(peer_set_id, source_evidence_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS peer_set_span_links (
    link_id TEXT PRIMARY KEY,
    peer_set_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(peer_set_id, span_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_conflict_links (
    link_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    conflict_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(relationship_id, conflict_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS competitive_processing_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitive_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    processor_name TEXT NOT NULL,
    target_id TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(processor_name, target_id, schema_version)
);

CREATE INDEX IF NOT EXISTS competitive_peer_sets_subject_status_idx ON competitive_peer_sets(subject_candidate_id, status);
CREATE INDEX IF NOT EXISTS competitive_peer_sets_scope_version_idx ON competitive_peer_sets(scope, peer_set_version, policy_id, policy_version);
CREATE INDEX IF NOT EXISTS competitive_peer_sets_time_idx ON competitive_peer_sets(effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS competitive_peer_sets_confidence_idx ON competitive_peer_sets(confidence, coverage, freshness);
CREATE INDEX IF NOT EXISTS competitive_peer_set_members_peer_idx ON competitive_peer_set_members(peer_candidate_id, relationship_kind, status);
CREATE INDEX IF NOT EXISTS competitive_peer_set_members_set_idx ON competitive_peer_set_members(peer_set_id, position);
CREATE INDEX IF NOT EXISTS competitive_relationships_subject_status_idx ON competitive_relationships(subject_candidate_id, status);
CREATE INDEX IF NOT EXISTS competitive_relationships_peer_type_idx ON competitive_relationships(peer_candidate_id, relationship_type);
CREATE INDEX IF NOT EXISTS competitive_relationships_type_time_idx ON competitive_relationships(relationship_type, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS competitive_relationships_claim_idx ON competitive_relationships(claim_id);
CREATE INDEX IF NOT EXISTS competitive_relationships_confidence_idx ON competitive_relationships(confidence, freshness);
CREATE INDEX IF NOT EXISTS algorithmic_peer_relationships_subject_status_idx ON algorithmic_peer_relationships(subject_candidate_id, status);
CREATE INDEX IF NOT EXISTS algorithmic_peer_relationships_peer_type_idx ON algorithmic_peer_relationships(peer_candidate_id, relationship_type);
CREATE INDEX IF NOT EXISTS algorithmic_peer_relationships_policy_idx ON algorithmic_peer_relationships(policy_id, policy_version, scope);
CREATE INDEX IF NOT EXISTS algorithmic_peer_relationships_time_idx ON algorithmic_peer_relationships(effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS competitive_comparison_dimensions_relationship_idx ON competitive_comparison_dimensions(relationship_kind, relationship_id);
CREATE INDEX IF NOT EXISTS competitive_comparison_dimensions_type_idx ON competitive_comparison_dimensions(dimension_type, match_status);
CREATE INDEX IF NOT EXISTS competitive_assessments_subject_idx ON competitive_assessments(subject_candidate_id, status);
CREATE INDEX IF NOT EXISTS competitive_assessments_peer_set_idx ON competitive_assessments(peer_set_id);
CREATE INDEX IF NOT EXISTS competitive_relationship_evidence_links_relationship_idx ON competitive_relationship_evidence_links(relationship_id);
CREATE INDEX IF NOT EXISTS competitive_relationship_evidence_links_evidence_idx ON competitive_relationship_evidence_links(source_evidence_id);
CREATE INDEX IF NOT EXISTS competitive_relationship_span_links_relationship_idx ON competitive_relationship_span_links(relationship_id);
CREATE INDEX IF NOT EXISTS competitive_relationship_span_links_span_idx ON competitive_relationship_span_links(span_id);
CREATE INDEX IF NOT EXISTS peer_set_evidence_links_set_idx ON peer_set_evidence_links(peer_set_id);
CREATE INDEX IF NOT EXISTS peer_set_evidence_links_evidence_idx ON peer_set_evidence_links(source_evidence_id);
CREATE INDEX IF NOT EXISTS peer_set_span_links_set_idx ON peer_set_span_links(peer_set_id);
CREATE INDEX IF NOT EXISTS peer_set_span_links_span_idx ON peer_set_span_links(span_id);
CREATE INDEX IF NOT EXISTS competitive_conflict_links_relationship_idx ON competitive_conflict_links(relationship_id);
CREATE INDEX IF NOT EXISTS competitive_conflict_links_conflict_idx ON competitive_conflict_links(conflict_id);
CREATE INDEX IF NOT EXISTS competitive_processing_runs_status_idx ON competitive_processing_runs(status, started_at);
CREATE INDEX IF NOT EXISTS competitive_checkpoints_processor_target_idx ON competitive_checkpoints(processor_name, target_id);
"""
