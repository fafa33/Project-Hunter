from __future__ import annotations

from datetime import UTC, datetime

from hunter.evidence_intelligence import (
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceRepository,
    normalize_content,
)

EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2026, 1, 15, tzinfo=UTC)
AFTER_RECORDED = datetime(2026, 2, 2, tzinfo=UTC)


def test_phase_three_intake_persists_document_version_lifecycle_authority_and_spans(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = EvidenceIntelligenceIntakeService(repository)
    reference = intake_reference(content=" Protocol runs on Ethereum. \r\nFees are documented. ")

    result = service.ingest(
        reference,
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        verifier_type="deterministic_system",
    )

    assert reference.content == " Protocol runs on Ethereum. \r\nFees are documented. "
    assert result.document.document_status == "active"
    assert result.document_version.normalized_content_hash == result.document.normalized_content_hash
    assert result.document_event.new_status == "active"
    assert result.authority_event.authority_status == "verified_official"
    assert len(result.spans) == 1
    assert repository.count("evidence_documents") == 1
    assert repository.count("evidence_document_versions") == 1
    assert repository.count("evidence_spans") == 1
    assert repository.count("document_lifecycle_events") == 1
    assert repository.count("source_authority_verification_events") == 1
    assert repository.count("document_lifecycle_event_span_links") == 1


def test_phase_three_intake_is_idempotent_for_same_evidence_reference(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = EvidenceIntelligenceIntakeService(repository)
    reference = intake_reference()

    first = service.ingest(reference, processing_run_id="run-1", processed_at=RECORDED_AT)
    second = service.ingest(reference, processing_run_id="run-1", processed_at=RECORDED_AT)

    assert second.document.document_id == first.document.document_id
    assert second.document_version.version_id == first.document_version.version_id
    assert tuple(span.span_id for span in second.spans) == tuple(span.span_id for span in first.spans)
    assert repository.count("evidence_documents") == 1
    assert repository.count("evidence_document_versions") == 1
    assert repository.count("evidence_spans") == len(first.spans)
    assert repository.count("document_lifecycle_events") == 1
    assert repository.count("source_authority_verification_events") == 1


def test_phase_three_document_lifecycle_reconstructs_historical_cutoffs(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = EvidenceIntelligenceIntakeService(repository)
    document = service.ingest(
        intake_reference(),
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
    ).document

    assert repository.document_status_at(document.document_id, BEFORE_RECORDED) == "active"
    assert repository.document_status_at(document.document_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None
    assert repository.document_status_at(document.document_id, AFTER_RECORDED, strict_known_by_hunter=True) == "active"


def test_phase_three_authority_reconstructs_strict_known_by_hunter_cutoffs(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = EvidenceIntelligenceIntakeService(repository)
    document = service.ingest(
        intake_reference(),
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        verifier_type="deterministic_system",
    ).document

    assert repository.authority_status_at(document.document_id, BEFORE_RECORDED) == "verified_official"
    assert repository.authority_status_at(document.document_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None
    assert (
        repository.authority_status_at(document.document_id, AFTER_RECORDED, strict_known_by_hunter=True)
        == "verified_official"
    )


def test_phase_three_current_authority_projection_does_not_leak_into_historical_confidence(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    service = EvidenceIntelligenceIntakeService(repository)
    document = service.ingest(
        intake_reference(),
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        verifier_type="deterministic_system",
    ).document

    assert document.authority_status == "verified_official"
    assert repository.authority_status_at(document.document_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None
    assert repository.authority_event_at(document.document_id, BEFORE_RECORDED, strict_known_by_hunter=True) is None


def test_phase_three_normalization_is_stable_without_mutating_raw_content() -> None:
    raw = "alpha \r\nbeta\t \r gamma  "

    normalized = normalize_content(raw)

    assert raw == "alpha \r\nbeta\t \r gamma  "
    assert normalized == "alpha\nbeta\n gamma"


def intake_reference(**overrides: object) -> EvidenceIntakeReference:
    values = {
        "source_evidence_id": "source-evidence-1",
        "raw_evidence_id": "raw-evidence-1",
        "normalized_evidence_id": "normalized-evidence-1",
        "candidate_id": "candidate-1",
        "identity_resolution_status": "exact",
        "source_url": "https://example.test/docs",
        "source_provider": "existing_hunter_evidence",
        "source_type": "official_documentation",
        "source_claimed_authority": "official",
        "title": "Protocol docs",
        "content": "Protocol runs on Ethereum.",
        "source_published_at": EFFECTIVE_AT,
        "observed_at": EFFECTIVE_AT,
        "retrieved_at": EFFECTIVE_AT,
        "available_at": EFFECTIVE_AT,
        "valid_from": EFFECTIVE_AT,
    }
    values.update(overrides)
    return EvidenceIntakeReference(**values)  # type: ignore[arg-type]
