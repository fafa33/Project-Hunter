"""Fail-closed historical candidate admission for ADR-0026 / issue #389.

The live Discovery registry is a mutable current-state index, not a point-in-time
source.  Only immutable, explicitly timestamped admission records may be replayed.
This module deliberately does not activate comparative valuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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
    supersedes_content_hash: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        if not all((self.source_id, self.source_version, self.taxonomy_id, self.taxonomy_version, self.content_hash)):
            raise ValueError("historical admission requires versioned provenance")
        if any(t.tzinfo is None for t in (self.effective_at, self.published_at, self.observed_at, self.known_at)):
            raise ValueError("historical admission timestamps must be timezone-aware")
        if (
            self.effective_at > self.observed_at
            or self.observed_at > self.published_at
            or self.published_at > self.known_at
        ):
            raise ValueError("historical admission chronology is inconsistent")
        if self.supersedes_content_hash is not None:
            if not self.correction_reason:
                raise ValueError("historical correction requires a reason")
            if self.supersedes_content_hash == self.content_hash:
                raise ValueError("historical correction must change the content hash")
            if (
                not self.supersedes_content_hash.startswith("sha256:")
                or len(self.supersedes_content_hash) != 71
                or any(c not in "0123456789abcdef" for c in self.supersedes_content_hash[7:])
            ):
                raise ValueError("historical correction requires a SHA-256 superseded hash")
        elif self.correction_reason:
            raise ValueError("historical correction reason requires superseded evidence")
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
    cutoff = cutoff.astimezone(UTC)
    known_by = known_by.astimezone(UTC)
    eligible: dict[str, HistoricalCandidateAdmission] = {}
    for item in admissions:
        if item.taxonomy_id != taxonomy_id or item.taxonomy_version != taxonomy_version:
            continue
        if item.effective_at > cutoff or item.known_at > known_by:
            continue
        if (
            item.candidate.source_record_id != item.source_id
            or item.candidate.source_record_version != item.source_version
        ):
            raise ValueError("candidate provenance does not match admission")
        if item.content_hash in eligible:
            previous = eligible[item.content_hash]
            if previous.candidate.identity.entity_id == item.candidate.identity.entity_id:
                raise ValueError("conflicting historical economic-entity membership")
            # Hash collisions between different identities are not valid lineage links;
            # preserve unrelated records for deterministic membership replay.
            if previous.candidate.identity != item.candidate.identity:
                eligible[item.content_hash + ":" + item.candidate.identity.entity_id] = item
                continue
        eligible[item.content_hash] = item

    children: dict[str, str] = {}
    for item in eligible.values():
        predecessor_hash = item.supersedes_content_hash
        if predecessor_hash is None:
            continue
        predecessor = eligible.get(predecessor_hash)
        if predecessor is None:
            raise ValueError("historical correction requires canonical lineage resolution")
        if (
            predecessor.candidate.identity != item.candidate.identity
            or predecessor.source_id != item.source_id
            or predecessor.taxonomy_id != item.taxonomy_id
            or predecessor.taxonomy_version != item.taxonomy_version
            or predecessor.known_at >= item.known_at
            or predecessor.effective_at > item.effective_at
        ):
            raise ValueError("historical correction lineage is inconsistent")
        if predecessor_hash in children:
            raise ValueError("branching historical correction lineage")
        children[predecessor_hash] = item.content_hash

    selected: dict[str, HistoricalCandidateAdmission] = {}
    for item in eligible.values():
        if item.content_hash in children:
            continue
        identity = item.candidate.identity
        if identity.entity_id in selected:
            raise ValueError("conflicting historical economic-entity membership")
        selected[identity.entity_id] = item
    return tuple(selected[key].candidate for key in sorted(selected))
