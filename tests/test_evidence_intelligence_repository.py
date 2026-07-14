from __future__ import annotations

from datetime import UTC, datetime

from hunter.evidence_intelligence import (
    ClaimConflictLink,
    ClaimLifecycleEvent,
    ConflictLifecycleEvent,
    DocumentLifecycleEvent,
    EvidenceDocument,
    EvidenceIntelligenceRepository,
    EvidenceSpan,
    EvidenceSpanLink,
    KnowledgeClaim,
    KnowledgeConflict,
    KnowledgeEntity,
    KnowledgeRelationship,
    PredicateDefinition,
    SourceEvidenceLink,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_two_schema_creates_authoritative_tables_and_indexes(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")

    tables = set(repository.table_names())
    assert {
        "evidence_documents",
        "document_lifecycle_events",
        "document_lifecycle_event_span_links",
        "source_authority_verification_events",
        "evidence_spans",
        "predicate_registry",
        "knowledge_entities",
        "knowledge_claims",
        "claim_source_evidence_links",
        "claim_evidence_span_links",
        "claim_conflict_links",
        "claim_claim_links",
        "claim_lifecycle_events",
        "knowledge_relationship_projections",
        "knowledge_conflicts",
        "conflict_claim_links",
        "conflict_source_evidence_links",
        "conflict_evidence_span_links",
        "conflict_lifecycle_events",
        "knowledge_versions",
        "evidence_processing_runs",
        "ai_provider_artifacts",
        "ai_provider_health",
        "extraction_schemas",
        "extraction_proposals",
        "knowledge_checkpoints",
        "security_audit_events",
    }.issubset(tables)

    indexes = set(repository.index_names())
    assert {
        "knowledge_claims_subject_predicate_status_idx",
        "claim_source_evidence_links_claim_idx",
        "claim_evidence_span_links_span_idx",
        "claim_lifecycle_events_cutoff_idx",
        "source_authority_events_cutoff_idx",
        "document_lifecycle_events_cutoff_idx",
        "conflict_lifecycle_events_cutoff_idx",
        "relationship_projections_claim_idx",
        "ai_provider_health_provider_time_idx",
        "extraction_proposals_document_idx",
        "security_audit_events_document_idx",
    }.issubset(indexes)


def test_phase_two_writes_are_idempotent(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persist_minimal_claim_graph(repository)
    persist_minimal_claim_graph(repository)

    assert repository.count("evidence_documents") == 1
    assert repository.count("evidence_spans") == 1
    assert repository.count("predicate_registry") == 1
    assert repository.count("knowledge_entities") == 1
    assert repository.count("knowledge_claims") == 1
    assert repository.count("claim_lifecycle_events") == 1
    assert repository.count("knowledge_conflicts") == 1
    assert repository.count("conflict_lifecycle_events") == 1
    assert repository.count("knowledge_relationship_projections") == 1
    assert repository.count("claim_source_evidence_links") == 1
    assert repository.count("claim_evidence_span_links") == 1
    assert repository.count("claim_conflict_links") == 1


def test_phase_two_reconstructs_claim_lineage_from_normalized_tables(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persist_minimal_claim_graph(repository)

    lineage = repository.claim_lineage("claim-1")

    assert lineage["claim"][0]["claim_id"] == "claim-1"
    assert lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert lineage["spans"][0]["span_id"] == "span-1"
    assert lineage["documents"][0]["document_id"] == "document-1"
    assert lineage["claim_events"][0]["event_id"] == "claim-event-1"
    assert lineage["conflicts"][0]["conflict_id"] == "conflict-1"


def test_phase_two_current_state_fields_are_materialization_targets_not_linkage(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persist_minimal_claim_graph(repository)

    lineage = repository.claim_lineage("claim-1")

    assert "source_evidence_ids" not in lineage["claim"][0]
    assert "supporting_span_ids" not in lineage["claim"][0]
    assert "conflicting_claim_ids" not in lineage["claim"][0]
    assert lineage["claim_events"][0]["new_status"] == "active"


def persist_minimal_claim_graph(repository: EvidenceIntelligenceRepository) -> None:
    repository.save_document(evidence_document())
    repository.save_span(evidence_span())
    repository.save_document_lifecycle_event(document_event())
    repository.save_predicate(predicate_definition())
    repository.save_entity(knowledge_entity())
    repository.save_claim(knowledge_claim())
    repository.save_claim_lifecycle_event(claim_event())
    repository.save_conflict(knowledge_conflict())
    repository.save_conflict_lifecycle_event(conflict_event())
    repository.save_relationship(relationship_projection())
    repository.save_source_evidence_links(
        "claim_source_evidence_links",
        (
            SourceEvidenceLink(
                link_id="claim-source-link-1",
                owner_id="claim-1",
                source_evidence_id="source-evidence-1",
                role="supporting",
                position=0,
                created_at=NOW,
                schema_version="link-v1",
            ),
        ),
    )
    repository.save_span_links(
        "claim_evidence_span_links",
        (
            EvidenceSpanLink(
                link_id="claim-span-link-1",
                owner_id="claim-1",
                span_id="span-1",
                role="supporting",
                position=0,
                created_at=NOW,
                schema_version="link-v1",
            ),
        ),
    )
    repository.save_claim_conflict_links(
        (
            ClaimConflictLink(
                link_id="claim-conflict-link-1",
                claim_id="claim-1",
                conflict_id="conflict-1",
                role="participant",
                created_at=NOW,
                schema_version="link-v1",
            ),
        )
    )


def evidence_document() -> EvidenceDocument:
    return EvidenceDocument(
        document_id="document-1",
        source_evidence_id="source-evidence-1",
        raw_evidence_id="raw-1",
        normalized_evidence_id="normalized-1",
        candidate_id="candidate-1",
        identity_resolution_status="exact",
        source_url="https://example.test/doc",
        source_provider="official_docs",
        source_type="technical_documentation",
        source_claimed_authority="official",
        title="Protocol docs",
        content_hash="content-hash",
        normalized_content_hash="normalized-hash",
        normalization_version="normalization-v1",
        parser_id="markdown-parser",
        rendition_id="rendition-1",
        content_type="text/markdown",
        language="en",
        source_published_at=NOW,
        observed_at=NOW,
        retrieved_at=NOW,
        available_at=NOW,
        processed_at=NOW,
        valid_from=NOW,
        valid_to=None,
        document_status="active",
        processing_status="processed",
        freshness=1.0,
        confidence=0.95,
    )


def evidence_span() -> EvidenceSpan:
    return EvidenceSpan(
        span_id="span-1",
        document_id="document-1",
        source_evidence_id="source-evidence-1",
        normalized_content_hash="normalized-hash",
        normalization_version="normalization-v1",
        parser_id="markdown-parser",
        rendition_id="rendition-1",
        offset_encoding="unicode_codepoint",
        start_offset=0,
        end_offset=12,
        chunk_id="chunk-1",
        chunk_version="chunk-v1",
        text_hash="text-hash",
        excerpt="runs on eth",
        section_title="Deployments",
        locator="section:deployments",
        span_status="active",
        created_at=NOW,
        validated_at=NOW,
    )


def document_event() -> DocumentLifecycleEvent:
    return DocumentLifecycleEvent(
        event_id="document-event-1",
        document_id="document-1",
        event_type="accepted",
        effective_at=NOW,
        recorded_at=NOW,
        source_evidence_id="source-evidence-1",
        reason="accepted by deterministic validation",
        previous_status=None,
        new_status="active",
        processing_run_id="run-1",
        schema_version="document-event-v1",
    )


def predicate_definition() -> PredicateDefinition:
    return PredicateDefinition(
        predicate_id="runs_on",
        name="Runs on",
        description="Protocol or application runs on a chain or network.",
        schema_version="predicate-v1",
        permitted_subject_types=("protocol", "project"),
        permitted_object_entity_types=("chain",),
        requires_object_entity=True,
        allows_literal_value=False,
        direction="subject_to_object",
        inverse_predicate="hosts",
        symmetric=False,
        asymmetric=True,
        valid_modalities=("asserted", "historical"),
        valid_polarities=("positive",),
        graph_projection_eligible=True,
        created_at=NOW,
    )


def knowledge_entity() -> KnowledgeEntity:
    return KnowledgeEntity(
        entity_id="entity-1",
        canonical_name="Aave",
        entity_type="protocol",
        candidate_id="candidate-1",
        registry_identity_status="exact",
        confidence=0.95,
        status="active",
        first_seen_at=NOW,
        last_seen_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def knowledge_claim() -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id="claim-1",
        subject_entity_id="entity-1",
        subject_candidate_id="candidate-1",
        predicate_id="runs_on",
        predicate_schema_version="predicate-v1",
        object_entity_id="entity-chain-1",
        literal_value=None,
        literal_value_type=None,
        unit="",
        scope="mainnet",
        polarity="positive",
        modality="asserted",
        valid_from=NOW,
        valid_to=None,
        observed_at=NOW,
        available_at=NOW,
        retrieved_at=NOW,
        processed_at=NOW,
        support_level="semantic_support",
        confidence=0.9,
        confidence_components={"source_authority": 0.9},
        status="active",
        authority_status="verified_official",
        processing_provider="deterministic-test",
        processing_artifact_id="artifact-1",
        schema_version="claim-v1",
        created_at=NOW,
    )


def claim_event() -> ClaimLifecycleEvent:
    return ClaimLifecycleEvent(
        event_id="claim-event-1",
        claim_id="claim-1",
        event_type="accepted",
        effective_at=NOW,
        recorded_at=NOW,
        source_evidence_id="source-evidence-1",
        reason="accepted by deterministic validation",
        previous_status=None,
        new_status="active",
        processing_run_id="run-1",
        schema_version="claim-event-v1",
    )


def knowledge_conflict() -> KnowledgeConflict:
    return KnowledgeConflict(
        conflict_id="conflict-1",
        predicate_id="charges_fee",
        subject_entity_id="entity-1",
        scope="ethereum",
        detected_at=NOW,
        effective_at=NOW,
        resolved_at=NOW,
        status="resolved",
        reason="official correction resolved conflict",
        schema_version="conflict-v1",
    )


def conflict_event() -> ConflictLifecycleEvent:
    return ConflictLifecycleEvent(
        event_id="conflict-event-1",
        conflict_id="conflict-1",
        event_type="resolved",
        effective_at=NOW,
        recorded_at=NOW,
        source_evidence_id="source-evidence-1",
        reason="accepted by deterministic validation",
        previous_status=None,
        new_status="resolved",
        processing_run_id="run-1",
        schema_version="conflict-event-v1",
    )


def relationship_projection() -> KnowledgeRelationship:
    return KnowledgeRelationship(
        relationship_id="relationship-1",
        claim_id="claim-1",
        subject_entity_id="entity-1",
        predicate_id="runs_on",
        object_entity_id="entity-chain-1",
        direction="subject_to_object",
        inverse_predicate_id="hosts",
        scope="mainnet",
        polarity="positive",
        modality="asserted",
        valid_from=NOW,
        valid_to=None,
        confidence=0.9,
        status="active",
        projection_version="projection-v1",
        created_at=NOW,
    )
