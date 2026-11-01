"""Registry-owned immutable canonical-entity identity evidence (ADR 0040).

Records are never inferred from mutable candidate or identity tables. External
attestation verification is a mandatory read-time gate for admission.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("canonical identity time must be timezone aware")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class CanonicalEntityRecord:
    candidate_id: str
    canonical_entity_id: str
    representation: str
    outcome: str
    lifecycle_status: str
    source_digest: str
    source_reference: str
    policy_version: str
    observed_at: datetime
    known_at: datetime
    upstream_attestation: str

    def payload(self, *, repository: str, ingested_at: datetime, previous_digest: str) -> dict[str, Any]:
        if not repository or not all(
            (
                self.candidate_id,
                self.canonical_entity_id,
                self.representation,
                self.source_digest,
                self.source_reference,
                self.policy_version,
                self.upstream_attestation,
            )
        ):
            raise ValueError("incomplete canonical identity provenance")
        if self.outcome not in {"exact", "probable", "ambiguous", "conflict", "rejected", "unresolved"}:
            raise ValueError("invalid canonical identity outcome")
        observed, known, ingested = _time(self.observed_at), _time(self.known_at), _time(ingested_at)
        if observed > known or known > ingested:
            raise ValueError("canonical identity chronology invalid")
        return {
            "repository": repository,
            "candidate_id": self.candidate_id,
            "canonical_entity_id": self.canonical_entity_id,
            "representation": self.representation,
            "outcome": self.outcome,
            "lifecycle_status": self.lifecycle_status,
            "source_digest": self.source_digest,
            "source_reference": self.source_reference,
            "policy_version": self.policy_version,
            "observed_at": observed,
            "known_at": known,
            "ingested_at": ingested,
            "upstream_attestation": self.upstream_attestation,
            "previous_digest": previous_digest,
        }


def digest_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replay_canonical_entities(
    entries: list[tuple[str, dict[str, Any]]],
    *,
    repository: str,
    cutoff: datetime,
    verify_attestation: Callable[[dict[str, Any]], bool],
) -> dict[str, str]:
    """Strict-known replay; returns entity -> candidate only for proven unique exact mappings."""
    cutoff_text = _time(cutoff)
    previous = f"GENESIS:{repository}"
    candidates: dict[str, dict[str, Any]] = {}
    for digest, payload in entries:
        if payload.get("repository") != repository or payload.get("previous_digest") != previous:
            raise ValueError("canonical identity chain mismatch")
        if digest_payload(payload) != digest:
            raise ValueError("canonical identity digest mismatch")
        previous = digest
        if payload.get("known_at", "~") > cutoff_text or payload.get("ingested_at", "~") > cutoff_text:
            continue
        if not verify_attestation(payload):
            raise ValueError("canonical identity attestation unverified")
        candidates[str(payload["candidate_id"])] = payload
    eligible: dict[str, str] = {}
    counts: dict[str, int] = {}
    for candidate_id, payload in candidates.items():
        if payload["outcome"] != "exact" or payload["lifecycle_status"] in {"rejected", "archived"}:
            continue
        entity = str(payload["canonical_entity_id"])
        counts[entity] = counts.get(entity, 0) + 1
        eligible[entity] = candidate_id
    return {entity: candidate for entity, candidate in eligible.items() if counts[entity] == 1}
