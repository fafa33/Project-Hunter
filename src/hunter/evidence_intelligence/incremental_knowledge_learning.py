"""Deterministic exact-head learning ledger for engineering finding observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.knowledge_event_ingestion import EVENT_SCHEMA_VERSION, SOURCE_MAP, ingest_event
from hunter.evidence_intelligence.knowledge_extraction_authority import KnowledgeExtractionAuthority

LEDGER_SCHEMA = "hunter-learning-ledger-v1"
_ALLOWED = {
    "source",
    "provider",
    "event_id",
    "source_pr",
    "reviewed_head_sha",
    "reviewed_base_sha",
    "reviewer",
    "path",
    "line",
    "message",
    "availability",
    "classification",
    "invariant",
    "affected_paths",
    "fix_reference",
    "regression_evidence",
    "claimed_family_id",
}


class LearningLedgerError(ValueError):
    """Raised when observations cannot be bound safely to the requested exact head."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _validate_observation(raw: Any, pr: int, head: str, base: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LearningLedgerError("learning observation must be an object")
    unknown = sorted(set(raw) - _ALLOWED)
    if unknown:
        raise LearningLedgerError(f"learning observation has unknown fields: {', '.join(unknown)}")
    source = raw.get("source")
    if source not in SOURCE_MAP or raw.get("provider") != source:
        raise LearningLedgerError("learning observation source/provider is unsupported")
    if raw.get("source_pr") != pr:
        raise LearningLedgerError("learning observation PR does not match ledger PR")
    if raw.get("reviewed_head_sha") != head:
        raise LearningLedgerError("learning observation does not match exact head")
    if raw.get("reviewed_base_sha") != base:
        raise LearningLedgerError("learning observation does not match exact base")
    if raw.get("availability") not in {"available", "unavailable"}:
        raise LearningLedgerError("learning observation availability is invalid")
    for field in ("event_id", "reviewer", "message"):
        if type(raw.get(field)) is not str or not raw[field].strip():
            raise LearningLedgerError(f"learning observation {field} is required")
    line = raw.get("line")
    if line is not None and (type(line) is not int or line <= 0):
        raise LearningLedgerError("learning observation line must be a positive integer")
    return dict(raw)


def _event_from_observation(raw: dict[str, Any]) -> dict[str, Any] | None:
    classification = raw.get("classification")
    if classification is None:
        return None
    if classification != "confirmed":
        classification = "false-positive"
    affected = raw.get("affected_paths") or ([raw["path"]] if raw.get("path") else [])
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "provider": raw["provider"],
        "event_id": raw["event_id"],
        "source_pr": raw["source_pr"],
        "reviewed_head_sha": raw["reviewed_head_sha"],
        "reviewed_base_sha": raw["reviewed_base_sha"],
        "reviewer": raw["reviewer"],
        "classification": classification,
        "invariant": raw.get("invariant") or "",
        "affected_paths": affected,
        "fix_reference": raw.get("fix_reference") or "",
        "regression_evidence": raw.get("regression_evidence") or [],
        "claimed_family_id": raw.get("claimed_family_id"),
    }


def build_learning_ledger(
    pr: int, head: str, base: str, observations: list[dict[str, Any]], registry: Path
) -> dict[str, Any]:
    if type(pr) is not int or pr <= 0:
        raise LearningLedgerError("ledger PR must be positive")
    if len(head) != 40 or len(base) != 40:
        raise LearningLedgerError("ledger head/base must be exact 40-character SHAs")
    normalized = [_validate_observation(item, pr, head, base) for item in observations]
    unique = {_digest(item): item for item in normalized}
    items: list[dict[str, Any]] = []
    availability: dict[str, str] = {}
    authority = KnowledgeExtractionAuthority(registry)
    for digest, raw in sorted(unique.items()):
        source = str(raw["source"])
        state = "insufficient-evidence"
        proposal_dict = None
        availability[source] = (
            "available"
            if raw["availability"] == "available" or availability.get(source) == "available"
            else "unavailable"
        )
        event = _event_from_observation(raw)
        if event is not None:
            try:
                finding = ingest_event(source, event)
                proposal = authority.extract(finding)
            except (ValueError, TypeError):
                state = "insufficient-evidence"
            else:
                state = proposal.outcome
                proposal_dict = asdict(proposal)
        items.append({"observation_id": digest, "state": state, "observation": raw, "proposal": proposal_dict})
    body = {
        "schema_version": LEDGER_SCHEMA,
        "source_pr": pr,
        "reviewed_head_sha": head,
        "reviewed_base_sha": base,
        "source_availability": dict(sorted(availability.items())),
        "items": items,
    }
    body["ledger_digest"] = _digest(body)
    return body


def historical_events(backfill: Path, head: str, base: str) -> list[dict[str, Any]]:
    raw = json.loads(backfill.read_text(encoding="utf-8"))
    records = raw.get("records")
    if not isinstance(records, list):
        raise LearningLedgerError("historical backfill records must be a list")
    events: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise LearningLedgerError("historical record must be an object")
        confirmed = record.get("classification") == "confirmed"
        dff = record.get("dff_id")
        events.append(
            {
                "source": "github-review",
                "provider": "github-review",
                "event_id": str(record.get("id")),
                "source_pr": record.get("source_pr"),
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": str(record.get("reviewer") or "historical-review"),
                "path": None,
                "line": None,
                "message": str(record.get("original_defect") or record.get("source_reference") or "historical finding"),
                "availability": "available",
                "classification": "confirmed" if confirmed else "false-positive",
                "invariant": _historical_invariant(dff) if confirmed else "",
                "affected_paths": _historical_paths(dff) if confirmed else [],
                "fix_reference": str(record.get("fix_reference") or ""),
                "regression_evidence": (
                    [record["regression_test_reference"]]
                    if confirmed and record.get("regression_test_reference")
                    else []
                ),
                "claimed_family_id": dff if confirmed and isinstance(dff, str) and dff.startswith("DFF-") else None,
            }
        )
    return events


def _registry_family(dff: Any) -> dict[str, Any] | None:
    if not isinstance(dff, str):
        return None
    registry = json.loads(Path("docs/DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    return next((f for f in registry["families"] if f["id"] == dff), None)


def _historical_invariant(dff: Any) -> str:
    family = _registry_family(dff)
    return str(family["invariant"]) if family else ""


def _historical_paths(dff: Any) -> list[str]:
    family = _registry_family(dff)
    if not family:
        return []
    paths = []
    for selector in family["applicability"]["changed_paths"]:
        # KnowledgeFinding requires a canonical repository-relative path, while
        # registry applicability may intentionally use a directory selector.
        paths.append(selector.rstrip("/") + "/__historical_evidence__" if selector.endswith("/") else selector)
    return paths
