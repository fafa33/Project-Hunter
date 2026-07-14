from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from hunter.discovery.models import CandidateIdentity, CandidateRecord, DiscoveryConflict
from hunter.discovery.repository import CandidateRegistryRepository
from hunter.execution.identity import identity

TRUSTED_PROVIDER_NAMESPACES = {"coingecko", "defillama", "hunter_project", "github_repository"}


@dataclass(frozen=True)
class IdentityResolutionSummary:
    evaluated: int
    by_outcome: dict[str, int]
    conflicts_persisted: int

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
        conflicts_persisted = 0
        counts: Counter[str] = Counter()
        evaluated = 0
        for batch in self.repository.iter_candidates(batch_size=batch_size):
            results = tuple(
                self.resolve_candidate(candidate, ticker_groups=ticker_groups, evaluated_at=now) for candidate in batch
            )
            self.repository.save_identity_results(results)
            for result in results:
                evaluated += 1
                counts[result.outcome] += 1
                if result.outcome in {"conflict", "ambiguous"}:
                    self.repository.save_conflict(_conflict_for_identity(result))
                    conflicts_persisted += 1
        return IdentityResolutionSummary(
            evaluated=evaluated, by_outcome=dict(counts), conflicts_persisted=conflicts_persisted
        )

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
        related = _related_ticker_candidates(candidate, ticker_groups or {})
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


def _related_ticker_candidates(
    candidate: CandidateRecord, ticker_groups: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    related: set[str] = set()
    for alias in candidate.aliases:
        if alias.alias_type != "ticker_symbol":
            continue
        for candidate_id in ticker_groups.get(alias.alias.upper(), ()):
            if candidate_id != candidate.candidate_id:
                related.add(candidate_id)
    return tuple(sorted(related))


def _has_official_domain(candidate: CandidateRecord) -> bool:
    links = candidate.metadata.get("official_links")
    return isinstance(links, (list, tuple)) and any(str(link).strip() for link in links)


def _has_rejection_marker(candidate: CandidateRecord) -> bool:
    return any(bool(candidate.metadata.get(marker)) for marker in ("spam", "scam", "impersonation", "blocked"))
