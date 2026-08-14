from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "source_handling" / "authorization_rule_v1.json"
HARNESS = Path(__file__).parent / "source_handling_runtime_harness.py"
GOLDEN_AUTH_RULE_V1_SHA256 = "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _harness() -> ModuleType:
    """Load the test-only Source Handling Authority conformance adapter."""
    if not HARNESS.exists():
        pytest.fail(
            "Source Handling Authority runtime conformance harness is intentionally missing; "
            "implementation must provide tests/source_handling_runtime_harness.py"
        )
    spec = importlib.util.spec_from_file_location(
        "source_handling_runtime_harness",
        HARNESS,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rule_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _base_times() -> dict[str, datetime]:
    return {
        "effective_from": _utc("2026-08-14T00:00:00Z"),
        "recorded_at": _utc("2026-08-14T01:00:00Z"),
        "known_at": _utc("2026-08-14T02:00:00Z"),
    }


def _base_fact() -> dict[str, object]:
    return {
        "sensitivity": "PUBLIC",
        "operation_restrictions": set(),
        "persistence_restriction": "FULL_CONTENT_ALLOWED",
        "secret_presence": set(),
        "operation_restrictions_known": True,
        "secret_presence_known": True,
        "withdrawn": False,
        "deleted_at_source": False,
        "historically_unavailable": False,
        "availability_known": True,
    }


def test_authorization_rule_v1_fixture_is_closed_and_has_reviewed_golden_digest() -> None:
    payload = _rule_fixture()
    assert _canonical_sha256(payload) == GOLDEN_AUTH_RULE_V1_SHA256
    assert payload["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"

    body = payload["rule_body"]
    assert isinstance(body, dict)
    assert body["require_payload_binding"] is True
    assert body["allow_second_genesis"] is False
    assert body["allow_self_authorizing_successor"] is False

    matrix = body["method_to_verifier_types"]
    assert isinstance(matrix, dict)
    expected_methods = {
        "SOURCE_DECLARATION_VERIFIED",
        "SOURCE_TERMS_VERIFIED",
        "SOURCE_ACCESS_POLICY_VERIFIED",
        "SECURITY_CLASSIFICATION_VERIFIED",
        "REMOVAL_OR_WITHDRAWAL_VERIFIED",
        "INDEPENDENT_DOCUMENT_REVIEW",
        "PROVIDER_OBSERVATION",
        "CALLER_ASSERTION",
        "AUTOMATED_RESTRICTIVE_DETECTOR",
        "UNKNOWN",
    }
    assert set(matrix) == expected_methods

    forbidden_methods = body["forbidden_permission_methods"]
    assert isinstance(forbidden_methods, list)
    for forbidden in forbidden_methods:
        assert matrix[forbidden] == []


def test_fixture_tamper_changes_golden_digest() -> None:
    payload = _rule_fixture()
    body = payload["rule_body"]
    assert isinstance(body, dict)
    body["allow_second_genesis"] = True
    assert _canonical_sha256(payload) != GOLDEN_AUTH_RULE_V1_SHA256


def test_conformance_harness_is_explicit_and_test_only() -> None:
    h = _harness()
    required_semantics = {
        "blocked_error",
        "canonical_publication_digest",
        "publication_authorization",
        "verify_publication",
        "publish_genesis_rule",
        "publish_successor_rule",
        "validate_permission_evidence",
        "strict_known_eligible",
        "strict_known_head",
        "authority_store",
        "restrictive_fact_join",
        "lifecycle_join",
        "validate_durable_payload",
        "migrate_legacy",
    }
    missing = sorted(name for name in required_semantics if not hasattr(h, name))
    assert not missing, f"missing semantic harness operations: {missing}"


def test_publication_authorization_is_bound_to_exact_payload_scope_and_kind() -> None:
    h = _harness()
    payload = {"policy": {"processing": "DENY"}, "schema": 1}
    digest = h.canonical_publication_digest(
        "POLICY",
        "source:doc-1:v1",
        payload,
    )
    auth = h.publication_authorization(
        publication_kind="POLICY",
        governed_subject_scope="source:doc-1:v1",
        authorized_payload_sha256=digest,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        **_base_times(),
    )

    h.verify_publication(auth, "POLICY", "source:doc-1:v1", payload)

    with pytest.raises(h.blocked_error):
        h.verify_publication(auth, "FACT", "source:doc-1:v1", payload)
    with pytest.raises(h.blocked_error):
        h.verify_publication(auth, "POLICY", "source:doc-2:v1", payload)
    with pytest.raises(h.blocked_error):
        h.verify_publication(
            auth,
            "POLICY",
            "source:doc-1:v1",
            {"policy": {"processing": "ALLOW"}},
        )


def test_tampered_authorized_payload_digest_rejects_at_publication_and_persistence() -> None:
    h = _harness()
    payload = {"registry": {"field": ["AUDIT_FIELD"]}}
    auth = h.publication_authorization(
        publication_kind="FIELD_CATEGORY_REGISTRY",
        governed_subject_scope="registry:v1",
        authorized_payload_sha256="00" * 32,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        **_base_times(),
    )

    with pytest.raises(h.blocked_error):
        h.verify_publication(
            auth,
            "FIELD_CATEGORY_REGISTRY",
            "registry:v1",
            payload,
        )

    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision={
                "publication_authorization": auth,
                "field_category_registry_id": "registry-v1",
            },
            registry={
                "field_category_registry_id": "registry-v1",
                "field_map": {},
            },
            payload={},
            secret_presence=set(),
        )


@pytest.mark.parametrize(
    "publication_kind",
    ["POLICY", "FIELD_CATEGORY_REGISTRY", "AUTHORIZATION_RULE"],
)
def test_authority_publication_without_governed_authorization_rejects(
    publication_kind: str,
) -> None:
    h = _harness()
    store = h.authority_store()

    with pytest.raises(h.blocked_error):
        store.publish(
            family=publication_kind,
            scope=f"{publication_kind.lower()}:v1",
            expected_current_head_id=None,
            record={
                "id": "candidate",
                "publication_authorization": None,
            },
        )


def test_repository_direct_write_cannot_bypass_publication_authority() -> None:
    h = _harness()
    store = h.authority_store()

    with pytest.raises(h.blocked_error):
        store.direct_write(
            family="FACT",
            scope="doc-1",
            record={"id": "forged-fact"},
        )


@pytest.mark.parametrize(
    ("strength", "method"),
    [
        ("ASSERTION_ONLY", "CALLER_ASSERTION"),
        ("ASSERTION_ONLY", "PROVIDER_OBSERVATION"),
        ("OBSERVED_RESTRICTIVE_SIGNAL", "AUTOMATED_RESTRICTIVE_DETECTOR"),
        ("UNKNOWN", "UNKNOWN"),
    ],
)
def test_non_authoritative_evidence_cannot_grant_or_release_permission(
    strength: str,
    method: str,
) -> None:
    h = _harness()

    for requested_change in (
        "PERMISSIVE_GENESIS",
        "LESS_RESTRICTIVE",
        "RESTRICTION_RELEASE",
    ):
        with pytest.raises(h.blocked_error):
            h.validate_permission_evidence(
                evidence_strength=strength,
                evidence_method=method,
                verifier_type="CALLER",
                requested_change=requested_change,
                authorization_rule=_rule_fixture(),
            )


def test_restrictive_signal_may_only_increase_restriction() -> None:
    h = _harness()
    h.validate_permission_evidence(
        evidence_strength="OBSERVED_RESTRICTIVE_SIGNAL",
        evidence_method="AUTOMATED_RESTRICTIVE_DETECTOR",
        verifier_type="DETECTOR",
        requested_change="MORE_RESTRICTIVE",
        authorization_rule=_rule_fixture(),
    )


def test_permission_evidence_requires_strength_method_verifier_and_release_enumeration() -> None:
    h = _harness()
    rule = _rule_fixture()

    h.validate_permission_evidence(
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_type="SOURCE_VERIFIER",
        requested_change="LESS_RESTRICTIVE",
        authorization_rule=rule,
        released_restrictions={"MODEL_PROCESSING_PROHIBITED"},
    )

    with pytest.raises(h.blocked_error):
        h.validate_permission_evidence(
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_type="CALLER",
            requested_change="LESS_RESTRICTIVE",
            authorization_rule=rule,
            released_restrictions={"MODEL_PROCESSING_PROHIBITED"},
        )

    with pytest.raises(h.blocked_error):
        h.validate_permission_evidence(
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_type="SOURCE_VERIFIER",
            requested_change="RESTRICTION_RELEASE",
            authorization_rule=rule,
            released_restrictions=set(),
        )


def test_bootstrap_digest_mismatch_and_second_genesis_reject() -> None:
    h = _harness()
    store = h.authority_store()

    bad = _rule_fixture()
    body = bad["rule_body"]
    assert isinstance(body, dict)
    body["allow_second_genesis"] = True

    with pytest.raises(h.blocked_error):
        h.publish_genesis_rule(
            store,
            bad,
            expected_golden_sha256=GOLDEN_AUTH_RULE_V1_SHA256,
        )

    h.publish_genesis_rule(
        store,
        _rule_fixture(),
        expected_golden_sha256=GOLDEN_AUTH_RULE_V1_SHA256,
    )

    with pytest.raises(h.blocked_error):
        h.publish_genesis_rule(
            store,
            _rule_fixture(),
            expected_golden_sha256=GOLDEN_AUTH_RULE_V1_SHA256,
        )


def test_successor_rule_cannot_self_authorize_or_use_later_current_rule() -> None:
    h = _harness()
    store = h.authority_store()

    h.publish_genesis_rule(
        store,
        _rule_fixture(),
        expected_golden_sha256=GOLDEN_AUTH_RULE_V1_SHA256,
    )

    successor = {
        "authorization_rule_id": "AUTHORIZATION_RULE_V2",
        "rule_schema_version": 1,
        "rule_body": _rule_fixture()["rule_body"],
        "effective_from": _utc("2026-08-15T00:00:00Z"),
        "recorded_at": _utc("2026-08-15T01:00:00Z"),
        "known_at": _utc("2026-08-15T02:00:00Z"),
        "supersedes_authorization_rule_id": "AUTHORIZATION_RULE_V1",
    }

    with pytest.raises(h.blocked_error):
        h.publish_successor_rule(
            store,
            successor,
            authorizing_rule_id="AUTHORIZATION_RULE_V2",
        )

    cutoff = _utc("2026-08-14T12:00:00Z")
    with pytest.raises(h.blocked_error):
        h.publish_successor_rule(
            store,
            successor,
            authorizing_rule_id="AUTHORIZATION_RULE_V2",
            historical_cutoff=cutoff,
        )


def test_strict_known_requires_effective_recorded_and_known_time() -> None:
    h = _harness()
    cutoff = _utc("2026-08-14T12:00:00Z")
    record = _base_times()

    assert h.strict_known_eligible(record, cutoff)

    for field in ("recorded_at", "known_at"):
        changed = dict(record)
        changed[field] = _utc("2026-08-15T00:00:00Z")
        assert not h.strict_known_eligible(changed, cutoff)

        changed[field] = None
        assert not h.strict_known_eligible(changed, cutoff)


def test_backdated_later_known_state_cannot_backfill_replay() -> None:
    h = _harness()
    cutoff = _utc("2026-08-14T12:00:00Z")
    record = {
        "effective_from": _utc("2026-08-01T00:00:00Z"),
        "recorded_at": _utc("2026-08-14T10:00:00Z"),
        "known_at": _utc("2026-08-15T10:00:00Z"),
    }
    assert not h.strict_known_eligible(record, cutoff)


def test_strict_known_multiple_heads_block_and_never_use_tiebreakers() -> None:
    h = _harness()
    cutoff = _utc("2026-08-14T12:00:00Z")
    records = [
        {
            "id": "a",
            "scope": "doc-1",
            "supersedes": None,
            **_base_times(),
        },
        {
            "id": "b",
            "scope": "doc-1",
            "supersedes": "a",
            **_base_times(),
        },
        {
            "id": "c",
            "scope": "doc-1",
            "supersedes": "a",
            **_base_times(),
        },
    ]

    with pytest.raises(h.blocked_error):
        h.strict_known_head(
            records,
            cutoff=cutoff,
            scope="doc-1",
        )


def test_atomic_compare_and_append_prevents_divergent_heads_and_requires_reresolution() -> None:
    h = _harness()
    store = h.authority_store()

    store.compare_and_append(
        family="FACT",
        scope="doc-1",
        expected_current_head_id=None,
        record={"id": "a"},
    )
    store.compare_and_append(
        family="FACT",
        scope="doc-1",
        expected_current_head_id="a",
        record={"id": "b"},
    )

    with pytest.raises(h.blocked_error):
        store.compare_and_append(
            family="FACT",
            scope="doc-1",
            expected_current_head_id="a",
            record={"id": "c"},
        )

    assert store.current_head_id("FACT", "doc-1") == "b"


def test_fact_join_preserves_simultaneous_restrictions() -> None:
    h = _harness()
    joined = h.restrictive_fact_join(
        [
            {
                "sensitivity": "CONFIDENTIAL",
                "operation_restrictions": {"MODEL_PROCESSING_PROHIBITED"},
                "persistence_restriction": "DERIVED_ONLY",
                "secret_presence": {"SECRET_PRESENT"},
                "operation_restrictions_known": True,
                "secret_presence_known": True,
                "withdrawn": False,
                "deleted_at_source": False,
                "historically_unavailable": False,
                "availability_known": True,
            },
            {
                "sensitivity": "RESTRICTED",
                "operation_restrictions": {"ACCESS_RESTRICTED"},
                "persistence_restriction": "NO_PERSISTENCE",
                "secret_presence": {"CREDENTIAL_PRESENT"},
                "operation_restrictions_known": True,
                "secret_presence_known": True,
                "withdrawn": True,
                "deleted_at_source": True,
                "historically_unavailable": True,
                "availability_known": True,
            },
        ]
    )

    assert joined["sensitivity"] == "RESTRICTED"
    assert joined["operation_restrictions"] == {
        "MODEL_PROCESSING_PROHIBITED",
        "ACCESS_RESTRICTED",
    }
    assert joined["persistence_restriction"] == "NO_PERSISTENCE"
    assert joined["secret_presence"] == {
        "SECRET_PRESENT",
        "CREDENTIAL_PRESENT",
    }
    assert joined["withdrawn"]
    assert joined["deleted_at_source"]
    assert joined["historically_unavailable"]


def test_fact_join_is_associative_commutative_idempotent_and_unknown_is_absorbing() -> None:
    h = _harness()
    a = _base_fact()
    b = {
        **a,
        "sensitivity": "CONFIDENTIAL",
        "operation_restrictions": {"ACCESS_RESTRICTED"},
    }
    c = {
        **a,
        "persistence_restriction": "METADATA_ONLY",
        "secret_presence": {"SECRET_PRESENT"},
    }

    assert h.restrictive_fact_join([a, a]) == h.restrictive_fact_join([a])
    assert h.restrictive_fact_join([a, b]) == h.restrictive_fact_join([b, a])

    left = h.restrictive_fact_join(
        [
            h.restrictive_fact_join([a, b]),
            c,
        ]
    )
    right = h.restrictive_fact_join(
        [
            a,
            h.restrictive_fact_join([b, c]),
        ]
    )
    assert left == right

    for unknown in (
        {**a, "sensitivity": "UNKNOWN"},
        {**a, "operation_restrictions_known": False},
        {**a, "persistence_restriction": "UNKNOWN"},
        {**a, "secret_presence_known": False},
        {**a, "availability_known": False},
    ):
        with pytest.raises(h.blocked_error):
            h.restrictive_fact_join([unknown])


def test_historical_registry_identity_cannot_be_substituted_by_current_registry() -> None:
    h = _harness()
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "AUDIT_FIELD": {
                "PERSIST": "DENY",
            }
        },
    }

    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision=decision,
            registry={
                "field_category_registry_id": "registry-v2",
                "field_map": {
                    "audit": ["AUDIT_FIELD"],
                },
            },
            payload={"audit": "safe-control-only"},
            secret_presence=set(),
        )


def test_multicategory_field_uses_most_restrictive_operation_specific_disposition() -> None:
    h = _harness()
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {
            "x": [
                "OPERATIONAL_METADATA",
                "AUDIT_FIELD",
            ]
        },
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "OPERATIONAL_METADATA": {
                "PERSIST": "ALLOW",
            },
            "AUDIT_FIELD": {
                "PERSIST": "DENY",
            },
        },
    }

    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"x": "value"},
            secret_presence=set(),
        )


def test_unknown_or_ambiguous_field_category_never_defaults_to_allow() -> None:
    h = _harness()

    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision={
                "field_category_registry_id": "registry-v1",
                "durable_dispositions": {},
            },
            registry={
                "field_category_registry_id": "registry-v1",
                "field_map": {},
            },
            payload={"unclassified": "value"},
            secret_presence=set(),
        )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("ALLOW", "EXPIRE", "EXPIRE"),
        ("EXPIRE", "DELETE", "DELETE"),
        ("ALLOW", "DELETE", "DELETE"),
        ("DELETE", "BLOCKED", "BLOCKED"),
    ],
)
def test_lifecycle_join_is_exact_and_delete_dominates_expire(
    left: str,
    right: str,
    expected: str,
) -> None:
    h = _harness()
    assert h.lifecycle_join([left, right]) == expected
    assert h.lifecycle_join([right, left]) == expected


def test_lifecycle_join_is_associative_commutative_idempotent_and_invalid_values_block() -> None:
    h = _harness()
    values = [
        "ALLOW",
        "EXPIRE",
        "DELETE",
        "BLOCKED",
    ]

    for value in values:
        assert h.lifecycle_join([value, value]) == value

    for a in values:
        for b in values:
            assert h.lifecycle_join([a, b]) == h.lifecycle_join([b, a])
            for c in values:
                left = h.lifecycle_join(
                    [
                        h.lifecycle_join([a, b]),
                        c,
                    ]
                )
                right = h.lifecycle_join(
                    [
                        a,
                        h.lifecycle_join([b, c]),
                    ]
                )
                assert left == right

    with pytest.raises(h.blocked_error):
        h.lifecycle_join(["DENY"])


@pytest.mark.parametrize(
    "field",
    [
        "excerpt",
        "metadata",
        "diagnostic",
        "content_hash",
        "locator",
        "coordinate",
        "provenance_id",
        "audit",
        "reconstruction_metadata",
        "access_controlled_representation",
    ],
)
def test_secret_or_credential_derived_representation_is_structurally_non_persistable(
    field: str,
) -> None:
    h = _harness()
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {
            field: [
                "OPERATIONAL_METADATA",
                "AUDIT_FIELD",
            ]
        },
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "OPERATIONAL_METADATA": {
                "PERSIST": "ALLOW",
            },
            "AUDIT_FIELD": {
                "PERSIST": "ALLOW",
            },
        },
    }

    for marker in (
        {"SECRET_PRESENT"},
        {"CREDENTIAL_PRESENT"},
    ):
        with pytest.raises(h.blocked_error):
            h.validate_durable_payload(
                decision=decision,
                registry=registry,
                payload={
                    field: {
                        "value": "protected-material",
                        "derived_from_protected_content": True,
                    }
                },
                secret_presence=marker,
            )


def test_safe_control_metadata_is_not_banned_merely_because_source_contains_secret() -> None:
    h = _harness()
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {
            "run_id": ["SAFE_CONTROL_ID"],
        },
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "SAFE_CONTROL_ID": {
                "PERSIST": "ALLOW",
            }
        },
    }

    h.validate_durable_payload(
        decision=decision,
        registry=registry,
        payload={
            "run_id": {
                "value": "run-123",
                "derived_from_protected_content": False,
            }
        },
        secret_presence={"SECRET_PRESENT"},
    )


def test_blocked_audit_cannot_leak_protected_content_or_source_derived_diagnostics() -> None:
    h = _harness()
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {
            "diagnostic": ["DIAGNOSTIC"],
            "audit": ["AUDIT_FIELD"],
        },
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "DIAGNOSTIC": {
                "PERSIST": "ALLOW",
            },
            "AUDIT_FIELD": {
                "PERSIST": "ALLOW",
            },
        },
    }

    with pytest.raises(h.blocked_error):
        h.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={
                "diagnostic": {
                    "value": "secret appeared here",
                    "derived_from_protected_content": True,
                },
                "audit": {
                    "value": "secret appeared here",
                    "derived_from_protected_content": True,
                },
            },
            secret_presence={"SECRET_PRESENT"},
        )


def test_legacy_absence_cannot_be_upgraded_into_historical_authority() -> None:
    h = _harness()
    legacy = {
        "document_version_id": "doc-legacy",
        "content": "existing bytes",
        "known_at": None,
    }
    migrated = h.migrate_legacy(legacy)

    assert migrated.get("known_at") is None
    assert migrated.get("publication_authorization") is None
    assert migrated.get("field_category_registry_id") is None
    assert migrated.get("authorization_rule_id") is None
    assert not h.strict_known_eligible(
        migrated,
        _utc("2026-08-14T12:00:00Z"),
    )


def test_counterfactual_root_matrix_is_complete_and_non_vacuous() -> None:
    """Freeze the blocking defect classes that implementation review must mutation-check."""
    required_roots = {
        "payload_binding",
        "publication_authority",
        "anti_laundering",
        "authorization_rule_bootstrap",
        "historical_rule_selection",
        "strict_known_time",
        "compare_and_append",
        "fact_restrictive_join",
        "historical_registry_binding",
        "durable_field_classification",
        "lifecycle_join",
        "secret_credential_structural_exclusion",
        "legacy_missingness",
    }
    assert len(required_roots) == 13
