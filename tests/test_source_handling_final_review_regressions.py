from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_source_handling_production_runtime import (
    _complete_authority,
    _fact_payload,
    _publish,
    _reference,
    _service,
)

import hunter.evidence_intelligence.source_handling_persistence as source_handling_persistence
from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService, evidence_document_id
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError
from hunter.evidence_intelligence.source_handling_persistence import IssueSourceTransientIntakeBoundary


def test_nested_issue_metadata_cannot_bypass_source_content_dispositions(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = replace(
        _reference("canonical issue body"),
        metadata={
            "issue_number": 407,
            "labels": ["runtime"],
            "body_copy": "canonical issue body",
        },
    )
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)

    repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(  # type: ignore[call-arg]
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=service.resolver(),
        clock=clock,
    )

    with pytest.raises(SourceHandlingBlockedError, match="metadata"):
        boundary.ingest(reference, processing_run_id="run-nested-metadata", processed_at=clock.now())
    assert repository.count("evidence_documents") == 0


def test_issue_intake_revalidates_authority_while_holding_database_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("authority must remain stable through the durable write")
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)

    repository = EvidenceIntelligenceRepository(service.path)
    resolver = service.resolver()
    boundary = IssueSourceTransientIntakeBoundary(  # type: ignore[call-arg]
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=resolver,
        clock=clock,
    )

    resolver_type = type(resolver)
    original_call = resolver_type.__call__
    observed_locked = False

    def call_while_asserting_write_lock(self, requested_document_id, cutoff):
        nonlocal observed_locked
        contender = sqlite3.connect(service.path, timeout=0.0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
            observed_locked = True
        finally:
            contender.close()
        return original_call(self, requested_document_id, cutoff)

    monkeypatch.setattr(resolver_type, "__call__", call_while_asserting_write_lock)
    result = boundary.ingest(reference, processing_run_id="run-atomic-authority", processed_at=clock.now())

    assert observed_locked is True
    assert result.document.document_id == document_id
    assert repository.count("evidence_documents") == 1


def test_admission_time_is_strictly_monotonic_across_unrelated_authority_scopes(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    _publish(
        service,
        family="FACT",
        scope="doc-global-a",
        payload=_fact_payload("doc-global-a", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:global-a",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    _publish(
        service,
        family="FACT",
        scope="doc-global-b",
        payload=_fact_payload("doc-global-b", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:global-b",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    connection = sqlite3.connect(service.path)
    try:
        values = [
            source_handling_persistence._parse_time(str(row[0]))
            for row in connection.execute("SELECT admission_time FROM source_handling_authority_records ORDER BY rowid")
        ]
    finally:
        connection.close()

    assert len(values) >= 3
    assert all(earlier < later for earlier, later in zip(values, values[1:], strict=True))
