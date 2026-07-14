from __future__ import annotations

from datetime import UTC, datetime

from hunter.evidence_intelligence import (
    ClaimPersistenceInput,
    ClaimPersistenceService,
    ConflictPersistenceService,
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceRepository,
    ExtractionValidationService,
    PredicateAwareConflictDetector,
    PredicateDefinition,
    PredicateRegistry,
    RelationshipProjectionService,
)

EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2026, 1, 15, tzinfo=UTC)
AFTER_RECORDED = datetime(2026, 2, 2, tzinfo=UTC)


def test_phase_eight_projects_only_eligible_entity_to_entity_claims(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    service = RelationshipProjectionService(repository)

    projections = service.refresh((persisted.claim,), graph_registry(), created_at=RECORDED_AT)

    assert len(projections) == 1
    assert projections[0].claim_id == persisted.claim.claim_id
    assert projections[0].confidence == persisted.claim.confidence
    assert projections[0].status == persisted.claim.status
    assert repository.count("knowledge_relationship_projections") == 1


def test_phase_eight_literal_or_non_graph_predicates_do_not_project(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    non_graph_registry = PredicateRegistry(
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
                graph_projection_eligible=False,
            ),
        ),
    )

    projections = RelationshipProjectionService(repository).refresh(
        (persisted.claim,),
        non_graph_registry,
        created_at=RECORDED_AT,
    )

    assert projections == ()
    assert repository.count("knowledge_relationship_projections") == 0


def test_phase_eight_rebuild_replaces_existing_projection_set(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon")
    service = RelationshipProjectionService(repository)

    service.refresh((ethereum.claim,), graph_registry(), created_at=RECORDED_AT)
    rebuilt = service.rebuild((polygon.claim,), graph_registry(), created_at=RECORDED_AT)

    projections = repository.relationship_projections()
    assert len(rebuilt) == 1
    assert len(projections) == 1
    assert projections[0]["claim_id"] == polygon.claim.claim_id


def test_phase_eight_historical_relationship_view_uses_claim_document_and_authority_events(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    projection = RelationshipProjectionService(repository).refresh(
        (persisted.claim,),
        graph_registry(),
        created_at=RECORDED_AT,
    )[0]

    service = RelationshipProjectionService(repository)
    assert service.view_at(projection.relationship_id, BEFORE_RECORDED) is not None
    assert service.view_at(projection.relationship_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None
    strict_view = service.view_at(projection.relationship_id, AFTER_RECORDED, strict_known_by_hunter=True)

    assert strict_view is not None
    assert strict_view.status == "active"
    assert strict_view.confidence == persisted.claim.confidence
    assert strict_view.document_statuses == ("active",)
    assert strict_view.authority_statuses == ("verified_official",)


def test_phase_eight_relationship_view_derives_disputed_status_from_conflict_events(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon")
    registry = graph_registry(conflict_rule="exclusive_object")
    candidate = PredicateAwareConflictDetector(repository).detect((ethereum.claim, polygon.claim), registry)[0]
    ConflictPersistenceService(repository).persist(
        candidate,
        effective_at=EFFECTIVE_AT,
        recorded_at=RECORDED_AT,
        processing_run_id="run-conflict",
    )
    projection = RelationshipProjectionService(repository).refresh(
        (ethereum.claim,),
        registry,
        created_at=RECORDED_AT,
    )[0]

    view = RelationshipProjectionService(repository).view_at(
        projection.relationship_id,
        AFTER_RECORDED,
        strict_known_by_hunter=True,
    )

    assert view is not None
    assert view.status == "disputed"
    assert view.conflict_statuses == ("detected",)


def test_phase_eight_relationship_projection_has_no_independent_lifecycle_conflict_or_truth_fields(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum")
    projection = RelationshipProjectionService(repository).refresh(
        (persisted.claim,),
        graph_registry(),
        created_at=RECORDED_AT,
    )[0]

    row = repository.relationship_projection(projection.relationship_id)
    assert row is not None
    assert "claim_id" in row
    assert "event_type" not in row
    assert "conflict_id" not in row
    assert "source_evidence_id" not in row


def persist_runs_on_claim(
    repository: EvidenceIntelligenceRepository,
    chain_name: str,
    object_entity_id: str,
):
    intake = EvidenceIntelligenceIntakeService(repository).ingest(
        EvidenceIntakeReference(
            source_evidence_id=f"source-evidence-{chain_name}",
            raw_evidence_id=f"raw-evidence-{chain_name}",
            normalized_evidence_id=f"normalized-evidence-{chain_name}",
            candidate_id="candidate-1",
            identity_resolution_status="exact",
            source_url=f"https://example.test/docs/{chain_name}",
            source_provider="existing_hunter_evidence",
            source_type="official_documentation",
            source_claimed_authority="official",
            title="Protocol docs",
            content=f"Aave runs on {chain_name}.",
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
                        "object_name": chain_name,
                        "object_type": "chain",
                        "span_id": intake.spans[0].span_id,
                        "support_level": "semantic_support",
                        "support_text": f"Aave runs on {chain_name}",
                        "explicit_support": True,
                    }
                ]
            },
            predicate_registry=graph_registry(),
        )
        .claims[0]
    )
    return ClaimPersistenceService(repository).persist(
        ClaimPersistenceInput(
            proposal=proposal,
            subject_entity_id="entity-aave",
            object_entity_id=object_entity_id,
            subject_candidate_id="candidate-1",
            predicate_schema_version="predicate-v1",
            source_evidence_ids=(f"source-evidence-{chain_name}",),
            spans=intake.spans,
            authority_status="verified_official",
            processing_provider="validated-extraction",
            processing_artifact_id=f"artifact-{chain_name}",
            observed_at=EFFECTIVE_AT,
            available_at=EFFECTIVE_AT,
            retrieved_at=EFFECTIVE_AT,
            processed_at=RECORDED_AT,
            effective_at=EFFECTIVE_AT,
            recorded_at=RECORDED_AT,
            processing_run_id="run-1",
        )
    )


def graph_registry(*, conflict_rule: str = "") -> PredicateRegistry:
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
                predicate_specific_conflict_rules=conflict_rule,
            ),
        ),
    )
