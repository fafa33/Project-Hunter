from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from hunter.evidence_intelligence import (
    CLAIM_EVENT_TYPES,
    CLAIM_STATUSES,
    CONFLICT_EVENT_TYPES,
    CONFLICT_STATUSES,
    ClaimLifecycleEvent,
    ConflictLifecycleEvent,
    DocumentLifecycleEvent,
    EvidenceDocument,
    EvidenceSpan,
    KnowledgeClaim,
    KnowledgeConflict,
    KnowledgeEntity,
    KnowledgeRelationship,
    PredicateDefinition,
    PredicateRegistry,
    SourceAuthorityVerificationEvent,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_one_models_validate_required_fields() -> None:
    assert evidence_document().document_status == "active"
    assert evidence_span().span_status == "active"
    assert document_event().new_status == "active"
    assert authority_event().authority_status == "verified_official"
    assert knowledge_entity().entity_type == "protocol"
    assert predicate_definition().schema_version == "predicate-v1"
    assert predicate_registry().get("runs_on").graph_projection_eligible is True
    assert knowledge_claim().status == "active"
    assert claim_event().new_status == "active"
    assert knowledge_conflict().status == "resolved"
    assert conflict_event().new_status == "resolved"
    assert relationship_projection().claim_id == "claim-1"


def test_required_text_fields_reject_blank_values() -> None:
    with pytest.raises(ValueError, match="document_id is required"):
        evidence_document(document_id="")
    with pytest.raises(ValueError, match="span_id is required"):
        evidence_span(span_id="")
    with pytest.raises(ValueError, match="entity_id is required"):
        knowledge_entity(entity_id="")
    with pytest.raises(ValueError, match="claim_id is required"):
        knowledge_claim(claim_id="")


def test_lifecycle_events_require_timezone_aware_cutoff_fields() -> None:
    naive = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match="effective_at must be timezone-aware"):
        claim_event(effective_at=naive)
    with pytest.raises(ValueError, match="recorded_at must be timezone-aware"):
        conflict_event(recorded_at=naive)


def test_claim_statuses_exclude_resolved() -> None:
    assert "resolved" not in CLAIM_STATUSES
    assert "resolved" not in CLAIM_EVENT_TYPES
    with pytest.raises(ValueError, match="status must be one of"):
        knowledge_claim(status="resolved")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event_type must be one of"):
        claim_event(event_type="resolved")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="new_status must be one of"):
        claim_event(new_status="resolved")  # type: ignore[arg-type]


def test_conflict_statuses_include_resolved() -> None:
    assert "resolved" in CONFLICT_STATUSES
    assert "resolved" in CONFLICT_EVENT_TYPES
    assert knowledge_conflict(status="resolved").status == "resolved"
    assert conflict_event(event_type="resolved", new_status="resolved").new_status == "resolved"


def test_relationship_projection_requires_claim_id() -> None:
    with pytest.raises(ValueError, match="claim_id is required"):
        relationship_projection(claim_id="")


def test_predicate_definitions_are_versioned_and_registry_rejects_duplicates() -> None:
    definition = predicate_definition()
    assert definition.schema_version == "predicate-v1"
    with pytest.raises(ValueError, match="duplicate predicate definition"):
        PredicateRegistry(schema_version="predicate-v1", predicates=(definition, definition))
    with pytest.raises(ValueError, match="must match registry schema_version"):
        PredicateRegistry(
            schema_version="predicate-v2",
            predicates=(definition,),
        )


def test_predicate_registry_validates_claim_shape() -> None:
    registry = predicate_registry()
    registry.validate_claim_shape(
        predicate_id="runs_on",
        subject_type="protocol",
        object_type="chain",
        literal_value_type=None,
        modality="asserted",
        polarity="positive",
    )
    with pytest.raises(ValueError, match="object_type must be one of"):
        registry.validate_claim_shape(
            predicate_id="runs_on",
            subject_type="protocol",
            object_type="repository",
            literal_value_type=None,
            modality="asserted",
            polarity="positive",
        )
    with pytest.raises(KeyError, match="unsupported predicate"):
        registry.validate_claim_shape(
            predicate_id="unknown_predicate",
            subject_type="protocol",
            object_type="chain",
            literal_value_type=None,
            modality="asserted",
            polarity="positive",
        )


def test_models_do_not_embed_sql_authoritative_lineage_lists() -> None:
    forbidden = {
        "source_evidence_ids",
        "supporting_span_ids",
        "conflicting_claim_ids",
        "supersedes_claim_ids",
        "superseded_by_claim_id",
        "correction_of_claim_id",
        "retraction_of_claim_id",
        "claim_ids",
    }
    for model in (
        KnowledgeClaim,
        KnowledgeConflict,
        ClaimLifecycleEvent,
        ConflictLifecycleEvent,
        DocumentLifecycleEvent,
        SourceAuthorityVerificationEvent,
    ):
        assert forbidden.isdisjoint({field.name for field in fields(model)})


def evidence_document(**overrides: object) -> EvidenceDocument:
    values = {
        "document_id": "document-1",
        "source_evidence_id": "source-evidence-1",
        "raw_evidence_id": "raw-1",
        "normalized_evidence_id": "normalized-1",
        "candidate_id": "candidate-1",
        "identity_resolution_status": "exact",
        "source_url": "https://example.test/doc",
        "source_provider": "official_docs",
        "source_type": "technical_documentation",
        "source_claimed_authority": "official",
        "title": "Protocol docs",
        "content_hash": "content-hash",
        "normalized_content_hash": "normalized-hash",
        "normalization_version": "normalization-v1",
        "parser_id": "markdown-parser",
        "rendition_id": "rendition-1",
        "content_type": "text/markdown",
        "language": "en",
        "source_published_at": NOW,
        "observed_at": NOW,
        "retrieved_at": NOW,
        "available_at": NOW,
        "processed_at": NOW,
        "valid_from": NOW,
        "valid_to": None,
        "document_status": "active",
        "processing_status": "processed",
        "freshness": 1.0,
        "confidence": 0.95,
    }
    values.update(overrides)
    return EvidenceDocument(**values)  # type: ignore[arg-type]


def evidence_span(**overrides: object) -> EvidenceSpan:
    values = {
        "span_id": "span-1",
        "document_id": "document-1",
        "source_evidence_id": "source-evidence-1",
        "normalized_content_hash": "normalized-hash",
        "normalization_version": "normalization-v1",
        "parser_id": "markdown-parser",
        "rendition_id": "rendition-1",
        "offset_encoding": "unicode_codepoint",
        "start_offset": 0,
        "end_offset": 12,
        "chunk_id": "chunk-1",
        "chunk_version": "chunk-v1",
        "text_hash": "text-hash",
        "excerpt": "runs on eth",
        "section_title": "Deployments",
        "locator": "section:deployments",
        "span_status": "active",
        "created_at": NOW,
        "validated_at": NOW,
    }
    values.update(overrides)
    return EvidenceSpan(**values)  # type: ignore[arg-type]


def document_event(**overrides: object) -> DocumentLifecycleEvent:
    values = lifecycle_values("document")
    values.update({"document_id": "document-1", "event_type": "accepted", "new_status": "active"})
    values.update(overrides)
    return DocumentLifecycleEvent(**values)  # type: ignore[arg-type]


def authority_event(**overrides: object) -> SourceAuthorityVerificationEvent:
    values = {
        "verification_id": "authority-1",
        "document_id": "document-1",
        "authority_status": "verified_official",
        "verification_method": "identity_trust_layer",
        "authority_evidence_id": "source-evidence-1",
        "effective_at": NOW,
        "recorded_at": NOW,
        "verifier_type": "identity_trust_layer",
        "reason": "official domain verified by identity layer",
        "processing_run_id": "run-1",
        "schema_version": "authority-v1",
    }
    values.update(overrides)
    return SourceAuthorityVerificationEvent(**values)  # type: ignore[arg-type]


def knowledge_entity(**overrides: object) -> KnowledgeEntity:
    values = {
        "entity_id": "entity-1",
        "canonical_name": "Aave",
        "entity_type": "protocol",
        "candidate_id": "candidate-1",
        "registry_identity_status": "exact",
        "confidence": 0.95,
        "status": "active",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return KnowledgeEntity(**values)  # type: ignore[arg-type]


def predicate_definition(**overrides: object) -> PredicateDefinition:
    values = {
        "predicate_id": "runs_on",
        "name": "Runs on",
        "description": "Protocol or application runs on a chain or network.",
        "schema_version": "predicate-v1",
        "permitted_subject_types": ("protocol", "project"),
        "permitted_object_entity_types": ("chain",),
        "requires_object_entity": True,
        "allows_literal_value": False,
        "direction": "subject_to_object",
        "inverse_predicate": "hosts",
        "symmetric": False,
        "asymmetric": True,
        "valid_modalities": ("asserted", "historical"),
        "valid_polarities": ("positive",),
        "graph_projection_eligible": True,
        "created_at": NOW,
    }
    values.update(overrides)
    return PredicateDefinition(**values)  # type: ignore[arg-type]


def predicate_registry() -> PredicateRegistry:
    return PredicateRegistry(schema_version="predicate-v1", predicates=(predicate_definition(),))


def knowledge_claim(**overrides: object) -> KnowledgeClaim:
    values = {
        "claim_id": "claim-1",
        "subject_entity_id": "entity-1",
        "subject_candidate_id": "candidate-1",
        "predicate_id": "runs_on",
        "predicate_schema_version": "predicate-v1",
        "object_entity_id": "entity-chain-1",
        "literal_value": None,
        "literal_value_type": None,
        "unit": "",
        "scope": "mainnet",
        "polarity": "positive",
        "modality": "asserted",
        "valid_from": NOW,
        "valid_to": None,
        "observed_at": NOW,
        "available_at": NOW,
        "retrieved_at": NOW,
        "processed_at": NOW,
        "support_level": "semantic_support",
        "confidence": 0.9,
        "confidence_components": {"source_authority": 0.9},
        "status": "active",
        "authority_status": "verified_official",
        "processing_provider": "deterministic-test",
        "processing_artifact_id": "artifact-1",
        "schema_version": "claim-v1",
        "created_at": NOW,
    }
    values.update(overrides)
    return KnowledgeClaim(**values)  # type: ignore[arg-type]


def claim_event(**overrides: object) -> ClaimLifecycleEvent:
    values = lifecycle_values("claim")
    values.update({"claim_id": "claim-1", "event_type": "accepted", "new_status": "active"})
    values.update(overrides)
    return ClaimLifecycleEvent(**values)  # type: ignore[arg-type]


def knowledge_conflict(**overrides: object) -> KnowledgeConflict:
    values = {
        "conflict_id": "conflict-1",
        "predicate_id": "charges_fee",
        "subject_entity_id": "entity-1",
        "scope": "ethereum",
        "detected_at": NOW,
        "effective_at": NOW,
        "resolved_at": NOW,
        "status": "resolved",
        "reason": "official correction resolved conflict",
        "schema_version": "conflict-v1",
    }
    values.update(overrides)
    return KnowledgeConflict(**values)  # type: ignore[arg-type]


def conflict_event(**overrides: object) -> ConflictLifecycleEvent:
    values = lifecycle_values("conflict")
    values.update({"conflict_id": "conflict-1", "event_type": "resolved", "new_status": "resolved"})
    values.update(overrides)
    return ConflictLifecycleEvent(**values)  # type: ignore[arg-type]


def relationship_projection(**overrides: object) -> KnowledgeRelationship:
    values = {
        "relationship_id": "relationship-1",
        "claim_id": "claim-1",
        "subject_entity_id": "entity-1",
        "predicate_id": "runs_on",
        "object_entity_id": "entity-chain-1",
        "direction": "subject_to_object",
        "inverse_predicate_id": "hosts",
        "scope": "mainnet",
        "polarity": "positive",
        "modality": "asserted",
        "valid_from": NOW,
        "valid_to": None,
        "confidence": 0.9,
        "status": "active",
        "projection_version": "projection-v1",
        "created_at": NOW,
    }
    values.update(overrides)
    return KnowledgeRelationship(**values)  # type: ignore[arg-type]


def lifecycle_values(kind: str) -> dict[str, object]:
    return {
        "event_id": f"{kind}-event-1",
        "effective_at": NOW,
        "recorded_at": NOW,
        "source_evidence_id": "source-evidence-1",
        "reason": "accepted by deterministic validation",
        "previous_status": None,
        "processing_run_id": "run-1",
        "schema_version": f"{kind}-event-v1",
    }
