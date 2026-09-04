from __future__ import annotations

import contextlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from test_source_handling_production_runtime import (
    _authorize,
    _complete_authority,
    _fact_payload,
    _operator_root,
    _policy_payload,
    _provenance,
    _publish,
    _reference,
    _service,
)

import hunter.evidence_intelligence.source_handling_persistence as source_handling_persistence
from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService, evidence_document_id
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    SourceHandlingAuthorityService,
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
    boundary = IssueSourceTransientIntakeBoundary(  # type: ignore[call-arg]
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
        clock=clock,
    )
    result = boundary.ingest(
        reference,
        processing_run_id="run-407-derived-only",
        processed_at=clock.now(),
    )

    assert result.document.document_id == document_id
    assert evidence_repository.count("evidence_documents") == 1


def test_fact_publication_requires_complete_explicit_availability_state(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-availability", clock.now())
    payload["fact"].pop("deleted_at_source")

    with pytest.raises(SourceHandlingBlockedError, match="availability state"):
        _authorize(
            service,
            family="FACT",
            scope="doc-availability",
            payload=payload,
            rule_id=rule_id,
            expected_head=None,
            authorization_id="auth:availability-incomplete",
            expires_at=clock.now() + timedelta(minutes=5),
        )


def test_live_issue_intake_uses_trusted_current_cutoff_not_caller_processed_at(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("must not bypass a current retention revocation")
    document_id = evidence_document_id(reference)
    heads = _complete_authority(service, clock, rule_id, document_id=document_id)
    stale_processed_at = clock.now()

    clock.value += timedelta(minutes=1)
    policy = _policy_payload(document_id, clock.now(), registry_id=f"registry:{document_id}:v1", retention="DENY")
    policy["supersedes_policy_record_id"] = heads["policy"]
    _publish(
        service,
        family="POLICY",
        scope=f"policy:{document_id}:v1",
        payload=policy,
        rule_id=rule_id,
        expected_head=heads["policy"],
        authorization_id="auth:policy:revoked",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(  # type: ignore[call-arg]
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=service.resolver(),
        clock=clock,
    )
    with pytest.raises(SourceHandlingBlockedError, match="retention"):
        boundary.ingest(reference, processing_run_id="run-current-cutoff", processed_at=stale_processed_at)
    assert repository.count("evidence_documents") == 0


def test_metadata_only_fact_blocks_non_metadata_prepared_artifacts(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("content that would create text and derived identifiers")
    document_id = evidence_document_id(reference)
    heads = _complete_authority(service, clock, rule_id, document_id=document_id)

    clock.value += timedelta(minutes=1)
    fact = _fact_payload(document_id, clock.now(), supersedes=heads["fact"])
    fact["fact"]["persistence_restriction"] = "METADATA_ONLY"
    _publish(
        service,
        family="FACT",
        scope=document_id,
        payload=fact,
        rule_id=rule_id,
        expected_head=heads["fact"],
        authorization_id="auth:fact:metadata-only",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(  # type: ignore[call-arg]
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=service.resolver(),
        clock=clock,
    )
    with pytest.raises(SourceHandlingBlockedError, match="persistence restriction"):
        boundary.ingest(reference, processing_run_id="run-metadata-only", processed_at=clock.now())
    assert repository.count("evidence_documents") == 0


def test_read_view_verifies_history_inside_one_explicit_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, clock, _key, _rule_id = _service(tmp_path)
    observed: list[bool] = []
    original = source_handling_persistence._verify_authenticated_history

    def verifying(connection, **kwargs):
        observed.append(connection.in_transaction)
        return original(connection, **kwargs)

    monkeypatch.setattr(source_handling_persistence, "_verify_authenticated_history", verifying)
    service.resolver()("missing-doc", clock.now()).store.current_canonical_head_id("FACT", "missing-doc")
    assert observed
    assert all(observed)


def test_repository_admission_times_are_strictly_monotonic_with_frozen_clock_across_restart(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-monotonic",
        payload=_fact_payload("doc-monotonic", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:monotonic:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    successor = _fact_payload("doc-monotonic", clock.now(), supersedes=first, sensitivity="RESTRICTED")
    _publish(
        restarted,
        family="FACT",
        scope="doc-monotonic",
        payload=successor,
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:monotonic:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    connection = sqlite3.connect(service.path)
    try:
        values = [
            source_handling_persistence._parse_time(str(row[0]))
            for row in connection.execute(
                "SELECT admission_time FROM source_handling_authority_records "
                "WHERE family = 'FACT' AND scope = 'doc-monotonic' ORDER BY rowid"
            )
        ]
    finally:
        connection.close()
    assert len(values) == 2
    assert values[0] < values[1]
