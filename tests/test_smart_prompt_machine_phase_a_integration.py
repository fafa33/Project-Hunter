from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evidence_pre_model_source_handling_fixture import source_handling_authority

from hunter.evidence_intelligence.models import EvidenceDocument, EvidenceSpan, evidence_text_digest
from hunter.evidence_intelligence.pre_model import EvidenceCapabilityConstraint, EvidencePromptSpecification
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_machine import (
    PromptBuildRequest,
    PromptContextCompiler,
    PromptMachineProfile,
    PromptMachineProfileRegistry,
)

NOW = datetime(2026, 8, 26, 18, 40, tzinfo=UTC)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


def test_real_compile_is_idempotent_and_strict_known_through_existing_persistence(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    repository.save_document(_document())
    repository.save_span(_span("span-a", "first governed context", 0))
    repository.save_span(_span("span-b", "second governed context", 32))

    def resolver(document_id: str, cutoff: datetime):
        return source_handling_authority(document_id=document_id, cutoff=cutoff)

    compiler = PromptContextCompiler(
        repository=repository,
        profiles=PromptMachineProfileRegistry((_profile(),)),
        source_handling_resolver=resolver,
        clock=_Clock(NOW),
    )
    request = PromptBuildRequest(
        document_id="document-1",
        execution_owner_id="run-1",
        profile_id="hunter-evidence-extraction",
        profile_version="1",
        task_text="Extract only supported facts.",
    )

    first = compiler.compile(request)
    second = compiler.compile(request)

    assert second.manifest.manifest_id == first.manifest.manifest_id
    assert second.manifest.build_record_id == first.manifest.build_record_id
    assert second.orchestration.persisted.recorded_at == first.orchestration.persisted.recorded_at

    before = compiler.strict_known_reconstruction(
        first.manifest.build_record_id,
        NOW - timedelta(microseconds=1),
    )
    assert before.status == "NOT_KNOWN_AT_CUTOFF"

    at_recorded = compiler.strict_known_reconstruction(first.manifest.build_record_id, NOW)
    assert at_recorded.status == "AVAILABLE"
    assert at_recorded.exact_prompt == first.orchestration.build_result.prompt_artifact.content


def test_real_compile_obeys_retention_authority_without_new_source_retention(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence.sqlite")
    repository.save_document(_document())
    repository.save_span(_span("span-a", "confidential governed context", 0))
    repository.save_span(_span("span-b", "second confidential context", 40))

    def resolver(document_id: str, cutoff: datetime):
        return source_handling_authority(
            document_id=document_id,
            cutoff=cutoff,
            retention="DENY",
            reconstruction="DENY",
        )

    compiler = PromptContextCompiler(
        repository=repository,
        profiles=PromptMachineProfileRegistry((_profile(),)),
        source_handling_resolver=resolver,
        clock=_Clock(NOW),
    )
    result = compiler.compile(
        PromptBuildRequest(
            document_id="document-1",
            execution_owner_id="run-1",
            profile_id="hunter-evidence-extraction",
            profile_version="1",
            task_text="Extract only supported facts.",
        )
    )

    assert result.orchestration.persisted.exact_source_bytes_retained is False
    reconstruction = compiler.strict_known_reconstruction(result.manifest.build_record_id, NOW)
    assert reconstruction.status == "UNAVAILABLE"
    assert reconstruction.reason_code == "EXACT_PROMPT_RETENTION_PROHIBITED"
    assert reconstruction.exact_prompt is None


def _profile() -> PromptMachineProfile:
    return PromptMachineProfile(
        profile_id="hunter-evidence-extraction",
        version="1",
        task_type="EVIDENCE_EXTRACTION",
        workflow_stage="evidence-intelligence",
        output_contract_id="extraction-proposal",
        output_contract_version="1",
        context_policy_id="evidence-context",
        context_policy_version="1",
        required_span_ids=("span-a", "span-b"),
        specification=EvidencePromptSpecification(
            specification_id="evidence-extraction",
            version="1",
            compiler_version="1",
            trusted_system_constraints="Return only governed extraction output.",
            task_instruction="Extract evidence according to the governed task.",
            output_contract='{"type":"object"}',
        ),
        capability=EvidenceCapabilityConstraint(
            constraint_id="phase-a-bytes",
            version="1",
            maximum_input_bytes=32_000,
            reserved_completion_bytes=4_000,
        ),
    )


def _document() -> EvidenceDocument:
    return EvidenceDocument(
        document_id="document-1",
        source_evidence_id="source-1",
        raw_evidence_id="raw-1",
        normalized_evidence_id="normalized-1",
        candidate_id="candidate-1",
        identity_resolution_status="exact",
        source_url="https://example.test/document",
        source_provider="official_docs",
        source_type="technical_documentation",
        source_claimed_authority="official",
        title="Document",
        content_hash="content-hash",
        normalized_content_hash="normalized-hash",
        normalization_version="1",
        parser_id="test-parser",
        rendition_id="rendition-1",
        content_type="text/plain",
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
        confidence=1.0,
    )


def _span(span_id: str, excerpt: str, start_offset: int) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        document_id="document-1",
        source_evidence_id="source-1",
        normalized_content_hash="normalized-hash",
        normalization_version="1",
        parser_id="test-parser",
        rendition_id="rendition-1",
        offset_encoding="utf-8-bytes",
        start_offset=start_offset,
        end_offset=start_offset + len(excerpt.encode("utf-8")),
        chunk_id=f"chunk-{span_id}",
        chunk_version="1",
        text_hash=evidence_text_digest(excerpt),
        excerpt=excerpt,
        section_title="Test",
        locator=f"test:{span_id}",
        span_status="active",
        created_at=NOW,
        validated_at=NOW,
    )
