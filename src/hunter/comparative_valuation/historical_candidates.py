"""Fail-closed historical candidate admission for ADR-0026 / issue #389.

The live Discovery registry is a mutable current-state index, not a point-in-time
source.  Only immutable, explicitly timestamped admission records may be replayed.
This module deliberately does not activate comparative valuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.comparative_valuation.models import PeerCandidate


@dataclass(frozen=True)
class HistoricalCandidateAdmission:
    candidate: PeerCandidate
    source_id: str
    source_version: str
    taxonomy_id: str
    taxonomy_version: str
    effective_at: datetime
    published_at: datetime
    observed_at: datetime
    known_at: datetime
    content_hash: str

    def __post_init__(self) -> None:
        if not all((self.source_id, self.source_version, self.taxonomy_id, self.taxonomy_version, self.content_hash)):
            raise ValueError("historical admission requires versioned provenance")
        if any(t.tzinfo is None for t in (self.effective_at, self.published_at, self.observed_at, self.known_at)):
            raise ValueError("historical admission timestamps must be timezone-aware")
        if (
            not self.content_hash.startswith("sha256:")
            or len(self.content_hash) != 71
            or any(c not in "0123456789abcdef" for c in self.content_hash[7:])
        ):
            raise ValueError("historical admission requires a SHA-256 content hash")


def select_historical_candidates(
    admissions: tuple[HistoricalCandidateAdmission, ...],
    *,
    cutoff: datetime,
    known_by: datetime,
    taxonomy_id: str,
    taxonomy_version: str,
) -> tuple[PeerCandidate, ...]:
    """Replay only records that existed at both historical coordinates.

    A later correction cannot silently replace an earlier observation. Conflicting
    known admissions for one representation fail closed until explicitly resolved
    by a separately versioned canonical source.
    """
    if cutoff.tzinfo is None or known_by.tzinfo is None or known_by > cutoff:
        raise ValueError("historical replay requires aware known_by <= cutoff")
    if not taxonomy_id or not taxonomy_version:
        raise ValueError("historical replay requires versioned taxonomy")
    selected: dict[tuple[str, str], HistoricalCandidateAdmission] = {}
    for item in admissions:
        if item.taxonomy_id != taxonomy_id or item.taxonomy_version != taxonomy_version:
            continue
        if max(item.effective_at, item.published_at, item.observed_at, item.known_at) > cutoff:
            continue
        if max(item.published_at, item.observed_at, item.known_at) > known_by:
            continue
        if (
            item.candidate.source_record_id != item.source_id
            or item.candidate.source_record_version != item.source_version
        ):
            raise ValueError("candidate provenance does not match admission")
        identity = item.candidate.identity
        key = (identity.entity_id, identity.representation_id)
        if any(existing.candidate.identity.entity_id == identity.entity_id for existing in selected.values()):
            raise ValueError("conflicting historical economic-entity membership")
        selected[key] = item
    return tuple(selected[key].candidate for key in sorted(selected))
