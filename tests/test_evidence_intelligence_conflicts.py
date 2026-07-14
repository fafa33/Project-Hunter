from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from hunter.evidence_intelligence import (
    ClaimPersistenceInput,
    ClaimPersistenceService,
    ConflictLifecycleEvent,
    ConflictPersistenceService,
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceRepository,
    ExtractionValidationService,
    PredicateAwareConflictDetector,
    PredicateDefinition,
    PredicateRegistry,
)

EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2026, 1, 15, tzinfo=UTC)
AFTER_RECORDED = datetime(2026, 2, 2, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 3, 1, tzinfo=UTC)


def test_phase_seven_different_values_are_not_automatic_conflicts(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon", conflict_rule="")

    conflicts = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, polygon.claim),
        predicate_registry(conflict_rule=""),
    )

    assert conflicts == ()


def test_phase_seven_predicate_specific_exclusive_rule_detects_conflict(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="exclusive_object")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon", conflict_rule="exclusive_object")

    conflicts = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, polygon.claim),
        predicate_registry(conflict_rule="exclusive_object"),
    )

    assert len(conflicts) == 1
    assert conflicts[0].reason == "predicate-specific exclusive rule rejects simultaneous different values"


def test_phase_seven_detection_respects_scope_validity_authority_status_and_document_lifecycle(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="exclusive_object")
    scoped_polygon = persist_runs_on_claim(
        repository,
        "Polygon",
        "entity-polygon",
        conflict_rule="exclusive_object",
        scope="polygon-market",
    )
    untrusted_polygon = persist_runs_on_claim(
        repository,
        "Polygon",
        "entity-polygon-untrusted",
        conflict_rule="exclusive_object",
        authority_status="unavailable",
    )
    retracted_polygon = persist_runs_on_claim(
        repository,
        "Polygon",
        "entity-polygon-retracted",
        conflict_rule="exclusive_object",
        source_suffix="retracted",
    )
    retracted_document = replace(retracted_polygon.document, document_status="retracted")
    repository.save_document(retracted_document)

    conflicts = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, scoped_polygon.claim, untrusted_polygon.claim, retracted_polygon.claim),
        predicate_registry(conflict_rule="exclusive_object"),
    )

    assert conflicts == ()


def test_phase_seven_persists_conflict_lifecycle_and_lineage_atomically(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="exclusive_object")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon", conflict_rule="exclusive_object")
    candidate = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, polygon.claim),
        predicate_registry(conflict_rule="exclusive_object"),
    )[0]

    persisted = ConflictPersistenceService(repository).persist(
        candidate,
        effective_at=EFFECTIVE_AT,
        recorded_at=RECORDED_AT,
        processing_run_id="run-conflict",
    )

    assert persisted.conflict.status == "detected"
    assert persisted.lifecycle_event.new_status == "detected"
    assert repository.count("knowledge_conflicts") == 1
    assert repository.count("conflict_lifecycle_events") == 1
    assert repository.count("conflict_claim_links") == 2
    assert repository.count("conflict_source_evidence_links") == 2
    assert repository.count("conflict_evidence_span_links") == 2
    assert repository.count("claim_conflict_links") == 2

    lineage = repository.conflict_lineage(persisted.conflict.conflict_id)
    assert len(lineage["claims"]) == 2
    assert len(lineage["source_evidence"]) == 2
    assert len(lineage["spans"]) == 2
    assert lineage["conflict_events"][0]["event_type"] == "detected"


def test_phase_seven_conflict_status_reconstructs_strict_known_by_hunter_cutoffs(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="exclusive_object")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon", conflict_rule="exclusive_object")
    candidate = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, polygon.claim),
        predicate_registry(conflict_rule="exclusive_object"),
    )[0]
    persisted = ConflictPersistenceService(repository).persist(
        candidate,
        effective_at=EFFECTIVE_AT,
        recorded_at=RECORDED_AT,
        processing_run_id="run-conflict",
    )

    assert repository.conflict_status_at(persisted.conflict.conflict_id, BEFORE_RECORDED) == "detected"
    assert (
        repository.conflict_status_at(
            persisted.conflict.conflict_id,
            BEFORE_RECORDED,
            strict_known_by_hunter=True,
        )
        is None
    )
    assert (
        repository.conflict_status_at(
            persisted.conflict.conflict_id,
            AFTER_RECORDED,
            strict_known_by_hunter=True,
        )
        == "detected"
    )


def test_phase_seven_resolution_belongs_to_conflict_not_claim(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    ethereum = persist_runs_on_claim(repository, "Ethereum", "entity-ethereum", conflict_rule="exclusive_object")
    polygon = persist_runs_on_claim(repository, "Polygon", "entity-polygon", conflict_rule="exclusive_object")
    candidate = PredicateAwareConflictDetector(repository).detect(
        (ethereum.claim, polygon.claim),
        predicate_registry(conflict_rule="exclusive_object"),
    )[0]
    persisted = ConflictPersistenceService(repository).persist(
        candidate,
        effective_at=EFFECTIVE_AT,
        recorded_at=RECORDED_AT,
        processing_run_id="run-conflict",
    )

    ConflictPersistenceService(repository).append_lifecycle_event(
        ConflictLifecycleEvent(
            event_id="conflict-event-resolved",
            conflict_id=persisted.conflict.conflict_id,
            event_type="resolved",
            effective_at=RESOLVED_AT,
            recorded_at=RESOLVED_AT,
            source_evidence_id="source-evidence-Ethereum",
            reason="manual conflict resolution selected current evidence",
            previous_status="detected",
            new_status="resolved",
            processing_run_id="run-conflict-resolution",
            schema_version="conflict-lifecycle-event-v1",
        )
    )

    current_conflict = repository.current_conflict(persisted.conflict.conflict_id)
    assert current_conflict is not None
    assert current_conflict["status"] == "resolved"
    assert current_conflict["resolved_at"] == RESOLVED_AT.isoformat()
    assert repository.current_claim(ethereum.claim.claim_id)["status"] == "active"  # type: ignore[index]
    assert repository.current_claim(polygon.claim.claim_id)["status"] == "active"  # type: ignore[index]


def persist_runs_on_claim(
    repository: EvidenceIntelligenceRepository,
    chain_name: str,
    object_entity_id: str,
    *,
    conflict_rule: str,
    scope: str = "",
    authority_status: str = "verified_official",
    source_suffix: str | None = None,
):
    suffix = source_suffix or chain_name
    intake = EvidenceIntelligenceIntakeService(repository).ingest(
        EvidenceIntakeReference(
            source_evidence_id=f"source-evidence-{suffix}",
            raw_evidence_id=f"raw-evidence-{suffix}",
            normalized_evidence_id=f"normalized-evidence-{suffix}",
            candidate_id="candidate-1",
            identity_resolution_status="exact",
            source_url=f"https://example.test/docs/{suffix}",
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
        authority_status=authority_status,  # type: ignore[arg-type]
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
            predicate_registry=predicate_registry(conflict_rule=conflict_rule),
        )
        .claims[0]
    )
    persisted = ClaimPersistenceService(repository).persist(
        ClaimPersistenceInput(
            proposal=proposal,
            subject_entity_id="entity-aave",
            object_entity_id=object_entity_id,
            subject_candidate_id="candidate-1",
            predicate_schema_version="predicate-v1",
            source_evidence_ids=(f"source-evidence-{suffix}",),
            spans=intake.spans,
            authority_status=authority_status,  # type: ignore[arg-type]
            processing_provider="validated-extraction",
            processing_artifact_id=f"artifact-{suffix}",
            observed_at=EFFECTIVE_AT,
            available_at=EFFECTIVE_AT,
            retrieved_at=EFFECTIVE_AT,
            processed_at=RECORDED_AT,
            effective_at=EFFECTIVE_AT,
            recorded_at=RECORDED_AT,
            processing_run_id="run-1",
            scope=scope,
        )
    )
    return SimpleNamespace(claim=persisted.claim, document=intake.document)


def predicate_registry(*, conflict_rule: str) -> PredicateRegistry:
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
