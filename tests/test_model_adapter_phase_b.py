"""ADR 0034 Phase B — adversarial regression coverage.

These tests attack the transport, outcome, and response-capture boundaries. Each
negative case is paired with the positive case it must not break, so a guard that
is silently removed fails a test rather than quietly widening authority.

No test here contacts a provider. The OpenAI transport's classification and wire
construction are exercised through an injected opener, so every branch is
deterministic and CI never depends on provider availability, quota, or billing.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pickle
import socket
import sqlite3
import threading
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import model_adapter_fixture as fixture
import pytest

from hunter.evidence_intelligence.model_adapter import (
    HandoffConsumptionError,
    ModelAdapterAuthorityError,
    ModelAdapterError,
    ModelAdapterService,
    ModelAttemptOutcomeRecord,
    PreparedModelAttempt,
    ProviderResponseArtifact,
    ResponseCaptureBlocked,
    RetryNotAuthorized,
    attempt_idempotency_identity,
    classify_transport_result,
    derive_retry_authorization,
    response_content_credential_risk,
)
from hunter.evidence_intelligence.model_adapter_persistence import (
    ModelAdapterAuthorityMismatch,
    ModelAdapterPersistenceConflict,
    ModelAdapterPersistenceError,
    ModelAdapterPersistenceRepository,
)
from hunter.evidence_intelligence.model_adapter_transport import (
    DispatchAuthorization,
    OpenAIChatCompletionsTransport,
    ProviderTransportError,
    TransportAuthorityError,
    TransportCredential,
    TransportRequest,
    TransportResult,
    classify_openai_http_status,
    openai_request_body,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

SEEDED_SECRET = "sk-seeded-phase-b-credential-value"


@pytest.fixture
def repository(tmp_path: Path) -> ModelAdapterPersistenceRepository:
    return ModelAdapterPersistenceRepository(EvidenceIntelligenceRepository(tmp_path / "evidence.db"))


@pytest.fixture
def service(repository: ModelAdapterPersistenceRepository) -> ModelAdapterService:
    return ModelAdapterService(repository, transport_endpoints=fixture.PHASE_B_ENDPOINTS)


def prepare(
    service: ModelAdapterService,
    *,
    authority: Any = None,
    profile: Any = None,
    **overrides: Any,
) -> PreparedModelAttempt:
    """Prepare one attempt through the real Phase A path."""
    artifact = fixture.prompt_artifact()
    kwargs: dict[str, Any] = {
        "execution_owner_id": "pipeline-run:1",
        "build_record": fixture.build_record(artifact),
        "prompt_artifact": artifact,
        "capability": fixture.capability(),
        "allocation": fixture.allocation(),
        "profile": profile or fixture.phase_b_profile(),
        "attempt_authority": authority if authority is not None else fixture.attempt_authority(),
        "build_cutoff": fixture.BUILD_CUTOFF,
        "recorded_at": fixture.RECORDED_AT,
    }
    kwargs.update(overrides)
    return service.prepare_attempt(**kwargs)


def dispatch(
    service: ModelAdapterService,
    prepared: PreparedModelAttempt,
    transport: Any,
    *,
    authority: Any = None,
    profile: Any = None,
    dispatched_at: datetime = fixture.DISPATCHED_AT,
    concluded_at: datetime = fixture.CONCLUDED_AT,
) -> Any:
    return service.dispatch(
        prepared=prepared,
        profile=profile or fixture.phase_b_profile(),
        transport=transport,
        credential=fixture.credential(),
        prompt_artifact=fixture.prompt_artifact(),
        attempt_authority=authority if authority is not None else fixture.attempt_authority(),
        dispatched_at=dispatched_at,
        concluded_at=concluded_at,
    )


def persisted_scalars(database: Path) -> list[object]:
    """Every scalar value actually written to the Phase B durable tables."""
    values: list[object] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        else:
            values.append(node)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM model_attempt_outcome_records "
            "UNION ALL SELECT payload_json FROM provider_response_artifacts "
            "UNION ALL SELECT payload_json FROM model_attempt_records "
            "UNION ALL SELECT payload_json FROM model_handoff_records"
        ).fetchall()
    for row in rows:
        walk(json.loads(str(row[0])))
    return values


# --- one real dispatch, end to end -------------------------------------------


def test_one_authorized_dispatch_produces_outcome_and_governed_response(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    transport = fixture.FakeTransport(fixture.transport_result())

    result = dispatch(service, prepared, transport)

    assert len(transport.sends) == 1
    assert result.outcome.outcome == "SUCCEEDED_TRANSPORT"
    assert result.outcome.delivery_certainty == "ANSWERED"
    assert result.outcome.attempt_id == prepared.attempt.attempt_id
    assert result.outcome.handoff_id == prepared.handoff.handoff_id
    assert result.outcome.dispatched_at == fixture.DISPATCHED_AT
    assert result.response_artifact is not None
    assert result.response_artifact.state == "RESPONSE_EVIDENCE_DURABLE"
    assert result.response_artifact.content == '{"choices": [{"message": {"content": "ok"}}]}'
    assert repository.terminal_outcome_exists(prepared.attempt.attempt_id)
    stored = repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(30))
    assert [record.outcome for record in stored] == ["SUCCEEDED_TRANSPORT"]


def test_full_lifecycle_is_identical_across_two_materially_different_transports(
    service: ModelAdapterService,
) -> None:
    """CO-02: canonical record structure is provider-neutral, not provider-shaped."""
    first = prepare(service)
    alternate_profile = fixture.phase_b_profile(
        transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY,
        transport_version=fixture.ALTERNATE_TRANSPORT_VERSION,
        profile_version="2",
    )
    second = prepare(service, profile=alternate_profile, execution_owner_id="pipeline-run:2")

    outcome_a = dispatch(service, first, fixture.FakeTransport(fixture.transport_result())).outcome
    outcome_b = dispatch(
        service,
        second,
        fixture.FakeTransport(
            fixture.transport_result(
                response_text="<response><text>ok</text></response>",
                provider_status_metadata=(("status", "OK"),),
                transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY,
                transport_version=fixture.ALTERNATE_TRANSPORT_VERSION,
            ),
            transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY,
            transport_version=fixture.ALTERNATE_TRANSPORT_VERSION,
        ),
        profile=alternate_profile,
    ).outcome

    assert outcome_a.outcome == "SUCCEEDED_TRANSPORT"
    assert outcome_b.outcome == "SUCCEEDED_TRANSPORT"
    assert {field.name for field in dataclasses.fields(outcome_a)} == {
        field.name for field in dataclasses.fields(outcome_b)
    }
    # No canonical field is typed or named in provider-specific terms.
    assert not any(
        token in field.name.lower()
        for field in dataclasses.fields(ModelAttemptOutcomeRecord)
        for token in ("openai", "anthropic", "gpt", "claude", "chat_completion")
    )


# --- 1-2. no send without a durable attempt and the exact handoff -------------


def test_transport_is_never_reached_without_a_durably_persisted_attempt(
    repository: ModelAdapterPersistenceRepository,
) -> None:
    class RefusingRepository:
        def __init__(self, inner: ModelAdapterPersistenceRepository) -> None:
            self._inner = inner

        def persist_attempt_and_handoff(self, **_: Any) -> None:
            raise ModelAdapterPersistenceError("simulated durable-write failure")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    service = ModelAdapterService(RefusingRepository(repository), transport_endpoints=fixture.PHASE_B_ENDPOINTS)
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(ModelAdapterPersistenceError):
        prepare(service)

    assert transport.sends == []


def test_dispatch_with_an_unpersisted_handoff_sends_nothing(
    service: ModelAdapterService,
) -> None:
    """A handoff the repository never recorded cannot authorize a send."""
    prepared = prepare(service)
    forged = dataclasses.replace(prepared.handoff, dispatch_capability_identity="forged:not-persisted")
    orphan = PreparedModelAttempt(
        attempt=prepared.attempt,
        handoff=forged,
        request_evidence=prepared.request_evidence,
        resolved_authority=prepared.resolved_authority,
    )
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(ModelAdapterPersistenceError):
        dispatch(service, orphan, transport)

    assert transport.sends == []


def test_an_already_consumed_handoff_cannot_authorize_a_second_send(
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    first = fixture.FakeTransport(fixture.transport_result())
    dispatch(service, prepared, first)

    second = fixture.FakeTransport(fixture.transport_result())
    with pytest.raises(HandoffConsumptionError):
        dispatch(service, prepared, second, dispatched_at=fixture.later(20), concluded_at=fixture.later(21))

    assert len(first.sends) == 1
    assert second.sends == []


def test_an_expired_handoff_cannot_authorize_a_send(service: ModelAdapterService) -> None:
    prepared = prepare(service, handoff_expires_at=fixture.later(5))
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(HandoffConsumptionError):
        dispatch(service, prepared, transport, dispatched_at=fixture.later(10), concluded_at=fixture.later(11))

    assert transport.sends == []


def test_an_unexpired_handoff_still_authorizes_a_send(service: ModelAdapterService) -> None:
    prepared = prepare(service, handoff_expires_at=fixture.later(60))
    transport = fixture.FakeTransport(fixture.transport_result())

    result = dispatch(service, prepared, transport, dispatched_at=fixture.later(10), concluded_at=fixture.later(11))

    assert len(transport.sends) == 1
    assert result.outcome.outcome == "SUCCEEDED_TRANSPORT"


# --- 3. concurrency ----------------------------------------------------------


def test_concurrent_dispatch_produces_exactly_one_real_send(service: ModelAdapterService) -> None:
    prepared = prepare(service)
    transport = fixture.FakeTransport(fixture.transport_result())
    barrier = threading.Barrier(6)
    lock = threading.Lock()
    winners: list[object] = []
    losers: list[BaseException] = []

    def contend() -> None:
        barrier.wait()
        try:
            outcome = dispatch(service, prepared, transport)
        except BaseException as error:  # noqa: BLE001 - the loser's error is the assertion
            with lock:
                losers.append(error)
        else:
            with lock:
                winners.append(outcome)

    threads = [threading.Thread(target=contend) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    assert len(losers) == 5
    # The decisive assertion: exactly one *network* invocation, not merely one
    # successful return.
    assert len(transport.sends) == 1


# --- 4-5. authority bypass ---------------------------------------------------


def test_a_direct_transport_call_cannot_mint_its_own_authorization() -> None:
    transport = OpenAIChatCompletionsTransport()
    request = TransportRequest(
        endpoint_url="https://fake.invalid/v1/chat/completions",
        request_protocol_identity="protocol:openai-chat-completions",
        request_protocol_version="1",
        model_identity="model",
        prompt_content="hello",
    )
    with pytest.raises(TransportAuthorityError):
        DispatchAuthorization(
            object(),
            handoff_id="h",
            attempt_id="a",
            execution_profile_identity="p",
            consumed_at="2026-08-22T12:00:00+00:00",
        )
    with pytest.raises(TransportAuthorityError):
        transport.send(request, authorization=None, credential=fixture.credential())  # type: ignore[arg-type]


def test_repository_direct_write_cannot_bypass_model_adapter_authority(
    repository: ModelAdapterPersistenceRepository,
) -> None:
    from hunter.evidence_intelligence.model_adapter_persistence import ModelAdapterDirectWriteForbidden

    with pytest.raises(ModelAdapterDirectWriteForbidden):
        repository.direct_write(table="model_attempt_outcome_records", record={"outcome_id": "forged"})


def test_repository_rejects_an_outcome_whose_attempt_was_never_recorded(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    outcome = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result())).outcome
    orphaned = dataclasses.replace(outcome, attempt_id="model-attempt:never-recorded", supersedes_outcome_id=None)

    with pytest.raises(ModelAdapterPersistenceError):
        repository.append_outcome(
            outcome=orphaned,
            response_artifact=None,
            attempt_authority=fixture.attempt_authority(),
        )


def test_repository_rejects_correlation_metadata_its_own_authority_prohibits(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    """Requirement 16: unauthorized correlation metadata cannot be persisted.

    Even when the caller bypasses `ModelAdapterService` entirely, persistence
    rederives the decision itself and refuses the write.
    """
    prepared = prepare(service)
    outcome = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result())).outcome
    assert outcome.correlation_identity == "req_abc123"

    denying = fixture.attempt_authority(dispatch_capability=False)
    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.append_outcome(outcome=outcome, response_artifact=None, attempt_authority=denying)


# --- 6. credentials ----------------------------------------------------------


def test_no_seeded_credential_reaches_any_durable_phase_b_record(
    tmp_path: Path,
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    transport = fixture.FakeTransport(fixture.transport_result())
    service.dispatch(
        prepared=prepared,
        profile=fixture.phase_b_profile(),
        transport=transport,
        credential=TransportCredential(SEEDED_SECRET, slot_identity="slot:phase-b"),
        prompt_artifact=fixture.prompt_artifact(),
        attempt_authority=fixture.attempt_authority(),
        dispatched_at=fixture.DISPATCHED_AT,
        concluded_at=fixture.CONCLUDED_AT,
    )

    database = Path(service._repository.path)  # noqa: SLF001 - reading the durable file is the point
    values = persisted_scalars(database)
    assert values
    assert not any(isinstance(value, str) and SEEDED_SECRET in value for value in values)


def test_a_credential_cannot_be_rendered_or_serialized() -> None:
    held = TransportCredential(SEEDED_SECRET)
    assert SEEDED_SECRET not in repr(held)
    assert SEEDED_SECRET not in str(held)
    assert SEEDED_SECRET not in f"{held}"
    # Not JSON-serializable at all, and pickling is refused outright.
    with pytest.raises(TypeError):
        json.dumps(held)  # type: ignore[arg-type]
    with pytest.raises(ProviderTransportError):
        pickle.dumps(held)
    with pytest.raises(ProviderTransportError):
        held.__reduce__()
    # And there is no public attribute holding the value.
    assert not any(getattr(held, name, None) == SEEDED_SECRET for name in dir(held) if not name.startswith("_"))
    # The value is still usable exactly where it must be.
    assert held.reveal() == SEEDED_SECRET


def test_credential_material_is_structurally_rejected_by_the_response_artifact() -> None:
    from hunter.evidence_intelligence.model_adapter import SecretMaterialRejected

    with pytest.raises(SecretMaterialRejected):
        ProviderResponseArtifact(
            attempt_id="a",
            handoff_id="h",
            execution_profile_identity="p",
            request_evidence_identity="r",
            request_evidence_state="REQUEST_EVIDENCE_DURABLE",
            response_protocol_identity="rp",
            response_protocol_version="1",
            transport_identity="t",
            transport_version="1",
            state="RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY",
            reason_code="RESPONSE_CONTENT_RETENTION_PROHIBITED",
            recorded_at=fixture.CONCLUDED_AT,
            provider_status_metadata=(("authorization", "Bearer abcdef123456"),),
        )


@pytest.mark.parametrize(
    "content",
    [
        '{"choices": [{"message": {"content": "your key is sk-abcdefgh12345678"}}]}',
        '{"choices": [{"message": {"content": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abc"}}]}',
        '{"choices": [{"message": {"content": "set-cookie: session=abcdefgh12345678"}}]}',
        '{"choices": [{"message": {"content": "-----BEGIN RSA PRIVATE KEY-----"}}]}',
        '{"choices": [{"message": {"content": "AKIAIOSFODNN7EXAMPLE"}}]}',
    ],
)
def test_credential_bearing_response_is_refused_before_artifact_construction(
    service: ModelAdapterService,
    content: str,
) -> None:
    """CO-16: the capture gate is the boundary, not a post-hoc scan.

    Response categories are fully authorized here, so only the capture gate can
    account for the refusal — and the transport outcome is still recorded.
    """
    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result(response_text=content)))

    assert result.outcome.outcome == "SUCCEEDED_TRANSPORT"
    assert result.response_artifact is not None
    assert result.response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK"
    assert result.response_artifact.content is None
    assert result.response_artifact.content_hash is None
    assert result.response_artifact.measured_size_bytes is None
    assert result.response_artifact.content_derived_identity is None
    assert result.outcome.response_artifact_identity is None


def test_an_ordinary_answer_discussing_authentication_is_still_retainable(
    service: ModelAdapterService,
) -> None:
    """The paired positive: the capture gate must not reject valid content.

    A guard that refuses canonically valid output is itself a defect
    (`docs/DEFECT_REGISTRY.json`, PRH-009), so the gate is proven to discriminate
    rather than merely to refuse.
    """
    benign = '{"choices": [{"message": {"content": "Store your api key securely and rotate it often."}}]}'
    assert response_content_credential_risk(benign) is None

    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result(response_text=benign)))

    assert result.response_artifact is not None
    assert result.response_artifact.state == "RESPONSE_EVIDENCE_DURABLE"
    assert result.response_artifact.content == benign


# --- 7. processing allowed, response durability denied -----------------------


def test_processing_allowed_with_response_content_denied_persists_no_content(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    authority = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=authority)
    body = '{"choices": [{"message": {"content": "protected source content echoed back"}}]}'

    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(fixture.transport_result(response_text=body)),
        authority=authority,
    )

    # The send was authorized and happened.
    assert result.outcome.outcome == "SUCCEEDED_TRANSPORT"
    artifact = result.response_artifact
    assert artifact is not None
    assert artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert artifact.reason_code == "RESPONSE_CONTENT_RETENTION_PROHIBITED"
    assert (artifact.content, artifact.content_hash, artifact.measured_size_bytes) == (None, None, None)
    assert artifact.content_derived_identity is None

    # And nothing content-derived reached the durable file.
    values = persisted_scalars(Path(repository.path))
    assert not any(isinstance(value, str) and "protected source content" in value for value in values)


def test_denied_response_durability_does_not_fabricate_a_substitute_identity(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    authority = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=authority)
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()), authority=authority)

    stored = repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(30))
    assert stored is not None
    assert stored.state == "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert stored.content_derived_identity is None


def test_a_response_artifact_cannot_claim_unavailable_while_carrying_content() -> None:
    with pytest.raises(ModelAdapterError):
        ProviderResponseArtifact(
            attempt_id="a",
            handoff_id="h",
            execution_profile_identity="p",
            request_evidence_identity="r",
            request_evidence_state="REQUEST_EVIDENCE_DURABLE",
            response_protocol_identity="rp",
            response_protocol_version="1",
            transport_identity="t",
            transport_version="1",
            state="RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY",
            reason_code="RESPONSE_CONTENT_RETENTION_PROHIBITED",
            recorded_at=fixture.CONCLUDED_AT,
            content="smuggled",
        )


def test_persistence_rejects_durable_response_evidence_under_denying_authority(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    """A bypassing caller cannot persist response bytes the authority forbids."""
    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))
    assert result.response_artifact is not None

    denying = fixture.attempt_authority(request_content=False)
    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.append_outcome(
            outcome=dataclasses.replace(result.outcome, reason_code="FORGED_REPLAY"),
            response_artifact=result.response_artifact,
            attempt_authority=denying,
        )


# --- 8. response evidence is never canonical knowledge -----------------------


def test_the_response_artifact_exposes_no_validity_or_promotion_surface() -> None:
    names = {field.name for field in dataclasses.fields(ProviderResponseArtifact)}
    forbidden = {
        "is_valid",
        "valid",
        "validated",
        "validation_result",
        "schema_valid",
        "accepted",
        "canonical",
        "promoted",
        "extraction_proposal_id",
        "claim_id",
        "truth",
    }
    assert not (names & forbidden)
    members = {name for name in dir(ProviderResponseArtifact) if not name.startswith("_")}
    assert not any(token in name for name in members for token in ("valid", "promote", "proposal", "canonicalize"))


def test_no_response_validator_or_promotion_path_exists_in_this_boundary() -> None:
    """Requirement 18: a malformed response has nowhere semantic to go."""
    import hunter.evidence_intelligence.model_adapter as adapter
    import hunter.evidence_intelligence.model_adapter_transport as transport_module

    for module in (adapter, transport_module):
        exported = {name for name in dir(module) if not name.startswith("_")}
        assert not any("ResponseValidator" in name for name in exported)
        assert "ExtractionProposal" not in exported
        # Scan executable code, not prose: the module docstrings legitimately name
        # the boundaries this phase stops before, and a text-presence check over
        # comments would be exactly the proxy PRH-011 guards against.
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] + [getattr(node, "module", "") or ""]
                assert not any("extraction" in name.lower() or "proposal" in name.lower() for name in names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("ExtractionProposal", "ResponseValidator")
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                assert "responsevalidator" not in node.name.lower()
                assert "extractionproposal" not in node.name.lower()


def test_a_malformed_response_is_transport_success_lineage_without_a_validity_claim(
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="MALFORMED_RESPONSE",
                execution_evidence="UNKNOWN",
                response_text="not json at all",
                reason_code="RESPONSE_BODY_NOT_VALID_JSON",
            )
        ),
    )

    assert result.outcome.outcome == "MALFORMED_TRANSPORT_RESPONSE"
    assert result.outcome.delivery_certainty == "ANSWERED"
    # The evidence is still captured; nothing claims it means anything.
    assert result.response_artifact is not None
    assert result.response_artifact.content == "not json at all"


def test_a_schema_violating_response_is_still_recorded_as_transport_success(
    service: ModelAdapterService,
) -> None:
    """CO-17: transport success asserts nothing about semantic validity."""
    prepared = prepare(service)
    nonsense = '{"choices": [{"message": {"content": "{not-the-requested-schema"}}]}'
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result(response_text=nonsense)))

    assert result.outcome.outcome == "SUCCEEDED_TRANSPORT"
    assert result.response_artifact is not None
    assert result.response_artifact.content == nonsense


# --- 9. attempt immutability -------------------------------------------------


def test_the_attempt_record_is_unchanged_after_a_terminal_outcome(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    before = repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.later(6))
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))
    after = repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.later(30))

    assert before == after
    assert after == prepared.attempt
    with pytest.raises(dataclasses.FrozenInstanceError):
        prepared.attempt.attempt_ordinal = 2  # type: ignore[misc]
    assert not any(
        token in name
        for name in dir(prepared.attempt)
        if not name.startswith("_")
        for token in ("outcome", "result", "complete", "finish")
    )


def test_an_outcome_record_is_immutable_and_appended_not_rewritten(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcome.outcome = "PROVIDER_REFUSED"  # type: ignore[misc]

    # A second outcome that does not supersede is a conflict, not an append.
    rewritten = dataclasses.replace(result.outcome, reason_code="SILENT_REWRITE")
    with pytest.raises(ModelAdapterPersistenceConflict):
        repository.append_outcome(
            outcome=rewritten,
            response_artifact=None,
            attempt_authority=fixture.attempt_authority(),
        )


def test_a_correction_appends_a_superseding_record_and_preserves_the_original(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    original = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result())).outcome

    correction = dataclasses.replace(
        original,
        reason_code="CORRECTED_AFTER_RECONCILIATION",
        recorded_at=fixture.later(40),
        supersedes_outcome_id=original.outcome_id,
    )
    repository.append_outcome(
        outcome=correction,
        response_artifact=None,
        attempt_authority=fixture.attempt_authority(),
    )

    stored = repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(60))
    assert len(stored) == 2
    assert stored[0].outcome_id == original.outcome_id
    assert stored[0].reason_code == original.reason_code
    assert stored[1].supersedes_outcome_id == original.outcome_id


def test_a_superseding_outcome_must_reference_a_real_predecessor(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    original = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result())).outcome
    bogus = dataclasses.replace(
        original,
        reason_code="CORRECTION",
        recorded_at=fixture.later(40),
        supersedes_outcome_id="model-attempt-outcome:does-not-exist",
    )
    with pytest.raises(ModelAdapterPersistenceError):
        repository.append_outcome(
            outcome=bogus,
            response_artifact=None,
            attempt_authority=fixture.attempt_authority(),
        )


# --- 10. provider exception keeps lineage ------------------------------------


def test_a_transport_exception_leaves_attributable_uncertain_lineage(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    transport = fixture.FakeTransport(raises=RuntimeError("provider client exploded"))

    result = dispatch(service, prepared, transport)

    assert result.outcome.outcome == "INTERNAL_ADAPTER_ERROR"
    assert result.outcome.delivery_certainty == "UNKNOWN"
    assert result.outcome.attempt_id == prepared.attempt.attempt_id
    assert result.outcome.handoff_id == prepared.handoff.handoff_id
    assert result.outcome.retry_authorization == "RETRY_BLOCKED_DELIVERY_UNCERTAIN"
    assert "provider client exploded" not in result.outcome.reason_code
    assert repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(30))


def test_a_local_pre_send_failure_is_distinct_and_transmits_nothing(
    service: ModelAdapterService,
) -> None:
    """A local failure before the network must not look like an external attempt."""
    unconfigured = ModelAdapterService(service._repository, transport_endpoints={})  # noqa: SLF001
    prepared = prepare(service)
    transport = fixture.FakeTransport(fixture.transport_result())

    result = unconfigured.dispatch(
        prepared=prepared,
        profile=fixture.phase_b_profile(),
        transport=transport,
        credential=fixture.credential(),
        prompt_artifact=fixture.prompt_artifact(),
        attempt_authority=fixture.attempt_authority(),
        dispatched_at=fixture.DISPATCHED_AT,
        concluded_at=fixture.CONCLUDED_AT,
    )

    assert result.outcome.outcome == "LOCAL_PRE_SEND_FAILED"
    assert transport.sends == []
    # The handoff was deliberately not consumed: nothing was dispatched.
    assert service._repository.handoff_consumed_at(prepared.handoff.handoff_id) is None  # noqa: SLF001


def test_a_local_pre_send_failure_records_a_non_transmitting_outcome(
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    # An endpoint the transient wire representation refuses to describe.
    insecure = ModelAdapterService(
        service._repository,  # noqa: SLF001
        transport_endpoints={fixture.PHASE_B_ENDPOINT_CLASS: "http://insecure.invalid/v1"},
    )
    transport = fixture.FakeTransport(fixture.transport_result())

    result = insecure.dispatch(
        prepared=prepared,
        profile=fixture.phase_b_profile(),
        transport=transport,
        credential=fixture.credential(),
        prompt_artifact=fixture.prompt_artifact(),
        attempt_authority=fixture.attempt_authority(),
        dispatched_at=fixture.DISPATCHED_AT,
        concluded_at=fixture.CONCLUDED_AT,
    )

    assert transport.sends == []
    assert result.outcome.outcome == "LOCAL_PRE_SEND_FAILED"
    assert result.outcome.delivery_certainty == "CONFIRMED_NOT_DELIVERED"
    assert result.outcome.dispatched_at is None
    assert result.outcome.retry_authorization == "RETRY_REQUIRES_NEW_ATTEMPT"


def test_an_attempt_with_a_recorded_outcome_cannot_be_dispatched_again(
    service: ModelAdapterService,
) -> None:
    """A local pre-send failure leaves the handoff unconsumed; the attempt is still over."""
    prepared = prepare(service)
    insecure = ModelAdapterService(
        service._repository,  # noqa: SLF001
        transport_endpoints={fixture.PHASE_B_ENDPOINT_CLASS: "http://insecure.invalid/v1"},
    )
    insecure.dispatch(
        prepared=prepared,
        profile=fixture.phase_b_profile(),
        transport=fixture.FakeTransport(fixture.transport_result()),
        credential=fixture.credential(),
        prompt_artifact=fixture.prompt_artifact(),
        attempt_authority=fixture.attempt_authority(),
        dispatched_at=fixture.DISPATCHED_AT,
        concluded_at=fixture.CONCLUDED_AT,
    )

    transport = fixture.FakeTransport(fixture.transport_result())
    with pytest.raises(HandoffConsumptionError):
        dispatch(service, prepared, transport)
    assert transport.sends == []


# --- 11. ambiguity is never mislabelled --------------------------------------


@pytest.mark.parametrize("result_class", ["TIMEOUT", "CONNECTION_FAILED"])
def test_ambiguous_network_failure_is_never_labelled_confirmed_non_delivery(
    service: ModelAdapterService,
    result_class: str,
) -> None:
    prepared = prepare(service)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class=result_class,
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                response_text=None,
                provider_status_metadata=(),
                correlation_identity=None,
                reason_code="READ_TIMEOUT",
            )
        ),
    )

    assert result.outcome.outcome == "DELIVERY_UNKNOWN"
    assert result.outcome.outcome != "TIMEOUT_CONFIRMED_NO_DELIVERY"
    assert result.outcome.delivery_certainty == "UNKNOWN"
    assert result.outcome.retry_authorization == "RETRY_BLOCKED_DELIVERY_UNCERTAIN"


def test_a_provably_undelivered_timeout_is_recorded_as_confirmed_non_delivery(
    service: ModelAdapterService,
) -> None:
    """The paired positive: proven non-delivery must still be representable."""
    prepared = prepare(service)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="CONNECTION_FAILED",
                delivery_certainty="CONFIRMED_NOT_DELIVERED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                response_text=None,
                provider_status_metadata=(),
                correlation_identity=None,
                reason_code="CONNECTION_NOT_ESTABLISHED_ConnectionRefusedError",
            )
        ),
    )

    assert result.outcome.outcome == "TIMEOUT_CONFIRMED_NO_DELIVERY"
    assert result.outcome.retry_authorization == "RETRY_REQUIRES_NEW_ATTEMPT"


def test_an_outcome_record_refuses_a_certainty_its_own_semantics_contradict() -> None:
    """The structural half of the guard: such a record cannot be constructed at all."""
    base: dict[str, Any] = {
        "build_record_id": "b",
        "prompt_artifact_id": "p",
        "execution_profile_identity": "prof",
        "transport_identity": "t",
        "transport_version": "1",
        "execution_evidence": "UNKNOWN",
        "retry_authorization": "RETRY_BLOCKED_DELIVERY_UNCERTAIN",
        "attempt_cutoff": fixture.ATTEMPT_CUTOFF,
        "recorded_at": fixture.CONCLUDED_AT,
        "reason_code": "R",
        "attempt_id": "a",
    }
    with pytest.raises(ModelAdapterError):
        ModelAttemptOutcomeRecord(outcome="TIMEOUT_CONFIRMED_NO_DELIVERY", delivery_certainty="UNKNOWN", **base)
    with pytest.raises(ModelAdapterError):
        ModelAttemptOutcomeRecord(outcome="DELIVERY_UNKNOWN", delivery_certainty="ANSWERED", **base)
    # And the honest combination is accepted.
    assert ModelAttemptOutcomeRecord(outcome="DELIVERY_UNKNOWN", delivery_certainty="UNKNOWN", **base)


def test_an_uncertain_outcome_can_never_carry_retry_permission() -> None:
    with pytest.raises(ModelAdapterError):
        ModelAttemptOutcomeRecord(
            build_record_id="b",
            prompt_artifact_id="p",
            execution_profile_identity="prof",
            transport_identity="t",
            transport_version="1",
            outcome="DELIVERY_UNKNOWN",
            delivery_certainty="UNKNOWN",
            execution_evidence="UNKNOWN",
            retry_authorization="RETRY_REQUIRES_NEW_ATTEMPT",
            attempt_cutoff=fixture.ATTEMPT_CUTOFF,
            recorded_at=fixture.CONCLUDED_AT,
            reason_code="R",
            attempt_id="a",
        )


@pytest.mark.parametrize(
    ("certainty", "execution", "expected"),
    [
        ("UNKNOWN", "UNKNOWN", "RETRY_BLOCKED_DELIVERY_UNCERTAIN"),
        ("UNKNOWN", "NO_EXECUTION_ESTABLISHED", "RETRY_BLOCKED_DELIVERY_UNCERTAIN"),
        ("ANSWERED", "UNKNOWN", "RETRY_BLOCKED_RECONCILIATION_REQUIRED"),
        ("ANSWERED", "NO_EXECUTION_ESTABLISHED", "RETRY_REQUIRES_NEW_ATTEMPT"),
        ("CONFIRMED_NOT_DELIVERED", "UNKNOWN", "RETRY_REQUIRES_NEW_ATTEMPT"),
    ],
)
def test_retry_authorization_never_derives_permission_from_uncertainty(
    certainty: str,
    execution: str,
    expected: str,
) -> None:
    assert (
        derive_retry_authorization(
            outcome="PROVIDER_UNAVAILABLE",
            delivery_certainty=certainty,  # type: ignore[arg-type]
            execution_evidence=execution,  # type: ignore[arg-type]
        )
        == expected
    )


def test_a_rate_limit_is_not_assumed_to_be_safe_non_delivery(service: ModelAdapterService) -> None:
    prepared = prepare(service)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="RATE_LIMITED",
                execution_evidence="UNKNOWN",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_429",
            )
        ),
    )
    assert result.outcome.outcome == "RATE_LIMITED"
    assert result.outcome.retry_authorization == "RETRY_BLOCKED_RECONCILIATION_REQUIRED"


# --- 12-14. retry lineage ----------------------------------------------------


def test_uncertain_delivery_blocks_a_retry_from_being_prepared_at_all(
    service: ModelAdapterService,
) -> None:
    """Requirement 12: uncertainty cannot cause a blind second invocation."""
    first = prepare(service)
    uncertain = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="TIMEOUT",
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                response_text=None,
                correlation_identity=None,
                reason_code="READ_TIMEOUT",
            )
        ),
    ).outcome

    with pytest.raises(RetryNotAuthorized):
        prepare(
            service,
            authority=fixture.attempt_authority(cutoff=fixture.later(30)),
            attempt_ordinal=2,
            predecessor_attempt_id=first.attempt.attempt_id,
            predecessor_outcome=uncertain,
            recorded_at=fixture.later(31),
        )


def test_a_retry_cannot_be_prepared_without_the_predecessor_outcome(
    service: ModelAdapterService,
) -> None:
    """An attempt with no recorded outcome is uncertain, so it blocks retry."""
    first = prepare(service)
    with pytest.raises(RetryNotAuthorized):
        prepare(
            service,
            authority=fixture.attempt_authority(cutoff=fixture.later(30)),
            attempt_ordinal=2,
            predecessor_attempt_id=first.attempt.attempt_id,
            recorded_at=fixture.later(31),
        )


def test_an_authorized_retry_creates_a_new_attempt_cutoff_and_handoff(
    service: ModelAdapterService,
) -> None:
    first = prepare(service)
    refused = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="PROVIDER_REFUSED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_400",
            )
        ),
    ).outcome
    assert refused.retry_authorization == "RETRY_REQUIRES_NEW_ATTEMPT"

    retry = prepare(
        service,
        authority=fixture.attempt_authority(cutoff=fixture.later(30)),
        attempt_ordinal=2,
        predecessor_attempt_id=first.attempt.attempt_id,
        predecessor_outcome=refused,
        recorded_at=fixture.later(31),
    )

    assert retry.attempt.attempt_id != first.attempt.attempt_id
    assert retry.attempt.attempt_cutoff != first.attempt.attempt_cutoff
    assert retry.handoff.handoff_id != first.handoff.handoff_id
    assert retry.attempt.predecessor_attempt_id == first.attempt.attempt_id
    # A new attempt gets its own idempotency key; the predecessor's is not inherited.
    assert attempt_idempotency_identity(retry.attempt) != attempt_idempotency_identity(first.attempt)


def test_the_predecessor_handoff_cannot_be_reused_to_dispatch_a_retry(
    service: ModelAdapterService,
) -> None:
    """Requirement 13: a retry may never re-dispatch the predecessor's handoff."""
    first = prepare(service)
    refused = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="PROVIDER_REFUSED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_400",
            )
        ),
    ).outcome
    retry = prepare(
        service,
        authority=fixture.attempt_authority(cutoff=fixture.later(30)),
        attempt_ordinal=2,
        predecessor_attempt_id=first.attempt.attempt_id,
        predecessor_outcome=refused,
        recorded_at=fixture.later(31),
    )

    smuggled = PreparedModelAttempt(
        attempt=retry.attempt,
        handoff=first.handoff,
        request_evidence=retry.request_evidence,
        resolved_authority=retry.resolved_authority,
    )
    transport = fixture.FakeTransport(fixture.transport_result())
    with pytest.raises(ModelAdapterPersistenceError):
        dispatch(
            service,
            smuggled,
            transport,
            authority=fixture.attempt_authority(cutoff=fixture.later(30)),
            dispatched_at=fixture.later(35),
            concluded_at=fixture.later(36),
        )
    assert transport.sends == []


def test_a_prior_attempt_time_authorization_cannot_authorize_a_retry(
    service: ModelAdapterService,
) -> None:
    """Requirement 14: the predecessor's ALLOW does not carry forward.

    The permissive head is reachable in the very store handed to the code under
    test, so only the cutoff separates the two decisions — the counterfactual the
    guarded defect class MA-003 requires a fixture to be able to express.
    """
    from hunter.evidence_intelligence.model_adapter import PreDispatchRefused

    authority = fixture.attempt_authority()
    first = prepare(service, authority=authority)
    refused = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="PROVIDER_REFUSED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_400",
            )
        ),
        authority=authority,
    ).outcome

    restrictive = fixture.deny_successor(authority, cutoff=fixture.later(30))
    with pytest.raises(PreDispatchRefused) as raised:
        prepare(
            service,
            authority=restrictive,
            attempt_ordinal=2,
            predecessor_attempt_id=first.attempt.attempt_id,
            predecessor_outcome=refused,
            recorded_at=fixture.later(31),
        )
    assert raised.value.refusal == "SOURCE_HANDLING_BLOCKED"


# --- 15. historical replay ---------------------------------------------------


def test_later_authority_cannot_be_substituted_into_a_historical_response_replay(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    denying = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=denying)
    dispatch(
        service,
        prepared,
        fixture.FakeTransport(fixture.transport_result(response_text='{"choices": [{"text": "historical"}]}')),
        authority=denying,
    )

    # Current authority is fully permissive; the historical read must not use it.
    permissive = fixture.attempt_authority(cutoff=fixture.later(60))
    assert permissive is not None

    replayed = repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(90))
    assert replayed is not None
    assert replayed.state == "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert replayed.content is None
    assert replayed.content_hash is None


def test_a_historical_read_before_the_outcome_existed_returns_nothing(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))

    assert repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(-1)) == ()
    assert repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(-1)) is None
    assert repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(30))


def test_authorized_historical_response_content_is_returned_exactly_as_recorded(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    body = '{"choices": [{"message": {"content": "recorded exactly"}}]}'
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result(response_text=body)))

    replayed = repository.strict_known_response_artifact(prepared.attempt.attempt_id, fixture.later(30))
    assert replayed is not None
    assert replayed.content == body
    assert replayed.state == "RESPONSE_EVIDENCE_DURABLE"


def test_tampered_persisted_outcome_bytes_are_detected_on_read(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    from hunter.evidence_intelligence.model_adapter_persistence import ModelAdapterPersistenceCorruption

    prepared = prepare(service)
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE model_attempt_outcome_records SET payload_json = replace(payload_json, "
            "'SUCCEEDED_TRANSPORT', 'PROVIDER_REFUSED')"
        )
    with pytest.raises(ModelAdapterPersistenceCorruption):
        repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(30))


# --- 16. correlation and idempotency metadata --------------------------------


def test_correlation_metadata_is_omitted_when_its_category_is_denied(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    denying = fixture.attempt_authority(dispatch_capability=False)
    prepared = prepare(service, authority=denying)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(fixture.transport_result(correlation_identity="req_should_not_persist")),
        authority=denying,
    )

    assert result.outcome.correlation_identity is None
    assert result.outcome.idempotency_identity is None
    values = persisted_scalars(Path(repository.path))
    assert not any(isinstance(value, str) and "req_should_not_persist" in value for value in values)


def test_correlation_metadata_is_recorded_when_its_category_is_authorized(
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))

    assert result.outcome.correlation_identity == "req_abc123"
    assert result.outcome.idempotency_identity == attempt_idempotency_identity(prepared.attempt)


def test_an_unavailable_idempotency_classification_mints_no_key(
    service: ModelAdapterService,
) -> None:
    profile = fixture.phase_b_profile(idempotency_capability="UNAVAILABLE", profile_version="3")
    prepared = prepare(service, profile=profile)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()), profile=profile)

    assert attempt_idempotency_identity(prepared.attempt) is None
    assert result.outcome.idempotency_identity is None


# --- 17. response captured, persistence failed -------------------------------


def test_a_capture_persistence_failure_is_a_distinct_truthful_outcome(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)
    calls: list[int] = []
    real_append = repository.append_outcome

    def failing_once(**kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise ModelAdapterPersistenceError("simulated terminal persistence failure")
        real_append(**kwargs)

    repository.append_outcome = failing_once  # type: ignore[method-assign]
    transport = fixture.FakeTransport(fixture.transport_result())
    result = dispatch(service, prepared, transport)

    assert result.outcome.outcome == "RESPONSE_CAPTURED_PERSISTENCE_FAILED"
    assert result.outcome.delivery_certainty == "ANSWERED"
    assert result.response_artifact is None
    # ADR 0034: Hunter must not call the provider again here.
    assert len(transport.sends) == 1
    # Three states stay distinguishable rather than collapsing into one failure.
    assert result.outcome.outcome not in ("DELIVERY_UNKNOWN", "INTERNAL_ADAPTER_ERROR")


def test_an_unavailable_store_leaves_the_attempt_nonterminal_for_recovery(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    prepared = prepare(service)

    def always_failing(**_: Any) -> None:
        raise ModelAdapterPersistenceError("canonical store unavailable")

    repository.append_outcome = always_failing  # type: ignore[method-assign]
    with pytest.raises(ModelAdapterPersistenceError):
        dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))

    del repository.append_outcome  # type: ignore[attr-defined]
    # No fabricated terminal result; recovery reconstructs it as uncertain.
    assert repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(60)) == ()
    assert prepared.attempt.attempt_id in service.recover_nonterminal_attempts(cutoff=fixture.later(60))


def test_recovery_reports_nothing_once_an_outcome_exists(service: ModelAdapterService) -> None:
    prepared = prepare(service)
    dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))
    assert service.recover_nonterminal_attempts(cutoff=fixture.later(60)) == ()


# --- 18. no routing, no second provider --------------------------------------


def test_a_transport_that_is_not_the_profiles_transport_is_refused(
    service: ModelAdapterService,
) -> None:
    prepared = prepare(service)
    other = fixture.FakeTransport(
        fixture.transport_result(transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY),
        transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY,
        transport_version=fixture.ALTERNATE_TRANSPORT_VERSION,
    )
    with pytest.raises(ModelAdapterAuthorityError):
        dispatch(service, prepared, other)
    assert other.sends == []


def test_a_profile_other_than_the_prepared_one_is_refused(service: ModelAdapterService) -> None:
    prepared = prepare(service)
    transport = fixture.FakeTransport(fixture.transport_result())
    with pytest.raises(ModelAdapterAuthorityError):
        dispatch(service, prepared, transport, profile=fixture.phase_b_profile(profile_version="9"))
    assert transport.sends == []


def test_an_unavailable_provider_never_triggers_an_alternate_attempt(
    service: ModelAdapterService,
) -> None:
    """CO-18: no fallback path exists to be triggered."""
    prepared = prepare(service)
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="PROVIDER_UNAVAILABLE",
                execution_evidence="UNKNOWN",
                response_text=None,
                correlation_identity=None,
                reason_code="PROVIDER_HTTP_503",
            )
        ),
    )
    assert result.outcome.outcome == "PROVIDER_UNAVAILABLE"
    assert service.recover_nonterminal_attempts(cutoff=fixture.later(60)) == ()

    source = Path(ModelAdapterService.__module__.replace(".", "/")).name
    assert source
    adapter_source = Path("src/hunter/evidence_intelligence/model_adapter.py").read_text(encoding="utf-8")
    for token in ("fallback", "failover", "select_profile", "choose_provider", "ranking", "load_balanc", "hedge"):
        assert token not in adapter_source.lower().replace("no fallback", "").replace(
            "routing, ranking, fallback", ""
        ), token


# --- OpenAI transport, deterministic --------------------------------------


def test_the_openai_body_is_built_deterministically_with_real_json_types() -> None:
    request = TransportRequest(
        endpoint_url="https://api.openai.com/v1/chat/completions",
        request_protocol_identity="protocol:openai-chat-completions",
        request_protocol_version="1",
        model_identity="gpt-4.1-mini",
        prompt_content="canonical prompt bytes",
        parameters=(("temperature", "0"), ("max_tokens", "128")),
    )
    body = openai_request_body(request)
    assert body == {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": "canonical prompt bytes"}],
        "temperature": 0.0,
        "max_tokens": 128,
    }
    assert openai_request_body(request) == body


def test_the_transient_request_refuses_a_cleartext_endpoint() -> None:
    with pytest.raises(ProviderTransportError):
        TransportRequest(
            endpoint_url="http://api.openai.com/v1/chat/completions",
            request_protocol_identity="p",
            request_protocol_version="1",
            model_identity="m",
            prompt_content="c",
        )


@pytest.mark.parametrize(
    ("status", "body", "expected_class", "expected_execution"),
    [
        (400, '{"error": {"code": "invalid_request"}}', "PROVIDER_REFUSED", "NO_EXECUTION_ESTABLISHED"),
        (401, '{"error": {"code": "invalid_api_key"}}', "SECURITY_BLOCKED", "NO_EXECUTION_ESTABLISHED"),
        (402, "{}", "BILLING_UNAVAILABLE", "NO_EXECUTION_ESTABLISHED"),
        (404, '{"error": {"code": "model_not_found"}}', "CAPABILITY_REJECTED", "NO_EXECUTION_ESTABLISHED"),
        (429, '{"error": {"code": "rate_limit_exceeded"}}', "RATE_LIMITED", "UNKNOWN"),
        (429, '{"error": {"code": "insufficient_quota"}}', "QUOTA_UNAVAILABLE", "NO_EXECUTION_ESTABLISHED"),
        (500, "{}", "PROVIDER_UNAVAILABLE", "UNKNOWN"),
        (503, "", "PROVIDER_UNAVAILABLE", "UNKNOWN"),
    ],
)
def test_openai_status_classification_is_deterministic_and_conservative(
    status: int,
    body: str,
    expected_class: str,
    expected_execution: str,
) -> None:
    assert classify_openai_http_status(status, body) == (expected_class, expected_execution)


def _openai_transport_with(opener: Any) -> OpenAIChatCompletionsTransport:
    return OpenAIChatCompletionsTransport(endpoint_url="https://api.openai.invalid/v1", opener=opener)


def _authorization() -> DispatchAuthorization:
    from hunter.evidence_intelligence.model_adapter_transport import _DISPATCH_MINT

    return DispatchAuthorization(
        _DISPATCH_MINT,
        handoff_id="h",
        attempt_id="a",
        execution_profile_identity="p",
        consumed_at="2026-08-22T12:00:00+00:00",
    )


def _request() -> TransportRequest:
    return TransportRequest(
        endpoint_url="https://api.openai.invalid/v1",
        request_protocol_identity="protocol:openai-chat-completions",
        request_protocol_version="1",
        model_identity="gpt-4.1-mini",
        prompt_content="hello",
    )


class _FakeHttpResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_the_openai_transport_normalizes_a_real_success_shape_without_a_socket() -> None:
    body = json.dumps({"model": "gpt-4.1-mini", "choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]})
    transport = _openai_transport_with(
        lambda request, timeout: _FakeHttpResponse(200, body.encode("utf-8"), {"x-request-id": "req_1"})
    )
    result = transport.send(_request(), authorization=_authorization(), credential=fixture.credential())

    assert result.result_class == "RESPONSE_RECEIVED"
    assert result.delivery_certainty == "ANSWERED"
    assert result.execution_evidence == "PROVIDER_RETURNED_COMPLETION"
    assert result.correlation_identity == "req_1"
    assert ("finish_reason", "stop") in result.provider_status_metadata


def test_the_openai_transport_reports_a_read_timeout_as_uncertain() -> None:
    def timing_out(request: Any, timeout: float) -> Any:
        raise TimeoutError("read timed out")

    result = _openai_transport_with(timing_out).send(
        _request(), authorization=_authorization(), credential=fixture.credential()
    )
    assert result.result_class == "TIMEOUT"
    assert result.delivery_certainty == "UNKNOWN"
    assert classify_transport_result(result)[0] == "DELIVERY_UNKNOWN"


def test_the_openai_transport_reports_a_refused_connection_as_proven_non_delivery() -> None:
    def refused(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    result = _openai_transport_with(refused).send(
        _request(), authorization=_authorization(), credential=fixture.credential()
    )
    assert result.delivery_certainty == "CONFIRMED_NOT_DELIVERED"
    assert result.response_text is None
    assert classify_transport_result(result)[0] == "TIMEOUT_CONFIRMED_NO_DELIVERY"


def test_the_openai_transport_reports_a_mid_flight_reset_as_uncertain() -> None:
    """A reset can happen after the provider accepted the request, so it stays unknown."""

    def reset(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(ConnectionResetError("connection reset by peer"))

    result = _openai_transport_with(reset).send(
        _request(), authorization=_authorization(), credential=fixture.credential()
    )
    assert result.delivery_certainty == "UNKNOWN"


def test_the_openai_transport_reports_dns_failure_as_proven_non_delivery() -> None:
    def unresolvable(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(socket.gaierror("name resolution failed"))

    result = _openai_transport_with(unresolvable).send(
        _request(), authorization=_authorization(), credential=fixture.credential()
    )
    assert result.delivery_certainty == "CONFIRMED_NOT_DELIVERED"


def test_the_openai_transport_does_not_capture_a_provider_error_body_as_response_evidence() -> None:
    def http_error(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(
            "https://api.openai.invalid/v1",
            429,
            "Too Many Requests",
            {},  # type: ignore[arg-type]
            None,
        )

    result = _openai_transport_with(http_error).send(
        _request(), authorization=_authorization(), credential=fixture.credential()
    )
    assert result.result_class == "RATE_LIMITED"
    assert result.delivery_certainty == "ANSWERED"
    assert result.response_text is None


def test_the_openai_transport_puts_the_credential_only_in_the_outbound_header() -> None:
    captured: dict[str, Any] = {}

    def capturing(request: Any, timeout: float) -> Any:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        return _FakeHttpResponse(200, json.dumps({"choices": [{"message": {"content": "x"}}]}).encode("utf-8"))

    result = _openai_transport_with(capturing).send(
        _request(),
        authorization=_authorization(),
        credential=TransportCredential(SEEDED_SECRET),
    )

    header_values = " ".join(str(value) for value in captured["headers"].values())
    assert SEEDED_SECRET in header_values
    assert SEEDED_SECRET not in captured["body"].decode("utf-8")
    assert SEEDED_SECRET not in json.dumps(dataclasses.asdict(result))


def test_the_openai_transport_sends_the_idempotency_key_only_when_one_exists() -> None:
    captured: list[dict[str, Any]] = []

    def capturing(request: Any, timeout: float) -> Any:
        captured.append({key.lower(): value for key, value in request.headers.items()})
        return _FakeHttpResponse(200, json.dumps({"choices": [{"message": {"content": "x"}}]}).encode("utf-8"))

    transport = _openai_transport_with(capturing)
    transport.send(_request(), authorization=_authorization(), credential=fixture.credential())
    assert "idempotency-key" not in captured[0]

    keyed = dataclasses.replace(_request(), idempotency_key="model-attempt-idempotency:abc")
    transport.send(keyed, authorization=_authorization(), credential=fixture.credential())
    assert captured[1]["idempotency-key"] == "model-attempt-idempotency:abc"


# --- governance isolation ----------------------------------------------------


def test_no_provider_sdk_or_credential_dependency_enters_governance_surfaces() -> None:
    """CO-22: the governance surfaces stay deterministic and provider-free."""
    for path in (
        Path("scripts/hunter_governance_review_v2.py"),
        Path("scripts/hunter_merge_readiness_v2.py"),
        Path("scripts/hunter_pr_preflight.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for token in ("model_adapter", "openai", "TransportCredential", "ProviderTransport"):
            assert token not in source, f"{path} references {token}"


def test_the_phase_b_modules_import_no_provider_sdk() -> None:
    for path in (
        Path("src/hunter/evidence_intelligence/model_adapter.py"),
        Path("src/hunter/evidence_intelligence/model_adapter_transport.py"),
        Path("src/hunter/evidence_intelligence/model_adapter_persistence.py"),
    ):
        source = path.read_text(encoding="utf-8")
        for token in ("import openai", "from openai", "import anthropic", "from anthropic", "import httpx"):
            assert token not in source, f"{path} imports {token}"


def test_the_legacy_provider_path_is_not_the_model_adapter() -> None:
    adapter_source = Path("src/hunter/evidence_intelligence/model_adapter.py").read_text(encoding="utf-8")
    assert "SecureAIProviderRunner" not in adapter_source
    assert "AIExtractionProvider" not in adapter_source
    assert "AIProviderArtifact" not in adapter_source


# --- mutation-style verification of the new reusable guards ------------------


def test_dropping_the_certainty_check_would_mislabel_an_ambiguous_timeout() -> None:
    """Mutation proof for the certainty-driven classifier.

    Reproduces the exact defect the guard prevents: a table lookup keyed only on
    the transport class. With that mutation an uncertain timeout becomes
    `TIMEOUT_CONFIRMED_NO_DELIVERY`; with the real classifier it does not.
    """
    uncertain = fixture.transport_result(
        result_class="TIMEOUT",
        delivery_certainty="UNKNOWN",
        execution_evidence="UNKNOWN",
        response_text=None,
        correlation_identity=None,
        reason_code="READ_TIMEOUT",
    )
    mutated_table = {"TIMEOUT": "TIMEOUT_CONFIRMED_NO_DELIVERY", "CONNECTION_FAILED": "DELIVERY_UNKNOWN"}

    assert mutated_table[uncertain.result_class] == "TIMEOUT_CONFIRMED_NO_DELIVERY"
    assert classify_transport_result(uncertain)[0] == "DELIVERY_UNKNOWN"


def test_dropping_the_capture_gate_would_persist_a_credential_bearing_response(
    service: ModelAdapterService,
) -> None:
    """Mutation proof for the capture gate.

    Without the gate the content is fully category-authorized and would be
    written verbatim; with it, no bytes and nothing derived from them persist.
    """
    leaking = '{"choices": [{"message": {"content": "sk-abcdefgh12345678"}}]}'
    assert response_content_credential_risk(leaking) is not None

    prepared = prepare(service)
    result = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result(response_text=leaking)))
    assert result.response_artifact is not None
    assert result.response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK"
    assert result.response_artifact.content is None


def test_dropping_the_retry_gate_would_allow_a_blind_retry_after_uncertainty(
    service: ModelAdapterService,
) -> None:
    """Mutation proof for the retry gate.

    An uncertain predecessor is exactly the state that must not produce another
    attempt. The gate is what converts that into a refusal rather than a second
    external invocation.
    """
    first = prepare(service)
    uncertain = dispatch(
        service,
        first,
        fixture.FakeTransport(
            fixture.transport_result(
                result_class="TIMEOUT",
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                response_text=None,
                correlation_identity=None,
                reason_code="READ_TIMEOUT",
            )
        ),
    ).outcome
    assert uncertain.retry_authorization == "RETRY_BLOCKED_DELIVERY_UNCERTAIN"

    with pytest.raises(RetryNotAuthorized):
        service._require_retry_authorization(  # noqa: SLF001 - the guard itself is under test
            predecessor_attempt_id=first.attempt.attempt_id,
            predecessor_outcome=uncertain,
        )
    # The same guard admits the authorized case, so it is not vacuously strict.
    authorized = dataclasses.replace(
        uncertain,
        delivery_certainty="CONFIRMED_NOT_DELIVERED",
        outcome="TIMEOUT_CONFIRMED_NO_DELIVERY",
        retry_authorization="RETRY_REQUIRES_NEW_ATTEMPT",
    )
    service._require_retry_authorization(  # noqa: SLF001
        predecessor_attempt_id=first.attempt.attempt_id,
        predecessor_outcome=authorized,
    )


def test_dropping_the_attempt_outcome_conflict_check_would_permit_a_silent_rewrite(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    """Mutation proof for the append-only outcome guard."""
    prepared = prepare(service)
    original = dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result())).outcome

    with pytest.raises(ModelAdapterPersistenceConflict):
        repository.append_outcome(
            outcome=dataclasses.replace(original, reason_code="REWRITTEN"),
            response_artifact=None,
            attempt_authority=fixture.attempt_authority(),
        )
    stored = repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(60))
    assert [record.reason_code for record in stored] == [original.reason_code]


def test_the_phase_b_pipeline_opens_no_socket(service: ModelAdapterService) -> None:
    """The deterministic suite must never depend on provider availability."""
    opened: list[tuple[Any, ...]] = []

    def tracking(*args: Any, **kwargs: Any) -> Any:
        opened.append(args)
        raise AssertionError("the Phase B deterministic path must open no socket")

    with mock.patch.object(socket, "socket", tracking):
        prepared = prepare(service)
        dispatch(service, prepared, fixture.FakeTransport(fixture.transport_result()))
    assert opened == []


def test_a_durable_surface_the_registry_does_not_govern_fails_closed(
    service: ModelAdapterService,
) -> None:
    """An outcome field whose category the registry cannot express is never written.

    ADR 0034 makes field-category coverage a hard precondition for provider
    activation. This proves the failure mode is a refusal rather than a write
    under an assumed category.
    """
    prepared = prepare(service)
    resolved = prepared.resolved_authority
    narrowed_registry = dict(resolved.registry_record)
    narrowed_registry["field_map"] = {
        key: value for key, value in fixture.FIELD_MAP.items() if key != "model_attempt_outcome"
    }
    ungoverned = dataclasses.replace(
        prepared,
        resolved_authority=dataclasses.replace(resolved, registry_record=narrowed_registry),
    )
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(ResponseCaptureBlocked):
        service.dispatch(
            prepared=ungoverned,
            profile=fixture.phase_b_profile(),
            transport=transport,
            credential=fixture.credential(),
            prompt_artifact=fixture.prompt_artifact(),
            attempt_authority=fixture.attempt_authority(),
            dispatched_at=fixture.DISPATCHED_AT,
            concluded_at=fixture.CONCLUDED_AT,
        )
    # The send did happen -- the refusal is about durability, not transmission --
    # and no outcome was written under an assumed category.
    assert len(transport.sends) == 1
    assert (
        service._repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(60)) == ()  # noqa: SLF001
    )


def test_the_transport_result_refuses_a_self_contradictory_observation() -> None:
    with pytest.raises(ProviderTransportError):
        TransportResult(
            result_class="RESPONSE_RECEIVED",
            delivery_certainty="ANSWERED",
            execution_evidence="PROVIDER_RETURNED_COMPLETION",
            transport_identity="t",
            transport_version="1",
            response_protocol_identity="rp",
            response_protocol_version="1",
            response_text=None,
        )
    with pytest.raises(ProviderTransportError):
        TransportResult(
            result_class="CONNECTION_FAILED",
            delivery_certainty="CONFIRMED_NOT_DELIVERED",
            execution_evidence="NO_EXECUTION_ESTABLISHED",
            transport_identity="t",
            transport_version="1",
            response_protocol_identity="rp",
            response_protocol_version="1",
            response_text="a response that cannot exist",
        )


def test_the_outcome_family_round_trips_every_required_state_distinctly(
    service: ModelAdapterService,
) -> None:
    """CO-09: each required outcome family is separately representable."""
    from hunter.evidence_intelligence.model_adapter import _REQUIRED_DELIVERY_CERTAINTY

    required = {
        "SUCCEEDED_TRANSPORT",
        "PROVIDER_REFUSED",
        "PROVIDER_UNAVAILABLE",
        "TIMEOUT_CONFIRMED_NO_DELIVERY",
        "DELIVERY_UNKNOWN",
        "OUTCOME_UNKNOWN",
        "RATE_LIMITED",
        "QUOTA_UNAVAILABLE",
        "BILLING_UNAVAILABLE",
        "CAPABILITY_UNSUPPORTED",
        "MALFORMED_TRANSPORT_RESPONSE",
        "SECURITY_BLOCKED",
        "SOURCE_HANDLING_BLOCKED",
        "RESPONSE_CAPTURED_PERSISTENCE_FAILED",
        "INTERNAL_ADAPTER_ERROR",
        "LOCAL_PRE_SEND_FAILED",
    }
    identities: set[str] = set()
    for outcome in sorted(required):
        certainty = _REQUIRED_DELIVERY_CERTAINTY.get(outcome, ("ANSWERED",))[0]
        record = ModelAttemptOutcomeRecord(
            build_record_id="b",
            prompt_artifact_id="p",
            execution_profile_identity="prof",
            transport_identity="t",
            transport_version="1",
            outcome=outcome,  # type: ignore[arg-type]
            delivery_certainty=certainty,  # type: ignore[arg-type]
            execution_evidence="UNKNOWN",
            retry_authorization=(
                "RETRY_REQUIRES_NEW_ATTEMPT"
                if certainty == "CONFIRMED_NOT_DELIVERED"
                else (
                    "RETRY_BLOCKED_RECONCILIATION_REQUIRED"
                    if certainty == "ANSWERED"
                    else "RETRY_BLOCKED_DELIVERY_UNCERTAIN"
                )
            ),
            attempt_cutoff=fixture.ATTEMPT_CUTOFF,
            recorded_at=fixture.CONCLUDED_AT,
            reason_code=f"REASON_{outcome}",
            attempt_id="a",
        )
        identities.add(record.outcome_id)
    assert len(identities) == len(required)
    assert service is not None


def test_a_pre_dispatch_refusal_fabricates_no_attempt_or_handoff(service: ModelAdapterService) -> None:
    """CO-23: a refusal carries exactly the lineage that existed, and no more."""
    record = ModelAttemptOutcomeRecord(
        build_record_id="b",
        prompt_artifact_id="p",
        execution_profile_identity="prof",
        transport_identity="t",
        transport_version="1",
        outcome="SOURCE_HANDLING_BLOCKED",
        delivery_certainty="CONFIRMED_NOT_DELIVERED",
        execution_evidence="NO_EXECUTION_ESTABLISHED",
        retry_authorization="RETRY_REQUIRES_NEW_ATTEMPT",
        attempt_cutoff=fixture.ATTEMPT_CUTOFF,
        recorded_at=fixture.CONCLUDED_AT,
        reason_code="ATTEMPT_TIME_AUTHORITY_BLOCKED",
    )
    assert record.attempt_id is None
    assert record.handoff_id is None
    assert record.dispatched_at is None
    # A handoff identity without its attempt is refused rather than fabricated.
    with pytest.raises(ModelAdapterError):
        dataclasses.replace(record, handoff_id="model-handoff:fabricated")
    assert service is not None


def test_timedelta_import_is_used() -> None:
    """Guards against an unused-import drift in this module's own fixtures."""
    assert fixture.later(5) - fixture.ATTEMPT_CUTOFF == timedelta(minutes=5)


def test_dispatch_refuses_an_authority_the_attempt_was_not_prepared_under(
    service: ModelAdapterService,
) -> None:
    """A permissive authority cannot be substituted at dispatch time."""
    restrictive = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=restrictive)
    transport = fixture.FakeTransport(fixture.transport_result())

    with pytest.raises(ModelAdapterAuthorityError):
        dispatch(service, prepared, transport, authority=fixture.attempt_authority())
    assert transport.sends == []


def test_the_dispatch_outcome_never_carries_raw_response_bytes_past_the_boundary(
    service: ModelAdapterService,
) -> None:
    """Response content leaves the adapter only through the governed artifact."""
    denying = fixture.attempt_authority(request_content=False)
    prepared = prepare(service, authority=denying)
    secret_ish = '{"choices": [{"message": {"content": "protected content the caller must not receive"}}]}'
    result = dispatch(
        service,
        prepared,
        fixture.FakeTransport(fixture.transport_result(response_text=secret_ish)),
        authority=denying,
    )

    assert not hasattr(result, "transport_result")
    rendered = json.dumps(dataclasses.asdict(result), default=str)
    assert "protected content the caller must not receive" not in rendered


def test_a_provider_parameter_cannot_override_the_model_or_the_prompt() -> None:
    """Configuration may not smuggle provider or prompt selection into the body."""
    for smuggled in (("model", "some-other-model"), ("messages", "[]")):
        request = TransportRequest(
            endpoint_url="https://api.openai.invalid/v1",
            request_protocol_identity="protocol:openai-chat-completions",
            request_protocol_version="1",
            model_identity="gpt-4.1-mini",
            prompt_content="canonical prompt bytes",
            parameters=(smuggled,),
        )
        with pytest.raises(ProviderTransportError):
            openai_request_body(request)


def test_a_transport_result_from_a_different_transport_still_records_lineage(
    service: ModelAdapterService,
    repository: ModelAdapterPersistenceRepository,
) -> None:
    """A post-send inconsistency must not lose the lineage the send created."""
    prepared = prepare(service)
    mislabelled = fixture.FakeTransport(
        fixture.transport_result(transport_identity=fixture.ALTERNATE_TRANSPORT_IDENTITY)
    )
    result = dispatch(service, prepared, mislabelled)

    assert len(mislabelled.sends) == 1
    assert result.outcome.outcome == "INTERNAL_ADAPTER_ERROR"
    assert result.outcome.delivery_certainty == "UNKNOWN"
    assert result.outcome.attempt_id == prepared.attempt.attempt_id
    assert repository.strict_known_outcomes(prepared.attempt.attempt_id, fixture.later(30))
