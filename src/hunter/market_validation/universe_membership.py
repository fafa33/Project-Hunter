"""ADR 0040: fail-closed, shadow-only universe membership authorization.

The caller must supply independently authenticated, cutoff-knowable evidence snapshots.
Current-state Candidate Registry rows are deliberately not accepted as replay proof.
This service does not execute Market Validation or persist its own decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from hunter.market_validation.models import ProjectValidationTarget


@dataclass(frozen=True)
class MembershipEvidence:
    candidate_id: str
    canonical_entity_id: str
    project: ProjectValidationTarget
    identity_outcome: str
    lifecycle_status: str
    known_at: datetime
    observed_at: datetime
    source_digest: str
    registry_reference: str
    provenance_verified: bool = False


@dataclass(frozen=True)
class MembershipDecision:
    candidate_id: str
    entity_id: str
    outcome: Literal["included", "not_included", "unavailable"]
    reason: str


@dataclass(frozen=True)
class AuthorizedUniverse:
    run_id: str
    cutoff: datetime
    policy_version: str
    decisions: tuple[MembershipDecision, ...]
    targets: tuple[ProjectValidationTarget, ...]
    manifest_digest: str
    runnable: bool


class MarketValidationUniverseMembershipAuthority:
    """Authorizes membership only; ADR 0020 retains evidence acceptance."""

    def authorize(
        self,
        evidence: tuple[MembershipEvidence, ...],
        *,
        cutoff: datetime,
        run_id: str,
        baseline_run_id: str,
        policy_version: str,
        minimum_entities: int = 50,
        output_directory: str = "",
        baseline_output_directory: str = "",
    ) -> AuthorizedUniverse:
        if cutoff.tzinfo is None or not run_id.strip() or not policy_version.strip():
            raise ValueError("aware cutoff, run_id and policy_version are required")
        if (
            run_id == baseline_run_id
            or minimum_entities < 50
            or not output_directory.strip()
            or not baseline_output_directory.strip()
        ):
            raise ValueError("candidate run must be isolated and preserve the 50-entity minimum")
        candidate_path = PurePosixPath(output_directory)
        baseline_path = PurePosixPath(baseline_output_directory)
        if any(part == ".." for part in candidate_path.parts + baseline_path.parts):
            raise ValueError("noncanonical output paths are forbidden")
        if candidate_path.is_absolute() or baseline_path.is_absolute():
            raise ValueError("output paths must be relative to the same workspace root")
        if (
            candidate_path == baseline_path
            or candidate_path in baseline_path.parents
            or baseline_path in candidate_path.parents
        ):
            raise ValueError("candidate output must not overlap baseline output")
        cutoff = cutoff.astimezone(UTC)
        decisions: list[MembershipDecision] = []
        targets: list[ProjectValidationTarget] = []
        used_entities: set[str] = set()
        used_candidates: set[str] = set()
        for item in sorted(evidence, key=lambda e: (e.candidate_id, e.canonical_entity_id)):
            entity = item.canonical_entity_id.strip()
            candidate = item.candidate_id.strip()
            if not candidate or candidate in used_candidates:
                raise ValueError("missing or duplicate candidate identity")
            used_candidates.add(candidate)
            if item.known_at.tzinfo is None or item.observed_at.tzinfo is None:
                outcome, reason = "unavailable", "missing aware historical timestamps"
            elif item.known_at > cutoff or item.observed_at > cutoff:
                outcome, reason = "unavailable", "evidence not knowable at cutoff"
            elif not item.provenance_verified or not item.source_digest.strip() or not item.registry_reference.strip():
                outcome, reason = "unavailable", "missing authenticated evidence provenance"
            elif not entity or item.project.project_id != entity:
                outcome, reason = "unavailable", "unproven canonical economic entity mapping"
            elif item.identity_outcome != "exact" or item.lifecycle_status in {"rejected", "archived"}:
                outcome, reason = "not_included", "identity or lifecycle ineligible"
            elif entity in used_entities:
                outcome, reason = "not_included", "duplicate economic entity"
            else:
                outcome, reason = "included", "eligible distinct economic entity"
                used_entities.add(entity)
                targets.append(item.project)
            decisions.append(MembershipDecision(candidate, entity, outcome, reason))
        # Never expose an undersized candidate universe as executable.
        runnable = len(targets) >= minimum_entities
        payload = {
            "cutoff": cutoff.isoformat(),
            "run_id": run_id,
            "policy_version": policy_version,
            "output_directory": str(candidate_path),
            "evidence": [
                {
                    "candidate_id": e.candidate_id,
                    "entity_id": e.canonical_entity_id,
                    "known_at": e.known_at.isoformat(),
                    "observed_at": e.observed_at.isoformat(),
                    "digest": e.source_digest,
                    "registry": e.registry_reference,
                    "verified": e.provenance_verified,
                }
                for e in sorted(evidence, key=lambda e: (e.candidate_id, e.canonical_entity_id))
            ],
            "decisions": [d.__dict__ for d in decisions],
            "targets": [
                {"project_id": t.project_id, "name": t.name, "sector": t.sector, "metadata": dict(t.metadata)}
                for t in targets
            ],
            "runnable": runnable,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return AuthorizedUniverse(run_id, cutoff, policy_version, tuple(decisions), tuple(targets), digest, runnable)
