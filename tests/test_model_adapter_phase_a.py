"""ADR 0034 Phase A — adversarial regression coverage.

These tests exist to attack the boundaries, not to mirror the implementation.
Each negative case is paired with the positive case it must not break, so a
guard that is silently removed fails a test rather than quietly widening
authority.
"""

from __future__ import annotations

import dataclasses
import gc
import json
import socket
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import model_adapter_fixture as fixture
import pytest

from hunter.evidence_intelligence.model_adapter import (
    _SECRET_VALUE_PATTERN,
    DISPATCH_CAPABILITY_CATEGORY,
    ModelAdapterAuthorityError,
    ModelAdapterError,
    ModelAdapterService,
    ModelAttemptRecord,
    ModelExecutionProfile,
    ModelHandoffRecord,
    NoNetworkDispatchSeam,
    PreDispatchRefused,
    ProviderRequestEvidence,
    SecretMaterialRejected,
)
from hunter.evidence_intelligence.model_adapter_persistence import (
    ModelAdapterAuthorityMismatch,
    ModelAdapterDirectWriteForbidden,
    ModelAdapterPersistenceConflict,
    ModelAdapterPersistenceError,
    ModelAdapterPersistenceRepository,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository


def _persisted_field_values(database: Path) -> list[object]:
    """Every scalar value actually written to the Phase A durable tables."""
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
            "SELECT payload_json FROM model_attempt_records " "UNION ALL SELECT payload_json FROM model_handoff_records"
        ).fetchall()
    for row in rows:
        walk(json.loads(str(row[0])))
    return values


@pytest.fixture
def repository(tmp_path: Path) -> ModelAdapterPersistenceRepository:
    return ModelAdapterPersistenceRepository(EvidenceIntelligenceRepository(tmp_path / "evidence.db"))


@pytest.fixture
def service(repository: ModelAdapterPersistenceRepository) -> ModelAdapterService:
    return ModelAdapterService(repository)


def _prepare(service: ModelAdapterService, **overrides):
    artifact = overrides.pop("prompt_artifact", None) or fixture.prompt_artifact()
    build = overrides.pop("build_record", None) or fixture.build_record(artifact)
    kwargs: dict[str, object] = {
        "execution_owner_id": "pipeline-run:1",
        "build_record": build,
        "prompt_artifact": artifact,
        "capability": fixture.capability(),
        "allocation": fixture.allocation(),
        "profile": fixture.execution_profile(),
        "attempt_authority": fixture.attempt_authority(),
        "build_cutoff": fixture.BUILD_CUTOFF,
        "recorded_at": fixture.RECORDED_AT,
    }
    kwargs.update(overrides)
    return service.prepare_attempt(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Positive baseline. Every negative case below must fail for its own reason,
# not because the happy path was broken.
# --------------------------------------------------------------------------


def test_authorized_attempt_produces_a_durable_attempt_and_single_use_handoff(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    prepared = _prepare(service)

    assert repository.attempt_exists(prepared.attempt.attempt_id)
    assert prepared.handoff.attempt_id == prepared.attempt.attempt_id
    assert prepared.handoff.processing_decision == "ALLOW"
    assert repository.handoff_consumed_at(prepared.handoff.handoff_id) is None

    result = service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(1))

    assert result.dispatched is True
    assert result.transmitted_bytes == 0
    assert repository.handoff_consumed_at(prepared.handoff.handoff_id) is not None


# --------------------------------------------------------------------------
# 1-5. Source Handling authority cannot be borrowed, cached, or supplied.
# --------------------------------------------------------------------------


def test_build_time_allow_cannot_authorize_an_attempt(service: ModelAdapterService) -> None:
    """A build-time ALLOW is lineage, never live permission.

    The build authority still resolves ALLOW at the build cutoff; only the
    attempt-cutoff authority denies. If the adapter consulted build-time
    authority, this would wrongly succeed.
    """
    build_time_authority = fixture.attempt_authority(cutoff=fixture.BUILD_CUTOFF, processing="ALLOW")
    assert build_time_authority.cutoff == fixture.BUILD_CUTOFF

    with pytest.raises(PreDispatchRefused) as error:
        _prepare(service, attempt_authority=fixture.attempt_authority(processing="DENY"))

    assert error.value.refusal == "SOURCE_HANDLING_BLOCKED"


def test_prior_attempt_allow_cannot_authorize_a_retry(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    first = _prepare(service)
    assert first.handoff.processing_decision == "ALLOW"

    with pytest.raises(PreDispatchRefused) as error:
        _prepare(
            service,
            attempt_authority=fixture.attempt_authority(cutoff=fixture.later(60), processing="DENY"),
            attempt_ordinal=2,
            predecessor_attempt_id=first.attempt.attempt_id,
        )

    assert error.value.refusal == "SOURCE_HANDLING_BLOCKED"


def test_caller_supplied_permissive_decision_cannot_authorize(service: ModelAdapterService) -> None:
    """A caller decision is evidence to re-verify, never authority to trust."""
    forged = {
        "processing_decision": "ALLOW",
        "retention_decision": "ALLOW",
        "reconstruction_decision": "ALLOW",
        "access_decision": "ALLOW",
        "deletion_lifecycle_decision": "ALLOW",
        "durable_dispositions": {},
        "fact_record_id": "forged",
        "policy_record_id": "forged",
        "field_category_registry_id": "forged",
        "authorization_rule_id": "forged",
    }

    with pytest.raises(ModelAdapterAuthorityError):
        _prepare(service, supplied_handling_decision=forged)


def test_restrictive_authority_at_the_attempt_cutoff_blocks_handoff_creation(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    with pytest.raises(PreDispatchRefused) as error:
        _prepare(service, attempt_authority=fixture.attempt_authority(processing="BLOCKED"))

    assert error.value.refusal == "SOURCE_HANDLING_BLOCKED"


def test_blocked_attempt_leaves_no_attempt_and_no_handoff(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository, tmp_path: Path
) -> None:
    """A refusal must transmit nothing and leave no dispatchable state behind."""
    with pytest.raises(PreDispatchRefused):
        _prepare(service, attempt_authority=fixture.attempt_authority(processing="DENY"))

    with sqlite3.connect(tmp_path / "evidence.db") as connection:
        attempts = connection.execute("SELECT COUNT(*) FROM model_attempt_records").fetchone()[0]
        handoffs = connection.execute("SELECT COUNT(*) FROM model_handoff_records").fetchone()[0]
    assert (attempts, handoffs) == (0, 0)


# --------------------------------------------------------------------------
# 6. Capability compatibility fails closed before anything durable exists.
# --------------------------------------------------------------------------


def test_capability_mismatch_fails_closed_before_any_handoff(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository, tmp_path: Path
) -> None:
    with pytest.raises(PreDispatchRefused) as error:
        _prepare(service, profile=fixture.execution_profile(required_capability_identity="capability:different"))

    assert error.value.refusal == "CAPABILITY_UNSUPPORTED"
    with sqlite3.connect(tmp_path / "evidence.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_handoff_records").fetchone()[0] == 0


def test_matching_capability_is_accepted(service: ModelAdapterService) -> None:
    """Paired positive: the mismatch guard must not reject a valid profile."""
    prepared = _prepare(service)
    assert prepared.attempt.attempt_ordinal == 1


# --------------------------------------------------------------------------
# 7. Durable-before-send.
# --------------------------------------------------------------------------


def test_attempt_persistence_failure_prevents_any_dispatchable_handoff(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository, tmp_path: Path
) -> None:
    def explode(**_: object) -> None:
        raise ModelAdapterPersistenceError("attempt persistence failed")

    repository.persist_attempt_and_handoff = explode  # type: ignore[method-assign]

    with pytest.raises(ModelAdapterPersistenceError):
        _prepare(service)

    with sqlite3.connect(tmp_path / "evidence.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_handoff_records").fetchone()[0] == 0


def test_handoff_cannot_be_consumed_without_a_persisted_attempt(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    """An unpersisted handoff authorizes nothing, even if the object exists."""
    prepared = _prepare(service)
    unknown = type(prepared.handoff)(  # a structurally valid but never-persisted handoff
        **{**prepared.handoff.__dict__, "attempt_id": "model-attempt:never-persisted"}
    )

    with pytest.raises(ModelAdapterPersistenceError):
        service.consume_handoff(handoff=unknown, consumed_at=fixture.later(1))


# --------------------------------------------------------------------------
# 8-9. Single-use consumption, including under concurrency.
# --------------------------------------------------------------------------


def test_duplicate_consumption_permits_exactly_one_winner(service: ModelAdapterService) -> None:
    prepared = _prepare(service)
    service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(1))

    with pytest.raises(ModelAdapterPersistenceError):
        service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(2))

    assert service.dispatch_seam.dispatched_handoff_ids == [prepared.handoff.handoff_id]


def test_concurrent_consumption_permits_exactly_one_winner(service: ModelAdapterService) -> None:
    prepared = _prepare(service)
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def consume() -> None:
        barrier.wait()
        try:
            service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(1))
            outcome = "won"
        except (ModelAdapterPersistenceError, sqlite3.Error):
            outcome = "rejected"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=consume) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("won") == 1, outcomes
    assert len(service.dispatch_seam.dispatched_handoff_ids) == 1


def test_expired_handoff_is_rejected(service: ModelAdapterService) -> None:
    prepared = _prepare(service, handoff_expires_at=fixture.later(5))

    with pytest.raises(ModelAdapterError):
        service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(10))


def test_unexpired_handoff_is_accepted(service: ModelAdapterService) -> None:
    """Paired positive: expiry must not reject a handoff inside its validity."""
    prepared = _prepare(service, handoff_expires_at=fixture.later(30))
    assert service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(10)).dispatched


def test_mismatched_handoff_lineage_is_rejected_at_persistence(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    prepared = _prepare(service)
    foreign = type(prepared.handoff)(**{**prepared.handoff.__dict__, "attempt_id": "model-attempt:foreign"})

    with pytest.raises(ModelAdapterPersistenceError):
        repository.persist_attempt_and_handoff(
            attempt=prepared.attempt,
            handoff=foreign,
            request_evidence=prepared.request_evidence,
            attempt_authority=fixture.attempt_authority(),
        )


# --------------------------------------------------------------------------
# 10. Processing permission never implies retention permission.
# --------------------------------------------------------------------------


def test_processing_allowed_with_content_denied_persists_no_content_derived_evidence(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository, tmp_path: Path
) -> None:
    prepared = _prepare(service, attempt_authority=fixture.attempt_authority(request_content=False))
    evidence = prepared.request_evidence

    assert evidence.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert evidence.content_hash is None
    assert evidence.measured_size_bytes is None
    assert evidence.content_derived_identity is None
    assert evidence.reason_code == "REQUEST_CONTENT_RETENTION_PROHIBITED"

    # The prohibited content must be absent from the durable bytes themselves,
    # not merely absent from the returned object. Checked structurally: a bare
    # substring scan would collide with unrelated digits inside timestamps.
    artifact = fixture.prompt_artifact()
    forbidden = {artifact.content, artifact.content_hash, artifact.measured_size_bytes}
    for value in _persisted_field_values(tmp_path / "evidence.db"):
        assert value not in forbidden, value


def test_top_level_retention_denial_blocks_content_even_when_categories_allow(
    service: ModelAdapterService,
) -> None:
    """Both gates matter, independently.

    Per-category dispositions may all permit persistence while the governing
    top-level retention decision denies it. Retention denial must still win, so
    neither check may be dropped in favour of the other.
    """
    prepared = _prepare(
        service,
        attempt_authority=fixture.attempt_authority(retention="DENY", request_content=True),
    )

    assert prepared.request_evidence.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert prepared.request_evidence.content_hash is None
    assert prepared.request_evidence.reason_code == "REQUEST_CONTENT_RETENTION_PROHIBITED"


def test_processing_allowed_with_content_allowed_persists_authorized_evidence(
    service: ModelAdapterService,
) -> None:
    """Paired positive: the denial path must not suppress authorized evidence."""
    prepared = _prepare(service)

    assert prepared.request_evidence.state == "REQUEST_EVIDENCE_DURABLE"
    assert prepared.request_evidence.content_hash == fixture.prompt_artifact().content_hash


def test_dispatch_capability_is_omitted_when_its_category_is_denied(service: ModelAdapterService) -> None:
    prepared = _prepare(service, attempt_authority=fixture.attempt_authority(dispatch_capability=False))

    assert DISPATCH_CAPABILITY_CATEGORY == "OPERATIONAL_METADATA"
    assert prepared.handoff.dispatch_capability_identity is None


def test_request_evidence_cannot_claim_unavailable_while_carrying_content() -> None:
    with pytest.raises(ModelAdapterError):
        ProviderRequestEvidence(
            state="REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY",
            reason_code="REQUEST_CONTENT_RETENTION_PROHIBITED",
            content_hash="deadbeef",
        )


def test_provider_request_evidence_is_distinct_from_the_prompt_artifact(service: ModelAdapterService) -> None:
    """The two identities must never collapse, even when content maps across."""
    artifact = fixture.prompt_artifact()
    prepared = _prepare(service, prompt_artifact=artifact, build_record=fixture.build_record(artifact))

    assert prepared.request_evidence.request_evidence_identity != artifact.artifact_id


# --------------------------------------------------------------------------
# 11-12. Structural secret exclusion.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_identity": "Bearer sk-live-abcdefghijklmnop"},
        {"endpoint_class_identity": "sk-live-abcdefghijklmnop"},
        {"credential_slot_identity": "sk-live-abcdefghijklmnop"},
        {"model_identity": "-----BEGIN RSA PRIVATE KEY-----"},
    ],
)
def test_secret_bearing_profile_values_are_structurally_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises(SecretMaterialRejected):
        fixture.execution_profile(**overrides)


@pytest.mark.parametrize(
    "parameter",
    [
        ("api_key", "abc123"),
        ("authorization", "abc123"),
        ("x-api-key", "abc123"),
        ("client_secret", "abc123"),
        ("session_token", "abc123"),
        ("temperature", "Bearer sk-live-abcdefghijklmnop"),
    ],
)
def test_secret_bearing_profile_parameters_are_structurally_rejected(parameter: tuple[str, str]) -> None:
    with pytest.raises(SecretMaterialRejected):
        fixture.execution_profile(parameters=(parameter,))


def test_profile_rejects_nested_mapping_and_raw_bytes_parameters() -> None:
    """The classic smuggling routes for a raw credential blob."""
    with pytest.raises(ModelAdapterError):
        fixture.execution_profile(parameters=(("headers", {"Authorization": "Bearer x"}),))  # type: ignore[arg-type]
    with pytest.raises(ModelAdapterError):
        fixture.execution_profile(parameters=(("blob", b"secret"),))  # type: ignore[arg-type]


def test_non_secret_profile_is_accepted_and_immutable() -> None:
    """Paired positive: secret exclusion must not reject a legitimate profile."""
    profile = fixture.execution_profile()

    assert profile.profile_identity.startswith("model-execution-profile:")
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.model_identity = "mutated"  # type: ignore[misc]


def test_profile_identity_changes_when_any_execution_field_changes() -> None:
    """A changed parameter is a new version, never an edit of an existing one."""
    base = fixture.execution_profile()
    changed = fixture.execution_profile(parameters=(("temperature", "1"),))
    versioned = fixture.execution_profile(profile_version="2")

    assert len({base.profile_identity, changed.profile_identity, versioned.profile_identity}) == 3


def test_secret_material_cannot_reach_the_durable_attempt_or_handoff(
    service: ModelAdapterService, tmp_path: Path
) -> None:
    """A seeded secret cannot reach durable bytes, because it cannot be built.

    The sentinel is rejected at profile construction, so it never reaches
    persistence at all; the durable scan then confirms no credential-shaped value
    is present on the authorized path either. `authorization_rule_id` is
    deliberately not treated as a leak: it is required Source Handling lineage,
    not a secret, which is why this scans values rather than field names.
    """
    sentinel = "sk-live-phaseasentinel00000"

    with pytest.raises(SecretMaterialRejected):
        fixture.execution_profile(parameters=(("api_key", sentinel),))

    _prepare(service)

    for value in _persisted_field_values(tmp_path / "evidence.db"):
        assert value != sentinel
        if isinstance(value, str):
            assert not _SECRET_VALUE_PATTERN.search(value.strip()), value


def test_benign_credential_slot_identity_is_accepted(service: ModelAdapterService) -> None:
    """The modelled slot field names a slot; it must remain usable.

    Regression: a generic field-name scan rejected `credential_slot_identity`
    outright because the name contains "credential", making the modelled field
    unusable and its value-shape check unreachable.
    """
    profile = fixture.execution_profile(credential_slot_identity="vault-slot:model-provider")

    assert profile.credential_slot_identity == "vault-slot:model-provider"
    prepared = _prepare(service, profile=profile)
    assert prepared.handoff.execution_profile_identity == profile.profile_identity


def test_secret_bearing_prohibited_capability_is_rejected() -> None:
    """Regression: prohibited capabilities were exempt from secret screening.

    They are hashed into `profile_identity`, which is persisted on the attempt and
    handoff, so an unscreened entry became a durable credential-derived
    representation despite the structural-exclusion guarantee.
    """
    with pytest.raises(SecretMaterialRejected):
        fixture.execution_profile(prohibited_capabilities=("tool_use", "sk-live-abcdefghijklmnop"))

    with pytest.raises(SecretMaterialRejected):
        fixture.execution_profile(prohibited_capabilities=("Bearer abcdefghijklmnop",))


def test_capability_must_be_the_one_recorded_by_the_governing_build(service: ModelAdapterService) -> None:
    """Regression: the capability check compared two caller-supplied objects only.

    A profile and capability that agree with each other, but not with the
    allocation the build actually used, must not authorize a dispatch.
    """
    foreign = fixture.capability(version="99")

    with pytest.raises(ModelAdapterError):
        _prepare(
            service,
            capability=foreign,
            profile=fixture.execution_profile(required_capability_identity=foreign.constraint_identity),
        )


def test_allocation_must_belong_to_the_governing_build(service: ModelAdapterService) -> None:
    """Isolates the allocation-identity guard from the capability guard.

    The substitute allocation records the *same* capability identity, so only the
    allocation-to-build binding can reject it.
    """
    foreign = dataclasses.replace(fixture.allocation(), ledger_id="ledger:foreign")
    assert foreign.capability_identity == fixture.allocation().capability_identity
    assert foreign.allocation_id != fixture.allocation().allocation_id

    with pytest.raises(ModelAdapterError):
        _prepare(service, allocation=foreign)


def test_persistence_rejects_durable_evidence_under_content_denying_authority(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """Regression: persistence checked identity consistency, not permission.

    A direct caller with genuine authority that denies content categories could
    still persist forged `REQUEST_EVIDENCE_DURABLE` content, embedding the hash
    and size in the durable payload.
    """
    denying = fixture.attempt_authority(request_content=False)
    prepared = _prepare(service, attempt_authority=denying)
    assert prepared.request_evidence.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"

    artifact = fixture.prompt_artifact()
    forged = ProviderRequestEvidence(
        state="REQUEST_EVIDENCE_DURABLE",
        content_hash=artifact.content_hash,
        measured_size_bytes=artifact.measured_size_bytes,
        content_derived_identity="forged",
    )
    attempt = dataclasses.replace(
        prepared.attempt,
        request_evidence_identity=forged.request_evidence_identity,
        request_evidence_state="REQUEST_EVIDENCE_DURABLE",
    )
    handoff = dataclasses.replace(
        prepared.handoff,
        attempt_id=attempt.attempt_id,
        request_evidence_identity=forged.request_evidence_identity,
    )

    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=attempt,
            handoff=handoff,
            request_evidence=forged,
            attempt_authority=denying,
        )


def test_persistence_rejects_evidence_state_that_authority_does_not_permit(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """Isolates the request-evidence check from the attempt-state check.

    The attempt still records the permitted state and still references this
    evidence identity, so only the evidence-object check can reject it.
    """
    denying = fixture.attempt_authority(request_content=False)
    prepared = _prepare(service, attempt_authority=denying)

    # Deliberately content-free: the separate "carries prohibited content" check
    # must not be the thing that rejects this, or the state check would be
    # redundant rather than load-bearing.
    forged = ProviderRequestEvidence(state="REQUEST_EVIDENCE_DURABLE")
    attempt = dataclasses.replace(prepared.attempt, request_evidence_identity=forged.request_evidence_identity)
    handoff = dataclasses.replace(
        prepared.handoff,
        attempt_id=attempt.attempt_id,
        request_evidence_identity=forged.request_evidence_identity,
    )
    assert attempt.request_evidence_state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"

    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=attempt,
            handoff=handoff,
            request_evidence=forged,
            attempt_authority=denying,
        )


def test_persistence_rejects_attempt_state_that_authority_does_not_permit(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """The paired case: only the attempt's recorded state is wrong."""
    denying = fixture.attempt_authority(request_content=False)
    prepared = _prepare(service, attempt_authority=denying)
    attempt = dataclasses.replace(prepared.attempt, request_evidence_state="REQUEST_EVIDENCE_DURABLE")
    handoff = dataclasses.replace(prepared.handoff, attempt_id=attempt.attempt_id)

    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=attempt,
            handoff=handoff,
            request_evidence=prepared.request_evidence,
            attempt_authority=denying,
        )


def test_persistence_closes_every_connection_it_opens(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """Regression: `with sqlite3.connect(...)` ends the transaction but never closes.

    Leaked handles accumulate in a long-running process and can hold SQLite locks
    until garbage collection.
    """
    prepared = _prepare(service)
    repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.later(60))
    repository.strict_known_request_evidence(prepared.attempt.attempt_id, fixture.later(60))
    repository.handoff_consumed_at(prepared.handoff.handoff_id)
    repository.attempt_exists(prepared.attempt.attempt_id)

    gc.collect()
    leaked = [obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection) and _is_open(obj)]
    assert leaked == []


def _is_open(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return False
    return True


# --------------------------------------------------------------------------
# 13. Retry is always a new attempt.
# --------------------------------------------------------------------------


def test_retry_creates_a_new_attempt_cutoff_resolution_and_handoff(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    first = _prepare(service)
    retry_cutoff = fixture.later(60)

    second = _prepare(
        service,
        attempt_authority=fixture.attempt_authority(cutoff=retry_cutoff),
        attempt_ordinal=2,
        predecessor_attempt_id=first.attempt.attempt_id,
        recorded_at=retry_cutoff + timedelta(minutes=1),
    )

    assert second.attempt.attempt_id != first.attempt.attempt_id
    assert second.handoff.handoff_id != first.handoff.handoff_id
    assert second.attempt.attempt_cutoff != first.attempt.attempt_cutoff
    assert second.attempt.predecessor_attempt_id == first.attempt.attempt_id
    assert repository.attempt_exists(first.attempt.attempt_id)
    assert repository.attempt_exists(second.attempt.attempt_id)


def test_retry_cannot_reuse_the_predecessor_dispatch_authorization(service: ModelAdapterService) -> None:
    first = _prepare(service)
    service.consume_handoff(handoff=first.handoff, consumed_at=fixture.later(1))

    with pytest.raises(ModelAdapterPersistenceError):
        service.consume_handoff(handoff=first.handoff, consumed_at=fixture.later(2))


def test_retry_without_predecessor_lineage_is_rejected() -> None:
    with pytest.raises(ModelAdapterError):
        ModelAttemptRecord(
            execution_owner_id="run:1",
            build_record_id="build:1",
            prompt_artifact_id="artifact:1",
            execution_profile_identity="profile:1",
            request_evidence_identity="evidence:1",
            request_evidence_state="REQUEST_EVIDENCE_DURABLE",
            attempt_ordinal=2,
            build_cutoff=fixture.BUILD_CUTOFF,
            attempt_cutoff=fixture.ATTEMPT_CUTOFF,
            recorded_at=fixture.RECORDED_AT,
            idempotency_capability="SUPPORTED",
            predecessor_attempt_id=None,
        )


def test_first_attempt_with_a_predecessor_is_rejected() -> None:
    with pytest.raises(ModelAdapterError):
        ModelAttemptRecord(
            execution_owner_id="run:1",
            build_record_id="build:1",
            prompt_artifact_id="artifact:1",
            execution_profile_identity="profile:1",
            request_evidence_identity="evidence:1",
            request_evidence_state="REQUEST_EVIDENCE_DURABLE",
            attempt_ordinal=1,
            build_cutoff=fixture.BUILD_CUTOFF,
            attempt_cutoff=fixture.ATTEMPT_CUTOFF,
            recorded_at=fixture.RECORDED_AT,
            idempotency_capability="SUPPORTED",
            predecessor_attempt_id="model-attempt:earlier",
        )


# --------------------------------------------------------------------------
# 14. Repository is mechanical and is not a public authority bypass.
# --------------------------------------------------------------------------


def test_repository_direct_write_is_forbidden(repository: ModelAdapterPersistenceRepository) -> None:
    with pytest.raises(ModelAdapterDirectWriteForbidden):
        repository.direct_write(table="model_attempt_records", record={"attempt_id": "forged"})


def test_repository_rejects_a_handoff_whose_lineage_does_not_match_its_attempt(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    prepared = _prepare(service)
    tampered = type(prepared.handoff)(
        **{**prepared.handoff.__dict__, "execution_profile_identity": "model-execution-profile:other"}
    )

    with pytest.raises(ModelAdapterPersistenceError):
        repository.persist_attempt_and_handoff(
            attempt=prepared.attempt,
            handoff=tampered,
            request_evidence=prepared.request_evidence,
            attempt_authority=fixture.attempt_authority(),
        )


def test_repository_rejects_reusing_an_identity_with_different_bytes(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """Append-only means a conflict, never a silent overwrite."""
    prepared = _prepare(service)

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE model_attempt_records SET payload_hash = ? WHERE attempt_id = ?",
            ("0" * 64, prepared.attempt.attempt_id),
        )

    with pytest.raises(ModelAdapterPersistenceConflict):
        repository.persist_attempt_and_handoff(
            attempt=prepared.attempt,
            handoff=prepared.handoff,
            request_evidence=prepared.request_evidence,
            attempt_authority=fixture.attempt_authority(),
        )


def test_tampered_persisted_bytes_are_detected_on_read(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    prepared = _prepare(service)

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE model_attempt_records SET payload_json = ? WHERE attempt_id = ?",
            ('{"tampered":true}', prepared.attempt.attempt_id),
        )

    with pytest.raises(ModelAdapterPersistenceError):
        repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.later(60))


def test_one_attempt_can_authorize_at_most_one_dispatch(service: ModelAdapterService) -> None:
    """Regression: single-use per *handoff* is not enough; it must be per attempt.

    Two handoffs differing only in expiry share one attempt identity. Before this
    was enforced, each was independently consumable and one attempt dispatched
    twice — precisely the duplicate-execution risk ADR 0034 exists to prevent.
    """
    first = _prepare(service, handoff_expires_at=fixture.later(30))

    with pytest.raises(ModelAdapterPersistenceConflict):
        _prepare(service, handoff_expires_at=fixture.later(60))

    service.consume_handoff(handoff=first.handoff, consumed_at=fixture.later(1))
    assert len(service.dispatch_seam.dispatched_handoff_ids) == 1


def test_repository_cannot_be_used_to_forge_an_authoritative_record(
    repository: ModelAdapterPersistenceRepository,
) -> None:
    """Regression: the repository is not a public bypass around adapter authority.

    A caller who never went through `ModelAdapterService` can still construct
    structurally valid records. Persistence independently rederives the authority
    (ADR 0033) and rejects bound identities that disagree, so a fabricated handoff
    cannot become dispatchable.
    """
    evidence = ProviderRequestEvidence(
        state="REQUEST_EVIDENCE_DURABLE",
        content_hash="deadbeef",
        measured_size_bytes=1,
        content_derived_identity="forged",
    )
    attempt = ModelAttemptRecord(
        execution_owner_id="pipeline-run:forged",
        build_record_id="build:forged",
        prompt_artifact_id="artifact:forged",
        execution_profile_identity="profile:forged",
        request_evidence_identity=evidence.request_evidence_identity,
        request_evidence_state="REQUEST_EVIDENCE_DURABLE",
        attempt_ordinal=1,
        build_cutoff=fixture.BUILD_CUTOFF,
        attempt_cutoff=fixture.ATTEMPT_CUTOFF,
        recorded_at=fixture.RECORDED_AT,
        idempotency_capability="SUPPORTED",
    )
    forged = ModelHandoffRecord(
        attempt_id=attempt.attempt_id,
        build_record_id="build:forged",
        prompt_artifact_id="artifact:forged",
        execution_profile_identity="profile:forged",
        request_evidence_identity=evidence.request_evidence_identity,
        fact_record_id="FORGED",
        policy_record_id="FORGED",
        field_category_registry_id="FORGED",
        authorization_rule_id="FORGED",
        attempt_cutoff=fixture.ATTEMPT_CUTOFF,
        processing_decision="ALLOW",
        durable_disposition_identity="FORGED",
    )

    # Even with a genuine authority store, the forged bindings are rejected.
    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=attempt,
            handoff=forged,
            request_evidence=evidence,
            attempt_authority=fixture.attempt_authority(),
        )

    assert not repository.attempt_exists(attempt.attempt_id)


def test_persistence_rejects_authority_that_does_not_permit_processing(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    """Persistence rederives rather than trusting the handoff's own ALLOW claim."""
    prepared = _prepare(service)

    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=prepared.attempt,
            handoff=prepared.handoff,
            request_evidence=prepared.request_evidence,
            attempt_authority=fixture.attempt_authority(processing="DENY"),
        )


def test_persistence_rejects_authority_resolved_at_a_different_cutoff(
    repository: ModelAdapterPersistenceRepository, service: ModelAdapterService
) -> None:
    prepared = _prepare(service)

    with pytest.raises(ModelAdapterAuthorityMismatch):
        repository.persist_attempt_and_handoff(
            attempt=prepared.attempt,
            handoff=prepared.handoff,
            request_evidence=prepared.request_evidence,
            attempt_authority=fixture.attempt_authority(cutoff=fixture.later(500)),
        )


# --------------------------------------------------------------------------
# 15. Strict-known historical reconstruction.
# --------------------------------------------------------------------------


def test_historical_read_before_the_record_existed_returns_nothing(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    prepared = _prepare(service)

    assert repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.BUILD_CUTOFF) is None
    assert repository.strict_known_attempt(prepared.attempt.attempt_id, fixture.later(60)) is not None


def test_prohibited_historical_request_content_is_never_regenerated(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    """Reconstruction reports governed unavailability rather than rebuilding.

    The prompt artifact still exists and could trivially be re-hashed. Strict-known
    reconstruction must refuse to do that, because the content categories were
    denied at the historical cutoff.
    """
    prepared = _prepare(service, attempt_authority=fixture.attempt_authority(request_content=False))

    reconstructed = repository.strict_known_request_evidence(prepared.attempt.attempt_id, fixture.later(60))

    assert reconstructed is not None
    assert reconstructed.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"
    assert reconstructed.content_hash is None
    assert reconstructed.measured_size_bytes is None
    assert reconstructed.content_derived_identity is None
    assert reconstructed.content_hash != fixture.prompt_artifact().content_hash


def test_authorized_historical_request_content_is_returned_as_recorded(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    """Paired positive: unavailability must not swallow authorized evidence."""
    prepared = _prepare(service)

    reconstructed = repository.strict_known_request_evidence(prepared.attempt.attempt_id, fixture.later(60))

    assert reconstructed is not None
    assert reconstructed.state == "REQUEST_EVIDENCE_DURABLE"
    assert reconstructed.content_hash == fixture.prompt_artifact().content_hash


def test_later_authority_cannot_be_substituted_into_a_historical_attempt(
    service: ModelAdapterService, repository: ModelAdapterPersistenceRepository
) -> None:
    """A permissive successor must not retroactively rewrite recorded evidence."""
    prepared = _prepare(service, attempt_authority=fixture.attempt_authority(request_content=False))

    # A later, fully permissive authority exists; the historical read must ignore it.
    fixture.attempt_authority(cutoff=fixture.later(120), request_content=True)

    reconstructed = repository.strict_known_request_evidence(prepared.attempt.attempt_id, fixture.later(180))

    assert reconstructed is not None
    assert reconstructed.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"


def test_upstream_prompt_and_build_identities_are_never_mutated(service: ModelAdapterService) -> None:
    artifact = fixture.prompt_artifact()
    build = fixture.build_record(artifact)
    before = (artifact.artifact_id, artifact.content, artifact.content_hash, build.build_record_id)

    _prepare(service, prompt_artifact=artifact, build_record=build)

    assert (artifact.artifact_id, artifact.content, artifact.content_hash, build.build_record_id) == before


def test_prompt_artifact_must_belong_to_the_supplied_build(service: ModelAdapterService) -> None:
    artifact = fixture.prompt_artifact()
    other = fixture.prompt_artifact(content="a different canonical prompt")

    with pytest.raises(ModelAdapterError):
        _prepare(service, prompt_artifact=other, build_record=fixture.build_record(artifact))


# --------------------------------------------------------------------------
# 16-17. Zero network, and the seam is not a provider abstraction.
# --------------------------------------------------------------------------


def test_the_whole_phase_a_pipeline_opens_no_socket(
    service: ModelAdapterService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest available zero-network proof: sockets are made to explode."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Phase A must not perform any network operation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    prepared = _prepare(service)
    result = service.consume_handoff(handoff=prepared.handoff, consumed_at=fixture.later(1))

    assert result.transmitted_bytes == 0


def test_the_dispatch_seam_exposes_no_provider_surface() -> None:
    """The seam must not already imply a live transport."""
    seam = NoNetworkDispatchSeam()
    names = {name for name in dir(seam) if not name.startswith("_")}

    assert names == {"dispatch", "dispatched_handoff_ids"}
    for forbidden in ("endpoint", "base_url", "client", "api_key", "session", "headers", "send_request"):
        assert not hasattr(seam, forbidden)


def test_no_provider_sdk_is_imported_by_the_model_adapter() -> None:
    import hunter.evidence_intelligence.model_adapter as module
    import hunter.evidence_intelligence.model_adapter_persistence as persistence

    for source in (Path(module.__file__).read_text(), Path(persistence.__file__).read_text()):
        lowered = source.lower()
        for forbidden in ("openai", "anthropic", "google.genai", "groq", "ollama", "httpx", "requests", "urllib"):
            assert forbidden not in lowered, forbidden


def test_legacy_provider_path_is_not_reused_as_the_model_adapter() -> None:
    """The legacy runner must not be relabeled as ADR 0034-conformant."""
    import hunter.evidence_intelligence.model_adapter as module

    source = Path(module.__file__).read_text()
    assert "SecureAIProviderRunner" not in source
    assert "AIExtractionProvider" not in source


def test_execution_profile_offers_no_routing_or_selection_surface() -> None:
    """Phase A carries one configured profile; nothing ranks or selects."""
    names = {name for name in dir(ModelExecutionProfile) if not name.startswith("_")}

    assert "satisfies" in names
    for forbidden in ("select", "rank", "score", "fallback", "route", "choose", "next_profile"):
        assert not any(forbidden in name for name in names), forbidden


def test_attempt_record_exposes_no_outcome_mutation_surface() -> None:
    """An attempt is never updated into a result."""
    names = {name for name in dir(ModelAttemptRecord) if not name.startswith("_")}

    for forbidden in ("outcome", "complete", "finish", "succeed", "fail", "response"):
        assert not any(forbidden in name for name in names), forbidden


def test_attempt_identity_is_deterministic_from_hunter_controlled_inputs() -> None:
    def build(recorded_at: datetime) -> ModelAttemptRecord:
        return ModelAttemptRecord(
            execution_owner_id="run:1",
            build_record_id="build:1",
            prompt_artifact_id="artifact:1",
            execution_profile_identity="profile:1",
            request_evidence_identity="evidence:1",
            request_evidence_state="REQUEST_EVIDENCE_DURABLE",
            attempt_ordinal=1,
            build_cutoff=fixture.BUILD_CUTOFF,
            attempt_cutoff=fixture.ATTEMPT_CUTOFF,
            recorded_at=recorded_at,
            idempotency_capability="SUPPORTED",
        )

    assert build(fixture.RECORDED_AT).attempt_id == build(fixture.RECORDED_AT).attempt_id
    assert build(fixture.RECORDED_AT).attempt_id != build(datetime(2026, 8, 22, 13, tzinfo=UTC)).attempt_id
