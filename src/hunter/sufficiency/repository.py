from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.sufficiency.models import (
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessment,
    DataSufficiencyCheckpoint,
    DataSufficiencyClaimLink,
    DataSufficiencyConflictLink,
    DataSufficiencyEvidenceLink,
    DataSufficiencyProcessingRun,
    DataSufficiencySpanLink,
    SourceDisagreement,
    SourceValidationResult,
)
from hunter.sufficiency.policies import DegradedModePolicy

DEFAULT_SUFFICIENCY_DB = Path("data/sufficiency/runtime/sufficiency.sqlite")


class DataSufficiencyRepository:
    def __init__(self, path: str | Path = DEFAULT_SUFFICIENCY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_requirement(self, requirement: DataRequirement) -> None:
        payload = _payload(requirement)
        source_types = tuple(payload.pop("required_source_types"))
        proxy_types = tuple(payload.pop("accepted_proxy_types"))
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "data_requirements", payload, key=_versioned_key("requirement_id"))
            _replace_requirement_rows(
                conn,
                "data_requirement_source_types",
                requirement.requirement_id,
                requirement.schema_version,
                requirement.effective_at,
                requirement.recorded_at,
                "source_type",
                source_types,
            )
            _replace_requirement_rows(
                conn,
                "data_requirement_proxy_types",
                requirement.requirement_id,
                requirement.schema_version,
                requirement.effective_at,
                requirement.recorded_at,
                "proxy_type",
                proxy_types,
            )

    def save_requirements(self, requirements: Iterable[DataRequirement]) -> None:
        for requirement in requirements:
            self.save_requirement(requirement)

    def save_degraded_mode_policy(self, policy: DegradedModePolicy) -> None:
        self._upsert("degraded_mode_policies", _payload(policy), key=_versioned_key("policy_id"))

    def save_availability(self, availability: DataAvailability) -> None:
        self._upsert("data_availability", _payload(availability), key=_versioned_key("availability_id"))

    def save_assessment(self, assessment: DataSufficiencyAssessment) -> None:
        self._upsert("data_sufficiency_assessments", _payload(assessment), key=_versioned_key("assessment_id"))

    def save_source_validation_result(self, result: SourceValidationResult) -> None:
        self._upsert("data_source_validation_results", _payload(result), key=_versioned_key("validation_id"))

    def save_disagreement(self, disagreement: SourceDisagreement) -> None:
        self._upsert("data_disagreement_records", _payload(disagreement), key=_versioned_key("disagreement_id"))

    def save_source_validation_with_lineage(
        self,
        result: SourceValidationResult,
        *,
        evidence_links: Iterable[DataSufficiencyEvidenceLink] = (),
        span_links: Iterable[DataSufficiencySpanLink] = (),
        claim_links: Iterable[DataSufficiencyClaimLink] = (),
        conflict_links: Iterable[DataSufficiencyConflictLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(
                conn,
                "data_source_validation_results",
                _payload(result),
                key=_versioned_key("validation_id"),
            )
            _save_lineage(conn, evidence_links, span_links, claim_links, conflict_links)

    def save_disagreement_with_lineage(
        self,
        disagreement: SourceDisagreement,
        *,
        evidence_links: Iterable[DataSufficiencyEvidenceLink] = (),
        span_links: Iterable[DataSufficiencySpanLink] = (),
        claim_links: Iterable[DataSufficiencyClaimLink] = (),
        conflict_links: Iterable[DataSufficiencyConflictLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(
                conn,
                "data_disagreement_records",
                _payload(disagreement),
                key=_versioned_key("disagreement_id"),
            )
            _save_lineage(conn, evidence_links, span_links, claim_links, conflict_links)

    def save_evidence_links(self, links: Iterable[DataSufficiencyEvidenceLink]) -> None:
        for link in links:
            self._upsert("data_sufficiency_evidence_links", _payload(link), key=("link_id",))

    def save_span_links(self, links: Iterable[DataSufficiencySpanLink]) -> None:
        for link in links:
            self._upsert("data_sufficiency_span_links", _payload(link), key=("link_id",))

    def save_claim_links(self, links: Iterable[DataSufficiencyClaimLink]) -> None:
        for link in links:
            self._upsert("data_sufficiency_claim_links", _payload(link), key=("link_id",))

    def save_conflict_links(self, links: Iterable[DataSufficiencyConflictLink]) -> None:
        for link in links:
            self._upsert("data_sufficiency_conflict_links", _payload(link), key=("link_id",))

    def save_processing_run(self, run: DataSufficiencyProcessingRun) -> None:
        self._upsert("data_sufficiency_processing_runs", _payload(run), key=("run_id",))

    def save_checkpoint(self, checkpoint: DataSufficiencyCheckpoint) -> None:
        self._upsert("data_sufficiency_checkpoints", _payload(checkpoint), key=("checkpoint_id",))

    def save_availability_with_lineage(
        self,
        availability: DataAvailability,
        *,
        evidence_links: Iterable[DataSufficiencyEvidenceLink] = (),
        span_links: Iterable[DataSufficiencySpanLink] = (),
        claim_links: Iterable[DataSufficiencyClaimLink] = (),
        conflict_links: Iterable[DataSufficiencyConflictLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "data_availability", _payload(availability), key=_versioned_key("availability_id"))
            _save_lineage(conn, evidence_links, span_links, claim_links, conflict_links)

    def save_assessment_with_lineage(
        self,
        assessment: DataSufficiencyAssessment,
        *,
        evidence_links: Iterable[DataSufficiencyEvidenceLink] = (),
        span_links: Iterable[DataSufficiencySpanLink] = (),
        claim_links: Iterable[DataSufficiencyClaimLink] = (),
        conflict_links: Iterable[DataSufficiencyConflictLink] = (),
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(
                conn,
                "data_sufficiency_assessments",
                _payload(assessment),
                key=_versioned_key("assessment_id"),
            )
            _save_lineage(conn, evidence_links, span_links, claim_links, conflict_links)

    def lineage(self, owner_type: str, owner_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
        params = (owner_type, owner_id)
        with self._connect() as conn:
            return {
                "source_evidence": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM data_sufficiency_evidence_links
                        WHERE owner_type = ? AND owner_id = ?
                        ORDER BY role, position, link_id
                        """,
                        params,
                    )
                ),
                "spans": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM data_sufficiency_span_links
                        WHERE owner_type = ? AND owner_id = ?
                        ORDER BY role, position, link_id
                        """,
                        params,
                    )
                ),
                "claims": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM data_sufficiency_claim_links
                        WHERE owner_type = ? AND owner_id = ?
                        ORDER BY role, position, link_id
                        """,
                        params,
                    )
                ),
                "conflicts": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM data_sufficiency_conflict_links
                        WHERE owner_type = ? AND owner_id = ?
                        ORDER BY role, position, link_id
                        """,
                        params,
                    )
                ),
            }

    def requirement_at(
        self,
        requirement_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        return self._version_at(
            "data_requirements",
            "requirement_id",
            requirement_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )

    def requirements(
        self,
        *,
        engine_id: str | None = None,
        analysis_purpose: str | None = None,
        output_field: str | None = None,
        policy_version: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        filters: list[str] = []
        params: list[object] = []
        if engine_id is not None:
            filters.append("engine_id = ?")
            params.append(engine_id)
        if analysis_purpose is not None:
            filters.append("analysis_purpose = ?")
            params.append(analysis_purpose)
        if output_field is not None:
            filters.append("output_field = ?")
            params.append(output_field)
        if policy_version is not None:
            filters.append("policy_version = ?")
            params.append(policy_version)
        return self._current_rows(
            "data_requirements",
            "requirement_id",
            where=" AND ".join(filters) if filters else None,
            params=tuple(params),
            order_by="engine_id, analysis_purpose, output_field, requirement_id",
        )

    def requirement_source_types(
        self,
        requirement_id: str,
        schema_version: str,
        *,
        effective_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> tuple[str, ...]:
        filters = ["requirement_id = ?", "schema_version = ?"]
        params: list[object] = [requirement_id, schema_version]
        if effective_at is not None and recorded_at is not None:
            filters.extend(["effective_at = ?", "recorded_at = ?"])
            params.extend([_serialize(effective_at), _serialize(recorded_at)])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_type FROM data_requirement_source_types
                WHERE {" AND ".join(filters)}
                ORDER BY position, source_type
                """,
                tuple(params),
            ).fetchall()
            return tuple(str(row["source_type"]) for row in rows)

    def requirement_proxy_types(
        self,
        requirement_id: str,
        schema_version: str,
        *,
        effective_at: datetime | None = None,
        recorded_at: datetime | None = None,
    ) -> tuple[str, ...]:
        filters = ["requirement_id = ?", "schema_version = ?"]
        params: list[object] = [requirement_id, schema_version]
        if effective_at is not None and recorded_at is not None:
            filters.extend(["effective_at = ?", "recorded_at = ?"])
            params.extend([_serialize(effective_at), _serialize(recorded_at)])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT proxy_type FROM data_requirement_proxy_types
                WHERE {" AND ".join(filters)}
                ORDER BY position, proxy_type
                """,
                tuple(params),
            ).fetchall()
            return tuple(str(row["proxy_type"]) for row in rows)

    def checkpoint(self, processor_name: str, target_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM data_sufficiency_checkpoints
                WHERE processor_name = ? AND target_id = ?
                ORDER BY updated_at DESC, checkpoint_id DESC
                LIMIT 1
                """,
                (processor_name, target_id),
            ).fetchone()
            return None if row is None else dict(row)

    def availability_at(
        self,
        availability_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        return self._version_at(
            "data_availability",
            "availability_id",
            availability_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )

    def assessment_at(
        self,
        assessment_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        return self._version_at(
            "data_sufficiency_assessments",
            "assessment_id",
            assessment_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )

    def availability_for_candidate(
        self,
        candidate_id: str,
        *,
        engine_id: str | None = None,
        analysis_purpose: str | None = None,
        availability_state: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        filters = ["candidate_id = ?"]
        params: list[object] = [candidate_id]
        if engine_id is not None:
            filters.append("engine_id = ?")
            params.append(engine_id)
        if analysis_purpose is not None:
            filters.append("analysis_purpose = ?")
            params.append(analysis_purpose)
        if availability_state is not None:
            filters.append("availability_state = ?")
            params.append(availability_state)
        where = " AND ".join(filters)
        return self._current_rows("data_availability", "availability_id", where=where, params=tuple(params))

    def availability_for_candidate_at(
        self,
        candidate_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        return self._rows_at(
            "data_availability",
            "availability_id",
            cutoff,
            "candidate_id = ?",
            (candidate_id,),
            strict_known_by_hunter=strict_known_by_hunter,
            order_by="engine_id, analysis_purpose, requirement_id",
        )

    def assessments_for_candidate(
        self,
        candidate_id: str,
        *,
        engine_id: str | None = None,
        analysis_purpose: str | None = None,
        sufficiency_state: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        filters = ["candidate_id = ?"]
        params: list[object] = [candidate_id]
        if engine_id is not None:
            filters.append("engine_id = ?")
            params.append(engine_id)
        if analysis_purpose is not None:
            filters.append("analysis_purpose = ?")
            params.append(analysis_purpose)
        if sufficiency_state is not None:
            filters.append("sufficiency_state = ?")
            params.append(sufficiency_state)
        where = " AND ".join(filters)
        return self._current_rows(
            "data_sufficiency_assessments",
            "assessment_id",
            where=where,
            params=tuple(params),
            order_by="engine_id, analysis_purpose, assessment_scope",
        )

    def assessments_for_candidate_at(
        self,
        candidate_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        return self._rows_at(
            "data_sufficiency_assessments",
            "assessment_id",
            cutoff,
            "candidate_id = ?",
            (candidate_id,),
            strict_known_by_hunter=strict_known_by_hunter,
            order_by="engine_id, analysis_purpose, assessment_scope",
        )

    def disagreements(
        self,
        *,
        candidate_id: str | None = None,
        engine_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        filters: list[str] = []
        params: list[object] = []
        if candidate_id is not None:
            filters.append("candidate_id = ?")
            params.append(candidate_id)
        if engine_id is not None:
            filters.append("engine_id = ?")
            params.append(engine_id)
        return self._current_rows(
            "data_disagreement_records",
            "disagreement_id",
            where=" AND ".join(filters) if filters else None,
            params=tuple(params),
            order_by="candidate_id, engine_id, analysis_purpose, disagreement_id",
        )

    def source_validation_results(
        self,
        *,
        candidate_id: str | None = None,
        engine_id: str | None = None,
        validation_status: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        filters: list[str] = []
        params: list[object] = []
        if candidate_id is not None:
            filters.append("candidate_id = ?")
            params.append(candidate_id)
        if engine_id is not None:
            filters.append("engine_id = ?")
            params.append(engine_id)
        if validation_status is not None:
            filters.append("validation_status = ?")
            params.append(validation_status)
        return self._current_rows(
            "data_source_validation_results",
            "validation_id",
            where=" AND ".join(filters) if filters else None,
            params=tuple(params),
            order_by="candidate_id, engine_id, analysis_purpose, validation_id",
        )

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

    def _version_at(
        self,
        table: str,
        identity_column: str,
        identity_value: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        _ensure_table(table)
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = f"""
                SELECT * FROM {table}
                WHERE {identity_column} = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, {identity_column} DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (identity_value, cutoff_value, cutoff_value)
        else:
            sql = f"""
                SELECT * FROM {table}
                WHERE {identity_column} = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, {identity_column} DESC
                LIMIT 1
            """
            params = (identity_value, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return None if row is None else dict(row)

    def _current_rows(
        self,
        table: str,
        identity_column: str,
        *,
        where: str | None = None,
        params: tuple[object, ...] = (),
        order_by: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        _ensure_table(table)
        where_sql = f"WHERE {where}" if where else ""
        conjunction = "AND" if where else "WHERE"
        sql = f"""
            SELECT * FROM {table} AS current
            {where_sql}
            {conjunction} NOT EXISTS (
                SELECT 1 FROM {table} AS newer
                WHERE newer.{identity_column} = current.{identity_column}
                  AND _version_tuple(newer.effective_at, newer.recorded_at)
                      > _version_tuple(current.effective_at, current.recorded_at)
            )
            ORDER BY {order_by or identity_column}
        """
        with self._connect() as conn:
            return tuple(_rows(conn, sql, params))

    def _rows_at(
        self,
        table: str,
        identity_column: str,
        cutoff: datetime,
        where: str,
        params: tuple[object, ...],
        *,
        strict_known_by_hunter: bool,
        order_by: str,
    ) -> tuple[dict[str, Any], ...]:
        _ensure_table(table)
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = f"""
                SELECT * FROM {table} AS current
                WHERE {where} AND effective_at <= ? AND recorded_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS newer
                      WHERE newer.{identity_column} = current.{identity_column}
                        AND newer.effective_at <= ? AND newer.recorded_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY {order_by}
            """
            query_params = (*params, cutoff_value, cutoff_value, cutoff_value, cutoff_value)
        else:
            sql = f"""
                SELECT * FROM {table} AS current
                WHERE {where} AND effective_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS newer
                      WHERE newer.{identity_column} = current.{identity_column}
                        AND newer.effective_at <= ?
                        AND _version_tuple(newer.effective_at, newer.recorded_at)
                            > _version_tuple(current.effective_at, current.recorded_at)
                  )
                ORDER BY {order_by}
            """
            query_params = (*params, cutoff_value, cutoff_value)
        with self._connect() as conn:
            return tuple(_rows(conn, sql, query_params))

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
        from hunter.sufficiency.migrations import migrate_data_sufficiency_schema

        with self._connect() as conn:
            migrate_data_sufficiency_schema(conn)


SUFFICIENCY_TABLES = frozenset(
    {
        "data_requirements",
        "data_requirement_source_types",
        "data_requirement_proxy_types",
        "data_availability",
        "data_sufficiency_assessments",
        "degraded_mode_policies",
        "data_source_validation_results",
        "data_disagreement_records",
        "data_sufficiency_evidence_links",
        "data_sufficiency_span_links",
        "data_sufficiency_claim_links",
        "data_sufficiency_conflict_links",
        "data_sufficiency_processing_runs",
        "data_sufficiency_checkpoints",
    }
)


def _save_lineage(
    conn: sqlite3.Connection,
    evidence_links: Iterable[DataSufficiencyEvidenceLink],
    span_links: Iterable[DataSufficiencySpanLink],
    claim_links: Iterable[DataSufficiencyClaimLink],
    conflict_links: Iterable[DataSufficiencyConflictLink],
) -> None:
    for link in evidence_links:
        _upsert_payload(conn, "data_sufficiency_evidence_links", _payload(link), key=("link_id",))
    for link in span_links:
        _upsert_payload(conn, "data_sufficiency_span_links", _payload(link), key=("link_id",))
    for link in claim_links:
        _upsert_payload(conn, "data_sufficiency_claim_links", _payload(link), key=("link_id",))
    for link in conflict_links:
        _upsert_payload(conn, "data_sufficiency_conflict_links", _payload(link), key=("link_id",))


def _replace_requirement_rows(
    conn: sqlite3.Connection,
    table: str,
    requirement_id: str,
    schema_version: str,
    effective_at: datetime,
    recorded_at: datetime,
    value_column: str,
    values: tuple[str, ...],
) -> None:
    effective = _serialize(effective_at)
    recorded = _serialize(recorded_at)
    conn.execute(
        f"DELETE FROM {table} WHERE requirement_id = ? AND schema_version = ? AND effective_at = ? AND recorded_at = ?",
        (requirement_id, schema_version, effective, recorded),
    )
    for position, value in enumerate(values):
        conn.execute(
            f"""
            INSERT INTO {table} (requirement_id, {value_column}, position, schema_version, effective_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (requirement_id, value, position, schema_version, effective, recorded),
        )


def _upsert_payload(conn: sqlite3.Connection, table: str, payload: dict[str, Any], *, key: tuple[str, ...]) -> None:
    normalized = {name: _serialize(value) for name, value in payload.items()}
    columns = tuple(normalized)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column not in key)
    conflict = ", ".join(key)
    if updates:
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO UPDATE SET {updates}
        """
    else:
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({conflict}) DO NOTHING
        """
    conn.execute(sql, tuple(normalized[column] for column in columns))


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in conn.execute(sql, params).fetchall())


def _payload(value: object) -> dict[str, Any]:
    if not is_dataclass(value):
        msg = f"expected dataclass payload: {value.__class__.__name__}"
        raise TypeError(msg)
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
    if table not in SUFFICIENCY_TABLES:
        msg = f"unsupported data sufficiency table: {table}"
        raise ValueError(msg)


def _versioned_key(identity_column: str) -> tuple[str, str, str, str]:
    return (identity_column, "effective_at", "recorded_at", "schema_version")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS data_requirements (
    requirement_id TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    analysis_purpose TEXT NOT NULL,
    output_field TEXT NOT NULL,
    requirement_kind TEXT NOT NULL,
    evidence_domain TEXT NOT NULL,
    required_entity_type TEXT NOT NULL,
    direct_observation_required INTEGER NOT NULL,
    proxy_allowed INTEGER NOT NULL,
    minimum_freshness_seconds INTEGER NOT NULL,
    minimum_source_authority TEXT NOT NULL,
    minimum_lineage_depth INTEGER NOT NULL,
    minimum_confidence REAL NOT NULL,
    historical_required INTEGER NOT NULL,
    blocking_level TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(requirement_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS data_requirement_source_types (
    requirement_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(requirement_id, source_type, schema_version, effective_at, recorded_at)
);
CREATE TABLE IF NOT EXISTS data_requirement_proxy_types (
    requirement_id TEXT NOT NULL,
    proxy_type TEXT NOT NULL,
    position INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(requirement_id, proxy_type, schema_version, effective_at, recorded_at)
);
CREATE TABLE IF NOT EXISTS data_availability (
    availability_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    analysis_purpose TEXT NOT NULL,
    availability_state TEXT NOT NULL,
    directness TEXT NOT NULL,
    proxy_type TEXT,
    freshness_seconds INTEGER,
    source_quality TEXT NOT NULL,
    lineage_complete INTEGER NOT NULL,
    conflict_state TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    missing_reason TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    cutoff_at TEXT,
    replay_mode TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(availability_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_assessments (
    assessment_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    analysis_purpose TEXT NOT NULL,
    assessment_scope TEXT NOT NULL,
    sufficiency_state TEXT NOT NULL,
    degraded_mode TEXT NOT NULL,
    coverage_score REAL NOT NULL,
    freshness_state TEXT NOT NULL,
    source_quality_state TEXT NOT NULL,
    lineage_state TEXT NOT NULL,
    conflict_state TEXT NOT NULL,
    direct_observation_coverage REAL NOT NULL,
    proxy_signal_coverage REAL NOT NULL,
    material_missing_count INTEGER NOT NULL,
    limitations_summary TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    cutoff_at TEXT,
    replay_mode TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(assessment_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS degraded_mode_policies (
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    unavailable_required_outcome TEXT NOT NULL,
    partial_required_outcome TEXT NOT NULL,
    stale_required_outcome TEXT NOT NULL,
    proxy_for_direct_outcome TEXT NOT NULL,
    optional_missing_outcome TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(policy_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS data_source_validation_results (
    validation_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    analysis_purpose TEXT NOT NULL,
    source_a TEXT NOT NULL,
    source_b TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    compatible_scope INTEGER NOT NULL,
    source_authority_state TEXT NOT NULL,
    freshness_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    cutoff_at TEXT,
    replay_mode TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL,
    UNIQUE(validation_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS data_disagreement_records (
    disagreement_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    analysis_purpose TEXT NOT NULL,
    disagreement_state TEXT NOT NULL,
    compared_source_count INTEGER NOT NULL,
    compatible_scope INTEGER NOT NULL,
    reason TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    replay_mode TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(disagreement_id, effective_at, recorded_at, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_evidence_links (
    link_id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(owner_type, owner_id, source_evidence_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_span_links (
    link_id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(owner_type, owner_id, span_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_claim_links (
    link_id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(owner_type, owner_id, claim_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_conflict_links (
    link_id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    conflict_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(owner_type, owner_id, conflict_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS data_sufficiency_processing_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    replay_mode TEXT NOT NULL,
    cutoff_at TEXT,
    schema_version TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_sufficiency_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    processor_name TEXT NOT NULL,
    target_id TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(processor_name, target_id, schema_version)
);

CREATE INDEX IF NOT EXISTS data_requirements_engine_purpose_field_policy_idx ON data_requirements(engine_id, analysis_purpose, output_field, policy_id, policy_version);
CREATE INDEX IF NOT EXISTS data_requirements_effective_recorded_idx ON data_requirements(effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_requirement_source_types_source_idx ON data_requirement_source_types(source_type, requirement_id);
CREATE INDEX IF NOT EXISTS data_requirement_proxy_types_proxy_idx ON data_requirement_proxy_types(proxy_type, requirement_id);
CREATE INDEX IF NOT EXISTS data_availability_candidate_requirement_state_time_idx ON data_availability(candidate_id, requirement_id, availability_state, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_availability_engine_purpose_state_idx ON data_availability(engine_id, analysis_purpose, availability_state);
CREATE INDEX IF NOT EXISTS data_availability_processing_run_idx ON data_availability(processing_run_id);
CREATE INDEX IF NOT EXISTS data_availability_stale_unavailable_idx ON data_availability(engine_id, analysis_purpose, availability_state, candidate_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_assessments_candidate_engine_purpose_state_time_idx ON data_sufficiency_assessments(candidate_id, engine_id, analysis_purpose, sufficiency_state, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_sufficiency_assessments_processing_run_idx ON data_sufficiency_assessments(processing_run_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_assessments_stale_unavailable_idx ON data_sufficiency_assessments(engine_id, analysis_purpose, sufficiency_state, candidate_id);
CREATE INDEX IF NOT EXISTS degraded_mode_policies_version_idx ON degraded_mode_policies(policy_id, policy_version, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_source_validation_results_candidate_requirement_idx ON data_source_validation_results(candidate_id, requirement_id, validation_status, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_source_validation_results_status_time_idx ON data_source_validation_results(engine_id, analysis_purpose, validation_status, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_source_validation_results_processing_run_idx ON data_source_validation_results(processing_run_id);
CREATE INDEX IF NOT EXISTS data_disagreement_records_candidate_requirement_idx ON data_disagreement_records(candidate_id, requirement_id, disagreement_state, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_disagreement_records_engine_purpose_idx ON data_disagreement_records(engine_id, analysis_purpose, disagreement_state);
CREATE INDEX IF NOT EXISTS data_disagreement_records_time_idx ON data_disagreement_records(engine_id, analysis_purpose, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS data_disagreement_records_processing_run_idx ON data_disagreement_records(processing_run_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_evidence_links_owner_idx ON data_sufficiency_evidence_links(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_evidence_links_evidence_idx ON data_sufficiency_evidence_links(source_evidence_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_span_links_owner_idx ON data_sufficiency_span_links(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_span_links_span_idx ON data_sufficiency_span_links(span_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_claim_links_owner_idx ON data_sufficiency_claim_links(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_claim_links_claim_idx ON data_sufficiency_claim_links(claim_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_conflict_links_owner_idx ON data_sufficiency_conflict_links(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_conflict_links_conflict_idx ON data_sufficiency_conflict_links(conflict_id);
CREATE INDEX IF NOT EXISTS data_sufficiency_processing_runs_status_idx ON data_sufficiency_processing_runs(status, started_at);
CREATE INDEX IF NOT EXISTS data_sufficiency_processing_runs_replay_idx ON data_sufficiency_processing_runs(replay_mode, cutoff_at);
CREATE INDEX IF NOT EXISTS data_sufficiency_checkpoints_processor_target_idx ON data_sufficiency_checkpoints(processor_name, target_id);
"""
