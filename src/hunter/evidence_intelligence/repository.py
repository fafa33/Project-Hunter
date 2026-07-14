from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.models import (
    AIProviderArtifact,
    AIProviderHealth,
    ClaimClaimLink,
    ClaimConflictLink,
    ClaimLifecycleEvent,
    ConflictClaimLink,
    ConflictLifecycleEvent,
    DocumentLifecycleEvent,
    EntityAliasLink,
    EntityIdentifierLink,
    EvidenceDocument,
    EvidenceDocumentVersion,
    EvidenceProcessingRun,
    EvidenceSpan,
    EvidenceSpanLink,
    ExtractionProposal,
    ExtractionSchema,
    KnowledgeCheckpoint,
    KnowledgeClaim,
    KnowledgeConflict,
    KnowledgeEntity,
    KnowledgeRelationship,
    PredicateDefinition,
    PredicateRegistry,
    SecurityAuditEvent,
    SourceAuthorityVerificationEvent,
    SourceEvidenceLink,
)

DEFAULT_EVIDENCE_INTELLIGENCE_DB = Path("data/evidence_intelligence/runtime/evidence_intelligence.sqlite")


class EvidenceIntelligenceRepository:
    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_INTELLIGENCE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_document(self, document: EvidenceDocument) -> None:
        self._upsert("evidence_documents", _payload(document), key=("document_id",))

    def save_document_version(self, version: EvidenceDocumentVersion) -> None:
        self._upsert("evidence_document_versions", _payload(version), key=("version_id",))

    def save_document_lifecycle_event(self, event: DocumentLifecycleEvent) -> None:
        self._upsert("document_lifecycle_events", _payload(event), key=("event_id",))

    def save_authority_event(self, event: SourceAuthorityVerificationEvent) -> None:
        self._upsert("source_authority_verification_events", _payload(event), key=("verification_id",))

    def save_span(self, span: EvidenceSpan) -> None:
        self._upsert("evidence_spans", _payload(span), key=("span_id",))

    def save_entity(self, entity: KnowledgeEntity) -> None:
        self._upsert("knowledge_entities", _payload(entity), key=("entity_id",))

    def save_predicate(self, predicate: PredicateDefinition) -> None:
        payload = _payload(predicate)
        scalar = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "permitted_subject_types",
                "permitted_object_entity_types",
                "permitted_literal_value_types",
                "valid_qualifiers",
                "valid_modalities",
                "valid_polarities",
            }
        }
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "predicate_registry", scalar, key=("predicate_id", "schema_version"))
            _replace_rule_rows(
                conn,
                "predicate_subject_type_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "subject_type",
                predicate.permitted_subject_types,
            )
            _replace_rule_rows(
                conn,
                "predicate_object_entity_type_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "object_entity_type",
                predicate.permitted_object_entity_types,
            )
            _replace_rule_rows(
                conn,
                "predicate_literal_value_type_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "literal_value_type",
                predicate.permitted_literal_value_types,
            )
            _replace_rule_rows(
                conn,
                "predicate_qualifier_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "qualifier",
                predicate.valid_qualifiers,
            )
            _replace_rule_rows(
                conn,
                "predicate_modality_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "modality",
                predicate.valid_modalities,
            )
            _replace_rule_rows(
                conn,
                "predicate_polarity_rules",
                predicate.predicate_id,
                predicate.schema_version,
                "polarity",
                predicate.valid_polarities,
            )

    def save_predicate_registry(self, registry: PredicateRegistry) -> None:
        for predicate in registry.predicates:
            self.save_predicate(predicate)

    def save_claim(self, claim: KnowledgeClaim) -> None:
        self._upsert("knowledge_claims", _payload(claim), key=("claim_id",))

    def save_claim_lifecycle_event(self, event: ClaimLifecycleEvent) -> None:
        self._upsert("claim_lifecycle_events", _payload(event), key=("event_id",))

    def save_claim_with_lifecycle(
        self,
        claim: KnowledgeClaim,
        event: ClaimLifecycleEvent,
        source_links: Iterable[SourceEvidenceLink],
        span_links: Iterable[EvidenceSpanLink],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "knowledge_claims", _payload(claim), key=("claim_id",))
            _upsert_payload(conn, "claim_lifecycle_events", _payload(event), key=("event_id",))
            for link in source_links:
                payload = _payload(link)
                payload["claim_id"] = payload.pop("owner_id")
                _upsert_payload(conn, "claim_source_evidence_links", payload, key=("link_id",))
            for link in span_links:
                payload = _payload(link)
                payload["claim_id"] = payload.pop("owner_id")
                _upsert_payload(conn, "claim_evidence_span_links", payload, key=("link_id",))

    def append_claim_lifecycle_event(
        self,
        event: ClaimLifecycleEvent,
        *,
        confidence: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "claim_lifecycle_events", _payload(event), key=("event_id",))
            updates: dict[str, Any] = {"status": event.new_status}
            if confidence is not None:
                updates["confidence"] = confidence
            if event.new_status == "superseded":
                updates["superseded_at"] = event.effective_at
            if event.new_status == "retracted":
                updates["retracted_at"] = event.effective_at
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE knowledge_claims SET {assignments} WHERE claim_id = ?",
                tuple(_serialize(value) for value in updates.values()) + (event.claim_id,),
            )

    def save_conflict(self, conflict: KnowledgeConflict) -> None:
        self._upsert("knowledge_conflicts", _payload(conflict), key=("conflict_id",))

    def save_conflict_lifecycle_event(self, event: ConflictLifecycleEvent) -> None:
        self._upsert("conflict_lifecycle_events", _payload(event), key=("event_id",))

    def save_conflict_with_lifecycle(
        self,
        conflict: KnowledgeConflict,
        event: ConflictLifecycleEvent,
        claim_links: Iterable[ConflictClaimLink],
        source_links: Iterable[SourceEvidenceLink],
        span_links: Iterable[EvidenceSpanLink],
        claim_conflict_links: Iterable[ClaimConflictLink],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "knowledge_conflicts", _payload(conflict), key=("conflict_id",))
            _upsert_payload(conn, "conflict_lifecycle_events", _payload(event), key=("event_id",))
            for link in claim_links:
                _upsert_payload(conn, "conflict_claim_links", _payload(link), key=("link_id",))
            for link in source_links:
                payload = _payload(link)
                payload["conflict_id"] = payload.pop("owner_id")
                _upsert_payload(conn, "conflict_source_evidence_links", payload, key=("link_id",))
            for link in span_links:
                payload = _payload(link)
                payload["conflict_id"] = payload.pop("owner_id")
                _upsert_payload(conn, "conflict_evidence_span_links", payload, key=("link_id",))
            for link in claim_conflict_links:
                _upsert_payload(conn, "claim_conflict_links", _payload(link), key=("link_id",))

    def append_conflict_lifecycle_event(self, event: ConflictLifecycleEvent) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            _upsert_payload(conn, "conflict_lifecycle_events", _payload(event), key=("event_id",))
            updates: dict[str, Any] = {"status": event.new_status}
            if event.new_status == "resolved":
                updates["resolved_at"] = event.effective_at
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE knowledge_conflicts SET {assignments} WHERE conflict_id = ?",
                tuple(_serialize(value) for value in updates.values()) + (event.conflict_id,),
            )

    def save_relationship(self, relationship: KnowledgeRelationship) -> None:
        self._upsert("knowledge_relationship_projections", _payload(relationship), key=("relationship_id",))

    def clear_relationship_projections(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM knowledge_relationship_projections")

    def save_processing_run(self, run: EvidenceProcessingRun) -> None:
        self._upsert("evidence_processing_runs", _payload(run), key=("run_id",))

    def save_provider_artifact(self, artifact: AIProviderArtifact) -> None:
        self._upsert("ai_provider_artifacts", _payload(artifact), key=("artifact_id",))

    def save_provider_health(self, health: AIProviderHealth) -> None:
        self._upsert("ai_provider_health", _payload(health), key=("health_id",))

    def save_extraction_schema(self, schema: ExtractionSchema) -> None:
        self._upsert("extraction_schemas", _payload(schema), key=("schema_id",))

    def save_extraction_proposal(self, proposal: ExtractionProposal) -> None:
        self._upsert("extraction_proposals", _payload(proposal), key=("proposal_id",))

    def save_checkpoint(self, checkpoint: KnowledgeCheckpoint) -> None:
        self._upsert("knowledge_checkpoints", _payload(checkpoint), key=("checkpoint_id",))

    def save_security_event(self, event: SecurityAuditEvent) -> None:
        self._upsert("security_audit_events", _payload(event), key=("event_id",))

    def save_source_evidence_links(self, table: str, links: Iterable[SourceEvidenceLink]) -> None:
        _ensure_table(
            table,
            {
                "claim_source_evidence_links",
                "conflict_source_evidence_links",
                "knowledge_entity_source_evidence_links",
            },
        )
        for link in links:
            payload = _payload(link)
            if table.startswith("claim_"):
                payload["claim_id"] = payload.pop("owner_id")
            elif table.startswith("conflict_"):
                payload["conflict_id"] = payload.pop("owner_id")
            else:
                payload["entity_id"] = payload.pop("owner_id")
            self._upsert(table, payload, key=("link_id",))

    def save_span_links(self, table: str, links: Iterable[EvidenceSpanLink]) -> None:
        _ensure_table(
            table,
            {
                "claim_evidence_span_links",
                "conflict_evidence_span_links",
                "knowledge_entity_span_links",
                "document_lifecycle_event_span_links",
                "claim_lifecycle_event_span_links",
                "conflict_lifecycle_event_span_links",
            },
        )
        for link in links:
            payload = _payload(link)
            if table.startswith("claim_") and "lifecycle_event" not in table:
                payload["claim_id"] = payload.pop("owner_id")
            elif table.startswith("conflict_") and "lifecycle_event" not in table:
                payload["conflict_id"] = payload.pop("owner_id")
            elif table.startswith("knowledge_entity_"):
                payload["entity_id"] = payload.pop("owner_id")
            else:
                payload["event_id"] = payload.pop("owner_id")
            self._upsert(table, payload, key=("link_id",))

    def save_claim_conflict_links(self, links: Iterable[ClaimConflictLink]) -> None:
        for link in links:
            self._upsert("claim_conflict_links", _payload(link), key=("link_id",))

    def save_claim_claim_links(self, links: Iterable[ClaimClaimLink]) -> None:
        for link in links:
            self._upsert("claim_claim_links", _payload(link), key=("link_id",))

    def save_conflict_claim_links(self, links: Iterable[ConflictClaimLink]) -> None:
        for link in links:
            self._upsert("conflict_claim_links", _payload(link), key=("link_id",))

    def save_entity_identifier_links(self, links: Iterable[EntityIdentifierLink]) -> None:
        for link in links:
            self._upsert("knowledge_entity_identifier_links", _payload(link), key=("link_id",))

    def save_entity_alias_links(self, links: Iterable[EntityAliasLink]) -> None:
        for link in links:
            self._upsert("knowledge_entity_alias_links", _payload(link), key=("link_id",))

    def claim_lineage(self, claim_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
        with self._connect() as conn:
            return {
                "claim": tuple(_rows(conn, "SELECT * FROM knowledge_claims WHERE claim_id = ?", (claim_id,))),
                "source_evidence": tuple(
                    _rows(
                        conn,
                        "SELECT * FROM claim_source_evidence_links WHERE claim_id = ? ORDER BY role, position",
                        (claim_id,),
                    )
                ),
                "spans": tuple(
                    _rows(
                        conn,
                        """
                        SELECT spans.* FROM evidence_spans spans
                        JOIN claim_evidence_span_links links ON spans.span_id = links.span_id
                        WHERE links.claim_id = ?
                        ORDER BY links.role, links.position
                        """,
                        (claim_id,),
                    )
                ),
                "documents": tuple(
                    _rows(
                        conn,
                        """
                        SELECT DISTINCT documents.* FROM evidence_documents documents
                        JOIN evidence_spans spans ON documents.document_id = spans.document_id
                        JOIN claim_evidence_span_links links ON spans.span_id = links.span_id
                        WHERE links.claim_id = ?
                        ORDER BY documents.document_id
                        """,
                        (claim_id,),
                    )
                ),
                "claim_events": tuple(
                    _rows(
                        conn,
                        "SELECT * FROM claim_lifecycle_events WHERE claim_id = ? ORDER BY effective_at, recorded_at, event_id",
                        (claim_id,),
                    )
                ),
                "conflicts": tuple(
                    _rows(
                        conn,
                        """
                        SELECT conflicts.* FROM knowledge_conflicts conflicts
                        JOIN claim_conflict_links links ON conflicts.conflict_id = links.conflict_id
                        WHERE links.claim_id = ?
                        ORDER BY conflicts.conflict_id
                        """,
                        (claim_id,),
                    )
                ),
            }

    def document_status_at(
        self,
        document_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> str | None:
        row = self._document_lifecycle_event_at(
            document_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )
        if row is None:
            return None
        return str(row["new_status"])

    def authority_status_at(
        self,
        document_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> str | None:
        row = self.authority_event_at(
            document_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )
        if row is None:
            return None
        return str(row["authority_status"])

    def authority_event_at(
        self,
        document_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM source_authority_verification_events
                WHERE document_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, verification_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (document_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM source_authority_verification_events
                WHERE document_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, verification_id DESC
                LIMIT 1
            """
            params = (document_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return dict(row)

    def document_lifecycle_events(self, document_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM document_lifecycle_events
                    WHERE document_id = ?
                    ORDER BY effective_at, recorded_at, event_id
                    """,
                    (document_id,),
                )
            )

    def documents(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(_rows(conn, "SELECT * FROM evidence_documents ORDER BY document_id"))

    def authority_events(self, document_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM source_authority_verification_events
                    WHERE document_id = ?
                    ORDER BY effective_at, recorded_at, verification_id
                    """,
                    (document_id,),
                )
            )

    def claim_status_at(
        self,
        claim_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> str | None:
        row = self.claim_lifecycle_event_at(
            claim_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )
        if row is None:
            return None
        return str(row["new_status"])

    def claim_lifecycle_event_at(
        self,
        claim_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM claim_lifecycle_events
                WHERE claim_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (claim_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM claim_lifecycle_events
                WHERE claim_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params = (claim_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return dict(row)

    def conflict_status_at(
        self,
        conflict_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> str | None:
        row = self.conflict_lifecycle_event_at(
            conflict_id,
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )
        if row is None:
            return None
        return str(row["new_status"])

    def conflict_lifecycle_event_at(
        self,
        conflict_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM conflict_lifecycle_events
                WHERE conflict_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (conflict_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM conflict_lifecycle_events
                WHERE conflict_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params = (conflict_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return dict(row)

    def current_conflict(self, conflict_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_conflicts WHERE conflict_id = ?", (conflict_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def conflict_lineage(self, conflict_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
        with self._connect() as conn:
            return {
                "conflict": tuple(
                    _rows(conn, "SELECT * FROM knowledge_conflicts WHERE conflict_id = ?", (conflict_id,))
                ),
                "claims": tuple(
                    _rows(
                        conn,
                        """
                        SELECT claims.* FROM knowledge_claims claims
                        JOIN conflict_claim_links links ON claims.claim_id = links.claim_id
                        WHERE links.conflict_id = ?
                        ORDER BY links.position, claims.claim_id
                        """,
                        (conflict_id,),
                    )
                ),
                "source_evidence": tuple(
                    _rows(
                        conn,
                        "SELECT * FROM conflict_source_evidence_links WHERE conflict_id = ? ORDER BY role, position",
                        (conflict_id,),
                    )
                ),
                "spans": tuple(
                    _rows(
                        conn,
                        """
                        SELECT spans.* FROM evidence_spans spans
                        JOIN conflict_evidence_span_links links ON spans.span_id = links.span_id
                        WHERE links.conflict_id = ?
                        ORDER BY links.role, links.position
                        """,
                        (conflict_id,),
                    )
                ),
                "conflict_events": tuple(
                    _rows(
                        conn,
                        """
                        SELECT * FROM conflict_lifecycle_events
                        WHERE conflict_id = ?
                        ORDER BY effective_at, recorded_at, event_id
                        """,
                        (conflict_id,),
                    )
                ),
            }

    def current_claim(self, claim_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_claims WHERE claim_id = ?", (claim_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def claims(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(_rows(conn, "SELECT * FROM knowledge_claims ORDER BY claim_id"))

    def conflicts(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(_rows(conn, "SELECT * FROM knowledge_conflicts ORDER BY conflict_id"))

    def relationship_projection(self, relationship_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_relationship_projections WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def relationship_projections(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    "SELECT * FROM knowledge_relationship_projections ORDER BY relationship_id",
                )
            )

    def provider_health_events(self, provider_name: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM ai_provider_health
                    WHERE provider_name = ?
                    ORDER BY checked_at, health_id
                    """,
                    (provider_name,),
                )
            )

    def provider_health(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM ai_provider_health
                    ORDER BY provider_name, checked_at, health_id
                    """,
                )
            )

    def extraction_proposals(self, document_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM extraction_proposals
                    WHERE document_id = ?
                    ORDER BY created_at, proposal_id
                    """,
                    (document_id,),
                )
            )

    def security_events(self, document_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            return tuple(
                _rows(
                    conn,
                    """
                    SELECT * FROM security_audit_events
                    WHERE document_id = ?
                    ORDER BY detected_at, event_id
                    """,
                    (document_id,),
                )
            )

    def count(self, table: str) -> int:
        _ensure_table(table, EVIDENCE_INTELLIGENCE_TABLES)
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
        _ensure_table(table, EVIDENCE_INTELLIGENCE_TABLES)
        with self._connect() as conn:
            _upsert_payload(conn, table, payload, key=key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def _document_lifecycle_event_at(
        self,
        document_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool,
    ) -> dict[str, Any] | None:
        cutoff_value = _serialize(cutoff)
        if strict_known_by_hunter:
            sql = """
                SELECT * FROM document_lifecycle_events
                WHERE document_id = ? AND effective_at <= ? AND recorded_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params: tuple[object, ...] = (document_id, cutoff_value, cutoff_value)
        else:
            sql = """
                SELECT * FROM document_lifecycle_events
                WHERE document_id = ? AND effective_at <= ?
                ORDER BY effective_at DESC, recorded_at DESC, event_id DESC
                LIMIT 1
            """
            params = (document_id, cutoff_value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return dict(row)


EVIDENCE_INTELLIGENCE_TABLES = frozenset(
    {
        "evidence_documents",
        "document_lifecycle_events",
        "document_lifecycle_event_span_links",
        "source_authority_verification_events",
        "evidence_document_versions",
        "evidence_spans",
        "predicate_registry",
        "predicate_subject_type_rules",
        "predicate_object_entity_type_rules",
        "predicate_literal_value_type_rules",
        "predicate_qualifier_rules",
        "predicate_modality_rules",
        "predicate_polarity_rules",
        "knowledge_entities",
        "knowledge_entity_identifier_links",
        "knowledge_entity_alias_links",
        "knowledge_entity_source_evidence_links",
        "knowledge_entity_span_links",
        "knowledge_claims",
        "claim_source_evidence_links",
        "claim_evidence_span_links",
        "claim_conflict_links",
        "claim_claim_links",
        "claim_lifecycle_events",
        "claim_lifecycle_event_span_links",
        "knowledge_relationship_projections",
        "knowledge_conflicts",
        "conflict_claim_links",
        "conflict_source_evidence_links",
        "conflict_evidence_span_links",
        "conflict_lifecycle_events",
        "conflict_lifecycle_event_span_links",
        "knowledge_versions",
        "evidence_processing_runs",
        "ai_provider_artifacts",
        "ai_provider_health",
        "extraction_schemas",
        "extraction_proposals",
        "knowledge_checkpoints",
        "security_audit_events",
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


def _replace_rule_rows(
    conn: sqlite3.Connection,
    table: str,
    predicate_id: str,
    schema_version: str,
    value_column: str,
    values: tuple[str, ...],
) -> None:
    conn.execute(f"DELETE FROM {table} WHERE predicate_id = ? AND schema_version = ?", (predicate_id, schema_version))
    for position, value in enumerate(values):
        conn.execute(
            f"""
            INSERT INTO {table} (predicate_id, schema_version, {value_column}, position)
            VALUES (?, ?, ?, ?)
            """,
            (predicate_id, schema_version, value, position),
        )


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in conn.execute(sql, params).fetchall())


def _payload(value: object) -> dict[str, Any]:
    payload = {field.name: getattr(value, field.name) for field in fields(value)}
    return {key: item for key, item in payload.items() if not isinstance(item, tuple)}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return json.dumps(dict(value), sort_keys=True)
    if isinstance(value, bool):
        return int(value)
    return value


def _ensure_table(table: str, allowed: frozenset[str] | set[str]) -> None:
    if table not in allowed:
        msg = f"unsupported evidence intelligence table: {table}"
        raise ValueError(msg)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence_documents (
    document_id TEXT PRIMARY KEY,
    source_evidence_id TEXT NOT NULL,
    raw_evidence_id TEXT NOT NULL,
    normalized_evidence_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    identity_resolution_status TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_claimed_authority TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    rendition_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    language TEXT NOT NULL,
    source_published_at TEXT,
    observed_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    processed_at TEXT,
    valid_from TEXT,
    valid_to TEXT,
    document_status TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    freshness REAL NOT NULL,
    confidence REAL NOT NULL,
    source_verified_authority TEXT NOT NULL,
    authority_verification_method TEXT NOT NULL,
    authority_verification_evidence_id TEXT NOT NULL,
    authority_verified_at TEXT,
    authority_status TEXT NOT NULL,
    superseded_at TEXT,
    retracted_at TEXT,
    metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES evidence_documents(document_id)
);
CREATE TABLE IF NOT EXISTS document_lifecycle_event_span_links (
    link_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(event_id, span_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS source_authority_verification_events (
    verification_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    authority_status TEXT NOT NULL,
    verification_method TEXT NOT NULL,
    authority_evidence_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    verifier_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES evidence_documents(document_id)
);
CREATE TABLE IF NOT EXISTS evidence_document_versions (
    version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    rendition_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_spans (
    span_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    rendition_id TEXT NOT NULL,
    offset_encoding TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    chunk_version TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    section_title TEXT NOT NULL,
    locator TEXT NOT NULL,
    span_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES evidence_documents(document_id)
);
CREATE TABLE IF NOT EXISTS predicate_registry (
    predicate_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    requires_object_entity INTEGER NOT NULL,
    allows_literal_value INTEGER NOT NULL,
    direction TEXT NOT NULL,
    inverse_predicate TEXT,
    symmetric INTEGER NOT NULL,
    asymmetric INTEGER NOT NULL,
    graph_projection_eligible INTEGER NOT NULL,
    predicate_specific_conflict_rules TEXT NOT NULL,
    scope_requirements TEXT NOT NULL,
    temporal_requirements TEXT NOT NULL,
    support_requirements TEXT NOT NULL,
    authority_requirements TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deprecated_at TEXT,
    replacement_predicate TEXT,
    PRIMARY KEY(predicate_id, schema_version)
);
CREATE TABLE IF NOT EXISTS predicate_subject_type_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, subject_type TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, subject_type));
CREATE TABLE IF NOT EXISTS predicate_object_entity_type_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, object_entity_type TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, object_entity_type));
CREATE TABLE IF NOT EXISTS predicate_literal_value_type_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, literal_value_type TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, literal_value_type));
CREATE TABLE IF NOT EXISTS predicate_qualifier_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, qualifier TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, qualifier));
CREATE TABLE IF NOT EXISTS predicate_modality_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, modality TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, modality));
CREATE TABLE IF NOT EXISTS predicate_polarity_rules (predicate_id TEXT NOT NULL, schema_version TEXT NOT NULL, polarity TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(predicate_id, schema_version, polarity));
CREATE TABLE IF NOT EXISTS knowledge_entities (
    entity_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    candidate_id TEXT,
    registry_identity_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_entity_identifier_links (
    link_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    value TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(entity_id, namespace, value, schema_version)
);
CREATE TABLE IF NOT EXISTS knowledge_entity_alias_links (
    link_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(entity_id, alias, alias_type, schema_version)
);
CREATE TABLE IF NOT EXISTS knowledge_entity_source_evidence_links (
    link_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(entity_id, source_evidence_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS knowledge_entity_span_links (
    link_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(entity_id, span_id, role, schema_version)
);
CREATE TABLE IF NOT EXISTS knowledge_claims (
    claim_id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL,
    subject_candidate_id TEXT,
    predicate_id TEXT NOT NULL,
    predicate_schema_version TEXT NOT NULL,
    object_entity_id TEXT,
    literal_value TEXT,
    literal_value_type TEXT,
    unit TEXT NOT NULL,
    scope TEXT NOT NULL,
    polarity TEXT NOT NULL,
    modality TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    support_level TEXT NOT NULL,
    confidence REAL NOT NULL,
    confidence_components TEXT NOT NULL,
    status TEXT NOT NULL,
    authority_status TEXT NOT NULL,
    processing_provider TEXT NOT NULL,
    processing_artifact_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_at TEXT,
    retracted_at TEXT
);
CREATE TABLE IF NOT EXISTS claim_source_evidence_links (link_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, source_evidence_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(claim_id, source_evidence_id, role, schema_version));
CREATE TABLE IF NOT EXISTS claim_evidence_span_links (link_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, span_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(claim_id, span_id, role, schema_version));
CREATE TABLE IF NOT EXISTS claim_conflict_links (link_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, conflict_id TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(claim_id, conflict_id, role, schema_version));
CREATE TABLE IF NOT EXISTS claim_claim_links (link_id TEXT PRIMARY KEY, source_claim_id TEXT NOT NULL, target_claim_id TEXT NOT NULL, relationship_type TEXT NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(source_claim_id, target_claim_id, relationship_type, schema_version));
CREATE TABLE IF NOT EXISTS claim_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES knowledge_claims(claim_id)
);
CREATE TABLE IF NOT EXISTS claim_lifecycle_event_span_links (link_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, span_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(event_id, span_id, role, schema_version));
CREATE TABLE IF NOT EXISTS knowledge_relationship_projections (
    relationship_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    predicate_id TEXT NOT NULL,
    object_entity_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    inverse_predicate_id TEXT,
    scope TEXT NOT NULL,
    polarity TEXT NOT NULL,
    modality TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES knowledge_claims(claim_id)
);
CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    conflict_id TEXT PRIMARY KEY,
    predicate_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    resolved_at TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conflict_claim_links (link_id TEXT PRIMARY KEY, conflict_id TEXT NOT NULL, claim_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(conflict_id, claim_id, role, schema_version));
CREATE TABLE IF NOT EXISTS conflict_source_evidence_links (link_id TEXT PRIMARY KEY, conflict_id TEXT NOT NULL, source_evidence_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(conflict_id, source_evidence_id, role, schema_version));
CREATE TABLE IF NOT EXISTS conflict_evidence_span_links (link_id TEXT PRIMARY KEY, conflict_id TEXT NOT NULL, span_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(conflict_id, span_id, role, schema_version));
CREATE TABLE IF NOT EXISTS conflict_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    conflict_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    processing_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY(conflict_id) REFERENCES knowledge_conflicts(conflict_id)
);
CREATE TABLE IF NOT EXISTS conflict_lifecycle_event_span_links (link_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, span_id TEXT NOT NULL, role TEXT NOT NULL, position INTEGER NOT NULL, created_at TEXT NOT NULL, schema_version TEXT NOT NULL, UNIQUE(event_id, span_id, role, schema_version));
CREATE TABLE IF NOT EXISTS knowledge_versions (version_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, record_type TEXT NOT NULL, schema_version TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_processing_runs (run_id TEXT PRIMARY KEY, run_type TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, schema_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_provider_artifacts (artifact_id TEXT PRIMARY KEY, processing_run_id TEXT NOT NULL, provider_name TEXT NOT NULL, provider_version TEXT NOT NULL, schema_version TEXT NOT NULL, prompt_version TEXT NOT NULL, content_hash TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_provider_health (
    health_id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    latency_ms INTEGER,
    failure_type TEXT NOT NULL,
    unavailable_reason TEXT NOT NULL,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_schemas (
    schema_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    output_contract TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deprecated_at TEXT,
    UNIQUE(name, schema_version)
);
CREATE TABLE IF NOT EXISTS extraction_proposals (
    proposal_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    status TEXT NOT NULL,
    proposed_payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    unavailable_reason TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    UNIQUE(artifact_id, document_id, schema_id, schema_version)
);
CREATE TABLE IF NOT EXISTS knowledge_checkpoints (checkpoint_id TEXT PRIMARY KEY, processor_name TEXT NOT NULL, target_id TEXT NOT NULL, cursor TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS security_audit_events (event_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, event_type TEXT NOT NULL, detected_at TEXT NOT NULL, severity TEXT NOT NULL, reason TEXT NOT NULL, schema_version TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS evidence_documents_candidate_processing_idx ON evidence_documents(candidate_id, processing_status);
CREATE INDEX IF NOT EXISTS evidence_documents_source_evidence_idx ON evidence_documents(source_evidence_id);
CREATE INDEX IF NOT EXISTS evidence_documents_point_in_time_idx ON evidence_documents(available_at, retrieved_at, processed_at);
CREATE INDEX IF NOT EXISTS evidence_documents_status_idx ON evidence_documents(document_status);
CREATE INDEX IF NOT EXISTS document_lifecycle_events_cutoff_idx ON document_lifecycle_events(document_id, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS document_lifecycle_events_type_idx ON document_lifecycle_events(event_type, effective_at);
CREATE INDEX IF NOT EXISTS document_lifecycle_events_status_idx ON document_lifecycle_events(new_status, effective_at);
CREATE INDEX IF NOT EXISTS source_authority_events_cutoff_idx ON source_authority_verification_events(document_id, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS source_authority_events_status_idx ON source_authority_verification_events(authority_status, effective_at);
CREATE INDEX IF NOT EXISTS source_authority_events_method_idx ON source_authority_verification_events(verification_method, effective_at);
CREATE INDEX IF NOT EXISTS source_authority_events_evidence_idx ON source_authority_verification_events(authority_evidence_id);
CREATE INDEX IF NOT EXISTS evidence_spans_document_chunk_idx ON evidence_spans(document_id, chunk_id);
CREATE INDEX IF NOT EXISTS evidence_spans_normalized_hash_idx ON evidence_spans(normalized_content_hash);
CREATE INDEX IF NOT EXISTS knowledge_entities_candidate_idx ON knowledge_entities(candidate_id);
CREATE INDEX IF NOT EXISTS knowledge_entities_type_name_idx ON knowledge_entities(entity_type, canonical_name);
CREATE INDEX IF NOT EXISTS knowledge_claims_subject_predicate_status_idx ON knowledge_claims(subject_entity_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS knowledge_claims_candidate_predicate_status_idx ON knowledge_claims(subject_candidate_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS knowledge_claims_object_predicate_status_idx ON knowledge_claims(object_entity_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS knowledge_claims_point_in_time_idx ON knowledge_claims(available_at, retrieved_at, processed_at);
CREATE INDEX IF NOT EXISTS knowledge_claims_validity_idx ON knowledge_claims(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS claim_source_evidence_links_claim_idx ON claim_source_evidence_links(claim_id);
CREATE INDEX IF NOT EXISTS claim_source_evidence_links_evidence_idx ON claim_source_evidence_links(source_evidence_id);
CREATE INDEX IF NOT EXISTS claim_evidence_span_links_claim_idx ON claim_evidence_span_links(claim_id);
CREATE INDEX IF NOT EXISTS claim_evidence_span_links_span_idx ON claim_evidence_span_links(span_id);
CREATE INDEX IF NOT EXISTS claim_conflict_links_claim_idx ON claim_conflict_links(claim_id);
CREATE INDEX IF NOT EXISTS claim_conflict_links_conflict_idx ON claim_conflict_links(conflict_id);
CREATE INDEX IF NOT EXISTS claim_claim_links_source_idx ON claim_claim_links(source_claim_id, relationship_type);
CREATE INDEX IF NOT EXISTS claim_claim_links_target_idx ON claim_claim_links(target_claim_id, relationship_type);
CREATE INDEX IF NOT EXISTS claim_lifecycle_events_cutoff_idx ON claim_lifecycle_events(claim_id, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS claim_lifecycle_events_type_idx ON claim_lifecycle_events(event_type, effective_at);
CREATE INDEX IF NOT EXISTS claim_lifecycle_events_status_idx ON claim_lifecycle_events(new_status, effective_at);
CREATE INDEX IF NOT EXISTS relationship_projections_claim_idx ON knowledge_relationship_projections(claim_id);
CREATE INDEX IF NOT EXISTS relationship_projections_subject_idx ON knowledge_relationship_projections(subject_entity_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS relationship_projections_object_idx ON knowledge_relationship_projections(object_entity_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS knowledge_conflicts_subject_idx ON knowledge_conflicts(subject_entity_id, predicate_id, status);
CREATE INDEX IF NOT EXISTS knowledge_conflicts_time_idx ON knowledge_conflicts(detected_at, effective_at, resolved_at);
CREATE INDEX IF NOT EXISTS conflict_claim_links_conflict_idx ON conflict_claim_links(conflict_id);
CREATE INDEX IF NOT EXISTS conflict_claim_links_claim_idx ON conflict_claim_links(claim_id);
CREATE INDEX IF NOT EXISTS conflict_source_evidence_links_conflict_idx ON conflict_source_evidence_links(conflict_id);
CREATE INDEX IF NOT EXISTS conflict_source_evidence_links_evidence_idx ON conflict_source_evidence_links(source_evidence_id);
CREATE INDEX IF NOT EXISTS conflict_evidence_span_links_conflict_idx ON conflict_evidence_span_links(conflict_id);
CREATE INDEX IF NOT EXISTS conflict_evidence_span_links_span_idx ON conflict_evidence_span_links(span_id);
CREATE INDEX IF NOT EXISTS conflict_lifecycle_events_cutoff_idx ON conflict_lifecycle_events(conflict_id, effective_at, recorded_at);
CREATE INDEX IF NOT EXISTS conflict_lifecycle_events_type_idx ON conflict_lifecycle_events(event_type, effective_at);
CREATE INDEX IF NOT EXISTS conflict_lifecycle_events_status_idx ON conflict_lifecycle_events(new_status, effective_at);
CREATE INDEX IF NOT EXISTS ai_provider_health_provider_time_idx ON ai_provider_health(provider_name, checked_at);
CREATE INDEX IF NOT EXISTS ai_provider_health_status_idx ON ai_provider_health(status, checked_at);
CREATE INDEX IF NOT EXISTS extraction_schemas_name_version_idx ON extraction_schemas(name, schema_version);
CREATE INDEX IF NOT EXISTS extraction_proposals_document_idx ON extraction_proposals(document_id, status);
CREATE INDEX IF NOT EXISTS extraction_proposals_artifact_idx ON extraction_proposals(artifact_id);
CREATE INDEX IF NOT EXISTS security_audit_events_document_idx ON security_audit_events(document_id, detected_at);
CREATE INDEX IF NOT EXISTS security_audit_events_type_idx ON security_audit_events(event_type, detected_at);
CREATE INDEX IF NOT EXISTS checkpoints_processor_target_idx ON knowledge_checkpoints(processor_name, target_id);
CREATE INDEX IF NOT EXISTS document_lifecycle_event_span_links_event_idx ON document_lifecycle_event_span_links(event_id);
CREATE INDEX IF NOT EXISTS document_lifecycle_event_span_links_span_idx ON document_lifecycle_event_span_links(span_id);
CREATE INDEX IF NOT EXISTS claim_lifecycle_event_span_links_event_idx ON claim_lifecycle_event_span_links(event_id);
CREATE INDEX IF NOT EXISTS claim_lifecycle_event_span_links_span_idx ON claim_lifecycle_event_span_links(span_id);
CREATE INDEX IF NOT EXISTS conflict_lifecycle_event_span_links_event_idx ON conflict_lifecycle_event_span_links(event_id);
CREATE INDEX IF NOT EXISTS conflict_lifecycle_event_span_links_span_idx ON conflict_lifecycle_event_span_links(span_id);
"""
