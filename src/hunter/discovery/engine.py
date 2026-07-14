from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.acquisition.project_identifiers import ProjectIdentifier, load_project_identifiers
from hunter.discovery.configuration import DiscoveryConfig
from hunter.discovery.models import (
    CandidateAlias,
    CandidateIdentifier,
    CandidateQueueEntry,
    CandidateRecord,
    CandidateScreeningResult,
    CandidateSource,
    DiscoveryRun,
)
from hunter.discovery.providers import (
    CoinGeckoDiscoveryProvider,
    DefiLlamaDiscoveryProvider,
    DexScreenerDiscoveryProvider,
    DiscoveredCandidate,
    DiscoveryProvider,
    GeckoTerminalDiscoveryProvider,
)
from hunter.discovery.repository import CandidateRegistryRepository
from hunter.execution.identity import identity
from hunter.market_validation import load_market_validation_config


class CandidateDiscoveryEngine:
    def __init__(
        self,
        repository: CandidateRegistryRepository,
        config: DiscoveryConfig,
        *,
        providers: dict[str, DiscoveryProvider] | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.providers = providers or self._configured_providers(config)

    def sync(self, *, provider: str = "seed", limit: int | None = None) -> DiscoveryRun:
        if provider == "seed":
            return self.sync_configured_universe()
        if provider == "all":
            total_seen = 0
            total_created = 0
            total_updated = 0
            started_at = datetime.now(tz=UTC)
            messages = []
            status = "succeeded"
            for name in sorted(self.providers):
                run = self.sync_provider(name, limit=limit)
                total_seen += run.candidates_seen
                total_created += run.candidates_created
                total_updated += run.candidates_updated
                messages.append(f"{name}:{run.status}:{run.message}".rstrip(":"))
                if run.status != "succeeded":
                    status = "partial" if total_seen else "failed"
            finished_at = datetime.now(tz=UTC)
            run = DiscoveryRun(
                run_id=identity("candidate-discovery-run", {"provider": "all", "started_at": started_at}),
                provider="all",
                started_at=started_at,
                finished_at=finished_at,
                candidates_seen=total_seen,
                candidates_created=total_created,
                candidates_updated=total_updated,
                status=status,
                message="; ".join(messages),
            )
            self.repository.save_run(run)
            return run
        return self.sync_provider(provider, limit=limit)

    def sync_configured_universe(self) -> DiscoveryRun:
        started_at = datetime.now(tz=UTC)
        market_config = load_market_validation_config(self.config.market_validation_config)
        identifiers = load_project_identifiers(self.config.project_identifiers_config)
        candidates = tuple(
            self._configured_candidate(
                project_id=target.project_id,
                name=target.name,
                sector=target.sector,
                project_identifier=identifiers.get(target.project_id),
                observed_at=started_at,
            )
            for target in market_config.project_universe
        )
        created, updated = self.repository.upsert_many(candidates)
        finished_at = datetime.now(tz=UTC)
        run = DiscoveryRun(
            run_id=identity("candidate-discovery-run", {"provider": "seed", "started_at": started_at}),
            provider="seed",
            started_at=started_at,
            finished_at=finished_at,
            candidates_seen=len(candidates),
            candidates_created=created,
            candidates_updated=updated,
            status="succeeded",
            message="seeded configured market-validation universe",
        )
        self.repository.save_run(run)
        return run

    def sync_provider(self, provider: str, *, limit: int | None = None) -> DiscoveryRun:
        started_at = datetime.now(tz=UTC)
        if provider not in self.providers:
            run = DiscoveryRun(
                run_id=identity("candidate-discovery-run", {"provider": provider, "started_at": started_at}),
                provider=provider,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                candidates_seen=0,
                candidates_created=0,
                candidates_updated=0,
                status="unsupported",
                message="provider is not configured",
            )
            self.repository.save_run(run)
            return run
        try:
            raw_candidates = self.providers[provider].discover(
                limit=limit or self.config.provider(provider).limit or 250,
            )
        except ProviderUnavailableError as exc:
            run = DiscoveryRun(
                run_id=identity("candidate-discovery-run", {"provider": provider, "started_at": started_at}),
                provider=provider,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                candidates_seen=0,
                candidates_created=0,
                candidates_updated=0,
                status="unavailable",
                message=str(exc),
            )
            self.repository.save_run(run)
            return run
        candidates = tuple(self._provider_candidate(item, observed_at=started_at) for item in raw_candidates)
        created, updated = self.repository.upsert_many(candidates)
        run = DiscoveryRun(
            run_id=identity("candidate-discovery-run", {"provider": provider, "started_at": started_at}),
            provider=provider,
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
            candidates_seen=len(candidates),
            candidates_created=created,
            candidates_updated=updated,
            status="succeeded",
            message=f"synced {len(candidates)} candidates",
        )
        self.repository.save_run(run)
        self.repository.save_checkpoint(provider, str(len(candidates)), run.status)
        return run

    def screen_candidates(self, *, limit: int = 1000) -> tuple[CandidateScreeningResult, ...]:
        screened_at = datetime.now(tz=UTC)
        results = tuple(
            self._screen_candidate(candidate, screened_at=screened_at)
            for candidate in self.repository.list_candidates(limit=limit)
        )
        for result in results:
            self.repository.save_screening_result(result)
        return results

    def refresh_queue(self, *, limit: int = 1000) -> tuple[CandidateQueueEntry, ...]:
        now = datetime.now(tz=UTC)
        results = self.screen_candidates(limit=limit)
        candidates = {candidate.candidate_id: candidate for candidate in self.repository.list_candidates(limit=limit)}
        entries = tuple(
            self._queue_entry(result, candidates[result.candidate_id], updated_at=now)
            for result in results
            if result.candidate_id in candidates
        )
        self.repository.save_queue_entries(entries)
        return entries

    def run_market_discovery(self, *, limit: int | None = None) -> dict[str, object]:
        seed_run = self.sync_configured_universe()
        provider_run = self.sync(provider="all", limit=limit)
        screening = self.screen_candidates()
        queue = self.refresh_queue()
        stats = self.repository.stats()
        return {
            "seed_run": seed_run.run_id,
            "provider_run": provider_run.run_id,
            "candidates": stats.total_candidates,
            "screened": len(screening),
            "queued": len(queue),
            "top": [entry.candidate_id for entry in queue[:10]],
        }

    def _configured_candidate(
        self,
        *,
        project_id: str,
        name: str,
        sector: str,
        project_identifier: ProjectIdentifier | None,
        observed_at: datetime,
    ) -> CandidateRecord:
        candidate_id = self._candidate_id("hunter_project", project_id)
        identifiers = [
            CandidateIdentifier(
                candidate_id, "hunter_project", project_id, "market_validation_config", 1.0, observed_at, observed_at
            )
        ]
        aliases = [CandidateAlias(candidate_id, name, "configured_name", "market_validation_config", 1.0)]
        sources = [
            CandidateSource(
                source_id=identity("candidate-source", {"provider": "seed", "project": project_id}),
                candidate_id=candidate_id,
                provider="seed",
                source_type="configuration",
                source_url=self.config.market_validation_config,
                source_ref=project_id,
                observed_at=observed_at,
                confidence=1.0,
            )
        ]
        metadata: dict[str, object] = {"configured_universe": True}
        if project_identifier is not None:
            if project_identifier.coingecko_id:
                identifiers.append(
                    CandidateIdentifier(
                        candidate_id,
                        "coingecko",
                        project_identifier.coingecko_id,
                        "project_identifiers_config",
                        1.0,
                        observed_at,
                        observed_at,
                    )
                )
            if project_identifier.defillama_slug:
                identifiers.append(
                    CandidateIdentifier(
                        candidate_id,
                        "defillama",
                        project_identifier.defillama_slug,
                        "project_identifiers_config",
                        1.0,
                        observed_at,
                        observed_at,
                    )
                )
            for repository in project_identifier.github_repositories:
                identifiers.append(
                    CandidateIdentifier(
                        candidate_id,
                        "github_repository",
                        repository,
                        "project_identifiers_config",
                        1.0,
                        observed_at,
                        observed_at,
                    )
                )
            metadata["identifier_config_present"] = True
        return CandidateRecord(
            candidate_id=candidate_id,
            slug=project_id,
            name=name,
            symbol=None,
            sector=sector,
            primary_chain=None,
            candidate_type="project",
            lifecycle_status="analyzable",
            discovery_source="seed",
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            confidence=1.0,
            identifiers=tuple(identifiers),
            aliases=tuple(aliases),
            sources=tuple(sources),
            source_ids=tuple(source.source_id for source in sources),
            metadata=metadata,
        )

    def _provider_candidate(self, candidate: DiscoveredCandidate, *, observed_at: datetime) -> CandidateRecord:
        candidate_id = self._candidate_id_for_provider_candidate(candidate)
        identifiers = [
            CandidateIdentifier(
                candidate_id,
                candidate.provider,
                candidate.provider_id,
                candidate.provider,
                0.95,
                observed_at,
                observed_at,
            )
        ]
        metadata = candidate.metadata or {}
        contract_address = str(metadata.get("contract_address") or "").strip().lower()
        chain = str(metadata.get("chain") or candidate.primary_chain or "").strip().lower()
        if chain and contract_address:
            identifiers.append(
                CandidateIdentifier(
                    candidate_id,
                    f"contract:{chain}",
                    contract_address,
                    candidate.provider,
                    0.98,
                    observed_at,
                    observed_at,
                )
            )
        aliases = [CandidateAlias(candidate_id, candidate.name, "provider_name", candidate.provider, 0.95)]
        if candidate.symbol:
            aliases.append(
                CandidateAlias(candidate_id, candidate.symbol.upper(), "ticker_symbol", candidate.provider, 0.55)
            )
        if candidate.primary_chain:
            aliases.append(
                CandidateAlias(candidate_id, candidate.primary_chain, "ecosystem_or_chain", candidate.provider, 0.75)
            )
        source = CandidateSource(
            source_id=identity(
                "candidate-source",
                {"provider": candidate.provider, "source_ref": candidate.provider_id},
            ),
            candidate_id=candidate_id,
            provider=candidate.provider,
            source_type="public_provider_listing",
            source_url=candidate.source_url,
            source_ref=candidate.provider_id,
            observed_at=observed_at,
            confidence=0.95,
        )
        lifecycle = "screenable" if len(identifiers) >= self.config.minimum_screening_identifiers else "identified"
        return CandidateRecord(
            candidate_id=candidate_id,
            slug=candidate.slug,
            name=candidate.name,
            symbol=candidate.symbol,
            sector=candidate.sector,
            primary_chain=candidate.primary_chain,
            candidate_type=candidate.candidate_type,  # type: ignore[arg-type]
            lifecycle_status=lifecycle,  # type: ignore[arg-type]
            discovery_source=candidate.provider,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            confidence=0.95,
            identifiers=tuple(identifiers),
            aliases=tuple(aliases),
            sources=(source,),
            source_ids=(source.source_id,),
            metadata=metadata,
        )

    def _configured_providers(self, config: DiscoveryConfig) -> dict[str, DiscoveryProvider]:
        providers: dict[str, DiscoveryProvider] = {}
        coingecko = config.provider("coingecko")
        if coingecko.enabled:
            providers["coingecko"] = CoinGeckoDiscoveryProvider(
                base_url=coingecko.base_url or "https://api.coingecko.com/api/v3",
                timeout_seconds=coingecko.timeout_seconds,
                max_attempts=coingecko.max_attempts,
                backoff_seconds=coingecko.backoff_seconds,
            )
        defillama = config.provider("defillama")
        if defillama.enabled:
            providers["defillama"] = DefiLlamaDiscoveryProvider(
                base_url=defillama.base_url or "https://api.llama.fi",
                timeout_seconds=defillama.timeout_seconds,
                max_attempts=defillama.max_attempts,
                backoff_seconds=defillama.backoff_seconds,
            )
        geckoterminal = config.provider("geckoterminal")
        if geckoterminal.enabled:
            providers["geckoterminal"] = GeckoTerminalDiscoveryProvider(
                base_url=geckoterminal.base_url or "https://api.geckoterminal.com/api/v2",
                timeout_seconds=geckoterminal.timeout_seconds,
                max_attempts=geckoterminal.max_attempts,
                backoff_seconds=geckoterminal.backoff_seconds,
            )
        dexscreener = config.provider("dexscreener")
        if dexscreener.enabled:
            providers["dexscreener"] = DexScreenerDiscoveryProvider(
                base_url=dexscreener.base_url or "https://api.dexscreener.com",
                timeout_seconds=dexscreener.timeout_seconds,
                max_attempts=dexscreener.max_attempts,
                backoff_seconds=dexscreener.backoff_seconds,
            )
        return providers

    def _candidate_id_for_provider_candidate(self, candidate: DiscoveredCandidate) -> str:
        metadata = candidate.metadata or {}
        contract_address = str(metadata.get("contract_address") or "").strip().lower()
        chain = str(metadata.get("chain") or candidate.primary_chain or "").strip().lower()
        if chain and contract_address:
            existing = self.repository.find_by_identifier(f"contract:{chain}", contract_address)
            if existing:
                return existing.candidate_id
        return self._candidate_id(candidate.provider, candidate.provider_id)

    def _candidate_id(self, namespace: str, value: str) -> str:
        existing = self.repository.find_by_identifier(namespace, value)
        if existing:
            return existing.candidate_id
        return identity("candidate", {"namespace": namespace, "value": value})

    def _screen_candidate(self, candidate: CandidateRecord, *, screened_at: datetime) -> CandidateScreeningResult:
        reasons: list[str] = []
        missing: list[str] = []
        score = 0.0
        rejected = _has_rejection_marker(candidate)
        if candidate.identifiers:
            score += 0.3
            reasons.append("has deterministic external identifier")
        else:
            missing.append("external_identifier")
        if candidate.sources:
            score += 0.2
            reasons.append("has source provenance")
        else:
            missing.append("source_provenance")
        if candidate.sector:
            score += 0.1
            reasons.append("has sector classification")
        else:
            missing.append("sector")
        if candidate.discovery_source != "seed":
            score += 0.15
            reasons.append("observed by live market-wide adapter")
        if any(
            item.namespace in {"coingecko", "defillama"} or item.namespace.startswith("contract:")
            for item in candidate.identifiers
        ):
            score += 0.15
            reasons.append("has market or protocol provider identity")
        if candidate.lifecycle_status == "analyzable":
            score += 0.1
            reasons.append("compatible with current deep analysis path")
        if _has_market_or_protocol_measure(candidate):
            score += 0.15
            reasons.append("has market or protocol measurement")
        else:
            missing.append("market_or_protocol_measurement")
        coverage = round((5 - min(len(missing), 5)) / 5, 4)
        score = round(min(score, 1.0), 4)
        minimum_quality = bool(candidate.identifiers and candidate.sources)
        advanced = minimum_quality and (
            candidate.lifecycle_status == "analyzable" or (score >= 0.65 and _has_market_or_protocol_measure(candidate))
        )
        if rejected or not minimum_quality:
            status = "rejected"
            advanced = False
            reasons.append("failed deterministic minimum quality gate")
        else:
            status = "advanced" if advanced else "deferred"
        return CandidateScreeningResult(
            screening_id=identity(
                "candidate-screening",
                {
                    "candidate": candidate.candidate_id,
                    "last_seen_at": candidate.last_seen_at,
                    "identifier_count": len(candidate.identifiers),
                    "source_count": len(candidate.sources),
                    "status": status,
                    "score": score,
                    "missing": tuple(sorted(missing)),
                },
            ),
            candidate_id=candidate.candidate_id,
            screened_at=screened_at,
            status=status,
            score=score,
            advanced=advanced,
            reasons=tuple(reasons or ("insufficient current evidence",)),
            missing_evidence=tuple(missing),
            confidence=round(score if score > 0 else 0.1, 4),
            coverage=coverage,
        )

    def _queue_entry(
        self,
        result: CandidateScreeningResult,
        candidate: CandidateRecord,
        *,
        updated_at: datetime,
    ) -> CandidateQueueEntry:
        score = (
            0.0 if result.status == "rejected" else round(min(result.score + (0.1 if result.advanced else 0.0), 1.0), 4)
        )
        if score >= 0.8:
            priority = "critical"
        elif score >= 0.65:
            priority = "high"
        elif score >= 0.45:
            priority = "medium"
        elif score >= 0.25:
            priority = "low"
        else:
            priority = "defer"
        return CandidateQueueEntry(
            queue_entry_id=identity("candidate-queue-entry", {"candidate": candidate.candidate_id}),
            candidate_id=candidate.candidate_id,
            priority_score=score,
            priority=priority,  # type: ignore[arg-type]
            priority_reasons=result.reasons,
            missing_evidence=result.missing_evidence,
            lifecycle_state=candidate.lifecycle_status,
            created_at=candidate.first_seen_at,
            updated_at=updated_at,
            source_run_id="latest-screening",
            eligible_for_deep_analysis=candidate.lifecycle_status == "analyzable" and result.advanced,
        )


def candidate_for_report(candidate: CandidateRecord) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "slug": candidate.slug,
        "name": candidate.name,
        "symbol": candidate.symbol,
        "sector": candidate.sector,
        "primary_chain": candidate.primary_chain,
        "candidate_type": candidate.candidate_type,
        "lifecycle_status": candidate.lifecycle_status,
        "discovery_source": candidate.discovery_source,
        "identifier_count": len(candidate.identifiers),
        "alias_count": len(candidate.aliases),
        "source_count": len(candidate.sources),
        "identity_resolution_status": candidate.identity_resolution_status,
        "queue_status": candidate.queue_status,
        "screening_status": candidate.screening_status,
        "intrinsic_value_status": candidate.intrinsic_value_status,
        "competition_status": candidate.competition_status,
        "network_effect_status": candidate.network_effect_status,
        "last_seen_at": candidate.last_seen_at.isoformat(),
        "metadata": candidate.metadata,
    }


def merge_candidate_status(candidate: CandidateRecord, status: str) -> CandidateRecord:
    return replace(candidate, lifecycle_status=status)  # type: ignore[arg-type]


def _has_market_or_protocol_measure(candidate: CandidateRecord) -> bool:
    keys = {
        "market_cap",
        "market_cap_rank",
        "tvl",
        "liquidity",
        "liquidity_usd",
        "volume",
        "volume_usd",
        "reserve_usd",
        "boost_amount",
    }
    return any(candidate.metadata.get(key) not in {None, ""} for key in keys)


def _has_rejection_marker(candidate: CandidateRecord) -> bool:
    markers = ("spam", "scam", "impersonation", "blocked")
    return any(bool(candidate.metadata.get(marker)) for marker in markers)
