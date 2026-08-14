from __future__ import annotations

import hunter.evidence_intelligence.source_handling as runtime_module
from hunter.evidence_intelligence.source_handling import (
    SourceHandlingBlockedError,
    canonical_publication_digest,
    derive_source_handling_decision,
    enforce_persistence,
    issue_publication_authorization,
    lifecycle_join,
    migrate_legacy,
    publication_authorization,
    publish_genesis_rule,
    publish_successor_rule,
    resolve_canonical_head,
    restrictive_fact_join,
    strict_known_eligible,
    strict_known_head,
    validate_durable_payload,
    validate_permission_evidence,
    verify_publication,
)
from hunter.evidence_intelligence.source_handling import (
    authority_store as _runtime_authority_store,
)


def _test_provenance_resolver(provenance_id: str, provenance_kind: str, cutoff):
    del cutoff
    base = {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "effective_from": "2026-08-14T00:00:00Z",
        "recorded_at": "2026-08-14T01:00:00Z",
        "known_at": "2026-08-14T02:00:00Z",
    }
    if provenance_kind == "EVIDENCE" and provenance_id.startswith("evidence:"):
        if "detector" in provenance_id:
            return {
                **base,
                "evidence_strength": "OBSERVED_RESTRICTIVE_SIGNAL",
                "evidence_method": "AUTOMATED_RESTRICTIVE_DETECTOR",
            }
        return {
            **base,
            "evidence_strength": "AUTHORITATIVE_SOURCE_EVIDENCE",
            "evidence_method": "SOURCE_TERMS_VERIFIED",
        }
    if provenance_kind == "VERIFIER" and provenance_id.startswith("verifier:"):
        return {**base, "verifier_type": "DETECTOR" if "detector" in provenance_id else "SOURCE_VERIFIER"}
    return None


def authority_store():
    return _runtime_authority_store(provenance_resolver=_test_provenance_resolver)


blocked_error = SourceHandlingBlockedError

__all__ = [
    "authority_store",
    "blocked_error",
    "canonical_publication_digest",
    "derive_source_handling_decision",
    "enforce_persistence",
    "issue_publication_authorization",
    "lifecycle_join",
    "migrate_legacy",
    "publication_authorization",
    "publish_genesis_rule",
    "publish_successor_rule",
    "resolve_canonical_head",
    "restrictive_fact_join",
    "runtime_module",
    "strict_known_eligible",
    "strict_known_head",
    "validate_durable_payload",
    "validate_permission_evidence",
    "verify_publication",
]
