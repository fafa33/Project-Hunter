"""ADR 0035 Phase B authorization and semantic-execution adversarial tests."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import response_validator_phase_b_fixture as fixture
from evidence_pre_model_source_handling_fixture import publish_policy_successor

from hunter.evidence_intelligence import model_adapter
from hunter.evidence_intelligence.response_validator import (
    DeterministicJsonValidationRuntime,
    ResponseValidationAuthorization,
    ResponseValidationAuthorizationError,
    ResponseValidationExecutionError,
    ResponseValidationProfile,
    ResponseValidationRuleUnavailable,
    ResponseValidator,
    ValidationAttestationKind,
    ValidationInputMode,
    ValidationState,
)
from hunter.evidence_intelligence.response_validator_persistence import _profile_payload


def _authorize(harness: fixture.Harness):
    result = harness.validator.authorize_event(harness.allocation)
    assert result.authorization is not None
    assert result.refusal is None
    return result.authorization


def _execute(harness: fixture.Harness):
    return harness.validator.execute(_authorize(harness))


def test_legitimate_event_authorizes_and_executes_with_full_canonical_lineage(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)

    authorization = _authorize(harness)
    result = harness.validator.execute(authorization)

    coordinates = authorization.coordinates
    assert coordinates.validation_event_id == harness.allocation.validation_event_id
    assert coordinates.validation_cutoff == harness.allocation.validation_cutoff
    assert coordinates.profile_publication_id == harness.profile.publication_id
    assert coordinates.attempt_id == harness.prepared.attempt.attempt_id
    assert coordinates.handoff_id == harness.prepared.handoff.handoff_id
    assert coordinates.build_record_id == harness.build_result.build_record.build_record_id
    assert coordinates.prompt_artifact_id == harness.build_result.prompt_artifact.artifact_id
    assert coordinates.input_mode is ValidationInputMode.DURABLE
    assert result.outcome.state is ValidationState.VALID
    assert result.attestation.kind is ValidationAttestationKind.SUCCESS
    assert result.attestation.decision_id == result.outcome.semantic_outcome_id


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("validation_event_id", "response-validation-event:forged"),
        ("validation_cutoff", fixture.VALIDATION_CUTOFF + timedelta(days=1)),
        ("profile_publication_id", "response-validation-profile-publication:forged"),
        ("profile_version", 99),
        ("requested_output_contract_identity", "schema:forged"),
        ("requested_output_contract_version", "999"),
        ("source_handling_resolution_id", "source-resolution:forged"),
        ("source_handling_policy_record_id", "policy:forged"),
        ("attempt_id", "model-attempt:forged"),
        ("handoff_id", "model-handoff:forged"),
        ("response_capture_identity", "provider-response:forged"),
        ("build_record_id", "evidence-pre-model-build:forged"),
        ("prompt_artifact_id", "evidence-prompt-artifact:forged"),
        ("evidence_input_identity", "evidence-input:forged"),
    ),
)
def test_caller_cannot_substitute_any_authoritative_coordinate(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    harness = fixture.make_harness(tmp_path)
    canonical = _authorize(harness).coordinates
    forged = dataclasses.replace(canonical, **{field: replacement})

    with pytest.raises(ResponseValidationAuthorizationError, match=field):
        harness.validator.authorize_event(
            harness.allocation,
            asserted_coordinates=forged,
        )


def test_forged_event_identity_and_cutoff_are_rejected_before_semantic_execution(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    forged = dataclasses.replace(
        harness.allocation,
        validation_cutoff=harness.allocation.validation_cutoff + timedelta(seconds=1),
    )

    with pytest.raises(ResponseValidationAuthorizationError, match="identity or cutoff"):
        harness.validator.authorize_event(forged)


def test_authorization_and_attestation_are_not_caller_mintable(tmp_path: Path) -> None:
    coordinates = _authorize(fixture.make_harness(tmp_path)).coordinates

    with pytest.raises(ResponseValidationAuthorizationError, match="minted only"):
        ResponseValidationAuthorization(object(), coordinates=coordinates)


def test_unknown_forged_authorization_cannot_execute(tmp_path: Path) -> None:
    first = fixture.make_harness(tmp_path / "first")
    second = fixture.make_harness(tmp_path / "second")

    with pytest.raises(ResponseValidationExecutionError, match="unknown, forged, or substituted"):
        first.validator.execute(_authorize(second))


def test_later_profile_does_not_reinterpret_historical_event(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    historical = _authorize(harness)
    later_time = fixture.VALIDATION_CUTOFF + timedelta(hours=1)
    harness.profile_authority._clock = fixture.SequenceClock(later_time)  # noqa: SLF001
    current = harness.profile_authority.supersede_profile(
        predecessor_publication_id=harness.profile.publication_id,
        spec=fixture.profile_spec(),
        correction_reason="later profile publication",
    )

    retried = _authorize(harness)
    latest = harness.profile_authority.resolve_strict_known(
        profile_selector="evidence-response-validation",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
        trusted_cutoff=later_time,
    )

    assert current.profile_version == 2
    assert latest.profile == current
    assert historical.coordinates.profile_version == 1
    assert retried is historical
    assert retried.coordinates.profile_publication_id == harness.profile.publication_id


def test_historically_unknowable_profile_refuses_closed(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    harness.foundation._clock = fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(seconds=1))  # noqa: SLF001
    unknown = harness.foundation.allocate_base_validation(
        dataclasses.replace(
            harness.allocation.base_validation_key,
            requested_profile_selector="profile-not-known-at-cutoff",
        )
    )

    result = harness.validator.authorize_event(unknown)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE
    assert result.refusal.attestation.kind is ValidationAttestationKind.REFUSAL


def test_canonical_requested_output_mismatch_is_rejected_against_adr_0031(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    profile_time = fixture.PROFILE_TIME + timedelta(minutes=1)
    harness.profile_authority._clock = fixture.SequenceClock(profile_time)  # noqa: SLF001
    harness.profile_authority.publish_profile(
        fixture.profile_spec(
            requested_output_contract_identity="substituted-schema",
            requested_output_contract_version="9",
        )
    )
    harness.foundation._clock = fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(seconds=1))  # noqa: SLF001
    mismatched = harness.foundation.allocate_base_validation(
        dataclasses.replace(
            harness.allocation.base_validation_key,
            requested_output_contract_identity="substituted-schema",
            requested_output_contract_version="9",
        )
    )

    with pytest.raises(ResponseValidationAuthorizationError, match="ADR 0031 authority"):
        harness.validator.authorize_event(mismatched)


def test_ambiguous_profile_authority_refuses_closed(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    moment = fixture.PROFILE_TIME + timedelta(minutes=1)
    forged = ResponseValidationProfile(
        spec=fixture.profile_spec(),
        profile_version=2,
        applicable_from=moment,
        published_at=moment,
        known_at=moment,
        supersedes_publication_id="response-validation-profile-publication:missing",
        correction_reason="disconnected branch",
    )
    payload = _profile_payload(forged)
    with sqlite3.connect(harness.database) as connection:
        connection.execute(
            """
            INSERT INTO response_validation_profiles(
                publication_id, applicability_key, profile_version,
                applicable_from, published_at, known_at,
                supersedes_publication_id, payload_hash, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                forged.publication_id,
                forged.spec.applicability_key,
                forged.profile_version,
                forged.applicable_from.isoformat(),
                forged.published_at.isoformat(),
                forged.known_at.isoformat(),
                forged.supersedes_publication_id,
                hashlib.sha256(payload.encode()).hexdigest(),
                payload,
            ),
        )

    result = harness.validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE


def test_missing_required_upstream_authority_refuses_closed(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)

    class MissingCaptureRepository:
        def strict_known_response_capture(self, *_: object) -> None:
            return None

    validator = ResponseValidator(
        harness.foundation,
        model_adapter_repository=MissingCaptureRepository(),
        pre_model_repository=harness.pre_model_repository,
        source_handling_store=harness.source_authority.store,
        runtime=DeterministicJsonValidationRuntime(),
    )

    result = validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.INPUT_UNAVAILABLE


def test_unavailable_capability_and_rule_refuse_with_distinct_states(tmp_path: Path) -> None:
    capability = fixture.make_harness(
        tmp_path / "capability",
        profile_overrides={"validator_contract_version": "99"},
    )
    rule = fixture.make_harness(
        tmp_path / "rule",
        profile_overrides={"syntax_schema_rule_version": "99"},
    )

    capability_result = capability.validator.authorize_event(capability.allocation)
    rule_result = rule.validator.authorize_event(rule.allocation)

    assert capability_result.refusal is not None
    assert capability_result.refusal.refusal.state is ValidationState.VALIDATOR_CAPABILITY_UNKNOWN
    assert rule_result.refusal is not None
    assert rule_result.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE


def test_unreadable_output_contract_refusal_preserves_resolved_authority(tmp_path: Path) -> None:
    harness = fixture.make_harness(
        tmp_path,
        output_contract='{"type":"object","oneOf":[]}',
    )

    result = harness.validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE
    available = dict(result.refusal.refusal.available_authority)
    assert available["validation_event_id"] == harness.allocation.validation_event_id
    assert available["profile_publication_id"] == harness.profile.publication_id


def test_excessive_output_contract_nesting_refuses_before_recursive_parsing(tmp_path: Path) -> None:
    schema: dict[str, Any] = {}
    for _ in range(65):
        schema = {"items": schema}
    harness = fixture.make_harness(
        tmp_path,
        output_contract=json.dumps(schema, sort_keys=True, separators=(",", ":")),
    )

    result = harness.validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE
    assert result.refusal.refusal.reason_code == "REQUESTED_OUTPUT_CONTRACT_EXCEEDS_DEPTH_POLICY"


def test_source_handling_block_at_validation_cutoff_refuses_closed(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    publish_policy_successor(
        harness.source_authority,
        cutoff=fixture.VALIDATION_CUTOFF,
        processing="DENY",
        durable_dispositions_override=fixture.model_fixture.dispositions(request_content=True),
    )

    result = harness.validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.SOURCE_HANDLING_BLOCKED


def test_credential_risk_response_capture_refuses_as_security_block(tmp_path: Path) -> None:
    harness = fixture.make_harness(
        tmp_path,
        raw_response='{"answer":"sk-abcdefgh12345678","lineage":{},"evidence_references":[]}',
    )

    result = harness.validator.authorize_event(harness.allocation)

    assert harness.dispatch_result.response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK"
    assert not hasattr(harness.dispatch_result, "transient_response_access")
    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.SECURITY_BLOCKED


@pytest.mark.parametrize(
    ("raw_response", "response_factory", "profile_overrides", "expected"),
    (
        ("not-json", None, None, ValidationState.INVALID_SYNTAX),
        ('{"extra":NaN}', None, {"required_dimensions": ("SYNTAX",)}, ValidationState.INVALID_SYNTAX),
        ("[]", None, {"required_dimensions": ("SCHEMA",)}, ValidationState.INVALID_SCHEMA),
        (
            None,
            lambda prepared, span: {
                **fixture.valid_response(prepared, span),
                "answer": "",
            },
            {"required_dimensions": ("OUTPUT_CONTRACT",)},
            ValidationState.INVALID_OUTPUT_CONTRACT,
        ),
        (
            None,
            lambda prepared, span: {
                **fixture.valid_response(prepared, span),
                "lineage": {
                    **fixture.valid_response(prepared, span)["lineage"],
                    "attempt_id": "model-attempt:substituted",
                },
            },
            None,
            ValidationState.INVALID_LINEAGE,
        ),
        (
            None,
            lambda prepared, span: {
                **fixture.valid_response(prepared, span),
                "evidence_references": [{"span_id": span.span_id}],
            },
            None,
            ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
        ),
        (
            None,
            lambda prepared, span: {**fixture.valid_response(prepared, span), "partial": True},
            None,
            ValidationState.PARTIAL_RESPONSE,
        ),
        (
            None,
            lambda prepared, span: {
                **fixture.valid_response(prepared, span),
                "evidence_references": [
                    {"span_id": span.span_id, "content_hash": span.text_hash},
                    {"span_id": span.span_id, "content_hash": "different-hash"},
                ],
            },
            None,
            ValidationState.EVIDENCE_AMBIGUOUS,
        ),
        (
            None,
            lambda prepared, span: {**fixture.valid_response(prepared, span), "tool_calls": []},
            None,
            ValidationState.SECURITY_BLOCKED,
        ),
    ),
)
def test_semantic_categories_map_to_closed_states(
    tmp_path: Path,
    raw_response: str | None,
    response_factory: Any,
    profile_overrides: dict[str, Any] | None,
    expected: ValidationState,
) -> None:
    harness = fixture.make_harness(
        tmp_path,
        raw_response=raw_response,
        response_factory=response_factory,
        profile_overrides=profile_overrides,
    )

    assert _execute(harness).outcome.state is expected


class FailingRuntime(DeterministicJsonValidationRuntime):
    def evaluate(self, **_: Any):
        raise RuntimeError("nondurable internal detail")


class MissingExecutionRuleRuntime(DeterministicJsonValidationRuntime):
    def evaluate(self, **_: Any):
        raise ResponseValidationRuleUnavailable("rule disappeared")


class CountingRuntime(DeterministicJsonValidationRuntime):
    def __init__(self) -> None:
        object.__setattr__(self, "calls", 0)

    def evaluate(self, **kwargs: Any):
        object.__setattr__(self, "calls", self.calls + 1)
        return super().evaluate(**kwargs)


@pytest.mark.parametrize(
    ("runtime", "expected"),
    (
        (FailingRuntime(), ValidationState.VALIDATOR_ERROR),
        (MissingExecutionRuleRuntime(), ValidationState.RULE_UNAVAILABLE),
    ),
)
def test_execution_failures_map_deterministically_without_exception_detail(
    tmp_path: Path,
    runtime: DeterministicJsonValidationRuntime,
    expected: ValidationState,
) -> None:
    harness = fixture.make_harness(tmp_path, runtime=runtime)

    first = _execute(harness)
    second = harness.validator.execute(_authorize(harness))

    assert first is second
    assert first.outcome.state is expected
    assert "nondurable" not in repr(first)
    assert "disappeared" not in repr(first)


def test_excessive_json_nesting_maps_to_resource_rule_unavailable(tmp_path: Path) -> None:
    raw_response = "[" * 65 + "0" + "]" * 65
    harness = fixture.make_harness(tmp_path, raw_response=raw_response)

    result = _execute(harness)

    assert result.outcome.state is ValidationState.RULE_UNAVAILABLE
    assert result.outcome.findings[0].reason_code == "EXECUTABLE_VALIDATION_RULE_UNAVAILABLE"


def test_unrepresentable_json_number_maps_to_resource_rule_unavailable(tmp_path: Path) -> None:
    raw_response = "1e" + "9" * 64
    harness = fixture.make_harness(tmp_path, raw_response=raw_response)

    result = _execute(harness)

    assert result.outcome.state is ValidationState.RULE_UNAVAILABLE
    assert result.outcome.findings[0].reason_code == "EXECUTABLE_VALIDATION_RULE_UNAVAILABLE"


def test_integral_decimal_satisfies_json_schema_integer(tmp_path: Path) -> None:
    harness = fixture.make_harness(
        tmp_path,
        output_contract='{"type":"integer"}',
        raw_response="1.0",
        profile_overrides={"required_dimensions": ("SCHEMA",)},
    )

    assert _execute(harness).outcome.state is ValidationState.VALID


@pytest.mark.parametrize("raw_response", ("1.5", "true"))
def test_non_integral_decimal_and_boolean_do_not_satisfy_json_schema_integer(
    tmp_path: Path,
    raw_response: str,
) -> None:
    harness = fixture.make_harness(
        tmp_path,
        output_contract='{"type":"integer"}',
        raw_response=raw_response,
        profile_overrides={"required_dimensions": ("SCHEMA",)},
    )

    assert _execute(harness).outcome.state is ValidationState.INVALID_SCHEMA


def test_huge_valid_json_integer_is_lossless_not_invalid_syntax(tmp_path: Path) -> None:
    raw_response = "9" * 5000
    harness = fixture.make_harness(
        tmp_path,
        raw_response=raw_response,
        output_contract='{"type":"integer"}',
        profile_overrides={"required_dimensions": ("SYNTAX", "SCHEMA")},
    )

    result = _execute(harness)

    assert result.outcome.state is ValidationState.VALID
    assert all(finding.state is not ValidationState.INVALID_SYNTAX for finding in result.outcome.findings)


@pytest.mark.parametrize(
    ("output_contract", "raw_response"),
    (
        ('{"enum":[1]}', "true"),
        ('{"const":0}', "false"),
        ('{"enum":[1e400]}', "1e401"),
    ),
)
def test_output_contract_enum_and_const_preserve_json_boolean_type(
    tmp_path: Path,
    output_contract: str,
    raw_response: str,
) -> None:
    harness = fixture.make_harness(
        tmp_path,
        output_contract=output_contract,
        raw_response=raw_response,
        profile_overrides={"required_dimensions": ("OUTPUT_CONTRACT",)},
    )

    assert _execute(harness).outcome.state is ValidationState.INVALID_OUTPUT_CONTRACT


def test_multiple_findings_use_phase_a_precedence_authority(tmp_path: Path) -> None:
    def response(prepared: Any, span: Any) -> dict[str, Any]:
        value = fixture.valid_response(prepared, span)
        value.update(
            {
                "answer": "",
                "lineage": {"attempt_id": "wrong"},
                "evidence_references": [{"bad": "shape"}],
                "partial": True,
                "tool_calls": [],
            }
        )
        return value

    result = _execute(fixture.make_harness(tmp_path, response_factory=response))

    assert {finding.state for finding in result.outcome.findings} >= {
        ValidationState.SECURITY_BLOCKED,
        ValidationState.INVALID_LINEAGE,
        ValidationState.INVALID_OUTPUT_CONTRACT,
        ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
        ValidationState.PARTIAL_RESPONSE,
    }
    assert result.outcome.state is ValidationState.SECURITY_BLOCKED


def test_identical_retry_returns_exact_same_authorization_and_semantic_result(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)

    first_authorization = _authorize(harness)
    first_result = harness.validator.execute(first_authorization)
    second_authorization = _authorize(harness)
    second_result = harness.validator.execute(second_authorization)

    assert first_authorization is second_authorization
    assert first_result is second_result
    assert first_result.outcome.semantic_outcome_id == second_result.outcome.semantic_outcome_id


def test_concurrent_execution_joins_one_single_use_authorization(tmp_path: Path) -> None:
    runtime = CountingRuntime()
    harness = fixture.make_harness(tmp_path, runtime=runtime)
    authorization = _authorize(harness)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(harness.validator.execute, (authorization,) * 16))

    assert runtime.calls == 1
    assert len({id(result) for result in results}) == 1
    assert results[0].outcome.state is ValidationState.VALID


def test_concurrent_authorization_retries_join_one_canonical_grant(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(harness.validator.authorize_event, (harness.allocation,) * 16))

    authorizations = tuple(result.authorization for result in results)
    assert all(authorization is not None for authorization in authorizations)
    assert len({id(authorization) for authorization in authorizations}) == 1


def test_transient_response_is_consumed_once_without_persistence_or_logging(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "TRANSIENT-RESPONSE-MARKER-0035"

    def response(prepared: Any, span: Any) -> dict[str, Any]:
        return {**fixture.valid_response(prepared, span), "answer": marker}

    caplog.set_level(logging.DEBUG)
    harness = fixture.make_harness(tmp_path, response_factory=response, transient=True)
    assert harness.dispatch_result.response_artifact.content is None
    assert not hasattr(harness.dispatch_result, "transient_response_access")
    vault_state = vars(harness.transient_response_vault)
    assert "_TransientResponseHandoffVault__entries" not in vault_state
    assert "_TransientResponseHandoffVault__consumer_endpoint" not in vault_state
    assert not any("owner" in name for name in vault_state)
    assert marker not in repr(vault_state)
    assert marker not in repr(vars(harness.adapter))
    producer = vault_state["_TransientResponseHandoffVault__producer_endpoint"]
    with pytest.raises(BlockingIOError):
        producer.recv(1, socket.MSG_DONTWAIT)

    authorization = _authorize(harness)
    consumer = harness.validator._ResponseValidator__transient_response_consumer  # noqa: SLF001
    assert marker not in repr(vars(consumer))
    result = harness.validator.execute(authorization)
    retry = harness.validator.execute(authorization)

    assert result is retry
    assert result.outcome.state is ValidationState.VALID
    assert consumer._TransientResponseConsumer__entries == {}  # noqa: SLF001
    assert marker not in caplog.text
    assert marker.encode() not in harness.database.read_bytes()


def test_empty_transient_response_reaches_semantic_syntax_validation(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, raw_response="", transient=True)

    result = _execute(harness)

    assert harness.dispatch_result.outcome.outcome == "SUCCEEDED_TRANSPORT"
    assert result.outcome.state is ValidationState.INVALID_SYNTAX


def test_caller_has_no_transient_response_consume_surface(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)

    assert not hasattr(harness.dispatch_result, "transient_response_access")
    assert not hasattr(harness.transient_response_vault, "consume_authorized")
    assert not hasattr(harness.transient_response_vault, "available_for_validation")
    assert "_VALIDATION_RESPONSE_CONSUME_MINT" not in vars(model_adapter)
    assert "transient_response_access" not in inspect.signature(harness.validator.authorize_event).parameters


def test_missing_transient_handoff_refuses_without_waiting_or_current_substitution(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)
    empty_handoff = model_adapter.TransientResponseHandoffVault()
    restarted_validator = ResponseValidator(
        harness.foundation,
        model_adapter_repository=harness.model_repository,
        pre_model_repository=harness.pre_model_repository,
        source_handling_store=harness.source_authority.store,
        runtime=DeterministicJsonValidationRuntime(),
        transient_response_vault=empty_handoff,
    )

    result = restarted_validator.authorize_event(harness.allocation)

    assert result.refusal is not None
    assert result.refusal.refusal.state is ValidationState.INPUT_UNAVAILABLE
    assert result.refusal.refusal.reason_code == "TRANSIENT_RESPONSE_ACCESS_UNAVAILABLE"


def test_refusing_mismatched_event_cannot_discard_canonical_transient_response(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)
    harness.foundation._clock = fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(seconds=1))  # noqa: SLF001
    mismatched = harness.foundation.allocate_base_validation(
        dataclasses.replace(
            harness.allocation.base_validation_key,
            requested_profile_selector="profile-not-known-at-cutoff",
        )
    )

    refused = harness.validator.authorize_event(mismatched)
    original = _execute(harness)

    assert refused.refusal is not None
    assert refused.refusal.refusal.state is ValidationState.RULE_UNAVAILABLE
    assert original.outcome.state is ValidationState.VALID


def test_declared_outcome_capture_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    response_artifact = harness.dispatch_result.response_artifact
    outcome = harness.dispatch_result.outcome
    assert response_artifact is not None
    forged_outcome = dataclasses.replace(outcome, response_artifact_identity="provider-response:substituted")

    with pytest.raises(ResponseValidationAuthorizationError, match="outcome capture lineage"):
        ResponseValidator._require_model_lineage(  # noqa: SLF001
            allocation=harness.allocation,
            response_artifact=response_artifact,
            outcome=forged_outcome,
            attempt=harness.prepared.attempt,
            handoff=harness.prepared.handoff,
        )


def test_phase_b_creates_no_terminal_record_or_persistence_owned_chronology(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    before = set(harness.database.read_bytes().splitlines())

    result = _execute(harness)

    with sqlite3.connect(harness.database) as connection:
        tables = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    assert result.outcome.semantic_outcome_id
    assert "response_validation_records" not in tables
    assert "response_validation_outcomes" not in tables
    assert "validation_recorded_at" not in repr(result)
    assert set(harness.database.read_bytes().splitlines()) == before


def test_phase_b_exposes_no_downstream_promotion_or_provider_invocation_surface() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(ResponseValidator, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == {"authorize_event", "execute"}
    assert not any(
        token in name
        for name in public_methods
        for token in ("extract", "promote", "rank", "recommend", "dispatch", "provider")
    )


def test_mutation_style_latest_profile_lookup_would_change_historical_authority(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    historical = _authorize(harness)
    later_time = fixture.VALIDATION_CUTOFF + timedelta(hours=1)
    harness.profile_authority._clock = fixture.SequenceClock(later_time)  # noqa: SLF001
    current = harness.profile_authority.supersede_profile(
        predecessor_publication_id=harness.profile.publication_id,
        spec=fixture.profile_spec(),
        correction_reason="mutation counterfactual",
    )

    mutated_latest = harness.profile_authority.resolve_strict_known(
        profile_selector="evidence-response-validation",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
        trusted_cutoff=later_time,
    )

    assert mutated_latest.profile == current
    assert mutated_latest.profile.publication_id != historical.coordinates.profile_publication_id
    assert _authorize(harness).coordinates.profile_publication_id == harness.profile.publication_id
