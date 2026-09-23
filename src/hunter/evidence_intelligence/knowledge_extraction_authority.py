"""Deterministic, proposal-only knowledge extraction for recurring engineering defects."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from hunter.evidence_intelligence.engineering_context_authority import (
    CANONICAL_DEFECT_LIFECYCLES,
    CANONICAL_PREVENTION_BOUNDARIES,
)

SCHEMA_VERSION = "hunter-knowledge-extraction-v1"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DFF_PATTERN = re.compile(r"^DFF-[0-9]{3}$")
SOURCE_KINDS = frozenset({"independent-review", "ci", "governance", "deterministic-gate"})
CONFIRMED_CLASSIFICATIONS = frozenset({"confirmed"})
EXCLUDED_CLASSIFICATIONS = frozenset({"false-positive", "style", "obsolete", "infrastructure", "provider-unavailable"})


class KnowledgeExtractionError(ValueError):
    """Raised when finding or registry evidence cannot be trusted."""


@dataclass(frozen=True)
class KnowledgeFinding:
    source_kind: str
    finding_id: str
    source_pr: int
    reviewed_head_sha: str
    reviewed_base_sha: str
    reviewer: str
    classification: str
    invariant: str
    affected_paths: tuple[str, ...]
    fix_reference: str
    regression_evidence: tuple[str, ...]
    claimed_family_id: str | None = None


@dataclass(frozen=True)
class KnowledgeExtractionProposal:
    schema_version: str
    proposal_id: str
    outcome: Literal["existing-family", "candidate-new-family", "excluded", "ambiguous"]
    finding_id: str
    source_pr: int
    reviewed_head_sha: str
    registry_digest: str
    finding: KnowledgeFinding
    canonical_family_id: str | None
    canonical_write_authorized: bool
    rationale: str


def _tuple_of_strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise KnowledgeExtractionError(f"{field} must be a list of strings")
    return tuple(value)


def _exact_scalar(raw: dict[str, Any], field: str, expected: type, *, optional: bool = False) -> Any:
    if optional and raw.get(field) is None:
        return None
    if field not in raw:
        raise KnowledgeExtractionError(f"finding input missing required field: {field}")
    value = raw[field]
    if type(value) is not expected:
        raise KnowledgeExtractionError(f"{field} has invalid type")
    return value


def finding_from_dict(raw: Any) -> KnowledgeFinding:
    """Parse the versioned external finding contract without granting extra fields authority."""
    if not isinstance(raw, dict):
        raise KnowledgeExtractionError("finding input must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise KnowledgeExtractionError("finding schema_version must be canonical")
    allowed = {
        "schema_version",
        "source_kind",
        "finding_id",
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
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise KnowledgeExtractionError(f"finding input has unknown fields: {', '.join(unknown)}")
    return KnowledgeFinding(
        source_kind=_exact_scalar(raw, "source_kind", str),
        finding_id=_exact_scalar(raw, "finding_id", str),
        source_pr=_exact_scalar(raw, "source_pr", int),
        reviewed_head_sha=_exact_scalar(raw, "reviewed_head_sha", str),
        reviewed_base_sha=_exact_scalar(raw, "reviewed_base_sha", str),
        reviewer=_exact_scalar(raw, "reviewer", str),
        classification=_exact_scalar(raw, "classification", str),
        invariant=_exact_scalar(raw, "invariant", str),
        affected_paths=_tuple_of_strings(raw.get("affected_paths"), "affected_paths"),
        fix_reference=_exact_scalar(raw, "fix_reference", str),
        regression_evidence=_tuple_of_strings(raw.get("regression_evidence"), "regression_evidence"),
        claimed_family_id=_exact_scalar(raw, "claimed_family_id", str, optional=True),
    )


class KnowledgeExtractionAuthority:
    """Validate one finding and produce a replayable, non-authorizing proposal."""

    def __init__(self, registry_path: Path) -> None:
        self._registry_path = registry_path

    def extract(self, finding: KnowledgeFinding) -> KnowledgeExtractionProposal:
        self._validate_finding(finding)
        families, registry_digest = self._load_families()

        if finding.classification in EXCLUDED_CLASSIFICATIONS:
            return self._proposal(
                finding,
                registry_digest,
                "excluded",
                None,
                "classification is explicitly excluded from canonical defect learning",
            )

        if finding.claimed_family_id is None:
            return self._proposal(
                finding,
                registry_digest,
                "candidate-new-family",
                None,
                "confirmed finding has no deterministically asserted existing-family mapping",
            )

        family = families.get(finding.claimed_family_id)
        if family is None:
            return self._proposal(
                finding,
                registry_digest,
                "ambiguous",
                None,
                "claimed family does not exist in the canonical registry",
            )

        if not self._matches(finding, family):
            return self._proposal(
                finding,
                registry_digest,
                "ambiguous",
                None,
                "claimed family conflicts with canonical invariant or applicability",
            )

        return self._proposal(
            finding,
            registry_digest,
            "existing-family",
            finding.claimed_family_id,
            "canonical invariant and applicability deterministically match the claimed family",
        )

    def _validate_finding(self, finding: KnowledgeFinding) -> None:
        scalar_types = {
            "source_kind": (finding.source_kind, str),
            "finding_id": (finding.finding_id, str),
            "source_pr": (finding.source_pr, int),
            "reviewed_head_sha": (finding.reviewed_head_sha, str),
            "reviewed_base_sha": (finding.reviewed_base_sha, str),
            "reviewer": (finding.reviewer, str),
            "classification": (finding.classification, str),
            "invariant": (finding.invariant, str),
            "fix_reference": (finding.fix_reference, str),
        }
        for field, (value, expected) in scalar_types.items():
            if type(value) is not expected:
                raise KnowledgeExtractionError(f"{field} has invalid type")
        if finding.claimed_family_id is not None and type(finding.claimed_family_id) is not str:
            raise KnowledgeExtractionError("claimed_family_id has invalid type")
        if finding.source_kind not in SOURCE_KINDS:
            raise KnowledgeExtractionError("source_kind must be canonical")
        if not finding.finding_id.strip():
            raise KnowledgeExtractionError("finding_id is required")
        if finding.source_pr <= 0:
            raise KnowledgeExtractionError("source_pr must be positive")
        for label, sha in (
            ("reviewed_head_sha", finding.reviewed_head_sha),
            ("reviewed_base_sha", finding.reviewed_base_sha),
        ):
            if not SHA_PATTERN.fullmatch(sha):
                raise KnowledgeExtractionError(f"{label} must be an exact 40-character lowercase SHA")
        if not finding.reviewer.strip():
            raise KnowledgeExtractionError("reviewer is required")
        if finding.classification not in CONFIRMED_CLASSIFICATIONS | EXCLUDED_CLASSIFICATIONS:
            raise KnowledgeExtractionError("classification must be canonical")
        if finding.claimed_family_id is not None and not DFF_PATTERN.fullmatch(finding.claimed_family_id):
            raise KnowledgeExtractionError("claimed_family_id must be canonical DFF identity")

        # Excluded evidence stays auditable but does not need fix/regression proof because
        # it is forbidden from becoming defect knowledge.
        if finding.classification in EXCLUDED_CLASSIFICATIONS:
            return

        if not finding.invariant.strip():
            raise KnowledgeExtractionError("confirmed finding invariant is required")
        if not finding.affected_paths or any(type(p) is not str or not p.strip() for p in finding.affected_paths):
            raise KnowledgeExtractionError("confirmed finding affected_paths are required")
        for path in finding.affected_paths:
            if not self._is_canonical_repo_path(path):
                raise KnowledgeExtractionError("affected_paths must be canonical repository-relative paths")
        if not finding.fix_reference.strip():
            raise KnowledgeExtractionError("confirmed finding fix_reference is required")
        if not finding.regression_evidence or any(not r.strip() for r in finding.regression_evidence):
            raise KnowledgeExtractionError("confirmed finding regression_evidence is required")

    def _load_families(self) -> tuple[dict[str, dict[str, Any]], str]:
        try:
            raw_bytes = self._registry_path.read_bytes()
            raw = json.loads(raw_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeExtractionError("canonical registry is unreadable") from exc
        families = raw.get("families") if isinstance(raw, dict) else None
        if not isinstance(families, list):
            raise KnowledgeExtractionError("canonical registry families must be a list")

        result: dict[str, dict[str, Any]] = {}
        for family in families:
            if not isinstance(family, dict):
                raise KnowledgeExtractionError("canonical family must be an object")
            family_id = family.get("id")
            invariant = family.get("invariant")
            lifecycle = family.get("lifecycle")
            applicability = family.get("applicability")
            prevention = family.get("prevention")
            if not isinstance(family_id, str) or not DFF_PATTERN.fullmatch(family_id):
                raise KnowledgeExtractionError("canonical family id is malformed")
            if family_id in result:
                raise KnowledgeExtractionError("canonical family ids must be unique")
            if not isinstance(invariant, str) or not invariant.strip():
                raise KnowledgeExtractionError(f"{family_id}: invariant is required")
            if lifecycle not in CANONICAL_DEFECT_LIFECYCLES:
                raise KnowledgeExtractionError(f"{family_id}: lifecycle must be canonical")
            if not isinstance(applicability, dict):
                raise KnowledgeExtractionError(f"{family_id}: applicability is required")
            changed_paths = applicability.get("changed_paths")
            if (
                not isinstance(changed_paths, list)
                or not changed_paths
                or any(not isinstance(p, str) or not p.strip() for p in changed_paths)
            ):
                raise KnowledgeExtractionError(f"{family_id}: changed_paths must be non-empty strings")
            if not isinstance(prevention, dict) or prevention.get("boundary") not in CANONICAL_PREVENTION_BOUNDARIES:
                raise KnowledgeExtractionError(f"{family_id}: prevention boundary must be canonical")
            result[family_id] = family
        registry_digest = hashlib.sha256(raw_bytes).hexdigest()
        return result, registry_digest

    @staticmethod
    def _normalize_invariant(value: str) -> str:
        return " ".join(value.split()).casefold()

    @classmethod
    def _matches(cls, finding: KnowledgeFinding, family: dict[str, Any]) -> bool:
        if cls._normalize_invariant(finding.invariant) != cls._normalize_invariant(str(family["invariant"])):
            return False
        selectors = family["applicability"]["changed_paths"]
        return any(cls._path_intersects(path, selector) for path in finding.affected_paths for selector in selectors)

    @staticmethod
    def _is_canonical_repo_path(path: str) -> bool:
        if not path or path != path.strip() or path.startswith(("/", "./", "../")):
            return False
        if "//" in path or "\\" in path:
            return False
        parts = path.split("/")
        return all(part not in {"", ".", ".."} for part in parts)

    @staticmethod
    def _path_intersects(path: str, selector: str) -> bool:
        if selector.endswith("/"):
            return path.startswith(selector)
        return path == selector or path.startswith(selector + "/")

    @staticmethod
    def _proposal_id(
        finding: KnowledgeFinding,
        registry_digest: str,
        outcome: str,
        family_id: str | None,
        rationale: str,
    ) -> str:
        payload = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "finding": asdict(finding),
                "registry_digest": registry_digest,
                "outcome": outcome,
                "canonical_family_id": family_id,
                "rationale": rationale,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return "KXP-" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _proposal(
        finding: KnowledgeFinding,
        registry_digest: str,
        outcome: Literal["existing-family", "candidate-new-family", "excluded", "ambiguous"],
        family_id: str | None,
        rationale: str,
    ) -> KnowledgeExtractionProposal:
        proposal_id = KnowledgeExtractionAuthority._proposal_id(finding, registry_digest, outcome, family_id, rationale)
        return KnowledgeExtractionProposal(
            schema_version=SCHEMA_VERSION,
            proposal_id=proposal_id,
            outcome=outcome,
            finding_id=finding.finding_id,
            source_pr=finding.source_pr,
            reviewed_head_sha=finding.reviewed_head_sha,
            registry_digest=registry_digest,
            finding=finding,
            canonical_family_id=family_id,
            canonical_write_authorized=False,
            rationale=rationale,
        )
