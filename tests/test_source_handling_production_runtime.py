from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.evidence_intelligence.intake import (
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    evidence_document_id,
)
from hunter.evidence_intelligence.pre_model import resolve_pre_model_source_handling
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.source_handling import (
    PublicationAuthorization,
    SourceHandlingBlockedError,
    canonical_publication_digest,
    publication_authorization,
    resolve_canonical_head,
)
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    SourceHandlingAuthorityRepository,
    SourceHandlingAuthorityService,
    SourceHandlingOperatorRoot,
)

RULE_FIXTURE = Path(__file__).parent / "fixtures" / "source_handling" / "authorization_rule_v1.json"
RULE_GOLDEN = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"
START = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
INTAKE_FIELD_MAP = {
    "issue_content": ["ISSUE_CONTENT"],
    "content_derived_ids": ["CONTENT_DERIVED_ID"],
    "locator_urls": ["LOCATOR_URL"],
    "source_derived_text": ["SOURCE_DERIVED_TEXT"],
    "intake_metadata": ["OPERATIONAL_METADATA"],
}
INTAKE_TABLES = (
    "evidence_documents",
    "evidence_document_versions",
    "evidence_spans",
    "document_lifecycle_events",
    "source_authority_verification_events",
    "document_lifecycle_event_span_links",
)


class MutableClock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _private_key_bytes() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_bytes(private_key: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(private_key)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def _operator_root(private_key: bytes, *, genesis_digest: str = RULE_GOLDEN) -> SourceHandlingOperatorRoot:
    return SourceHandlingOperatorRoot(
        genesis_rule_sha256=genesis_digest,
        verification_key_sha256=hashlib.sha256(_public_key_bytes(private_key)).hexdigest(),
    )


def _provenance(provenance_id: str, provenance_kind: str, cutoff: datetime) -> dict[str, Any] | None:
    known = cutoff - timedelta(days=1)
    base = {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "effective_from": known,
        "recorded_at": known,
        "known_at": known,
    }
    if provenance_kind == "EVIDENCE" and provenance_id.startswith("evidence:"):
        return {
            **base,
            "evidence_strength": "AUTHORITATIVE_SOURCE_EVIDENCE",
            "evidence_method": "SOURCE_TERMS_VERIFIED",
        }
    if provenance_kind == "VERIFIER" and provenance_id.startswith("verifier:"):
        return {**base, "verifier_type": "SOURCE_VERIFIER"}
    return None


def _service(
    tmp_path: Path,
    *,
    clock: MutableClock | None = None,
    key: bytes | None = None,
) -> tuple[SourceHandlingAuthorityService, MutableClock, bytes, str]:
    active_clock = clock or MutableClock()
    active_key = key or _private_key_bytes()
    service = SourceHandlingAuthorityService(
        tmp_path / "evidence.sqlite",
        signing_private_key=active_key,
        operator_root=_operator_root(active_key),
        provenance_resolver=_provenance,
        clock=active_clock,
    )
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    result = service.publish_genesis_rule(rule)
    return service, active_clock, active_key, result.record_id


def _times(value: datetime) -> dict[str, datetime]:
    return {"effective_from": value, "recorded_at": value, "known_at": value}


def _fact_payload(
    document_id: str,
    at: datetime,
    *,
    supersedes: str | None = None,
    sensitivity: str = "PUBLIC",
    secrets: tuple[str, ...] = (),
    known_overrides: dict[str, bool] | None = None,
) -> dict[str, Any]:
    known = {
        "sensitivity_known": True,
        "operation_restrictions_known": True,
        "persistence_restriction_known": True,
        "secret_presence_known": True,
        **(known_overrides or {}),
    }
    payload: dict[str, Any] = {
        "scope": document_id,
        "fact": {
            "sensitivity": sensitivity,
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": list(secrets),
            **known,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        **_times(at),
    }
    if supersedes is not None:
        payload["supersedes_fact_record_id"] = supersedes
    return payload


def _registry_payload(document_id: str, at: datetime, *, registry_id: str) -> dict[str, Any]:
    return {
        "scope": f"registry:{document_id}:v1",
        "field_category_registry_id": registry_id,
        "field_map": copy.deepcopy(INTAKE_FIELD_MAP),
        "safe_control_proofs": {},
        **_times(at),
    }


def _policy_payload(
    document_id: str,
    at: datetime,
    *,
    registry_id: str,
    retention: str = "ALLOW",
    persist: str = "ALLOW",
    deletion: str = "ALLOW",
    include_unused_denial: bool = False,
    category_persist_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    overrides = category_persist_overrides or {}
    dispositions = {
        category: {
            "PERSIST": overrides.get(category, persist),
            "READ_ACCESS": "ALLOW",
            "RECONSTRUCT": "ALLOW",
            "DELETE_OR_EXPIRE": "ALLOW",
        }
        for categories in INTAKE_FIELD_MAP.values()
        for category in categories
    }
    if include_unused_denial:
        dispositions["UNUSED_CATEGORY"] = {
            "PERSIST": "DENY",
            "READ_ACCESS": "DENY",
            "RECONSTRUCT": "DENY",
            "DELETE_OR_EXPIRE": "DELETE",
        }
    return {
        "scope": f"policy:{document_id}:v1",
        "field_category_registry_id": registry_id,
        "policy_body": {
            "processing_decision": "ALLOW",
            "retention_decision": retention,
            "reconstruction_decision": "ALLOW",
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": deletion,
            "durable_dispositions": dispositions,
        },
        **_times(at),
    }


def _authorize(
    service: SourceHandlingAuthorityService,
    *,
    family: str,
    scope: str,
    payload: dict[str, Any],
    rule_id: str,
    expected_head: str | None,
    authorization_id: str,
    expires_at: datetime,
) -> PublicationAuthorization:
    at = datetime.fromisoformat(str(payload["known_at"]).replace("Z", "+00:00"))
    return service.issue_authorization(
        publication_kind=family,
        governed_subject_scope=scope,
        payload=payload,
        authorization_rule_id=rule_id,
        expected_current_head_id=expected_head,
        evidence_ids=(f"evidence:{authorization_id}",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=(f"verifier:{authorization_id}",),
        verifier_type="SOURCE_VERIFIER",
        effective_from=at,
        recorded_at=at,
        known_at=at,
        expires_at=expires_at,
        authorization_id=authorization_id,
    )


def _publish(
    service: SourceHandlingAuthorityService,
    *,
    family: str,
    scope: str,
    payload: dict[str, Any],
    rule_id: str,
    expected_head: str | None,
    authorization_id: str,
    expires_at: datetime,
) -> tuple[str, PublicationAuthorization]:
    authorization = _authorize(
        service,
        family=family,
        scope=scope,
        payload=payload,
        rule_id=rule_id,
        expected_head=expected_head,
        authorization_id=authorization_id,
        expires_at=expires_at,
    )
    result = service.publish(
        family=family,
        scope=scope,
        expected_current_head_id=expected_head,
        payload=payload,
        authorization=authorization,
    )
    return result.record_id, authorization


def _complete_authority(
    service: SourceHandlingAuthorityService,
    clock: MutableClock,
    rule_id: str,
    *,
    document_id: str = "doc-1",
    secrets: tuple[str, ...] = (),
    retention: str = "ALLOW",
    persist: str = "ALLOW",
    deletion: str = "ALLOW",
    include_unused_denial: bool = False,
    category_persist_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    fact_id, _ = _publish(
        service,
        family="FACT",
        scope=document_id,
        payload=_fact_payload(document_id, clock.now(), secrets=secrets),
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:fact:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    registry_logical_id = f"registry:{document_id}:v1"
    registry_id, _ = _publish(
        service,
        family="FIELD_CATEGORY_REGISTRY",
        scope=f"registry:{document_id}:v1",
        payload=_registry_payload(document_id, clock.now(), registry_id=registry_logical_id),
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:registry:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    policy_id, _ = _publish(
        service,
        family="POLICY",
        scope=f"policy:{document_id}:v1",
        payload=_policy_payload(
            document_id,
            clock.now(),
            registry_id=registry_logical_id,
            retention=retention,
            persist=persist,
            deletion=deletion,
            include_unused_denial=include_unused_denial,
            category_persist_overrides=category_persist_overrides,
        ),
        rule_id=rule_id,
        expected_head=None,
        authorization_id=f"auth:policy:{document_id}",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    return {"fact": fact_id, "registry": registry_id, "policy": policy_id, "rule": rule_id}


def _reference(content: str = "ordinary issue content") -> EvidenceIntakeReference:
    return EvidenceIntakeReference(
        source_evidence_id="issue:407",
        raw_evidence_id="issue:407:body",
        normalized_evidence_id="issue:407:body:normalized",
        candidate_id="candidate-1",
        identity_resolution_status="resolved",
        source_url="https://github.com/fafa33/Project-Hunter/issues/407",
        source_provider="github",
        source_type="issue",
        source_claimed_authority="repository-owner",
        title="Issue 407",
        content=content,
        metadata={"issue_number": 407, "labels": ["runtime"]},
    )


def _assert_zero_durable_intake(repository: EvidenceIntelligenceRepository) -> None:
    assert {table: repository.count(table) for table in INTAKE_TABLES} == {table: 0 for table in INTAKE_TABLES}


@pytest.mark.parametrize("key", [b"", b"x" * 31, b"x" * 33])
def test_missing_or_malformed_signing_key_fails_closed(tmp_path: Path, key: bytes) -> None:
    with pytest.raises(SourceHandlingBlockedError, match="key material"):
        SourceHandlingAuthorityService(
            tmp_path / "db.sqlite",
            signing_private_key=key,
            operator_root=SourceHandlingOperatorRoot(
                genesis_rule_sha256=RULE_GOLDEN,
                verification_key_sha256="00" * 32,
            ),
            provenance_resolver=_provenance,
        )


@pytest.mark.parametrize("key", [b"", b"x" * 31, b"x" * 33])
def test_missing_or_malformed_verification_key_fails_closed(tmp_path: Path, key: bytes) -> None:
    with pytest.raises(SourceHandlingBlockedError, match="key material"):
        SourceHandlingAuthorityRepository(
            tmp_path / "db.sqlite",
            verification_public_key=key,
            operator_root=SourceHandlingOperatorRoot(
                genesis_rule_sha256=RULE_GOLDEN,
                verification_key_sha256="00" * 32,
            ),
            record_integrity_signer=lambda _message: b"",
            provenance_resolver=_provenance,
        )


def test_genesis_digest_replay_and_history_guards(tmp_path: Path) -> None:
    service, _clock, _key, _rule_id = _service(tmp_path)
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(SourceHandlingBlockedError, match="empty history"):
        service.publish_genesis_rule(rule)

    other = copy.deepcopy(rule)
    other["authorization_rule_id"] = "AUTHORIZATION_RULE_V2"
    with pytest.raises(SourceHandlingBlockedError, match="digest mismatch"):
        service.publish_genesis_rule(other)


def test_genesis_golden_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    clock = MutableClock()
    key = _private_key_bytes()
    service = SourceHandlingAuthorityService(
        tmp_path / "db.sqlite",
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    altered = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    altered["rule_body"]["permissive_evidence_strengths"] = ["CALLER_ASSERTION"]
    with pytest.raises(SourceHandlingBlockedError, match="digest mismatch"):
        service.publish_genesis_rule(altered)


def test_runtime_caller_cannot_supply_matching_digest_for_arbitrary_genesis(tmp_path: Path) -> None:
    key = _private_key_bytes()
    service = SourceHandlingAuthorityService(
        tmp_path / "db.sqlite",
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=MutableClock(),
    )
    altered = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    altered["rule_body"]["permissive_evidence_strengths"] = ["CALLER_ASSERTION"]
    caller_digest = hashlib.sha256(
        json.dumps(altered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(TypeError, match="expected_golden_sha256"):
        service.publish_genesis_rule(altered, expected_golden_sha256=caller_digest)  # type: ignore[call-arg]

    genuine = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    assert service.publish_genesis_rule(genuine).record_id


def test_genesis_and_verification_key_are_pinned_across_restart(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    assert (
        resolve_canonical_head(
            restarted.resolver()("doc-1", clock.now()).store,
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            cutoff=clock.now(),
        )["id"]
        == rule_id
    )

    unrelated_key = _private_key_bytes()
    with pytest.raises(SourceHandlingBlockedError, match="operator root|verification key"):
        SourceHandlingAuthorityService(
            service.path,
            signing_private_key=unrelated_key,
            operator_root=_operator_root(unrelated_key),
            provenance_resolver=_provenance,
            clock=clock,
        )


def test_rewritten_genesis_with_recomputed_hash_fails_closed(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    with sqlite3.connect(service.path) as connection:
        payload = json.loads(
            str(
                connection.execute(
                    "SELECT payload_json FROM source_handling_authority_records WHERE record_id = ?",
                    (rule_id,),
                ).fetchone()[0]
            )
        )
        payload["rule_body"]["permissive_evidence_strengths"] = ["CALLER_ASSERTION"]
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        rewritten_id = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE source_handling_authority_records "
            "SET record_id = ?, payload_sha256 = ?, payload_json = ? WHERE record_id = ?",
            (rewritten_id, rewritten_id, payload_json, rule_id),
        )
        connection.execute(
            "UPDATE source_handling_canonical_keys SET current_record_id = ? "
            "WHERE family = 'AUTHORIZATION_RULE' AND scope = 'SOURCE_HANDLING'",
            (rewritten_id,),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        resolve_canonical_head(
            service.resolver()("doc-1", clock.now()).store,
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            cutoff=clock.now(),
        )


def test_repository_has_no_direct_write_bypass(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    repository = SourceHandlingAuthorityRepository(
        tmp_path / "db.sqlite",
        verification_public_key=public_key,
        operator_root=SourceHandlingOperatorRoot(
            genesis_rule_sha256=RULE_GOLDEN,
            verification_key_sha256=hashlib.sha256(public_key).hexdigest(),
        ),
        record_integrity_signer=private_key.sign,
        provenance_resolver=_provenance,
    )
    with pytest.raises(SourceHandlingBlockedError, match="direct repository authority writes"):
        repository.direct_write()


def test_signed_authorization_is_consumed_once_and_replay_fails(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:once",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    result = service.publish(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        payload=payload,
        authorization=authorization,
    )
    assert service.authorization_consumed("auth:once") is True
    assert result.record_id
    with pytest.raises(SourceHandlingBlockedError, match="consumed|head changed"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda auth: replace(auth, authorization_id="changed-id"),
        lambda auth: replace(auth, evidence_strength="INDEPENDENT_VERIFIED_EVIDENCE"),
        lambda auth: replace(auth, issuer_signature="00" * 64),
    ],
)
def test_changed_authorization_id_claim_or_signature_fails_closed(tmp_path: Path, mutator) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:exact-claims",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with pytest.raises(SourceHandlingBlockedError, match="signature"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=mutator(authorization),
        )
    assert service.authorization_consumed("auth:exact-claims") is False


def test_unissued_forged_authorization_fails_closed(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    forged = publication_authorization(
        publication_kind="FACT",
        governed_subject_scope="doc-1",
        authorized_payload_sha256=canonical_publication_digest("FACT", "doc-1", payload),
        authorization_rule_id=rule_id,
        effective_from=clock.now(),
        recorded_at=clock.now(),
        known_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        authorization_id="forged",
        evidence_ids=("evidence:forged",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=("verifier:forged",),
        verifier_type="SOURCE_VERIFIER",
        issuer_signature="00" * 64,
    )
    with pytest.raises(SourceHandlingBlockedError, match="signature"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=forged,
        )


def test_failed_transaction_does_not_consume_and_same_authorization_retries(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:rollback",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute("""
            CREATE TRIGGER fail_source_handling_insert
            BEFORE INSERT ON source_handling_authority_records
            BEGIN SELECT RAISE(ABORT, 'simulated crash'); END
            """)
    with pytest.raises(SourceHandlingBlockedError):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )
    assert service.authorization_consumed("auth:rollback") is False
    with sqlite3.connect(service.path) as connection:
        connection.execute("DROP TRIGGER fail_source_handling_insert")
    result = service.publish(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        payload=payload,
        authorization=authorization,
    )
    assert result.record_id
    assert service.authorization_consumed("auth:rollback") is True


def test_head_cas_race_rejects_loser_without_consuming_it(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    first = _fact_payload("doc-1", clock.now())
    second = _fact_payload("doc-1", clock.now(), sensitivity="INTERNAL")
    first_auth = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=first,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:race:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    second_auth = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=second,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:race:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    service.publish(
        family="FACT", scope="doc-1", expected_current_head_id=None, payload=first, authorization=first_auth
    )
    with pytest.raises(SourceHandlingBlockedError, match="head changed"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=second,
            authorization=second_auth,
        )
    assert service.authorization_consumed("auth:race:second") is False


def test_stale_predecessor_is_rejected(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    head, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:head",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    stale_payload = _fact_payload("doc-1", clock.now(), supersedes=head, sensitivity="INTERNAL")
    stale_auth = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=stale_payload,
        rule_id=rule_id,
        expected_head=head,
        authorization_id="auth:stale",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    winner_payload = _fact_payload("doc-1", clock.now(), supersedes=head, sensitivity="CONFIDENTIAL")
    _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=winner_payload,
        rule_id=rule_id,
        expected_head=head,
        authorization_id="auth:winner",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with pytest.raises(SourceHandlingBlockedError, match="head changed"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=head,
            payload=stale_payload,
            authorization=stale_auth,
        )
    assert service.authorization_consumed("auth:stale") is False


def test_backdated_successor_is_invisible_to_earlier_cutoff(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    original_cutoff = clock.now()
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", original_cutoff),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:historical:first",
        expires_at=original_cutoff + timedelta(hours=1),
    )
    clock.value = original_cutoff + timedelta(minutes=10)
    backdated = _fact_payload("doc-1", original_cutoff, supersedes=first, sensitivity="RESTRICTED")
    second, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=backdated,
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:historical:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    view = service.resolver()("doc-1", original_cutoff).store
    assert resolve_canonical_head(view, family="FACT", scope="doc-1", cutoff=original_cutoff)["id"] == first
    assert resolve_canonical_head(view, family="FACT", scope="doc-1", cutoff=clock.now())["id"] == second


@pytest.mark.parametrize("delta", [timedelta(minutes=-10), timedelta(minutes=10)])
def test_direct_admission_time_mutation_fails_tamper_verification(tmp_path: Path, delta: timedelta) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    record_id, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:admission:tamper",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_authority_records SET admission_time = ? WHERE record_id = ?",
            ((clock.now() + delta).isoformat().replace("+00:00", "Z"), record_id),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")


def test_tampered_backdated_admission_cannot_change_earlier_replay(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    original_cutoff = clock.now()
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", original_cutoff),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:admission:first",
        expires_at=original_cutoff + timedelta(hours=1),
    )
    clock.value += timedelta(minutes=10)
    second, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", original_cutoff, supersedes=first, sensitivity="RESTRICTED"),
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:admission:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_authority_records SET admission_time = ? WHERE record_id = ?",
            (original_cutoff.isoformat().replace("+00:00", "Z"), second),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        resolve_canonical_head(
            service.resolver()("doc-1", original_cutoff).store,
            family="FACT",
            scope="doc-1",
            cutoff=original_cutoff,
        )


def test_restart_persistence_and_read_only_resolver(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    ids = _complete_authority(service, clock, rule_id)
    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    resolver = restarted.resolver()
    authority = resolver("doc-1", clock.now())
    resolved = resolve_pre_model_source_handling(authority)
    assert resolved.fact_record["id"] == ids["fact"]
    assert resolved.decision["retention_decision"] == "ALLOW"
    assert not hasattr(resolver, "publish")
    assert not hasattr(authority.store, "publish")
    assert not hasattr(authority.store, "direct_write")


def test_tampered_payload_fails_closed_on_read(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    ids = _complete_authority(service, clock, rule_id)
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_authority_records SET payload_json = replace(payload_json, 'PUBLIC', 'RESTRICTED') "
            "WHERE record_id = ?",
            (ids["fact"],),
        )
    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        resolve_pre_model_source_handling(service.resolver()("doc-1", clock.now()))


def test_missing_predecessor_and_divergent_canonical_key_fail_closed(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:chain:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    second, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now(), supersedes=first, sensitivity="INTERNAL"),
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:chain:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_canonical_keys SET current_record_id = ? "
            "WHERE family = 'FACT' AND scope = 'doc-1'",
            (first,),
        )
    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_canonical_keys SET current_record_id = ? "
            "WHERE family = 'FACT' AND scope = 'doc-1'",
            (second,),
        )
        connection.execute(
            "UPDATE source_handling_authority_records SET supersedes_record_id = 'missing' WHERE record_id = ?",
            (second,),
        )
    with pytest.raises(SourceHandlingBlockedError):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")


def test_authenticated_history_rejects_head_rewind_and_successor_truncation(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:rewind:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    second, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now(), supersedes=first, sensitivity="INTERNAL"),
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:rewind:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_canonical_keys "
            "SET current_record_id = ?, revision = 1 "
            "WHERE family = 'FACT' AND scope = 'doc-1'",
            (first,),
        )
        connection.execute(
            "DELETE FROM source_handling_authority_records WHERE record_id = ?",
            (second,),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")


def test_authenticated_history_rejects_deleted_middle_record(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:middle:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    second, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now(), supersedes=first, sensitivity="INTERNAL"),
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:middle:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now(), supersedes=second, sensitivity="RESTRICTED"),
        rule_id=rule_id,
        expected_head=second,
        authorization_id="auth:middle:third",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "DELETE FROM source_handling_authority_records WHERE record_id = ?",
            (second,),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE source_handling_canonical_keys SET revision = 1 " "WHERE family = 'FACT' AND scope = 'doc-1'",
        "UPDATE source_handling_canonical_keys SET current_record_id = "
        "(SELECT supersedes_record_id FROM source_handling_authority_records "
        "WHERE record_id = source_handling_canonical_keys.current_record_id) "
        "WHERE family = 'FACT' AND scope = 'doc-1'",
    ],
    ids=["lower-revision", "stale-head"],
)
def test_authenticated_history_rejects_canonical_state_rewrite(tmp_path: Path, mutation: str) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    first, _ = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:canonical:first",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=_fact_payload("doc-1", clock.now(), supersedes=first, sensitivity="INTERNAL"),
        rule_id=rule_id,
        expected_head=first,
        authorization_id="auth:canonical:second",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(mutation)

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.resolver()("doc-1", clock.now()).store.canonical_records("FACT", "doc-1")


def test_consumption_reset_is_tamper_evident_and_authorization_cannot_replay(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    _record_id, authorization = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:consumption-reset",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute(
            "UPDATE source_handling_publication_authorizations "
            "SET consumed_at = NULL, consumed_record_id = NULL "
            "WHERE authorization_id = ?",
            (authorization.authorization_id,),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )


def test_deleted_publication_and_consumption_reset_cannot_enable_replay(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    record_id, authorization = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:delete-and-reset",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with sqlite3.connect(service.path) as connection:
        connection.execute("DELETE FROM source_handling_canonical_keys WHERE family = 'FACT' AND scope = 'doc-1'")
        connection.execute(
            "DELETE FROM source_handling_authority_records WHERE record_id = ?",
            (record_id,),
        )
        connection.execute(
            "UPDATE source_handling_publication_authorizations "
            "SET consumed_at = NULL, consumed_record_id = NULL "
            "WHERE authorization_id = ?",
            (authorization.authorization_id,),
        )

    with pytest.raises(SourceHandlingBlockedError, match="TAMPER_DETECTED"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )


def test_consumption_remains_single_use_after_restart_and_new_authorization_works(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    _record_id, authorization = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:restart-once",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    with pytest.raises(SourceHandlingBlockedError, match="consumed|head changed"):
        restarted.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )

    new_record, _ = _publish(
        restarted,
        family="FACT",
        scope="doc-2",
        payload=_fact_payload("doc-2", clock.now()),
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:restart-new",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    assert new_record


@pytest.mark.parametrize("flag", ["sensitivity_known", "persistence_restriction_known"])
def test_unknown_fact_dimensions_block_before_authorization(tmp_path: Path, flag: str) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now(), known_overrides={flag: False})
    with pytest.raises(SourceHandlingBlockedError, match="unknown"):
        _authorize(
            service,
            family="FACT",
            scope="doc-1",
            payload=payload,
            rule_id=rule_id,
            expected_head=None,
            authorization_id=f"auth:unknown:{flag}",
            expires_at=clock.now() + timedelta(minutes=5),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("secret_presence", ["UNKNOWN"]),
        ("secret_presence", ["SECRET_PRESENT", "UNKNOWN"]),
        ("operation_restrictions", ["UNKNOWN"]),
        ("operation_restrictions", ["ACCESS_RESTRICTED", "UNKNOWN"]),
        ("secret_presence", [123]),
        ("secret_presence", ["SECRET_PRESENT", None]),
        ("operation_restrictions", [{"value": "ACCESS_RESTRICTED"}]),
        ("operation_restrictions", ["ACCESS_RESTRICTED", False]),
        ("secret_presence", "SECRET_PRESENT"),
        ("operation_restrictions", ("ACCESS_RESTRICTED",)),
    ],
)
def test_unknown_or_malformed_fact_restriction_values_block_before_authorization(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    payload["fact"][field] = value

    with pytest.raises(SourceHandlingBlockedError, match="unknown|unsupported"):
        _authorize(
            service,
            family="FACT",
            scope="doc-1",
            payload=payload,
            rule_id=rule_id,
            expected_head=None,
            authorization_id=f"auth:invalid:{field}",
            expires_at=clock.now() + timedelta(minutes=5),
        )


def test_all_supported_fact_restriction_values_publish_and_resolve(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    payload["fact"]["operation_restrictions"] = [
        "MODEL_PROCESSING_PROHIBITED",
        "RECONSTRUCTION_PROHIBITED",
        "ACCESS_RESTRICTED",
    ]
    payload["fact"]["secret_presence"] = ["SECRET_PRESENT", "CREDENTIAL_PRESENT"]

    record_id, _authorization = _publish(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:all-supported-restrictions",
        expires_at=clock.now() + timedelta(minutes=5),
    )

    record = resolve_canonical_head(
        service.resolver()("doc-1", clock.now()).store,
        family="FACT",
        scope="doc-1",
        cutoff=clock.now(),
    )
    assert record["id"] == record_id
    assert set(record["fact"]["operation_restrictions"]) == {
        "MODEL_PROCESSING_PROHIBITED",
        "RECONSTRUCTION_PROHIBITED",
        "ACCESS_RESTRICTED",
    }
    assert set(record["fact"]["secret_presence"]) == {"SECRET_PRESENT", "CREDENTIAL_PRESENT"}


def test_expired_authorization_is_not_consumed(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    payload = _fact_payload("doc-1", clock.now())
    authorization = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:expires",
        expires_at=clock.now() + timedelta(minutes=1),
    )
    clock.value += timedelta(minutes=2)
    with pytest.raises(SourceHandlingBlockedError, match="expired"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=payload,
            authorization=authorization,
        )
    assert service.authorization_consumed("auth:expires") is False


@pytest.mark.parametrize(
    "malformation",
    [
        "missing_rule_body",
        "malformed_rule_body",
        "missing_required_rule_field",
        "wrong_nested_field_type",
        "unknown_evidence_strength",
        "unknown_evidence_method",
        "unknown_verifier_type",
        "incomplete_verifier_matrix",
        "weakened_forbidden_methods",
        "invalid_permission_claim",
        "no_usable_authorization_path",
        "wrong_schema_version_type",
    ],
)
def test_malformed_successor_rule_is_rejected_without_consumption_or_head_change(
    tmp_path: Path,
    malformation: str,
) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    clock.value += timedelta(minutes=1)
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    valid_payload = {
        **rule,
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": "SOURCE_HANDLING",
        "supersedes_authorization_rule_id": rule_id,
        **_times(clock.now()),
    }
    authorization_id = f"auth:malformed-rule:{malformation}"
    authorization = _authorize(
        service,
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        payload=valid_payload,
        rule_id=rule_id,
        expected_head=rule_id,
        authorization_id=authorization_id,
        expires_at=clock.now() + timedelta(minutes=5),
    )
    malformed = copy.deepcopy(valid_payload)
    body = malformed["rule_body"]
    if malformation == "missing_rule_body":
        malformed.pop("rule_body")
    elif malformation == "malformed_rule_body":
        malformed["rule_body"] = "not-an-object"
    elif malformation == "missing_required_rule_field":
        body.pop("method_to_verifier_types")
    elif malformation == "wrong_nested_field_type":
        body["permissive_evidence_strengths"] = "AUTHORITATIVE_SOURCE_EVIDENCE"
    elif malformation == "unknown_evidence_strength":
        body["permissive_evidence_strengths"] = ["UNKNOWN"]
    elif malformation == "unknown_evidence_method":
        body["forbidden_permission_methods"].append("NOT_A_METHOD")
    elif malformation == "unknown_verifier_type":
        body["method_to_verifier_types"]["SOURCE_TERMS_VERIFIED"] = ["UNKNOWN_VERIFIER"]
    elif malformation == "incomplete_verifier_matrix":
        body["method_to_verifier_types"].pop("SOURCE_TERMS_VERIFIED")
    elif malformation == "weakened_forbidden_methods":
        body["forbidden_permission_methods"].remove("CALLER_ASSERTION")
    elif malformation == "invalid_permission_claim":
        body["require_payload_binding"] = False
    elif malformation == "no_usable_authorization_path":
        body["method_to_verifier_types"] = {method: [] for method in body["method_to_verifier_types"]}
    elif malformation == "wrong_schema_version_type":
        malformed["rule_schema_version"] = "1"
    else:
        raise AssertionError(f"unhandled malformation: {malformation}")

    with pytest.raises(SourceHandlingBlockedError, match="authorization-rule"):
        service.publish(
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            expected_current_head_id=rule_id,
            payload=malformed,
            authorization=authorization,
        )
    assert service.authorization_consumed(authorization_id) is False
    assert (
        service.resolver()("doc-after-malformed-rule", clock.now()).store.current_canonical_head_id(
            "AUTHORIZATION_RULE", "SOURCE_HANDLING"
        )
        == rule_id
    )

    result = service.publish(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id=rule_id,
        payload=valid_payload,
        authorization=authorization,
    )
    assert service.authorization_consumed(authorization_id) is True
    assert result.record_id != rule_id

    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    assert (
        restarted.resolver()("doc-after-malformed-rule", clock.now()).store.current_canonical_head_id(
            "AUTHORIZATION_RULE", "SOURCE_HANDLING"
        )
        == result.record_id
    )


def test_successor_authorization_rule_uses_normal_signed_publication(tmp_path: Path) -> None:
    service, clock, key, rule_id = _service(tmp_path)
    clock.value += timedelta(minutes=1)
    successor_cutoff = clock.now()
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    payload = {
        **rule,
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": "SOURCE_HANDLING",
        "supersedes_authorization_rule_id": rule_id,
        **_times(clock.now()),
    }
    successor, authorization = _publish(
        service,
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        payload=payload,
        rule_id=rule_id,
        expected_head=rule_id,
        authorization_id="auth:rule:v2",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    assert successor != rule_id
    assert authorization.authorization_rule_id == rule_id
    assert service.authorization_consumed("auth:rule:v2") is True

    view = service.resolver()("doc-after-rule", successor_cutoff).store
    assert (
        resolve_canonical_head(
            view,
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            cutoff=successor_cutoff - timedelta(microseconds=1),
        )["id"]
        == rule_id
    )
    assert (
        resolve_canonical_head(
            view,
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            cutoff=successor_cutoff,
        )["id"]
        == successor
    )

    clock.value += timedelta(minutes=1)
    ids = _complete_authority(service, clock, successor, document_id="doc-after-rule")
    assert ids["fact"] and ids["policy"] and ids["registry"]

    restarted = SourceHandlingAuthorityService(
        service.path,
        signing_private_key=key,
        operator_root=_operator_root(key),
        provenance_resolver=_provenance,
        clock=clock,
    )
    resolved = resolve_pre_model_source_handling(restarted.resolver()("doc-after-rule", clock.now()))
    assert resolved.authorization_rule["id"] == successor


def test_self_authorizing_successor_rule_is_rejected(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    clock.value += timedelta(minutes=1)
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    payload = {
        **rule,
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": "SOURCE_HANDLING",
        "supersedes_authorization_rule_id": rule_id,
        **_times(clock.now()),
    }
    successor_id = canonical_publication_digest("AUTHORIZATION_RULE", "SOURCE_HANDLING", payload)
    with pytest.raises(SourceHandlingBlockedError, match="stale authorization rule"):
        _authorize(
            service,
            family="AUTHORIZATION_RULE",
            scope="SOURCE_HANDLING",
            payload=payload,
            rule_id=successor_id,
            expected_head=rule_id,
            authorization_id="auth:rule:self",
            expires_at=clock.now() + timedelta(minutes=5),
        )


def test_authorization_issued_under_superseded_rule_is_stale_and_unconsumed(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    fact_payload = _fact_payload("doc-1", clock.now())
    fact_authorization = _authorize(
        service,
        family="FACT",
        scope="doc-1",
        payload=fact_payload,
        rule_id=rule_id,
        expected_head=None,
        authorization_id="auth:stale-rule:fact",
        expires_at=clock.now() + timedelta(minutes=10),
    )
    clock.value += timedelta(minutes=1)
    rule = json.loads(RULE_FIXTURE.read_text(encoding="utf-8"))
    successor_payload = {
        **rule,
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": "SOURCE_HANDLING",
        "supersedes_authorization_rule_id": rule_id,
        **_times(clock.now()),
    }
    _publish(
        service,
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        payload=successor_payload,
        rule_id=rule_id,
        expected_head=rule_id,
        authorization_id="auth:stale-rule:successor",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    with pytest.raises(SourceHandlingBlockedError, match="stale authorization rule"):
        service.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            payload=fact_payload,
            authorization=fact_authorization,
        )
    assert service.authorization_consumed("auth:stale-rule:fact") is False


def test_issue_content_allowed_only_after_complete_retention_authority(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _complete_authority(service, clock, rule_id, document_id=document_id)
    evidence_repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
    )
    result = boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())
    assert result.document.document_id == document_id
    assert dict(result.document.metadata) == {"issue_number": 407, "labels": ["runtime"]}
    assert {table: evidence_repository.count(table) for table in INTAKE_TABLES} == {table: 1 for table in INTAKE_TABLES}


@pytest.mark.parametrize(
    "denied_category",
    ["CONTENT_DERIVED_ID", "LOCATOR_URL", "SOURCE_DERIVED_TEXT", "OPERATIONAL_METADATA"],
)
def test_issue_intake_denied_secondary_artifact_fails_before_any_durable_write(
    tmp_path: Path,
    denied_category: str,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _complete_authority(
        service,
        clock,
        rule_id,
        document_id=document_id,
        category_persist_overrides={denied_category: "DENY"},
    )
    evidence_repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
    )

    with pytest.raises(SourceHandlingBlockedError, match="persistence is not allowed"):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())

    _assert_zero_durable_intake(evidence_repository)


def test_issue_intake_requires_only_payload_categories_to_allow_persistence(tmp_path: Path) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference()
    document_id = evidence_document_id(reference)
    _complete_authority(
        service,
        clock,
        rule_id,
        document_id=document_id,
        include_unused_denial=True,
    )
    evidence_repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
    )
    boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())
    assert evidence_repository.count("evidence_documents") == 1


@pytest.mark.parametrize(
    ("secrets", "retention", "persist", "deletion"),
    [
        (("SECRET_PRESENT",), "ALLOW", "ALLOW", "ALLOW"),
        ((), "DENY", "ALLOW", "ALLOW"),
        ((), "ALLOW", "DENY", "ALLOW"),
        ((), "ALLOW", "ALLOW", "DELETE"),
    ],
)
def test_issue_content_remains_transient_when_authority_blocks(
    tmp_path: Path,
    secrets: tuple[str, ...],
    retention: str,
    persist: str,
    deletion: str,
) -> None:
    service, clock, _key, rule_id = _service(tmp_path)
    reference = _reference("credential=super-secret")
    document_id = evidence_document_id(reference)
    _complete_authority(
        service,
        clock,
        rule_id,
        document_id=document_id,
        secrets=secrets,
        retention=retention,
        persist=persist,
        deletion=deletion,
    )
    evidence_repository = EvidenceIntelligenceRepository(service.path)
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(evidence_repository),
        resolver=service.resolver(),
    )
    with pytest.raises(SourceHandlingBlockedError):
        boundary.ingest(reference, processing_run_id="run-407", processed_at=clock.now())
    _assert_zero_durable_intake(evidence_repository)
