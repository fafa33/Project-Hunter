"""Pure, replay-bound strengthening of existing canonical DPM families."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from hunter.evidence_intelligence.knowledge_extraction_authority import (
    SCHEMA_VERSION,
    KnowledgeExtractionAuthority,
    KnowledgeExtractionError,
    KnowledgeExtractionProposal,
)


class CanonicalIntegrationError(ValueError):
    """Raised when a proposal cannot safely strengthen canonical knowledge."""


@dataclass(frozen=True)
class CanonicalIntegrationResult:
    registry_bytes: bytes
    changed: bool
    family_id: str


class CanonicalIntegrationAuthority:
    """Integrate only replay-proven existing-family evidence; never persist it."""

    def already_integrated(self, proposal: KnowledgeExtractionProposal, registry_bytes: bytes) -> bool:
        """Return whether this exact accepted event is already canonical.

        This is the only stale-registry exception: replaying byte-identical accepted
        evidence after it was integrated is a no-op. A conflicting reuse of the
        provider event identity still fails closed.
        """
        try:
            document = json.loads(registry_bytes)
        except json.JSONDecodeError as exc:
            raise CanonicalIntegrationError("registry is unreadable") from exc
        families = document.get("families")
        if not isinstance(families, list) or proposal.canonical_family_id is None:
            return False
        family = next(
            (f for f in families if isinstance(f, dict) and f.get("id") == proposal.canonical_family_id), None
        )
        if not isinstance(family, dict):
            return False
        sources = family.get("sources")
        evidence = family.get("regression_evidence")
        if not isinstance(sources, list) or not isinstance(evidence, list):
            raise CanonicalIntegrationError("canonical family learning fields are malformed")
        self._validate_proposal_contract(proposal)
        self._reject_event_identity_conflict(proposal, sources)
        return self._source_record(proposal) in sources and all(
            item in evidence for item in proposal.finding.regression_evidence
        )

    @staticmethod
    def _validate_proposal_contract(proposal: KnowledgeExtractionProposal) -> None:
        if proposal.schema_version != SCHEMA_VERSION:
            raise CanonicalIntegrationError("proposal schema is unsupported")
        if proposal.outcome != "existing-family" or proposal.canonical_family_id is None:
            raise CanonicalIntegrationError("only existing-family proposals may be integrated")
        if proposal.canonical_write_authorized:
            raise CanonicalIntegrationError("proposal must not self-authorize canonical writes")
        if proposal.finding.claimed_family_id != proposal.canonical_family_id:
            raise CanonicalIntegrationError("proposal family claim conflicts with canonical family")

    def integrate(self, proposal: KnowledgeExtractionProposal, registry_bytes: bytes) -> CanonicalIntegrationResult:
        self._validate_proposal_contract(proposal)
        digest = hashlib.sha256(registry_bytes).hexdigest()
        if digest != proposal.registry_digest:
            raise CanonicalIntegrationError("stale registry snapshot; re-extraction is required")
        replay = self._replay(proposal, registry_bytes)
        if replay != proposal:
            raise CanonicalIntegrationError("proposal replay does not match supplied evidence")
        try:
            document = json.loads(registry_bytes)
        except json.JSONDecodeError as exc:
            raise CanonicalIntegrationError("registry is unreadable") from exc
        families = document.get("families")
        if not isinstance(families, list):
            raise CanonicalIntegrationError("registry families are malformed")
        family = next(
            (f for f in families if isinstance(f, dict) and f.get("id") == proposal.canonical_family_id), None
        )
        if family is None:
            raise CanonicalIntegrationError("canonical family is missing")
        before = json.loads(json.dumps(family))
        sources = family.get("sources")
        evidence = family.get("regression_evidence")
        if not isinstance(sources, list) or not isinstance(evidence, list):
            raise CanonicalIntegrationError("canonical family learning fields are malformed")
        self._reject_event_identity_conflict(proposal, sources)
        for item in proposal.finding.regression_evidence:
            self._validate_regression_target(item)
        source = self._source_record(proposal)
        if source not in sources:
            sources.append(source)
        for item in proposal.finding.regression_evidence:
            if item not in evidence:
                evidence.append(item)
        changed = family != before
        rendered = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
        return CanonicalIntegrationResult(rendered, changed, proposal.canonical_family_id)

    @staticmethod
    def _stable_event_identity(finding_id: str) -> str:
        return finding_id.split("@", 1)[0]

    @classmethod
    def _reject_event_identity_conflict(cls, proposal: KnowledgeExtractionProposal, sources: list[object]) -> None:
        stable = cls._stable_event_identity(proposal.finding.finding_id)
        marker = f" {stable}@"
        current = f" {proposal.finding.finding_id} "
        for source in sources:
            if isinstance(source, str) and marker in source and current not in source:
                raise CanonicalIntegrationError("provider event identity is already bound to different evidence")

    @staticmethod
    def _validate_regression_target(reference: str) -> None:
        path_text, separator, node = reference.partition("::")
        if not separator or not path_text or not node:
            raise CanonicalIntegrationError("regression evidence must be a resolvable pytest target")
        path = Path(path_text)
        if path.is_absolute() or ".." in path.parts or not path.is_file() or path.suffix != ".py":
            raise CanonicalIntegrationError("regression evidence target is invalid")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            raise CanonicalIntegrationError("regression evidence target is unreadable") from exc
        parts = node.split("::")
        bodies = [tree.body]
        for index, part in enumerate(parts):
            candidates = []
            for body in bodies:
                candidates.extend(
                    item
                    for item in body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part
                )
            if not candidates:
                raise CanonicalIntegrationError("regression evidence target does not resolve")
            if index < len(parts) - 1:
                bodies = [item.body for item in candidates if isinstance(item, ast.ClassDef)]
                if not bodies:
                    raise CanonicalIntegrationError("regression evidence target does not resolve")

    @staticmethod
    def _source_record(proposal: KnowledgeExtractionProposal) -> str:
        finding = proposal.finding
        return (
            f"PR #{finding.source_pr} {finding.source_kind} {finding.finding_id} "
            f"reviewer={finding.reviewer}; fix={finding.fix_reference}"
        )

    @staticmethod
    def _replay(proposal: KnowledgeExtractionProposal, registry_bytes: bytes) -> KnowledgeExtractionProposal:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_bytes(registry_bytes)
            try:
                return KnowledgeExtractionAuthority(path).extract(proposal.finding)
            except KnowledgeExtractionError as exc:
                raise CanonicalIntegrationError("proposal replay failed") from exc
