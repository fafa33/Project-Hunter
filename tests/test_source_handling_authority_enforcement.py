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


def _authorization(
    h: ModuleType,
    store,
    *,
    family: str,
    scope: str,
    payload: object,
    authorization_id: str | None = None,
):
    return h.issue_publication_authorization(
        store,
        publication_kind=family,
        governed_subject_scope=scope,
        payload=payload,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        authorization_id=authorization_id or f"auth:{family}:{scope}",
        evidence_ids=(f"evidence:{family}:{scope}",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=("verifier:source-handling",),
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
    authorization = _authorization(h, store, family=family, scope=scope, payload=payload)
    store.publish(
        family=family,
        scope=scope,
        expected_current_head_id=None,
        record={
            "id": record_id,
            **payload,
            "publication_payload": payload,
            "publication_authorization": authorization,
        },
    )


def _complete_disposition(persist: str = "ALLOW") -> dict[str, str]:
    return {
        "PERSIST": persist,
        "READ_ACCESS": "ALLOW",
        "RECONSTRUCT": "ALLOW",
        "DELETE_OR_EXPIRE": "ALLOW",
    }


def _ready_store(
    h: ModuleType,
    *,
    persist: str = "ALLOW",
    retention: str = "ALLOW",
    secret_presence: list[str] | None = None,
    field_category: str = "AUDIT_FIELD",
):
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    fact_payload = {
        "scope": "doc-1",
        "fact": {
            "sensitivity": "PUBLIC",
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": secret_presence or [],
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
    _publish(
        h,
        store,
        family="FACT",
        scope="doc-1",
        record_id="fact-v1",
        payload=fact_payload,
    )

    registry_payload = {
        "scope": "registry:source-handling:v1",
        "field_category_registry_id": "registry-v1",
        "field_map": {"audit": [field_category]},
        "safe_control_proofs": (
            {"audit": {"proof_id": "proof:audit-control:v1", "allowed_values": ["safe"]}}
            if field_category == "SAFE_CONTROL_ID"
            else {}
        ),
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
            "retention_decision": retention,
            "reconstruction_decision": "ALLOW",
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": "ALLOW",
            "durable_dispositions": {field_category: _complete_disposition(persist)},
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


def test_caller_constructed_but_unissued_authorization_cannot_publish() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {"scope": "doc-1", "value": "candidate", **_times()}
    forged = h.publication_authorization(
        publication_kind="FACT",
        governed_subject_scope="doc-1",
        authorized_payload_sha256=h.canonical_publication_digest("FACT", "doc-1", payload),
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        authorization_id="forged-auth",
        evidence_ids=("forged-evidence",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=("forged-verifier",),
        verifier_type="SOURCE_VERIFIER",
        **_times(),
    )

    with pytest.raises(h.blocked_error):
        store.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            record={"id": "forged", **payload, "publication_authorization": forged},
        )


def test_repository_publication_recomputes_exact_candidate_body_not_supplied_payload_alias() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    authorized_payload = {
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
        **_times(),
    }
    authorization = _authorization(
        h,
        store,
        family="FACT",
        scope="doc-1",
        payload=authorized_payload,
    )

    with pytest.raises(h.blocked_error):
        store.publish(
            family="FACT",
            scope="doc-1",
            expected_current_head_id=None,
            record={
                "id": "fact-v1",
                "scope": "doc-1",
                "fact": {
                    **authorized_payload["fact"],
                    "sensitivity": "INTERNAL",
                },
                **_times(),
                "publication_payload": authorized_payload,
                "publication_authorization": authorization,
            },
        )
    assert store.current_canonical_head_id("FACT", "doc-1") is None


def test_physical_compare_and_append_cannot_forge_canonical_marker() -> None:
    h = _harness()
    store = h.authority_store()
    store.compare_and_append(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={
            "id": "physical-only",
            "scope": "doc-1",
            "_publication_verified": True,
            **_times(),
        },
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


def test_append_deep_copies_candidate_so_caller_mutation_cannot_rewrite_history() -> None:
    h = _harness()
    store = h.authority_store()
    nested = {"value": "original"}
    store.compare_and_append(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={"id": "physical", "nested": nested},
    )
    nested["value"] = "mutated"
    assert store.records("FACT", "doc-1")[0]["nested"] == {"value": "original"}


def test_strict_known_orphan_predecessor_blocks_instead_of_becoming_head() -> None:
    h = _harness()
    with pytest.raises(h.blocked_error):
        h.strict_known_head(
            [
                {
                    "id": "orphan",
                    "scope": "doc-1",
                    "supersedes": "missing-parent",
                    **_times(),
                }
            ],
            cutoff=_utc("2026-08-14T12:00:00Z"),
            scope="doc-1",
        )


def test_persistence_independently_resolves_rederives_and_accepts_matching_authority() -> None:
    h = _harness()
    store = _ready_store(h, field_category="SAFE_CONTROL_ID")

    decision = h.enforce_persistence(
        store,
        fact_scope="doc-1",
        policy_scope="policy:source-handling:v1",
        cutoff=_utc("2026-08-14T12:00:00Z"),
        payload={
            "audit": {
                "value": "safe",
                "derived_from_protected_content": False,
            }
        },
    )

    assert decision["fact_record_id"] == "fact-v1"
    assert decision["policy_record_id"] == "policy-v1"
    assert decision["field_category_registry_id"] == "registry-v1"
    assert decision["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"


def test_persistence_rejects_caller_decision_even_when_payload_itself_would_be_allowed() -> None:
    h = _harness()
    store = _ready_store(h, field_category="SAFE_CONTROL_ID")

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
        "durable_dispositions": {"SAFE_CONTROL_ID": _complete_disposition()},
    }

    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={
                "audit": {
                    "value": "safe",
                    "derived_from_protected_content": False,
                }
            },
            supplied_decision=forged,
        )


def test_persistence_enforces_rederived_durable_disposition_not_caller_payload_preference() -> None:
    h = _harness()
    store = _ready_store(
        h,
        persist="DENY",
        field_category="SAFE_CONTROL_ID",
    )

    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={
                "audit": {
                    "value": "safe",
                    "derived_from_protected_content": False,
                }
            },
        )


def test_complete_durable_operation_map_is_required_before_persistence() -> None:
    h = _harness()
    fact_record = {
        "id": "fact-v1",
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
    }
    policy_record = {
        "id": "policy-v1",
        "field_category_registry_id": "registry-v1",
        "policy_body": {
            "processing_decision": "ALLOW",
            "retention_decision": "ALLOW",
            "reconstruction_decision": "ALLOW",
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": "ALLOW",
            "durable_dispositions": {"AUDIT_FIELD": {"PERSIST": "ALLOW"}},
        },
    }
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {"audit": ["AUDIT_FIELD"]},
    }
    with pytest.raises(h.blocked_error):
        h.derive_source_handling_decision(
            fact_record=fact_record,
            policy_record=policy_record,
            registry_record=registry,
            authorization_rule={"id": "AUTHORIZATION_RULE_V1"},
        )


def test_secret_source_cannot_use_risky_secondary_category_even_with_plain_scalar() -> None:
    h = _harness()
    store = _ready_store(
        h,
        secret_presence=["SECRET_PRESENT"],
        field_category="AUDIT_FIELD",
    )

    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={"audit": "plain-text-that-could-carry-protected-material"},
        )


def test_counterfactual_payload_binding_mutation_is_non_vacuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)

    payload = {
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
        **_times(),
    }
    authorization = _authorization(
        h,
        store,
        family="FACT",
        scope="doc-1",
        payload=payload,
    )
    monkeypatch.setattr(
        h.runtime_module,
        "verify_publication",
        lambda *_args, **_kwargs: None,
    )

    store.publish(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={
            "id": "mutant-fact",
            **payload,
            "publication_authorization": authorization,
        },
    )
    assert store.current_canonical_head_id("FACT", "doc-1") == "mutant-fact"


def test_counterfactual_persistence_field_enforcement_mutation_is_non_vacuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness()
    store = _ready_store(
        h,
        persist="DENY",
        field_category="SAFE_CONTROL_ID",
    )

    monkeypatch.setattr(
        h.runtime_module,
        "validate_durable_payload",
        lambda **_kwargs: None,
    )
    decision = h.enforce_persistence(
        store,
        fact_scope="doc-1",
        policy_scope="policy:source-handling:v1",
        cutoff=_utc("2026-08-14T12:00:00Z"),
        payload={
            "audit": {
                "value": "would-have-been-denied",
                "derived_from_protected_content": False,
            }
        },
    )
    assert decision["durable_dispositions"]["SAFE_CONTROL_ID"]["PERSIST"] == "DENY"


def test_codex_p1_caller_cannot_launder_provenance_classification() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-x",
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
        **_times(),
    }
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(
            store,
            authorization_id="auth:launder",
            publication_kind="FACT",
            governed_subject_scope="doc-x",
            payload=payload,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            evidence_ids=("not-canonical-evidence",),
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_ids=("not-canonical-verifier",),
            verifier_type="SOURCE_VERIFIER",
            **_times(),
        )


def test_codex_p1_restrictive_label_cannot_hide_permissive_genesis() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-permissive",
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
        **_times(),
    }
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(
            store,
            authorization_id="auth:detector-launder",
            publication_kind="FACT",
            governed_subject_scope="doc-permissive",
            payload=payload,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            evidence_ids=("evidence:detector-launder",),
            evidence_strength="OBSERVED_RESTRICTIVE_SIGNAL",
            evidence_method="AUTOMATED_RESTRICTIVE_DETECTOR",
            verifier_ids=("verifier:detector-launder",),
            verifier_type="DETECTOR",
            requested_change="MORE_RESTRICTIVE",
            **_times(),
        )


def test_codex_p1_top_level_retention_denial_blocks_persistence() -> None:
    h = _harness()
    store = _ready_store(h, retention="DENY", field_category="AUDIT_FIELD")
    with pytest.raises(h.blocked_error):
        h.enforce_persistence(
            store,
            fact_scope="doc-1",
            policy_scope="policy:source-handling:v1",
            cutoff=_utc("2026-08-14T12:00:00Z"),
            payload={"audit": "safe-audit"},
        )


def test_codex_p1_safe_control_requires_canonical_registry_proof() -> None:
    h = _harness()
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {"SAFE_CONTROL_ID": _complete_disposition()},
    }
    registry = {"field_category_registry_id": "registry-v1", "field_map": {"audit": ["SAFE_CONTROL_ID"]}}
    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"audit": {"value": "secret", "derived_from_protected_content": False}},
            secret_presence={"SECRET_PRESENT"},
        )


def test_codex_p1_stale_authorization_rule_cannot_issue_after_successor_is_applicable() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    v2 = {
        "id": "AUTHORIZATION_RULE_V2",
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "scope": "SOURCE_HANDLING",
        "rule_schema_version": 1,
        "rule_body": _rule_fixture()["rule_body"],
        "effective_from": _utc("2026-08-14T03:00:00Z"),
        "recorded_at": _utc("2026-08-14T04:00:00Z"),
        "known_at": _utc("2026-08-14T05:00:00Z"),
        "supersedes_authorization_rule_id": "AUTHORIZATION_RULE_V1",
    }
    store.compare_and_append(
        family="AUTHORIZATION_RULE",
        scope="SOURCE_HANDLING",
        expected_current_head_id="AUTHORIZATION_RULE_V1",
        record=v2,
    )
    store._canonical_keys.add(("AUTHORIZATION_RULE", "SOURCE_HANDLING", "AUTHORIZATION_RULE_V2"))
    payload = {
        "scope": "doc-stale",
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
        **_times(),
    }
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(
            store,
            authorization_id="auth:stale-rule",
            publication_kind="FACT",
            governed_subject_scope="doc-stale",
            payload=payload,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            evidence_ids=("evidence:stale-rule",),
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_ids=("verifier:stale-rule",),
            verifier_type="SOURCE_VERIFIER",
            **_times(),
        )


def test_replay_rederives_change_type_instead_of_trusting_persisted_label() -> None:
    h = _harness()
    store = h.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-replay-derived-change",
        "fact": {
            "sensitivity": "INTERNAL",
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
    authorization = h.issue_publication_authorization(
        store,
        authorization_id="auth:replay-derived-change",
        publication_kind="FACT",
        governed_subject_scope="doc-replay-derived-change",
        payload=payload,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        evidence_ids=("evidence:detector:replay-derived-change",),
        evidence_strength="OBSERVED_RESTRICTIVE_SIGNAL",
        evidence_method="AUTOMATED_RESTRICTIVE_DETECTOR",
        verifier_ids=("verifier:detector:replay-derived-change",),
        verifier_type="DETECTOR",
        requested_change="PERMISSIVE_GENESIS",
        **_times(),
    )
    store.publish(
        family="FACT",
        scope="doc-replay-derived-change",
        expected_current_head_id=None,
        record={
            "id": "fact-replay-derived-change",
            **payload,
            "publication_authorization": authorization,
        },
    )
    resolved = h.resolve_canonical_head(
        store,
        family="FACT",
        scope="doc-replay-derived-change",
        cutoff=_utc("2026-08-14T12:00:00Z"),
    )
    assert resolved["id"] == "fact-replay-derived-change"


def test_default_authority_store_cannot_authorize_without_canonical_provenance() -> None:
    h = _harness()
    store = h.runtime_module.authority_store()
    h.publish_genesis_rule(store, _rule_fixture(), expected_golden_sha256=GOLDEN)
    payload = {
        "scope": "doc-no-provenance",
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
        **_times(),
    }
    with pytest.raises(h.blocked_error):
        h.issue_publication_authorization(
            store,
            authorization_id="auth:no-provenance",
            publication_kind="FACT",
            governed_subject_scope="doc-no-provenance",
            payload=payload,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            evidence_ids=("evidence:invented",),
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_ids=("verifier:invented",),
            verifier_type="SOURCE_VERIFIER",
            **_times(),
        )
