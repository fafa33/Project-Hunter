from __future__ import annotations

from datetime import UTC, datetime

from hunter.evidence_intelligence import (
    ClaimLifecycleEvent,
    ClaimPersistenceInput,
    ClaimPersistenceService,
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceRepository,
    ExtractionValidationService,
    PredicateDefinition,
    PredicateRegistry,
)

EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2026, 1, 15, tzinfo=UTC)
AFTER_RECORDED = datetime(2026, 2, 2, tzinfo=UTC)
SUPERSEDED_AT = datetime(2026, 3, 1, tzinfo=UTC)


def test_phase_six_persists_claim_lifecycle_and_lineage_atomically(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = ClaimPersistenceService(repository).persist(claim_input(repository))

    assert persisted.claim.status == "active"
    assert persisted.lifecycle_event.new_status == "active"
    assert repository.count("knowledge_claims") == 1
    assert repository.count("claim_lifecycle_events") == 1
    assert repository.count("claim_source_evidence_links") == 1
    assert repository.count("claim_evidence_span_links") == 1

    lineage = repository.claim_lineage(persisted.claim.claim_id)
    assert lineage["claim"][0]["claim_id"] == persisted.claim.claim_id
    assert lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert lineage["spans"][0]["span_id"] == persisted.span_links[0].span_id
    assert lineage["claim_events"][0]["event_type"] == "accepted"


def test_phase_six_claim_persistence_is_idempotent(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = ClaimPersistenceService(repository)
    item = claim_input(repository)

    first = service.persist(item)
    second = service.persist(item)

    assert second.claim.claim_id == first.claim.claim_id
    assert repository.count("knowledge_claims") == 1
    assert repository.count("claim_lifecycle_events") == 1
    assert repository.count("claim_source_evidence_links") == 1
    assert repository.count("claim_evidence_span_links") == 1


def test_phase_six_claim_status_reconstructs_historical_cutoffs(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = ClaimPersistenceService(repository).persist(claim_input(repository))

    assert repository.claim_status_at(persisted.claim.claim_id, BEFORE_RECORDED) == "active"
    assert repository.claim_status_at(persisted.claim.claim_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None
    assert repository.claim_status_at(persisted.claim.claim_id, AFTER_RECORDED, strict_known_by_hunter=True) == "active"


def test_phase_six_lifecycle_append_updates_only_current_projection(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = ClaimPersistenceService(repository)
    persisted = service.persist(claim_input(repository))

    service.append_lifecycle_event(
        ClaimLifecycleEvent(
            event_id="claim-event-superseded",
            claim_id=persisted.claim.claim_id,
            event_type="superseded",
            effective_at=SUPERSEDED_AT,
            recorded_at=SUPERSEDED_AT,
            source_evidence_id="source-evidence-1",
            reason="superseded by later validated evidence",
            previous_status="active",
            new_status="superseded",
            processing_run_id="run-2",
            schema_version="claim-lifecycle-event-v1",
        ),
        confidence=0.42,
    )

    current = repository.current_claim(persisted.claim.claim_id)
    assert current is not None
    assert current["status"] == "superseded"
    assert current["confidence"] == 0.42
    assert current["superseded_at"] == SUPERSEDED_AT.isoformat()
    assert repository.claim_status_at(persisted.claim.claim_id, AFTER_RECORDED, strict_known_by_hunter=True) == "active"
    assert (
        repository.claim_status_at(persisted.claim.claim_id, SUPERSEDED_AT, strict_known_by_hunter=True) == "superseded"
    )
    assert len(repository.claim_lineage(persisted.claim.claim_id)["claim_events"]) == 2


def test_phase_six_confidence_projection_is_deterministic_and_componentized(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = ClaimPersistenceService(repository)
    first = service.persist(claim_input(repository))
    second = service.persist(claim_input(repository))

    assert first.claim.confidence == second.claim.confidence
    assert dict(first.claim.confidence_components) == {
        "support": 0.7,
        "authority": 1.0,
        "lineage": 0.7,
    }
    assert first.claim.confidence == 0.805


def claim_input(repository: EvidenceIntelligenceRepository) -> ClaimPersistenceInput:
    intake = EvidenceIntelligenceIntakeService(repository).ingest(
        EvidenceIntakeReference(
            source_evidence_id="source-evidence-1",
            raw_evidence_id="raw-evidence-1",
            normalized_evidence_id="normalized-evidence-1",
            candidate_id="candidate-1",
            identity_resolution_status="exact",
            source_url="https://example.test/docs",
            source_provider="existing_hunter_evidence",
            source_type="official_documentation",
            source_claimed_authority="official",
            title="Protocol docs",
            content="Aave runs on Ethereum.",
            observed_at=EFFECTIVE_AT,
            retrieved_at=EFFECTIVE_AT,
            available_at=EFFECTIVE_AT,
        ),
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        verifier_type="deterministic_system",
    )
    proposal = (
        ExtractionValidationService()
        .validate(
            document_id=intake.document.document_id,
            spans=intake.spans,
            payload={
                "claims": [
                    {
                        "predicate_id": "runs_on",
                        "subject_name": "Aave",
                        "subject_type": "protocol",
                        "object_name": "Ethereum",
                        "object_type": "chain",
                        "span_id": intake.spans[0].span_id,
                        "support_level": "semantic_support",
                        "support_text": "Aave runs on Ethereum",
                        "explicit_support": True,
                    }
                ]
            },
            predicate_registry=predicate_registry(),
        )
        .claims[0]
    )
    return ClaimPersistenceInput(
        proposal=proposal,
        subject_entity_id="entity-aave",
        object_entity_id="entity-ethereum",
        subject_candidate_id="candidate-1",
        predicate_schema_version="predicate-v1",
        source_evidence_ids=("source-evidence-1",),
        spans=intake.spans,
        authority_status="verified_official",
        processing_provider="validated-extraction",
        processing_artifact_id="artifact-1",
        observed_at=EFFECTIVE_AT,
        available_at=EFFECTIVE_AT,
        retrieved_at=EFFECTIVE_AT,
        processed_at=RECORDED_AT,
        effective_at=EFFECTIVE_AT,
        recorded_at=RECORDED_AT,
        processing_run_id="run-1",
    )


def predicate_registry() -> PredicateRegistry:
    return PredicateRegistry(
        schema_version="predicate-v1",
        predicates=(
            PredicateDefinition(
                predicate_id="runs_on",
                name="runs on",
                description="protocol runs on chain",
                schema_version="predicate-v1",
                permitted_subject_types=("protocol",),
                permitted_object_entity_types=("chain",),
                requires_object_entity=True,
                graph_projection_eligible=True,
            ),
        ),
    )
