from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.response_validator import (
    VALIDATION_STATE_PRECEDENCE,
    BaseValidationKey,
    ResponseValidationProfile,
    ResponseValidationProfileAuthority,
    ResponseValidationProfileResolutionError,
    ResponseValidationProfileSpec,
    ResponseValidatorFoundation,
    UnknownValidationStateError,
    ValidationEventAllocationError,
    ValidationState,
    canonical_validation_state,
    highest_precedence_validation_state,
)
from hunter.evidence_intelligence.response_validator_persistence import (
    ResponseValidatorDirectWriteForbidden,
    ResponseValidatorPersistenceCorruption,
    ResponseValidatorPersistenceRepository,
    _profile_payload,
)

T1 = datetime(2026, 8, 24, 10, tzinfo=UTC)
T2 = T1 + timedelta(hours=1)
T3 = T2 + timedelta(hours=1)
T4 = T3 + timedelta(hours=1)


class SequenceClock:
    def __init__(self, *moments: datetime) -> None:
        self._moments = list(moments)
        self._lock = threading.Lock()
        self.calls = 0

    def now(self) -> datetime:
        with self._lock:
            if not self._moments:
                raise AssertionError("trusted clock was sampled more often than expected")
            self.calls += 1
            return self._moments.pop(0)


def repository(tmp_path: Path) -> ResponseValidatorPersistenceRepository:
    evidence = EvidenceIntelligenceRepository(tmp_path / "evidence.db")
    return ResponseValidatorPersistenceRepository(evidence)


def profile_spec(*, validator_version: str = "1") -> ResponseValidationProfileSpec:
    return ResponseValidationProfileSpec(
        profile_selector="evidence-response-validation",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
        validator_contract_identity="response-validator-contract",
        validator_contract_version=validator_version,
        syntax_schema_rule_identity="syntax-schema-rules",
        syntax_schema_rule_version=validator_version,
        parser_canonicalization_identity="json-parser-contract",
        parser_canonicalization_version="1",
        evidence_reference_rule_identity="evidence-reference-structure",
        evidence_reference_rule_version="1",
        resource_policy_identity="bounded-validation-resources",
        resource_policy_version="1",
        required_dimensions=("SYNTAX", "SCHEMA", "OUTPUT_CONTRACT", "LINEAGE"),
        security_rule_identity="validator-security-structure",
        security_rule_version="1",
    )


def base_key() -> BaseValidationKey:
    return BaseValidationKey(
        response_capture_identity="provider-response:1",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
        requested_profile_selector="evidence-response-validation",
    )


def test_profile_publication_and_version_history_are_immutable(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))

    first = authority.publish_profile(profile_spec())
    second = authority.supersede_profile(
        predecessor_publication_id=first.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="publish validator contract v2",
    )

    assert (first.profile_version, second.profile_version) == (1, 2)
    assert second.supersedes_publication_id == first.publication_id
    assert authority.profile_history(
        profile_selector="evidence-response-validation",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
    ) == (first, second)
    with pytest.raises(FrozenInstanceError):
        first.profile_version = 99  # type: ignore[misc]


def test_historical_profile_resolution_returns_version_knowable_at_cutoff(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))
    first = authority.publish_profile(profile_spec())
    authority.supersede_profile(
        predecessor_publication_id=first.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="publish v2",
    )

    resolution = authority.resolve_strict_known(
        profile_selector="evidence-response-validation",
        requested_output_contract_identity="extraction-schema",
        requested_output_contract_version="1",
        trusted_cutoff=T2,
    )

    assert resolution.profile == first
    assert resolution.publication_lineage == (first.publication_id,)


def test_later_profile_state_is_not_substituted_into_earlier_event(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))
    first = authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))
    allocation = validator.allocate_base_validation(base_key())
    current = authority.supersede_profile(
        predecessor_publication_id=first.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="publish v2",
    )

    assert (
        authority.resolve_strict_known(
            profile_selector="evidence-response-validation",
            requested_output_contract_identity="extraction-schema",
            requested_output_contract_version="1",
            trusted_cutoff=T4,
        ).profile
        == current
    )
    assert validator.resolve_profile_for_event(allocation).profile == first


def test_unknown_profile_history_fails_closed(tmp_path: Path) -> None:
    authority = ResponseValidationProfileAuthority(repository(tmp_path), clock=SequenceClock(T1))

    with pytest.raises(ResponseValidationProfileResolutionError, match="unavailable"):
        authority.resolve_strict_known(
            profile_selector="unknown-profile",
            requested_output_contract_identity="extraction-schema",
            requested_output_contract_version="1",
            trusted_cutoff=T2,
        )


def test_ambiguous_or_unresolvable_profile_history_fails_closed(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    first = authority.publish_profile(profile_spec())
    forged = ResponseValidationProfile(
        spec=profile_spec(validator_version="2"),
        profile_version=2,
        applicable_from=T2,
        published_at=T2,
        known_at=T2,
        supersedes_publication_id="response-validation-profile-publication:missing",
        correction_reason="disconnected forged branch",
    )
    payload = _profile_payload(forged)
    with sqlite3.connect(store.path) as connection:
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

    with pytest.raises(ResponseValidationProfileResolutionError, match="ambiguous|unresolvable|branch"):
        authority.resolve_strict_known(
            profile_selector=first.spec.profile_selector,
            requested_output_contract_identity=first.spec.requested_output_contract_identity,
            requested_output_contract_version=first.spec.requested_output_contract_version,
            trusted_cutoff=T4,
        )


def test_caller_cannot_forge_profile_publication_identity_or_history(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    canonical = authority.publish_profile(profile_spec())

    with pytest.raises(ResponseValidatorDirectWriteForbidden):
        store.direct_write(
            table="response_validation_profiles",
            record={"publication_id": "caller-chosen", "profile_version": 999},
        )

    signature = inspect.signature(authority.publish_profile)
    for forbidden in ("publication_id", "profile_version", "known_at", "published_at", "applicable_from"):
        assert forbidden not in signature.parameters
    assert canonical.publication_id.startswith("response-validation-profile-publication:")


def test_same_base_key_joins_same_event_and_cutoff_without_resampling_clock(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    authority.publish_profile(profile_spec())
    clock = SequenceClock(T2)
    validator = ResponseValidatorFoundation(store, authority, clock=clock)

    first = validator.allocate_base_validation(base_key())
    retry = validator.allocate_base_validation(base_key())

    assert retry.validation_event_id == first.validation_event_id
    assert retry.validation_cutoff == first.validation_cutoff == T2
    assert clock.calls == 1


def test_retry_does_not_mint_a_new_event(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))

    first = validator.allocate_base_validation(base_key())
    assert validator.allocate_base_validation(base_key()) == first
    assert store.validation_events(base_key().base_validation_key_id) == (first,)


def test_explicit_revalidation_creates_one_distinct_event_and_cutoff(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))
    first_profile = authority.publish_profile(profile_spec())
    clock = SequenceClock(T2, T4)
    validator = ResponseValidatorFoundation(store, authority, clock=clock)
    base = validator.allocate_base_validation(base_key())
    second_profile = authority.supersede_profile(
        predecessor_publication_id=first_profile.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="fresh profile for explicit re-validation",
    )

    revalidation = validator.allocate_revalidation(predecessor_validation_event_id=base.validation_event_id)
    joined = validator.allocate_revalidation(predecessor_validation_event_id=base.validation_event_id)

    assert revalidation == joined
    assert revalidation.validation_event_id != base.validation_event_id
    assert revalidation.validation_cutoff == T4
    assert revalidation.validation_cutoff != base.validation_cutoff
    assert revalidation.revalidation_generation == 1
    assert revalidation.predecessor_validation_event_id == base.validation_event_id
    assert validator.resolve_profile_for_event(base).profile == first_profile
    assert validator.resolve_profile_for_event(revalidation).profile == second_profile
    assert clock.calls == 2


def test_caller_cannot_choose_validation_event_id_or_cutoff(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))

    base_signature = inspect.signature(validator.allocate_base_validation)
    revalidation_signature = inspect.signature(validator.allocate_revalidation)
    for forbidden in ("validation_event_id", "validation_cutoff"):
        assert forbidden not in base_signature.parameters
        assert forbidden not in revalidation_signature.parameters
    allocation = validator.allocate_base_validation(base_key())
    assert allocation.validation_cutoff == T2


def test_duplicate_concurrent_allocation_cannot_create_sibling_base_events(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    authority.publish_profile(profile_spec())
    clock = SequenceClock(T2)
    validator = ResponseValidatorFoundation(store, authority, clock=clock)
    barrier = threading.Barrier(8)
    lock = threading.Lock()
    allocations = []
    errors: list[BaseException] = []

    def contend() -> None:
        barrier.wait()
        try:
            allocation = validator.allocate_base_validation(base_key())
        except BaseException as error:  # noqa: BLE001 - collected for deterministic assertion
            with lock:
                errors.append(error)
        else:
            with lock:
                allocations.append(allocation)

    threads = [threading.Thread(target=contend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len({item.validation_event_id for item in allocations}) == 1
    assert len({item.validation_cutoff for item in allocations}) == 1
    assert len(store.validation_events(base_key().base_validation_key_id)) == 1
    assert clock.calls == 1


def test_validation_vocabulary_rejects_unknown_state() -> None:
    with pytest.raises(UnknownValidationStateError):
        canonical_validation_state("VALID_BUT_TRUST_ME")
    with pytest.raises(UnknownValidationStateError):
        highest_precedence_validation_state((ValidationState.VALID, "UNKNOWN"))


def test_validation_precedence_exactly_matches_adr_0035() -> None:
    expected = (
        "SECURITY_BLOCKED",
        "SOURCE_HANDLING_BLOCKED",
        "VALIDATOR_ERROR",
        "VALIDATOR_CAPABILITY_UNKNOWN",
        "INPUT_UNAVAILABLE",
        "RULE_UNAVAILABLE",
        "EVIDENCE_AMBIGUOUS",
        "INVALID_LINEAGE",
        "INVALID_SYNTAX",
        "INVALID_SCHEMA",
        "INVALID_OUTPUT_CONTRACT",
        "INVALID_EVIDENCE_REFERENCE_STRUCTURE",
        "PARTIAL_RESPONSE",
        "VALID",
    )

    assert tuple(state.value for state in VALIDATION_STATE_PRECEDENCE) == expected
    for index, state in enumerate(VALIDATION_STATE_PRECEDENCE):
        assert highest_precedence_validation_state(VALIDATION_STATE_PRECEDENCE[index:]) == state
    assert (
        highest_precedence_validation_state(reversed(VALIDATION_STATE_PRECEDENCE)) == ValidationState.SECURITY_BLOCKED
    )


def test_current_state_substitution_adversarial_case_is_rejected(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))
    first = authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))
    event = validator.allocate_base_validation(base_key())
    later = authority.supersede_profile(
        predecessor_publication_id=first.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="later state",
    )

    assert later.profile_version == 2
    assert validator.resolve_profile_for_event(event).profile.publication_id == first.publication_id


def test_forged_profile_identity_adversarial_case_fails_closed(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    canonical = authority.publish_profile(profile_spec())
    with sqlite3.connect(store.path) as connection:
        payload = json.loads(_profile_payload(canonical))
        payload["publication_id"] = "response-validation-profile-publication:forged"
        forged_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        connection.execute(
            "UPDATE response_validation_profiles SET payload_hash = ?, payload_json = ? WHERE publication_id = ?",
            (
                hashlib.sha256(forged_payload.encode()).hexdigest(),
                forged_payload,
                canonical.publication_id,
            ),
        )

    with pytest.raises(ResponseValidatorPersistenceCorruption, match="identity|canonical"):
        authority.profile_history(
            profile_selector="evidence-response-validation",
            requested_output_contract_identity="extraction-schema",
            requested_output_contract_version="1",
        )


def test_substituted_cutoff_adversarial_case_fails_closed(tmp_path: Path) -> None:
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1))
    authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))
    allocation = validator.allocate_base_validation(base_key())
    forged = dataclasses.replace(allocation, validation_cutoff=T4)

    with pytest.raises(ValidationEventAllocationError, match="unknown|canonical"):
        validator.resolve_profile_for_event(forged)


def test_mutation_style_current_state_fallback_would_change_historical_result(tmp_path: Path) -> None:
    """Non-vacuity proof for the reusable event-cutoff resolution guard."""
    store = repository(tmp_path)
    authority = ResponseValidationProfileAuthority(store, clock=SequenceClock(T1, T3))
    first = authority.publish_profile(profile_spec())
    validator = ResponseValidatorFoundation(store, authority, clock=SequenceClock(T2))
    event = validator.allocate_base_validation(base_key())
    later = authority.supersede_profile(
        predecessor_publication_id=first.publication_id,
        spec=profile_spec(validator_version="2"),
        correction_reason="later state",
    )

    mutated_latest_lookup = authority.resolve_strict_known(
        profile_selector=base_key().requested_profile_selector,
        requested_output_contract_identity=base_key().requested_output_contract_identity,
        requested_output_contract_version=base_key().requested_output_contract_version,
        trusted_cutoff=T4,
    )
    guarded = validator.resolve_profile_for_event(event)

    assert mutated_latest_lookup.profile == later
    assert guarded.profile == first


def test_mutation_style_lexical_precedence_would_select_the_wrong_state() -> None:
    """Non-vacuity proof that precedence is a governed table, not string ordering."""
    states = (ValidationState.SECURITY_BLOCKED, ValidationState.VALIDATOR_ERROR)
    assert min(states, key=lambda state: state.value) == ValidationState.SECURITY_BLOCKED
    states = (ValidationState.SOURCE_HANDLING_BLOCKED, ValidationState.SECURITY_BLOCKED)
    assert max(states, key=lambda state: state.value) == ValidationState.SOURCE_HANDLING_BLOCKED
    assert highest_precedence_validation_state(states) == ValidationState.SECURITY_BLOCKED
