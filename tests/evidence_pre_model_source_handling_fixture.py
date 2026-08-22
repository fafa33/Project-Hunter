from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from hunter.evidence_intelligence.pre_model import EvidencePreModelSourceHandlingAuthority
from hunter.evidence_intelligence.source_handling import (
    AuthorityStore,
    authority_store,
    issue_publication_authorization,
    publish_genesis_rule,
    publish_successor_rule,
)

_RULE_FIXTURE = Path(__file__).parent / "fixtures" / "source_handling" / "authorization_rule_v1.json"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provenance_resolver(provenance_id: str, provenance_kind: str, cutoff):
    known_at = cutoff - timedelta(minutes=5)
    base = {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "effective_from": known_at,
        "recorded_at": known_at,
        "known_at": known_at,
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


def _times(cutoff):
    return {
        "effective_from": cutoff,
        "recorded_at": cutoff,
        "known_at": cutoff,
    }


def _publish(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    record_id: str,
    payload: dict[str, object],
    cutoff,
    authorization_rule_id: str = "AUTHORIZATION_RULE_V1",
    times: datetime | None = None,
) -> None:
    authorization = issue_publication_authorization(
        store,
        publication_kind=family,
        governed_subject_scope=scope,
        payload=payload,
        authorization_rule_id=authorization_rule_id,
        authorization_id=f"auth:{family}:{scope}:{record_id}",
        evidence_ids=(f"evidence:{family}:{scope}:{record_id}",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=(f"verifier:{family}:{scope}:{record_id}",),
        verifier_type="SOURCE_VERIFIER",
        **_times(times or cutoff),
    )
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


def _publish_rule_successor(
    store: AuthorityStore,
    *,
    rule_id: str,
    cutoff,
    authorization_rule_id: str,
    times: datetime | None = None,
) -> None:
    rule = copy.deepcopy(json.loads(_RULE_FIXTURE.read_text(encoding="utf-8")))
    rule_time = times or (cutoff - timedelta(minutes=30))
    rule_time_text = rule_time.isoformat().replace("+00:00", "Z")
    successor = {
        "authorization_rule_id": rule_id,
        "rule_schema_version": 1,
        "rule_body": rule["rule_body"],
        "effective_from": rule_time_text,
        "recorded_at": rule_time_text,
        "known_at": rule_time_text,
        "supersedes_authorization_rule_id": authorization_rule_id,
    }
    publication_payload = {**successor, "scope": "SOURCE_HANDLING"}
    authorization = issue_publication_authorization(
        store,
        publication_kind="AUTHORIZATION_RULE",
        governed_subject_scope="SOURCE_HANDLING",
        payload=publication_payload,
        authorization_rule_id=authorization_rule_id,
        authorization_id=f"auth:rule:{rule_id}",
        evidence_ids=(f"evidence:rule:{rule_id}",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=(f"verifier:rule:{rule_id}",),
        verifier_type="SOURCE_VERIFIER",
        **_times(times or cutoff),
    )
    publish_successor_rule(
        store,
        {**successor, "publication_authorization": authorization},
        authorizing_rule_id=authorization_rule_id,
    )


def source_handling_authority(
    *,
    document_id: str,
    cutoff,
    processing: str = "ALLOW",
    retention: str = "ALLOW",
    reconstruction: str = "ALLOW",
    secret_presence: Sequence[str] | str = (),
    field_map: Mapping[str, Sequence[str]] | None = None,
    safe_control_proofs: Mapping[str, object] | None = None,
    policy_authorization_rule_id: str = "AUTHORIZATION_RULE_V1",
    durable_dispositions_override: Mapping[str, Mapping[str, str]] | None = None,
) -> EvidencePreModelSourceHandlingAuthority:
    """Build a governed authority store for tests.

    ``durable_dispositions_override``, when supplied, replaces the default per-category
    disposition map verbatim. It exists so a caller can govern categories beyond
    the pre-model defaults (for example content-derived request categories)
    without duplicating this fixture.
    """
    store = authority_store(provenance_resolver=_provenance_resolver)

    rule = copy.deepcopy(json.loads(_RULE_FIXTURE.read_text(encoding="utf-8")))
    rule_time = cutoff - timedelta(minutes=30)
    rule_time_text = rule_time.isoformat().replace("+00:00", "Z")
    rule["effective_from"] = rule_time_text
    rule["recorded_at"] = rule_time_text
    rule["known_at"] = rule_time_text
    publish_genesis_rule(store, rule, expected_golden_sha256=_canonical_sha256(rule))
    conflicting_rules = policy_authorization_rule_id != "AUTHORIZATION_RULE_V1"
    fact_times = cutoff - timedelta(minutes=15) if conflicting_rules else cutoff
    if conflicting_rules:
        _publish_rule_successor(
            store,
            rule_id=policy_authorization_rule_id,
            cutoff=cutoff,
            authorization_rule_id="AUTHORIZATION_RULE_V1",
            times=cutoff - timedelta(minutes=5),
        )

    fact_payload: dict[str, object] = {
        "scope": document_id,
        "fact": {
            "sensitivity": "PUBLIC",
            "operation_restrictions": [],
            "persistence_restriction": "FULL_CONTENT_ALLOWED",
            "secret_presence": secret_presence if isinstance(secret_presence, str) else list(secret_presence),
            "operation_restrictions_known": True,
            "secret_presence_known": True,
            "withdrawn": False,
            "deleted_at_source": False,
            "historically_unavailable": False,
            "availability_known": True,
        },
        **_times(fact_times),
    }
    _publish(
        store,
        family="FACT",
        scope=document_id,
        record_id=f"fact:{document_id}:v1",
        payload=fact_payload,
        cutoff=cutoff,
        times=fact_times,
    )

    registry_scope = f"registry:{document_id}:v1"
    registry_id = f"registry:{document_id}:v1"
    registry_payload: dict[str, object] = {
        "scope": registry_scope,
        "field_category_registry_id": registry_id,
        "field_map": dict(
            field_map
            or {
                "pre_model_bundle": ["AUDIT_FIELD"],
                "locator": ["AUDIT_FIELD"],
            }
        ),
        "safe_control_proofs": dict(safe_control_proofs or {}),
        **_times(cutoff),
    }
    _publish(
        store,
        family="FIELD_CATEGORY_REGISTRY",
        scope=registry_scope,
        record_id=registry_id,
        payload=registry_payload,
        cutoff=cutoff,
        authorization_rule_id=policy_authorization_rule_id,
    )

    policy_scope = f"policy:{document_id}:v1"
    durable_dispositions: dict[str, object] = {
        "AUDIT_FIELD": {
            "PERSIST": "ALLOW",
            "READ_ACCESS": "ALLOW",
            "RECONSTRUCT": "ALLOW",
            "DELETE_OR_EXPIRE": "ALLOW",
        }
    }
    categories = {
        category
        for mapped in registry_payload["field_map"].values()
        for category in (str(item) for item in (mapped if isinstance(mapped, (list, tuple)) else [mapped]))
    }
    if "SAFE_CONTROL_ID" in categories:
        durable_dispositions["SAFE_CONTROL_ID"] = {
            "PERSIST": "ALLOW",
            "READ_ACCESS": "ALLOW",
            "RECONSTRUCT": "ALLOW",
            "DELETE_OR_EXPIRE": "ALLOW",
        }
    if durable_dispositions_override is not None:
        durable_dispositions = {category: dict(values) for category, values in durable_dispositions_override.items()}
    policy_payload: dict[str, object] = {
        "scope": policy_scope,
        "field_category_registry_id": registry_id,
        "policy_body": {
            "processing_decision": processing,
            "retention_decision": retention,
            "reconstruction_decision": reconstruction,
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": "ALLOW",
            "durable_dispositions": durable_dispositions,
        },
        **_times(cutoff),
    }
    _publish(
        store,
        family="POLICY",
        scope=policy_scope,
        record_id=f"policy:{document_id}:v1",
        payload=policy_payload,
        cutoff=cutoff,
        authorization_rule_id=policy_authorization_rule_id,
    )

    return EvidencePreModelSourceHandlingAuthority(
        store=store,
        fact_scope=document_id,
        policy_scope=policy_scope,
        cutoff=cutoff,
    )
