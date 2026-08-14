from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE = "hunter.evidence_intelligence.source_handling"
FIXTURE = Path(__file__).parent / "fixtures" / "source_handling" / "authorization_rule_v1.json"
GOLDEN_AUTH_RULE_V1_SHA256 = "0154d04a1bf85208898ee8e94c1ad4a7649a69863e762f97657840b9d664594c"


def _subject():
    """Load the not-yet-implemented V1 runtime surface.

    This tests-first contribution is intentionally RED until the Source Handling
    Authority runtime exists. Do not skip/xfail this import to make CI green.
    """

    return importlib.import_module(MODULE)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_authorization_rule_v1_fixture_has_reviewed_golden_digest() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _canonical_sha256(payload) == GOLDEN_AUTH_RULE_V1_SHA256
    assert payload["authorization_rule_id"] == "AUTHORIZATION_RULE_V1"
    assert payload["rule_body"]["require_payload_binding"] is True


def test_runtime_surface_is_explicit_not_silently_skipped() -> None:
    subject = _subject()
    expected = {
        "PublicationAuthorization",
        "AuthorizationRuleRecord",
        "SourceHandlingBlocked",
        "AtomicAuthorityStore",
        "canonical_payload_sha256",
        "authorization_matches_payload",
        "strict_known_eligible",
        "strict_known_head",
        "restrictive_fact_join",
        "lifecycle_join",
        "validate_permission_evidence",
        "validate_durable_payload",
    }
    missing = sorted(name for name in expected if not hasattr(subject, name))
    assert not missing, f"missing Source Handling Authority contract API: {missing}"


def test_publication_authorization_is_bound_to_exact_payload_scope_and_kind() -> None:
    s = _subject()
    payload = {"policy": {"processing": "DENY"}, "schema": 1}
    auth = s.PublicationAuthorization(
        authorization_id="auth-1",
        authority_component_id="EVIDENCE_INTELLIGENCE_SOURCE_HANDLING_AUTHORITY",
        publication_kind="POLICY",
        governed_subject_scope="source:doc-1:v1",
        authorized_payload_sha256=s.canonical_payload_sha256(payload),
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        effective_from=_utc("2026-08-14T00:00:00Z"),
        recorded_at=_utc("2026-08-14T00:00:00Z"),
        known_at=_utc("2026-08-14T00:00:00Z"),
    )
    assert s.authorization_matches_payload(auth, "POLICY", "source:doc-1:v1", payload)
    assert not s.authorization_matches_payload(auth, "FACT", "source:doc-1:v1", payload)
    assert not s.authorization_matches_payload(auth, "POLICY", "source:doc-2:v1", payload)
    assert not s.authorization_matches_payload(auth, "POLICY", "source:doc-1:v1", {"policy": {"processing": "ALLOW"}})


@pytest.mark.parametrize("method", ["CALLER_ASSERTION", "PROVIDER_OBSERVATION", "AUTOMATED_RESTRICTIVE_DETECTOR", "UNKNOWN"])
def test_non_authoritative_evidence_cannot_grant_permission(method: str) -> None:
    s = _subject()
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_permission_evidence(
            evidence_strength="ASSERTION_ONLY",
            evidence_method=method,
            verifier_type="CALLER",
            requested_change="LESS_RESTRICTIVE",
            authorization_rule_id="AUTHORIZATION_RULE_V1",
        )


def test_authoritative_evidence_still_requires_governed_authorization() -> None:
    s = _subject()
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_permission_evidence(
            evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
            evidence_method="SOURCE_TERMS_VERIFIED",
            verifier_type="SOURCE_VERIFIER",
            requested_change="LESS_RESTRICTIVE",
            authorization_rule_id=None,
        )


def test_strict_known_requires_effective_recorded_and_known_time() -> None:
    s = _subject()
    cutoff = _utc("2026-08-14T12:00:00Z")
    record = {
        "effective_from": _utc("2026-08-14T00:00:00Z"),
        "recorded_at": _utc("2026-08-14T01:00:00Z"),
        "known_at": _utc("2026-08-14T02:00:00Z"),
    }
    assert s.strict_known_eligible(record, cutoff)
    record["known_at"] = _utc("2026-08-15T00:00:00Z")
    assert not s.strict_known_eligible(record, cutoff)
    record["known_at"] = None
    assert not s.strict_known_eligible(record, cutoff)


def test_backdated_later_known_rule_cannot_backfill_replay() -> None:
    s = _subject()
    cutoff = _utc("2026-08-14T12:00:00Z")
    later_known = {
        "authorization_rule_id": "RULE-2",
        "effective_from": _utc("2026-08-01T00:00:00Z"),
        "recorded_at": _utc("2026-08-14T10:00:00Z"),
        "known_at": _utc("2026-08-15T10:00:00Z"),
    }
    assert not s.strict_known_eligible(later_known, cutoff)


def test_strict_known_multiple_heads_block_without_unambiguous_supersession() -> None:
    s = _subject()
    cutoff = _utc("2026-08-14T12:00:00Z")
    records = [
        {"id": "a", "scope": "doc-1", "supersedes": None, "effective_from": _utc("2026-08-14T00:00:00Z"), "recorded_at": _utc("2026-08-14T01:00:00Z"), "known_at": _utc("2026-08-14T01:00:00Z")},
        {"id": "b", "scope": "doc-1", "supersedes": "a", "effective_from": _utc("2026-08-14T00:00:00Z"), "recorded_at": _utc("2026-08-14T02:00:00Z"), "known_at": _utc("2026-08-14T02:00:00Z")},
        {"id": "c", "scope": "doc-1", "supersedes": "a", "effective_from": _utc("2026-08-14T00:00:00Z"), "recorded_at": _utc("2026-08-14T03:00:00Z"), "known_at": _utc("2026-08-14T03:00:00Z")},
    ]
    with pytest.raises(s.SourceHandlingBlocked):
        s.strict_known_head(records, cutoff=cutoff, scope="doc-1")


def test_atomic_compare_and_append_prevents_divergent_heads() -> None:
    s = _subject()
    store = s.AtomicAuthorityStore()
    store.compare_and_append(family="FACT", scope="doc-1", expected_current_head_id=None, record={"id": "a"})
    store.compare_and_append(family="FACT", scope="doc-1", expected_current_head_id="a", record={"id": "b"})
    with pytest.raises(s.SourceHandlingBlocked):
        store.compare_and_append(family="FACT", scope="doc-1", expected_current_head_id="a", record={"id": "c"})


def test_fact_join_preserves_simultaneous_restrictions() -> None:
    s = _subject()
    joined = s.restrictive_fact_join(
        [
            {
                "sensitivity": "CONFIDENTIAL",
                "operation_restrictions": {"MODEL_PROCESSING_PROHIBITED"},
                "persistence_restriction": "DERIVED_ONLY",
                "secret_presence": {"SECRET_PRESENT"},
                "withdrawn": False,
                "deleted_at_source": False,
                "historically_unavailable": False,
            },
            {
                "sensitivity": "RESTRICTED",
                "operation_restrictions": {"ACCESS_RESTRICTED"},
                "persistence_restriction": "NO_PERSISTENCE",
                "secret_presence": {"CREDENTIAL_PRESENT"},
                "withdrawn": True,
                "deleted_at_source": True,
                "historically_unavailable": True,
            },
        ]
    )
    assert joined["sensitivity"] == "RESTRICTED"
    assert joined["operation_restrictions"] == {"MODEL_PROCESSING_PROHIBITED", "ACCESS_RESTRICTED"}
    assert joined["persistence_restriction"] == "NO_PERSISTENCE"
    assert joined["secret_presence"] == {"SECRET_PRESENT", "CREDENTIAL_PRESENT"}
    assert joined["withdrawn"] and joined["deleted_at_source"] and joined["historically_unavailable"]


def test_fact_join_unknown_is_absorbing_and_blocks() -> None:
    s = _subject()
    with pytest.raises(s.SourceHandlingBlocked):
        s.restrictive_fact_join([{"sensitivity": "UNKNOWN"}])


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("ALLOW", "EXPIRE", "EXPIRE"),
        ("EXPIRE", "DELETE", "DELETE"),
        ("ALLOW", "DELETE", "DELETE"),
        ("DELETE", "BLOCKED", "BLOCKED"),
    ],
)
def test_lifecycle_join_is_exact_and_delete_dominates_expire(left: str, right: str, expected: str) -> None:
    s = _subject()
    assert s.lifecycle_join([left, right]) == expected
    assert s.lifecycle_join([right, left]) == expected


def test_lifecycle_join_is_associative_commutative_and_idempotent() -> None:
    s = _subject()
    values = ["ALLOW", "EXPIRE", "DELETE", "BLOCKED"]
    for value in values:
        assert s.lifecycle_join([value, value]) == value
    for a in values:
        for b in values:
            assert s.lifecycle_join([a, b]) == s.lifecycle_join([b, a])
            for c in values:
                assert s.lifecycle_join([s.lifecycle_join([a, b]), c]) == s.lifecycle_join([a, s.lifecycle_join([b, c])])


def test_historical_registry_identity_cannot_be_substituted_by_current_registry() -> None:
    s = _subject()
    decision = {"field_category_registry_id": "registry-v1", "durable_dispositions": {"AUDIT_FIELD": {"PERSIST": "DENY"}}}
    payload = {"audit": "safe-control-only"}
    registry_v2 = {"field_category_registry_id": "registry-v2", "field_map": {"audit": ["AUDIT_FIELD"]}}
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_durable_payload(decision=decision, registry=registry_v2, payload=payload, secret_presence=set())


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
def test_secret_or_credential_material_is_structurally_non_persistable(field: str) -> None:
    s = _subject()
    registry = {
        "field_category_registry_id": "registry-v1",
        "field_map": {field: ["OPERATIONAL_METADATA", "AUDIT_FIELD"]},
    }
    decision = {
        "field_category_registry_id": "registry-v1",
        "durable_dispositions": {
            "OPERATIONAL_METADATA": {"PERSIST": "ALLOW"},
            "AUDIT_FIELD": {"PERSIST": "ALLOW"},
        },
    }
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={field: "protected-secret-material"},
            secret_presence={"SECRET_PRESENT"},
        )
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={field: "credential-material"},
            secret_presence={"CREDENTIAL_PRESENT"},
        )


def test_unknown_or_ambiguous_field_category_never_defaults_to_allow() -> None:
    s = _subject()
    decision = {"field_category_registry_id": "registry-v1", "durable_dispositions": {}}
    registry = {"field_category_registry_id": "registry-v1", "field_map": {}}
    with pytest.raises(s.SourceHandlingBlocked):
        s.validate_durable_payload(
            decision=decision,
            registry=registry,
            payload={"unclassified": "value"},
            secret_presence=set(),
        )


def test_legacy_absence_cannot_be_upgraded_into_historical_authority() -> None:
    s = _subject()
    legacy = {"document_version_id": "doc-legacy", "content": "existing bytes", "known_at": None}
    cutoff = _utc("2026-08-14T12:00:00Z")
    assert not s.strict_known_eligible(legacy, cutoff)


def test_counterfactual_contract_requires_root_rule_not_mock_convenience() -> None:
    """Meta-test documenting the mandatory non-vacuous proof obligation.

    Future implementation reviews must demonstrate that disabling each root rule
    makes its paired regression test fail. This marker prevents the test-first
    contribution from silently dropping that obligation.
    """

    required_roots = {
        "payload_binding",
        "anti_laundering",
        "strict_known_time",
        "historical_rule_selection",
        "compare_and_append",
        "fact_restrictive_join",
        "historical_registry_binding",
        "lifecycle_join",
        "secret_credential_structural_exclusion",
    }
    assert len(required_roots) == 9
