from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

HARNESS = Path(__file__).parent / "source_handling_runtime_harness.py"
FIXTURE = Path(__file__).parent / "fixtures" / "source_handling" / "authorization_rule_v1.json"
GOLDEN = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("source_handling_runtime_harness_enforcement", HARNESS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _times() -> dict[str, datetime]:
    return {
        "effective_from": _utc("2026-08-14T03:00:00Z"),
        "recorded_at": _utc("2026-08-14T04:00:00Z"),
        "known_at": _utc("2026-08-14T05:00:00Z"),
    }


def _rule_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _authorization(h: ModuleType, *, family: str, scope: str, payload: object):
    return h.publication_authorization(
        publication_kind=family,
        governed_subject_scope=scope,
        authorized_payload_sha256=h.canonical_publication_digest(family, scope, payload),
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        authorization_id=f"auth:{family}:{scope}",
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_type="SOURCE_VERIFIER",
        **_times(),
    )


def _publish(
    h: ModuleType,
    store,
    *,
    family: str,
    scope: str,
    record_id: str,
    payload: dict[str, object],
) -> None:
    store.publish(
        family=family,
        scope=scope,
        expected_current_head_id=None,
        record={
            "id": record_id,
            **payload,
            "publication_payload": payload,
            "publication_authorization": _authorization(h, family=family, scope=scope, payload=payload),
        },
    )


def _ready_store(h: ModuleType, *, persist: str = "ALLOW"):
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    fact_payload = {
        "scope": "doc-1",
        "fact": {
            "sensitivity": "PUBLIC",
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": [],
            "operation_restrictions_known": True,
            "secret_presence_known": True,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        "requested_change": "PERMISSIVE_GENESIS",
        **_times(),
    }
    _publish(h, store, family="FACT", scope="doc-1", record_id="fact-v1", payload=fact_payload)

    registry_payload = {
        "scope": "registry:source-handling:v1",
        "field_category_registry_id": "registry-v1",
        "field_map": {"audit": ["AUDIT_FIELD"]},
        "requested_change": "PERMISSIVE_GENESIS",
        **_times(),
    }
    _publish(
        h,
        store,
        family="FIELD_CATEGORY_REGISTRY",
        scope="registry:source-handling:v1",
        record_id="registry-v1",
        payload=registry_payload,
    )

    policy_payload = {
        "scope": "policy:source-handling:v1",
        "field_category_registry_id": "registry-v1",
        "policy_body": {
            "processing_decision": "ALLOW",
            "retention_decision": "ALLOW",
            "reconstruction_decision": "ALLOW",
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": "ALLOW",
            "durable_dispositions": {"AUDIT_FIELD": {"PERSIST": persist}},
        },
        "requested_change": "PERMISSIVE_GENESIS",
        **_times(),
    }
    _publish(
        h,
        store,
        family="POLICY",
        scope="policy:source-handling:v1",
        record_id="policy-v1",
        payload=policy_payload,
    )
    return store


def test_repository_publication_rejects_non_authoritative_non_null_token() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    with pytest.raises(h.blocked_error):
        store.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            record={
                "id": "forged",
                "publication_authorization": {"looks": "non-null"},
            },
        )


def test_repository_publication_recomputes_exact_payload_binding_before_canonical_append() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    authorized_payload = {"scope": "doc-1", "value": "authorized", **_times()}
    tampered_payload = {"scope": "doc-1", "value": "tampered", **_times()}
    authorization = _authorization(h, family="FACT", scope="doc-1", payload=authorized_payload)

    with pytest.raises(h.blocked_error):
        store.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            record={
                "id": "fact-v1",
                **tampered_payload,
                "publication_payload": tampered_payload,
                "publication_authorization": authorization,
            },
        )
    assert store.current_canonical_head_id("FACT", "doc-1") is None


def test_physical_compare_and_append_cannot_create_canonical_authority() -> None:
    h = _harness()
    store = h.authority_store()
    store.compare_and_append(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={"id": "physical-only", "scope": "doc-1", **_times()},
    )

    assert store.current_head_id("FACT", "doc-1") == "physical-only"
    assert store.current_canonical_head_id("FACT", "doc-1") is None
    with pytest.raises(h.blocked_error):
        h.resolve_canonical_head(
            store,
            family="FACT",
            scope="doc-1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
        )


def test_persistence_independently_resolves_rederives_and_accepts_matching_authority() -> None:
    h = _harness()
    store = _ready_store(h)

    decision = h.enforce_persistence(
        store,
        fact_scope="doc-1",
        policy_scope="policy:source-handling:v1",
        cutoff=_utc("2026-08-14T12:00:00Z"),
        payload={"audit": {"value": "safe", "derived_from_protected_content": False}},
    )

    assert decision["fact_record_id"] == "fact-v1"
    assert decision["policy_record_id"] == "policy-v1"
    assert decision["field_category_registry_id"] == "registry-v1"
    assert decision["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"


def test_persistence_rejects_caller_decision_even_when_payload_itself_would_be_allowed() -> None:
    h = _harness()
    store = _ready_store(h)

    forged = {
        "fact_record_id": "fact-v1",
        "policy_record_id": "policy-v1",
        "field_category_registry_id": "registry-current-substitute",
        "authorization_rule_id": "AUTHORIZATION_RULE_V1",
        "processing_decision": "ALLOW",
        "retention_decision": "ALLOW",
        "reconstruction_decision": "ALLOW",
        "access_decision": "ALLOW",
        "deletion_lifecycle_decision": "ALLOW",
        "durable_dispositions": {"AUDIT_FIELD": {"PERSIST": "ALLOW"}},
    }

    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={"audit": {"value": "safe", "derived_from_protected_content": False}},
            supplied_decision=forged,
        )


def test_persistence_enforces_rederived_durable_disposition_not_caller_payload_preference() -> None:
    h = _harness()
    store = _ready_store(h, persist="DENY")

    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={"audit": {"value": "safe", "derived_from_protected_content": False}},
        )


def test_counterfactual_payload_binding_mutation_is_non_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    authorized_payload = {"scope": "doc-1", "value": "authorized", **_times()}
    tampered_payload = {"scope": "doc-1", "value": "tampered", **_times()}
    authorization = _authorization(h, family="FACT", scope="doc-1", payload=authorized_payload)

    monkeypatch.setattr(h.runtime_module, "verify_publication", lambda *_args, **_kwargs: None)
    store.publish(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={
            "id": "mutant-fact",
            **tampered_payload,
            "publication_payload": tampered_payload,
            "publication_authorization": authorization,
        },
    )
    assert store.current_canonical_head_id("FACT", "doc-1") == "mutant-fact"


def test_counterfactual_persistence_field_enforcement_mutation_is_non_vacuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness()
    store = _ready_store(h, persist="DENY")

    monkeypatch.setattr(h.runtime_module, "validate_durable_payload", lambda **_kwargs: None)
    decision = h.enforce_persistence(
        store,
        fact_scope="doc-1",
        policy_scope="policy:source-handling:v1",
        cutoff=_utc("2026-08-14T12:00:00Z"),
        payload={"audit": {"value": "would-have-been-denied", "derived_from_protected_content": False}},
    )
    assert decision["durable_dispositions"]["AUDIT_FIELD"]["PERSIST"] == "DENY"
