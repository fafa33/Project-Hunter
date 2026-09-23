"""Provider-bounded event ingestion into Hunter's canonical knowledge-finding contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from hunter.evidence_intelligence.knowledge_extraction_authority import (
    SCHEMA_VERSION,
    KnowledgeExtractionError,
    KnowledgeFinding,
    finding_from_dict,
)

EVENT_SCHEMA_VERSION = "hunter-finding-event-v1"
SOURCE_MAP = {
    "sonar": "deterministic-gate",
    "github-review": "independent-review",
    "hunter-ci": "ci",
    "hunter-governance": "governance",
}
_ALLOWED_FIELDS = {
    "schema_version",
    "provider",
    "event_id",
    "source_pr",
    "reviewed_head_sha",
    "reviewed_base_sha",
    "reviewer",
    "classification",
    "invariant",
    "affected_paths",
    "fix_reference",
    "regression_evidence",
    "claimed_family_id",
}


class EventIngestionError(ValueError):
    """Raised when an event cannot be normalized without inventing authority."""


def ingest_event(source: str, raw: Any) -> KnowledgeFinding:
    if source not in SOURCE_MAP:
        raise EventIngestionError("event source is unsupported")
    if not isinstance(raw, dict):
        raise EventIngestionError("event must be an object")
    if raw.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise EventIngestionError("event schema_version must be canonical")
    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise EventIngestionError(f"event has unknown fields: {', '.join(unknown)}")
    if raw.get("provider") != source:
        raise EventIngestionError("event provider must match the selected adapter")
    event_id = raw.get("event_id")
    if type(event_id) is not str or not event_id.strip():
        raise EventIngestionError("event_id is required")
    identity_payload = {key: raw.get(key) for key in sorted(_ALLOWED_FIELDS)}
    payload_digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_kind": SOURCE_MAP[source],
        "finding_id": f"{source}:{event_id}@{payload_digest}",
        "source_pr": raw.get("source_pr"),
        "reviewed_head_sha": raw.get("reviewed_head_sha"),
        "reviewed_base_sha": raw.get("reviewed_base_sha"),
        "reviewer": raw.get("reviewer"),
        "classification": raw.get("classification"),
        "invariant": raw.get("invariant"),
        "affected_paths": raw.get("affected_paths"),
        "fix_reference": raw.get("fix_reference"),
        "regression_evidence": raw.get("regression_evidence"),
        "claimed_family_id": raw.get("claimed_family_id"),
    }
    try:
        return finding_from_dict(payload)
    except KnowledgeExtractionError as exc:
        raise EventIngestionError(str(exc)) from exc
