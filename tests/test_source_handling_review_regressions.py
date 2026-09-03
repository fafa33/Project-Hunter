from __future__ import annotations

import contextlib
from datetime import timedelta
from pathlib import Path

import pytest

from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService, evidence_document_id
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    SourceHandlingAuthorityService,
)
from tests.test_source_handling_production_runtime import (
    _authorize,
    _complete_authority,
    _fact_payload,
    _operator_root,
    _provenance,
    _reference,
    _service,
)


def test_publication_admission_time_is_sampled_after_write_transaction_acquisition(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-lock-wait", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-lock-wait",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:lock-wait",
        expires_at=clock.now() + timedelta(seconds=1),
    )

    repository = service._repository  # type: ignore[attr-defined]
    original_transaction = repository._transaction

    @contextlib.contextmanager
    def transaction_after_wait():
        with original_transaction() as connection:
            clock.value += timedelta(seconds=2)
            yield connection

    repository._transaction = transaction_after_wait  # type: ignore[method-assign]

    with pytest.raises(SourceHandlingBlockedError, match="expired"):
        service.publish(
            family="FACT",
            scope="doc-lock-wait",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )

    assert service.authorization_consumed("auth:lock-wait") is False
    assert (
        service.resolver()("doc-lock-wait", clock.now()).store.current_canonical_head_id("FACT", "doc-lock-wait")
        is None
    )


def test_payload_cannot_override_repository_record_id_and_authorization_remains_reusable(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-reserved-id", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-reserved-id",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:reserved-id",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    attacker_payload = {**payload, "id": "attacker-controlled-record-id"}

    with pytest.raises(SourceHandlingBlockedError, match="repository record identity"):
        service.publish(
            family="FACT",
            scope="doc-reserved-id",
            expected_current_head_id=None,
            payload=attacker_payload,
            authorization=authorization,
        )

    assert service.authorization_consumed("auth:reserved-id") is False
    assert (
        service.resolver()("doc-reserved-id", clock.now()).store.current_canonical_head_id("FACT", "doc-reserved-id")
        is None
    )

    result = service.publish(
        family="FACT",
        scope="doc-reserved-id",
        expected_current_head_id=None,
        payload=payload,
        authorization=authorization,
    )
    assert service.authorization_consumed("auth:reserved-id") is True

    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    record = restarted.resolver()("doc-reserved-id", clock.now()).store.canonical_record_by_id("FACT", result.record_id)
    assert record is not None
    assert record["id"] == result.record_id
    assert record["id"] != "attacker-controlled-record-id"


def test_raw_issue_content_denial_does_not_block_allowed_durable_artifacts(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("transient issue body that must not require durable permission")
    document_id = evidence_document_id(reference)
    _complete_authority(
        service,
        clock,
        rule_id,
        document_id=document_id,
        category_persist_overrides={"ISSUE_CONTENT": "DENY"},
    )

    evidence_repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
    )
    result = boundary.ingest(
        reference,
        processing_run_id="run-407-derived-only",
        processed_at=clock.now(),
    )

    assert result.document.document_id == document_id
    assert evidence_repository.count("evidence_documents") == 1
