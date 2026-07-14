from __future__ import annotations

from datetime import UTC, datetime

from hunter.competitive import (
    CompetitiveInputSelector,
    CompetitiveRelationshipBuilder,
    CompetitiveRepository,
    EvidenceClaimInput,
    InputSelectionContext,
    RelationshipProjectionInput,
)
from hunter.discovery.models import CandidateIdentity, CandidateRecord
from hunter.evidence_intelligence import (
    ClaimLifecycleEvent,
    DocumentLifecycleEvent,
    EvidenceDocument,
    EvidenceIntelligenceRepository,
    EvidenceSpan,
    EvidenceSpanLink,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeRelationship,
    PredicateDefinition,
    SourceAuthorityVerificationEvent,
    SourceEvidenceLink,
)

JAN_1 = datetime(2026, 1, 1, tzinfo=UTC)
JAN_15 = datetime(2026, 1, 15, tzinfo=UTC)
FEB_1 = datetime(2026, 2, 1, tzinfo=UTC)


def test_phase_three_candidate_selector_uses_indexed_trust_inputs() -> None:
    repository = FakeCandidateRepository(
        (
            candidate_record("candidate-a", identity_resolution_status="exact"),
            candidate_record("candidate-b", identity_resolution_status="unresolved"),
            candidate_record("candidate-c", identity_resolution_status="conflict"),
        ),
        (
            identity_result("candidate-a", "exact"),
            identity_result("candidate-b", "unresolved"),
            identity_result("candidate-c", "conflict"),
        ),
    )
    selector = CompetitiveInputSelector(candidate_repository=repository)

    inputs = selector.select_candidate_inputs(candidate_ids=("candidate-a", "candidate-b", "candidate-c"))

    assert repository.get_calls == ["candidate-a", "candidate-b", "candidate-c"]
    assert repository.list_called is False
    assert [item.available for item in inputs] == [True, False, False]
    assert inputs[1].reason == "identity_unresolved_or_conflicted"
    assert inputs[2].reason == "identity_unresolved_or_conflicted"


def test_phase_three_explicit_candidate_ids_cannot_bypass_trust_gating(tmp_path) -> None:
    candidate_repository = FakeCandidateRepository(
        (candidate_record("candidate-1", identity_resolution_status="conflict"),),
        (identity_result("candidate-1", "conflict"),),
    )
    evidence_repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    persist_claim_graph(evidence_repository)
    selector = CompetitiveInputSelector(
        candidate_repository=candidate_repository,
        evidence_repository=evidence_repository,
    )

    selection = selector.select(candidate_ids=("candidate-1",))

    assert selection.candidates[0].available is False
    assert selection.claims == ()
    assert selection.relationship_projections == ()


def test_phase_three_claim_selector_respects_strict_known_by_hunter_authority_cutoff(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    persist_claim_graph(repository, claim_recorded_at=FEB_1, authority_recorded_at=FEB_1)
    selector = CompetitiveInputSelector(evidence_repository=repository)

    strict_inputs = selector.select_claim_inputs(
        context=InputSelectionContext(cutoff=JAN_15, strict_known_by_hunter=True)
    )
    reconstructed_inputs = selector.select_claim_inputs(
        context=InputSelectionContext(cutoff=JAN_15, strict_known_by_hunter=False)
    )

    assert strict_inputs[0].available is False
    assert strict_inputs[0].claim_status == "unavailable"
    assert strict_inputs[0].authority_statuses == ("unavailable",)
    assert strict_inputs[0].replay_mode == "historical_strict_known_by_hunter"
    assert reconstructed_inputs[0].available is True
    assert reconstructed_inputs[0].claim_status == "active"
    assert reconstructed_inputs[0].authority_statuses == ("verified_official",)
    assert reconstructed_inputs[0].replay_mode == "reconstructed_after_cutoff"
    assert reconstructed_inputs[0].known_at_cutoff is False


def test_phase_three_claim_selector_preserves_unavailable_lifecycle_states(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    persist_claim_graph(repository, claim_status="disputed")
    selector = CompetitiveInputSelector(evidence_repository=repository)

    inputs = selector.select_claim_inputs()

    assert inputs[0].available is False
    assert inputs[0].reason == "claim_lifecycle_status_not_usable:disputed"


def test_phase_three_relationship_projection_inherits_claim_availability(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    persist_claim_graph(repository)
    selector = CompetitiveInputSelector(evidence_repository=repository)

    claims = selector.select_claim_inputs()
    projections = selector.select_relationship_projection_inputs(claim_inputs=claims)

    assert claims[0].available is True
    assert projections[0].available is True
    assert projections[0].claim_id == claims[0].claim_id
    assert projections[0].reason == "relationship_projection_inherits_available_claim_state"
    assert projections[0].replay_mode == "current"


def test_phase_four_builder_persists_evidence_backed_relationship_with_lineage(tmp_path) -> None:
    evidence_repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    competitive_repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_claim_graph(evidence_repository)
    selector = CompetitiveInputSelector(evidence_repository=evidence_repository)
    builder = CompetitiveRelationshipBuilder(
        evidence_repository=evidence_repository,
        competitive_repository=competitive_repository,
    )

    result = builder.build_from_inputs(
        claim_inputs=selector.select_claim_inputs(),
        projection_inputs=selector.select_relationship_projection_inputs(claim_inputs=selector.select_claim_inputs()),
    )

    assert len(result.relationships) == 1
    relationship = result.relationships[0]
    assert relationship.relationship_type == "direct_competitor"
    assert relationship.subject_candidate_id == "candidate-1"
    assert relationship.peer_candidate_id == "candidate-2"
    assert relationship.claim_id == "claim-1"
    assert relationship.projection_id == "relationship-1"
    lineage = competitive_repository.relationship_lineage(relationship.relationship_id)
    assert lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert lineage["spans"][0]["span_id"] == "span-1"


def test_phase_four_builder_rejects_co_mentions_and_unavailable_claims(tmp_path) -> None:
    unavailable_evidence = EvidenceIntelligenceRepository(tmp_path / "unavailable-evidence.sqlite")
    unavailable_competitive = CompetitiveRepository(tmp_path / "unavailable-competitive.sqlite")
    persist_claim_graph(unavailable_evidence, claim_status="disputed")
    unavailable_selector = CompetitiveInputSelector(evidence_repository=unavailable_evidence)
    unavailable_builder = CompetitiveRelationshipBuilder(
        evidence_repository=unavailable_evidence,
        competitive_repository=unavailable_competitive,
    )
    unavailable_claims = unavailable_selector.select_claim_inputs()

    active_evidence = EvidenceIntelligenceRepository(tmp_path / "active-evidence.sqlite")
    active_competitive = CompetitiveRepository(tmp_path / "active-competitive.sqlite")
    persist_claim_graph(active_evidence)
    active_selector = CompetitiveInputSelector(evidence_repository=active_evidence)
    active_builder = CompetitiveRelationshipBuilder(
        evidence_repository=active_evidence,
        competitive_repository=active_competitive,
    )
    active_claims = active_selector.select_claim_inputs()
    co_mention_projection = RelationshipProjectionInput(
        relationship_id="relationship-co-mention",
        claim_id="claim-1",
        subject_entity_id="entity-1",
        object_entity_id="entity-2",
        predicate_id="co_mentions",
        projection_status="active",
        claim_status="active",
        confidence=0.9,
        availability="available",
        reason="test available input",
        replay_mode="current",
        known_at_cutoff=True,
    )

    disputed_result = unavailable_builder.build_from_inputs(
        claim_inputs=unavailable_claims,
        projection_inputs=unavailable_selector.select_relationship_projection_inputs(claim_inputs=unavailable_claims),
    )
    co_mention_result = active_builder.build_from_inputs(
        claim_inputs=active_claims,
        projection_inputs=(co_mention_projection,),
    )

    assert disputed_result.relationships == ()
    assert co_mention_result.relationships == ()
    assert unavailable_competitive.count("competitive_relationships") == 0
    assert active_competitive.count("competitive_relationships") == 0


def test_phase_four_builder_respects_historical_strict_input_selection(tmp_path) -> None:
    evidence_repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    competitive_repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_claim_graph(evidence_repository, claim_recorded_at=FEB_1, authority_recorded_at=FEB_1)
    selector = CompetitiveInputSelector(evidence_repository=evidence_repository)
    builder = CompetitiveRelationshipBuilder(
        evidence_repository=evidence_repository,
        competitive_repository=competitive_repository,
    )
    claims = selector.select_claim_inputs(context=InputSelectionContext(cutoff=JAN_15, strict_known_by_hunter=True))

    result = builder.build_from_inputs(
        claim_inputs=claims,
        projection_inputs=selector.select_relationship_projection_inputs(
            claim_inputs=claims,
            context=InputSelectionContext(cutoff=JAN_15, strict_known_by_hunter=True),
        ),
    )

    assert claims[0].available is False
    assert result.relationships == ()
    assert competitive_repository.count("competitive_relationships") == 0


def test_phase_four_builder_uses_replay_safe_inputs_without_current_state_reread(tmp_path) -> None:
    evidence_repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    competitive_repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_claim_graph(evidence_repository, claim_status="disputed")
    builder = CompetitiveRelationshipBuilder(
        evidence_repository=evidence_repository,
        competitive_repository=competitive_repository,
    )
    claim_input = EvidenceClaimInput(
        claim_id="claim-1",
        subject_candidate_id="candidate-1",
        predicate_id="competes_with",
        claim_status="active",
        confidence=0.91,
        availability="available",
        reason="historical_strict_input_available",
        source_evidence_ids=("source-evidence-1",),
        span_ids=("span-1",),
        document_ids=("document-1",),
        document_statuses=("active",),
        authority_statuses=("verified_official",),
        replay_mode="historical_strict_known_by_hunter",
        known_at_cutoff=True,
        predicate_schema_version="competitive-predicate-v1",
        scope="global",
        polarity="positive",
        modality="asserted",
        valid_from=JAN_1,
        observed_at=JAN_1,
        processed_at=JAN_1,
        freshness=1.0,
    )
    projection_input = RelationshipProjectionInput(
        relationship_id="relationship-1",
        claim_id="claim-1",
        subject_entity_id="entity-1",
        object_entity_id="entity-2",
        predicate_id="competes_with",
        projection_status="active",
        claim_status="active",
        confidence=0.91,
        availability="available",
        reason="historical_projection_available",
        replay_mode="historical_strict_known_by_hunter",
        known_at_cutoff=True,
        scope="global",
        polarity="positive",
        modality="asserted",
        valid_from=JAN_1,
        created_at=JAN_1,
        object_candidate_id="candidate-2",
    )

    result = builder.build_from_inputs(claim_inputs=(claim_input,), projection_inputs=(projection_input,))

    assert len(result.relationships) == 1
    assert result.relationships[0].metadata["replay_mode"] == "historical_strict_known_by_hunter"
    assert result.relationships[0].peer_candidate_id == "candidate-2"


class FakeCandidateRepository:
    def __init__(self, candidates: tuple[CandidateRecord, ...], identities: tuple[CandidateIdentity, ...]) -> None:
        self.candidates = {candidate.candidate_id: candidate for candidate in candidates}
        self.identities = {identity.candidate_id: identity for identity in identities}
        self.get_calls: list[str] = []
        self.list_called = False

    def get(self, candidate_id: str) -> CandidateRecord | None:
        self.get_calls.append(candidate_id)
        return self.candidates.get(candidate_id)

    def list_candidates(self, *, limit: int = 100, offset: int = 0) -> tuple[CandidateRecord, ...]:
        self.list_called = True
        return tuple(self.candidates.values())[offset : offset + limit]

    def latest_identity_by_candidate(self) -> dict[str, CandidateIdentity]:
        return self.identities


def candidate_record(candidate_id: str, *, identity_resolution_status: str) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        slug=candidate_id,
        name=candidate_id.title(),
        symbol=None,
        sector=None,
        primary_chain=None,
        candidate_type="protocol",
        lifecycle_status="identified",
        discovery_source="test",
        first_seen_at=JAN_1,
        last_seen_at=JAN_1,
        confidence=0.9,
        identity_resolution_status=identity_resolution_status,
    )


def identity_result(candidate_id: str, outcome: str) -> CandidateIdentity:
    return CandidateIdentity(
        candidate_id=candidate_id,
        outcome=outcome,  # type: ignore[arg-type]
        confidence=0.9,
        evidence_ids=(f"evidence-{candidate_id}",),
        reason="test identity",
        evaluated_at=JAN_1,
    )


def persist_claim_graph(
    repository: EvidenceIntelligenceRepository,
    *,
    claim_status: str = "active",
    claim_recorded_at: datetime = JAN_1,
    authority_recorded_at: datetime = JAN_1,
) -> None:
    repository.save_document(evidence_document())
    repository.save_document_lifecycle_event(document_event())
    repository.save_authority_event(authority_event(recorded_at=authority_recorded_at))
    repository.save_span(evidence_span())
    repository.save_predicate(predicate_definition())
    repository.save_entity(knowledge_entity())
    repository.save_entity(peer_entity())
    repository.save_claim_with_lifecycle(
        knowledge_claim(status=claim_status),
        claim_event(status=claim_status, recorded_at=claim_recorded_at),
        (
            SourceEvidenceLink(
                link_id="claim-source-link-1",
                owner_id="claim-1",
                source_evidence_id="source-evidence-1",
                role="supporting",
                position=0,
                created_at=claim_recorded_at,
                schema_version="link-v1",
            ),
        ),
        (
            EvidenceSpanLink(
                link_id="claim-span-link-1",
                owner_id="claim-1",
                span_id="span-1",
                role="supporting",
                position=0,
                created_at=claim_recorded_at,
                schema_version="link-v1",
            ),
        ),
    )
    repository.save_relationship(relationship_projection(status=claim_status))


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
        source_type="documentation",
        source_claimed_authority="official",
        title="Protocol docs",
        content_hash="content-hash",
        normalized_content_hash="normalized-hash",
        normalization_version="normalization-v1",
        parser_id="markdown-parser",
        rendition_id="rendition-1",
        content_type="text/markdown",
        language="en",
        source_published_at=JAN_1,
        observed_at=JAN_1,
        retrieved_at=JAN_1,
        available_at=JAN_1,
        processed_at=JAN_1,
        valid_from=JAN_1,
        valid_to=None,
        document_status="active",
        processing_status="processed",
        freshness=1.0,
        confidence=0.95,
        authority_status="verified_official",
    )


def document_event() -> DocumentLifecycleEvent:
    return DocumentLifecycleEvent(
        event_id="document-event-1",
        document_id="document-1",
        event_type="accepted",
        effective_at=JAN_1,
        recorded_at=JAN_1,
        source_evidence_id="source-evidence-1",
        reason="accepted by deterministic validation",
        previous_status=None,
        new_status="active",
        processing_run_id="run-1",
        schema_version="document-event-v1",
    )


def authority_event(*, recorded_at: datetime) -> SourceAuthorityVerificationEvent:
    return SourceAuthorityVerificationEvent(
        verification_id="authority-event-1",
        document_id="document-1",
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        authority_evidence_id="source-evidence-1",
        effective_at=JAN_1,
        recorded_at=recorded_at,
        verifier_type="deterministic_system",
        reason="verified official source",
        processing_run_id="run-1",
        schema_version="authority-event-v1",
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
        end_offset=20,
        chunk_id="chunk-1",
        chunk_version="chunk-v1",
        text_hash="text-hash",
        excerpt="competes with peer",
        section_title="Competition",
        locator="section:competition",
        span_status="active",
        created_at=JAN_1,
        validated_at=JAN_1,
    )


def predicate_definition() -> PredicateDefinition:
    return PredicateDefinition(
        predicate_id="competes_with",
        name="Competes with",
        description="Explicit evidence-backed competition.",
        schema_version="competitive-predicate-v1",
        permitted_subject_types=("protocol", "project"),
        permitted_object_entity_types=("protocol", "project"),
        requires_object_entity=True,
        allows_literal_value=False,
        direction="bidirectional",
        symmetric=True,
        asymmetric=False,
        valid_modalities=("asserted",),
        valid_polarities=("positive",),
        graph_projection_eligible=True,
        support_requirements="explicit competition evidence is required",
        created_at=JAN_1,
    )


def knowledge_entity() -> KnowledgeEntity:
    return KnowledgeEntity(
        entity_id="entity-1",
        canonical_name="Subject",
        entity_type="protocol",
        candidate_id="candidate-1",
        registry_identity_status="exact",
        confidence=0.95,
        status="active",
        first_seen_at=JAN_1,
        last_seen_at=JAN_1,
        created_at=JAN_1,
        updated_at=JAN_1,
    )


def peer_entity() -> KnowledgeEntity:
    return KnowledgeEntity(
        entity_id="entity-2",
        canonical_name="Peer",
        entity_type="protocol",
        candidate_id="candidate-2",
        registry_identity_status="exact",
        confidence=0.95,
        status="active",
        first_seen_at=JAN_1,
        last_seen_at=JAN_1,
        created_at=JAN_1,
        updated_at=JAN_1,
    )


def knowledge_claim(*, status: str) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id="claim-1",
        subject_entity_id="entity-1",
        subject_candidate_id="candidate-1",
        predicate_id="competes_with",
        predicate_schema_version="competitive-predicate-v1",
        object_entity_id="entity-2",
        literal_value=None,
        literal_value_type=None,
        unit="",
        scope="global",
        polarity="positive",
        modality="asserted",
        valid_from=JAN_1,
        valid_to=None,
        observed_at=JAN_1,
        available_at=JAN_1,
        retrieved_at=JAN_1,
        processed_at=JAN_1,
        support_level="literal_support",
        confidence=0.91,
        confidence_components={"source_authority": 0.9},
        status=status,  # type: ignore[arg-type]
        authority_status="verified_official",
        processing_provider="deterministic-test",
        processing_artifact_id="artifact-1",
        schema_version="claim-v1",
        created_at=JAN_1,
    )


def claim_event(*, status: str, recorded_at: datetime) -> ClaimLifecycleEvent:
    event_type = "accepted" if status == "active" else status
    return ClaimLifecycleEvent(
        event_id="claim-event-1",
        claim_id="claim-1",
        event_type=event_type,  # type: ignore[arg-type]
        effective_at=JAN_1,
        recorded_at=recorded_at,
        source_evidence_id="source-evidence-1",
        reason="deterministic lifecycle state",
        previous_status=None,
        new_status=status,  # type: ignore[arg-type]
        processing_run_id="run-1",
        schema_version="claim-event-v1",
    )


def relationship_projection(*, status: str) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        relationship_id="relationship-1",
        claim_id="claim-1",
        subject_entity_id="entity-1",
        predicate_id="competes_with",
        object_entity_id="entity-2",
        direction="bidirectional",
        inverse_predicate_id=None,
        scope="global",
        polarity="positive",
        modality="asserted",
        valid_from=JAN_1,
        valid_to=None,
        confidence=0.91,
        status=status,  # type: ignore[arg-type]
        projection_version="projection-v1",
        created_at=JAN_1,
    )
