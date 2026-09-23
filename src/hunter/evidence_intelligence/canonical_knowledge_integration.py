"""Pure, replay-bound strengthening of existing canonical DPM families."""

from __future__ import annotations

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

    def integrate(self, proposal: KnowledgeExtractionProposal, registry_bytes: bytes) -> CanonicalIntegrationResult:
        if proposal.schema_version != SCHEMA_VERSION:
            raise CanonicalIntegrationError("proposal schema is unsupported")
        if proposal.outcome != "existing-family" or proposal.canonical_family_id is None:
            raise CanonicalIntegrationError("only existing-family proposals may be integrated")
        if proposal.canonical_write_authorized:
            raise CanonicalIntegrationError("proposal must not self-authorize canonical writes")
        if proposal.finding.claimed_family_id != proposal.canonical_family_id:
            raise CanonicalIntegrationError("proposal family claim conflicts with canonical family")
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
