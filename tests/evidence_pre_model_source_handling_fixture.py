from __future__ import annotations

import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from hunter.evidence_intelligence.pre_model import EvidencePreModelSourceHandlingAuthority
from hunter.evidence_intelligence.source_handling import (
    AuthorityStore,
    authority_store,
    issue_publication_authorization,
    publish_genesis_rule,
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
    point = cutoff - timedelta(minutes=15)
    return {
        "effective_from": point,
        "recorded_at": point,
        "known_at": point,
    }


def _publish(
    store: AuthorityStore,
    *,
    family: str,
    scope: str,
    record_id: str,
    payload: dict[str, object],
    cutoff,
) -> None:
    authorization = issue_publication_authorization(
        store,
        publication_kind=family,
        governed_subject_scope=scope,
        payload=payload,
        authorization_rule_id="AUTHORIZATION_RULE_V1",
        authorization_id=f"auth:{family}:{scope}:{record_id}",
        evidence_ids=(f"evidence:{family}:{scope}:{record_id}",),
        evidence_strength="AUTHORITATIVE_SOURCE_EVIDENCE",
        evidence_method="SOURCE_TERMS_VERIFIED",
        verifier_ids=(f"verifier:{family}:{scope}:{record_id}",),
        verifier_type="SOURCE_VERIFIER",
        **_times(cutoff),
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


def source_handling_authority(
    *,
    document_id: str,
    cutoff,
    processing: str = "ALLOW",
    retention: str = "ALLOW",
    reconstruction: str = "ALLOW",
) -> EvidencePreModelSourceHandlingAuthority:
    store = authority_store(provenance_resolver=_provenance_resolver)

    rule = copy.deepcopy(json.loads(_RULE_FIXTURE.read_text(encoding="utf-8")))
    rule_time = cutoff - timedelta(minutes=30)
    rule_time_text = rule_time.isoformat().replace("+00:00", "Z")
    rule["effective_from"] = rule_time_text
    rule["recorded_at"] = rule_time_text
    rule["known_at"] = rule_time_text
    publish_genesis_rule(store, rule, expected_golden_sha256=_canonical_sha256(rule))

    fact_payload: dict[str, object] = {
        "scope": document_id,
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
        **_times(cutoff),
    }
    _publish(
        store,
        family="FACT",
        scope=document_id,
        record_id=f"fact:{document_id}:v1",
        payload=fact_payload,
        cutoff=cutoff,
    )

    registry_scope = f"registry:{document_id}:v1"
    registry_id = f"registry:{document_id}:v1"
    registry_payload: dict[str, object] = {
        "scope": registry_scope,
        "field_category_registry_id": registry_id,
        "field_map": {"pre_model_bundle": ["AUDIT_FIELD"]},
        "safe_control_proofs": {},
        **_times(cutoff),
    }
    _publish(
        store,
        family="FIELD_CATEGORY_REGISTRY",
        scope=registry_scope,
        record_id=registry_id,
        payload=registry_payload,
        cutoff=cutoff,
    )

    policy_scope = f"policy:{document_id}:v1"
    policy_payload: dict[str, object] = {
        "scope": policy_scope,
        "field_category_registry_id": registry_id,
        "policy_body": {
            "processing_decision": processing,
            "retention_decision": retention,
            "reconstruction_decision": reconstruction,
            "access_decision": "ALLOW",
            "deletion_lifecycle_decision": "ALLOW",
            "durable_dispositions": {
                "AUDIT_FIELD": {
                    "PERSIST": "ALLOW",
                    "READ_ACCESS": "ALLOW",
                    "RECONSTRUCT": "ALLOW",
                    "DELETE_OR_EXPIRE": "ALLOW",
                }
            },
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
    )

    return EvidencePreModelSourceHandlingAuthority(
        store=store,
        fact_scope=document_id,
        policy_scope=policy_scope,
        cutoff=cutoff,
    )
