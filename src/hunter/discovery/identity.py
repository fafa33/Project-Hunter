from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from hunter.discovery.models import (
    IDENTITY_POLICY_VERSION,
    CandidateIdentity,
    CandidateRecord,
    DiscoveryConflict,
)
from hunter.discovery.repository import CandidateRegistryRepository
from hunter.execution.identity import identity

TRUSTED_PROVIDER_NAMESPACES = {"coingecko", "defillama", "hunter_project", "github_repository"}

# Values refreshed by acquisition cadence are not identity evidence. Excluding
# them keeps an unchanged registry from being re-resolved on every cycle.
VOLATILE_IDENTITY_METADATA_KEYS = frozenset(
    {
        "last_updated",
        "market_evidence_observed_at",
        "observed_at",
        "fetched_at",
        "retrieved_at",
        "scraped_at",
        "seen_at",
    }
)


def identity_evidence_fingerprint(candidate: CandidateRecord, *, related_candidate_ids: tuple[str, ...] = ()) -> str:
    """Stable digest of the persisted evidence that may change an identity outcome."""
    metadata = {
        str(key): value
        for key, value in sorted(candidate.metadata.items())
        if str(key) not in VOLATILE_IDENTITY_METADATA_KEYS
    }
    payload = {
        "candidate": candidate.candidate_id,
        "slug": candidate.slug,
        "symbol": (candidate.symbol or "").upper(),
        "candidate_type": candidate.candidate_type,
        "identifiers": tuple(
            sorted(
                (
                    item.namespace,
                    item.value,
                    item.source,
                    round(item.confidence, 4),
                )
                for item in candidate.identifiers
            )
        ),
        "aliases": tuple(
            sorted((item.alias_type, item.alias, round(item.confidence, 4)) for item in candidate.aliases)
        ),
        "source_ids": tuple(sorted({*(candidate.source_ids), *(item.source_id for item in candidate.sources)})),
        "evidence_ids": tuple(sorted(candidate.evidence_ids)),
        "metadata": metadata,
        # Ticker collisions are part of identity evidence: adding a colliding
        # candidate must invalidate prior outcomes on both sides.
        "related_candidate_ids": tuple(sorted(related_candidate_ids)),
    }
    return identity("candidate-identity-evidence", payload)


def identity_is_accepted(
    result: CandidateIdentity | None,
    *,
    candidate: CandidateRecord | None = None,
    related_candidate_ids: tuple[str, ...] | None = None,
) -> bool:
    """True only for a current, policy-valid identity that gates deep analysis."""
    if result is None or result.policy_version != IDENTITY_POLICY_VERSION:
        return False
    if result.outcome not in {"exact", "probable"} or result.confidence < 0.7:
        return False
    if "provider_metadata_disagreement" in result.conflicts:
        return False
    if candidate is not None:
        related = related_candidate_ids
        if related is None:
            related = result.related_candidate_ids
        if result.evidence_fingerprint != identity_evidence_fingerprint(candidate, related_candidate_ids=related):
            return False
    return True


@dataclass(frozen=True)
class IdentityResolutionSummary:
    evaluated: int
    by_outcome: dict[str, int]
    conflicts_persisted: int
    unchanged: int = 0

    @property
    def coverage(self) -> float:
        if self.evaluated == 0:
            return 0.0
        resolved = self.evaluated - self.by_outcome.get("unresolved", 0)
        return round(resolved / self.evaluated, 4)


class CandidateIdentityResolutionEngine:
    def __init__(self, repository: CandidateRegistryRepository) -> None:
        self.repository = repository

    def resolve_all(self, *, batch_size: int = 1000) -> IdentityResolutionSummary:
        now = datetime.now(tz=UTC)
        ticker_groups = self.repository.alias_groups(alias_type="ticker_symbol")
        counts: Counter[str] = Counter()
        evaluated = 0
        conflicts_persisted = 0
        for batch in self.repository.iter_candidates(batch_size=batch_size):
            results = self._evaluate_batch(batch, ticker_groups=ticker_groups, evaluated_at=now)
            conflicts_persisted += self._persist_results(results)
            evaluated += len(results)
            counts.update(result.outcome for result in results)
        return IdentityResolutionSummary(
            evaluated=evaluated, by_outcome=dict(counts), conflicts_persisted=conflicts_persisted
        )

    def resolve_batch(self, *, limit: int = 500) -> IdentityResolutionSummary:
        """Resolve one fair, resumable batch; skip evidence-identical candidates."""
        now = datetime.now(tz=UTC)
        ticker_groups = self.repository.alias_groups(alias_type="ticker_symbol")
        batch = self.repository.fair_candidates("identity", limit)
        pending: list[CandidateRecord] = []
        unchanged = 0
        for candidate in batch:
            related = related_ticker_candidates(candidate, ticker_groups)
            fingerprint = identity_evidence_fingerprint(candidate, related_candidate_ids=related)
            prior = self.repository.identity_result_for(candidate.candidate_id)
            if (
                prior is not None
                and prior.policy_version == IDENTITY_POLICY_VERSION
                and prior.evidence_fingerprint == fingerprint
            ):
                unchanged += 1
                continue
            pending.append(candidate)
        results = self._evaluate_batch(tuple(pending), ticker_groups=ticker_groups, evaluated_at=now)
        conflicts_persisted = self._persist_results(results)
        return IdentityResolutionSummary(
            evaluated=len(results),
            by_outcome=dict(Counter(result.outcome for result in results)),
            conflicts_persisted=conflicts_persisted,
            unchanged=unchanged,
        )

    def _evaluate_batch(
        self,
        candidates: tuple[CandidateRecord, ...],
        *,
        ticker_groups: dict[str, tuple[str, ...]],
        evaluated_at: datetime,
    ) -> tuple[CandidateIdentity, ...]:
        results: list[CandidateIdentity] = []
        for candidate in candidates:
            related = related_ticker_candidates(candidate, ticker_groups)
            result = self.resolve_candidate(candidate, ticker_groups=ticker_groups, evaluated_at=evaluated_at)
            results.append(
                replace(
                    result,
                    policy_version=IDENTITY_POLICY_VERSION,
                    evidence_fingerprint=identity_evidence_fingerprint(candidate, related_candidate_ids=related),
                )
            )
        return tuple(results)

    def _persist_results(self, results: tuple[CandidateIdentity, ...]) -> int:
        if not results:
            return 0
        self.repository.save_identity_results(results)
        conflicts = 0
        for result in results:
            if result.outcome in {"conflict", "ambiguous"}:
                self.repository.save_conflict(_conflict_for_identity(result))
                conflicts += 1
        return conflicts

    def resolve_candidate(
        self,
        candidate: CandidateRecord,
        *,
        ticker_groups: dict[str, tuple[str, ...]] | None = None,
        evaluated_at: datetime | None = None,
    ) -> CandidateIdentity:
        evaluated = evaluated_at or datetime.now(tz=UTC)
        source_ids = tuple(source.source_id for source in candidate.sources)
        evidence_ids = tuple(candidate.evidence_ids or source_ids)
        missing = _missing_identity_evidence(candidate)
        source_candidate_ids = _source_candidate_ids(candidate)
        related = related_ticker_candidates(candidate, ticker_groups or {})
        conflicts = _identity_conflicts(candidate, related)
        if _has_rejection_marker(candidate):
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="rejected",
                confidence=0.1,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="candidate contains deterministic rejection marker",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if conflicts and "provider_metadata_disagreement" in conflicts:
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="conflict",
                confidence=0.25,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="provider evidence contains unresolved disagreement",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if _has_hunter_identity(candidate):
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="exact",
                confidence=1.0,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="configured Hunter project identity is present",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if _has_chain_contract(candidate) and len(candidate.sources) >= 2:
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="exact",
                confidence=0.98,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="chain-aware contract identity is corroborated by multiple source records",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if _trusted_provider_count(candidate) >= 2:
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="exact",
                confidence=0.95,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="multiple trusted provider identities are present",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if related and not _has_chain_contract(candidate):
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="ambiguous",
                confidence=0.35,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="ticker symbol collides with other candidates and lacks stronger identity evidence",
                missing_evidence=missing,
                conflicts=tuple(sorted({*conflicts, "ticker_collision"})),
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if _has_chain_contract(candidate):
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="probable",
                confidence=0.85,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="chain-aware contract identity is present",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        if _trusted_provider_count(candidate) == 1:
            return CandidateIdentity(
                candidate_id=candidate.candidate_id,
                outcome="probable",
                confidence=0.7,
                evidence_ids=evidence_ids,
                source_candidate_ids=source_candidate_ids,
                source_ids=source_ids,
                reason="single trusted provider identity is present",
                missing_evidence=missing,
                conflicts=conflicts,
                related_candidate_ids=related,
                evaluated_at=evaluated,
            )
        return CandidateIdentity(
            candidate_id=candidate.candidate_id,
            outcome="unresolved",
            confidence=0.2,
            evidence_ids=evidence_ids,
            source_candidate_ids=source_candidate_ids,
            source_ids=source_ids,
            reason="candidate lacks sufficient high-confidence identity evidence",
            missing_evidence=missing,
            conflicts=conflicts,
            related_candidate_ids=related,
            evaluated_at=evaluated,
        )


def _conflict_for_identity(result: CandidateIdentity) -> DiscoveryConflict:
    return DiscoveryConflict(
        conflict_id=identity(
            "candidate-identity-conflict",
            {"candidate": result.candidate_id, "outcome": result.outcome, "conflicts": result.conflicts},
        ),
        candidate_id=result.candidate_id,
        conflict_type=f"identity_{result.outcome}",
        description=result.reason,
        detected_at=result.evaluated_at,
        source_ids=result.source_ids,
        status="unresolved",
    )


def _has_hunter_identity(candidate: CandidateRecord) -> bool:
    return any(identifier.namespace == "hunter_project" for identifier in candidate.identifiers)


def _has_chain_contract(candidate: CandidateRecord) -> bool:
    return any(identifier.namespace.startswith("contract:") for identifier in candidate.identifiers)


def _trusted_provider_count(candidate: CandidateRecord) -> int:
    return len(
        {
            identifier.namespace
            for identifier in candidate.identifiers
            if identifier.namespace in TRUSTED_PROVIDER_NAMESPACES
        }
    )


def _source_candidate_ids(candidate: CandidateRecord) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"{identifier.namespace}:{identifier.value}"
                for identifier in candidate.identifiers
                if identifier.namespace in TRUSTED_PROVIDER_NAMESPACES or identifier.namespace.startswith("contract:")
            }
        )
    )


def _missing_identity_evidence(candidate: CandidateRecord) -> tuple[str, ...]:
    missing: list[str] = []
    if not _has_chain_contract(candidate):
        missing.append("verified_chain_contract")
    if not _has_official_domain(candidate):
        missing.append("official_domain")
    if not any(identifier.namespace == "github_repository" for identifier in candidate.identifiers):
        missing.append("official_repository")
    if _trusted_provider_count(candidate) == 0:
        missing.append("trusted_provider_identity")
    return tuple(missing)


def _identity_conflicts(candidate: CandidateRecord, related_ticker_candidates: tuple[str, ...]) -> tuple[str, ...]:
    conflicts: set[str] = set()
    if candidate.metadata.get("provider_disagreements"):
        conflicts.add("provider_metadata_disagreement")
    if related_ticker_candidates:
        conflicts.add("ticker_collision")
    return tuple(sorted(conflicts))


def related_ticker_candidates(candidate: CandidateRecord, ticker_groups: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    related: set[str] = set()
    for alias in candidate.aliases:
        if alias.alias_type != "ticker_symbol":
            continue
        for candidate_id in ticker_groups.get(alias.alias.upper(), ()):
            if candidate_id != candidate.candidate_id:
                related.add(candidate_id)
    return tuple(sorted(related))


def _has_official_domain(candidate: CandidateRecord) -> bool:
    links = candidate.metadata.get("verified_official_links")
    return isinstance(links, (list, tuple)) and any(str(link).strip() for link in links)


def _has_rejection_marker(candidate: CandidateRecord) -> bool:
    return any(bool(candidate.metadata.get(marker)) for marker in ("spam", "scam", "impersonation", "blocked"))
