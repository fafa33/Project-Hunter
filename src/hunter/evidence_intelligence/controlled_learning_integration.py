"""Controlled, replay-bound integration of an exact-head learning ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hunter.evidence_intelligence.canonical_knowledge_integration import CanonicalIntegrationAuthority
from hunter.evidence_intelligence.incremental_knowledge_learning import LEDGER_SCHEMA
from hunter.evidence_intelligence.knowledge_extraction_authority import (
    SCHEMA_VERSION,
    KnowledgeExtractionAuthority,
    KnowledgeExtractionProposal,
    finding_from_dict,
)

_ALLOWED_STATES = {"existing-family", "candidate-new-family", "excluded", "ambiguous", "insufficient-evidence"}


class ControlledLearningIntegrationError(ValueError):
    """Raised when a learning artifact cannot safely produce a registry candidate."""


@dataclass(frozen=True)
class ControlledLearningIntegrationResult:
    registry_bytes: bytes
    changed: bool
    integrated_proposal_ids: tuple[str, ...]
    skipped_items: int


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _verify_ledger(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA:
        raise ControlledLearningIntegrationError("learning ledger schema is unsupported")
    supplied = ledger.get("ledger_digest")
    body = dict(ledger)
    body.pop("ledger_digest", None)
    if type(supplied) is not str or _digest(body) != supplied:
        raise ControlledLearningIntegrationError("learning ledger digest does not match payload")
    if type(ledger.get("source_pr")) is not int or ledger["source_pr"] <= 0:
        raise ControlledLearningIntegrationError("learning ledger PR is invalid")
    for field in ("reviewed_head_sha", "reviewed_base_sha"):
        value = ledger.get(field)
        if type(value) is not str or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise ControlledLearningIntegrationError(f"learning ledger {field} is invalid")
    if not isinstance(ledger.get("items"), list):
        raise ControlledLearningIntegrationError("learning ledger items must be a list")
    return ledger


def _proposal_from_dict(raw: Any) -> KnowledgeExtractionProposal:
    if not isinstance(raw, dict):
        raise ControlledLearningIntegrationError("existing-family ledger item requires a proposal")
    expected = {
        "schema_version",
        "proposal_id",
        "outcome",
        "finding_id",
        "source_pr",
        "reviewed_head_sha",
        "registry_digest",
        "finding",
        "canonical_family_id",
        "canonical_write_authorized",
        "rationale",
    }
    if set(raw) != expected or not isinstance(raw.get("finding"), dict):
        raise ControlledLearningIntegrationError("proposal fields are not canonical")
    finding_payload = dict(raw["finding"])
    for field in ("affected_paths", "regression_evidence"):
        if isinstance(finding_payload.get(field), tuple):
            finding_payload[field] = list(finding_payload[field])
    finding = finding_from_dict({"schema_version": SCHEMA_VERSION, **finding_payload})
    return KnowledgeExtractionProposal(
        schema_version=raw["schema_version"],
        proposal_id=raw["proposal_id"],
        outcome=raw["outcome"],
        finding_id=raw["finding_id"],
        source_pr=raw["source_pr"],
        reviewed_head_sha=raw["reviewed_head_sha"],
        registry_digest=raw["registry_digest"],
        finding=finding,
        canonical_family_id=raw["canonical_family_id"],
        canonical_write_authorized=raw["canonical_write_authorized"],
        rationale=raw["rationale"],
    )


def integrate_learning_ledger(ledger: Any, registry_bytes: bytes) -> ControlledLearningIntegrationResult:
    ledger = _verify_ledger(ledger)
    initial = bytes(registry_bytes)
    proposals = []
    skipped = 0
    with TemporaryDirectory() as directory:
        registry_path = Path(directory) / "registry.json"
        registry_path.write_bytes(initial)
        authority = KnowledgeExtractionAuthority(registry_path)
        for item in ledger["items"]:
            if not isinstance(item, dict) or set(item) != {"observation_id", "state", "observation", "proposal"}:
                raise ControlledLearningIntegrationError("learning ledger item is malformed")
            state = item.get("state")
            if state not in _ALLOWED_STATES:
                raise ControlledLearningIntegrationError("learning ledger item state is unsupported")
            if state != "existing-family":
                skipped += 1
                continue
            supplied = _proposal_from_dict(item.get("proposal"))
            finding = supplied.finding
            if (
                supplied.outcome != "existing-family"
                or supplied.source_pr != ledger["source_pr"]
                or supplied.reviewed_head_sha != ledger["reviewed_head_sha"]
                or finding.reviewed_base_sha != ledger["reviewed_base_sha"]
            ):
                raise ControlledLearningIntegrationError("proposal is not bound to the exact ledger identity")
            if authority.extract(finding) != supplied:
                raise ControlledLearningIntegrationError("ledger proposal does not replay against canonical registry")
            proposals.append(supplied)
        current = initial
        integrated = []
        for supplied in proposals:
            registry_path.write_bytes(current)
            fresh = KnowledgeExtractionAuthority(registry_path).extract(supplied.finding)
            if fresh.outcome != "existing-family":
                raise ControlledLearningIntegrationError("proposal no longer maps to an existing canonical family")
            result = CanonicalIntegrationAuthority().integrate(fresh, current)
            current = result.registry_bytes
            integrated.append(fresh.proposal_id)
    return ControlledLearningIntegrationResult(current, current != initial, tuple(integrated), skipped)
