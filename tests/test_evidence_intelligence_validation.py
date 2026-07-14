from __future__ import annotations

from datetime import UTC, datetime

from hunter.evidence_intelligence import (
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceRepository,
    ExtractionValidationService,
    PredicateDefinition,
    PredicateRegistry,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_five_classifies_evidence_deterministically(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "Aave runs on Ethereum and has a GitHub repository.")

    classification = ExtractionValidationService().classify(document_id="document-1", spans=spans)

    assert classification.categories == ("repository", "integration")
    assert classification.confidence == 0.7


def test_phase_five_validates_entities_and_claims_against_predicate_registry(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "Aave runs on Ethereum.")
    payload = {
        "entities": [
            {
                "name": "Aave",
                "entity_type": "protocol",
                "span_id": spans[0].span_id,
                "support_text": "Aave",
                "confidence": 0.9,
            }
        ],
        "claims": [
            {
                "predicate_id": "runs_on",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "object_name": "Ethereum",
                "object_type": "chain",
                "span_id": spans[0].span_id,
                "support_level": "semantic_support",
                "support_text": "Aave runs on Ethereum",
                "explicit_support": True,
            }
        ],
    }

    result = ExtractionValidationService().validate(
        document_id="document-1",
        spans=spans,
        payload=payload,
        predicate_registry=predicate_registry(),
    )

    assert result.entities[0].name == "Aave"
    assert result.claims[0].predicate_id == "runs_on"
    assert result.rejections == ()


def test_phase_five_rejects_unsupported_predicates_and_prevents_co_mention_claims(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "Aave and Ethereum are mentioned on the same page.")
    payload = {
        "claims": [
            {
                "predicate_id": "unknown_predicate",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "object_name": "Ethereum",
                "object_type": "chain",
                "span_id": spans[0].span_id,
                "support_level": "semantic_support",
                "support_text": "Aave and Ethereum are mentioned",
                "explicit_support": True,
            },
            {
                "predicate_id": "runs_on",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "object_name": "Ethereum",
                "object_type": "chain",
                "span_id": spans[0].span_id,
                "support_level": "semantic_support",
                "support_text": "Aave and Ethereum are mentioned",
            },
        ]
    }

    result = ExtractionValidationService().validate(
        document_id="document-1",
        spans=spans,
        payload=payload,
        predicate_registry=predicate_registry(),
    )

    assert result.claims == ()
    assert {rejection.reason for rejection in result.rejections} == {
        "unsupported_predicate",
        "semantic_support_not_explicit",
    }


def test_phase_five_requires_literal_support_for_numbers_dates_addresses_urls_and_quotes(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "The protocol fee is 0.05 and the official docs are at https://aave.com.")
    payload = {
        "claims": [
            {
                "predicate_id": "charges_fee",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "literal_value": "0.05",
                "literal_value_type": "decimal",
                "span_id": spans[0].span_id,
                "support_level": "literal_support",
                "support_text": "The protocol fee is 0.05",
                "explicit_support": True,
            },
            {
                "predicate_id": "charges_fee",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "literal_value": "0.30",
                "literal_value_type": "decimal",
                "span_id": spans[0].span_id,
                "support_level": "literal_support",
                "support_text": "The protocol fee is 0.05",
                "explicit_support": True,
            },
            {
                "predicate_id": "official_website",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "literal_value": "https://aave.com",
                "literal_value_type": "url",
                "span_id": spans[0].span_id,
                "support_level": "literal_support",
                "support_text": "the official docs are at https://aave.com",
                "direct_quote": True,
            },
        ]
    }

    result = ExtractionValidationService().validate(
        document_id="document-1",
        spans=spans,
        payload=payload,
        predicate_registry=predicate_registry(),
    )

    assert [claim.literal_value for claim in result.claims] == ["0.05", "https://aave.com"]
    assert result.rejections[0].reason == "literal_support_required"


def test_phase_five_rejects_inferred_predictive_causal_ownership_and_investment_conclusions(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "Aave integrates with Ethereum.")
    payload = {
        "claims": [
            {
                "predicate_id": "integrates_with",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "object_name": "Ethereum",
                "object_type": "chain",
                "span_id": spans[0].span_id,
                "support_level": "semantic_support",
                "support_text": "Aave integrates with Ethereum",
                "explicit_support": True,
                "conclusion_type": conclusion_type,
            }
            for conclusion_type in ("inferred", "predictive", "causal", "ownership", "investment")
        ]
    }

    result = ExtractionValidationService().validate(
        document_id="document-1",
        spans=spans,
        payload=payload,
        predicate_registry=predicate_registry(),
    )

    assert result.claims == ()
    assert [rejection.reason for rejection in result.rejections] == ["unsupported_conclusion_type"] * 5


def test_phase_five_does_not_create_claims_or_relationships(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    spans = evidence_spans(repository, "Aave runs on Ethereum.")
    payload = {
        "claims": [
            {
                "predicate_id": "runs_on",
                "subject_name": "Aave",
                "subject_type": "protocol",
                "object_name": "Ethereum",
                "object_type": "chain",
                "span_id": spans[0].span_id,
                "support_level": "semantic_support",
                "support_text": "Aave runs on Ethereum",
                "explicit_support": True,
            }
        ]
    }

    result = ExtractionValidationService().validate(
        document_id="document-1",
        spans=spans,
        payload=payload,
        predicate_registry=predicate_registry(),
    )

    assert len(result.claims) == 1
    assert repository.count("knowledge_claims") == 0
    assert repository.count("knowledge_relationship_projections") == 0


def evidence_spans(repository: EvidenceIntelligenceRepository, content: str):
    return (
        EvidenceIntelligenceIntakeService(repository)
        .ingest(
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
                content=content,
                observed_at=NOW,
                retrieved_at=NOW,
                available_at=NOW,
            ),
            processing_run_id="run-1",
            processed_at=NOW,
        )
        .spans
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
            PredicateDefinition(
                predicate_id="integrates_with",
                name="integrates with",
                description="protocol integrates with entity",
                schema_version="predicate-v1",
                permitted_subject_types=("protocol",),
                permitted_object_entity_types=("chain", "protocol"),
                requires_object_entity=True,
                graph_projection_eligible=True,
            ),
            PredicateDefinition(
                predicate_id="charges_fee",
                name="charges fee",
                description="protocol fee literal",
                schema_version="predicate-v1",
                permitted_subject_types=("protocol",),
                permitted_literal_value_types=("decimal",),
                allows_literal_value=True,
            ),
            PredicateDefinition(
                predicate_id="official_website",
                name="official website",
                description="official website url",
                schema_version="predicate-v1",
                permitted_subject_types=("protocol",),
                permitted_literal_value_types=("url",),
                allows_literal_value=True,
            ),
        ),
    )
