from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.acquisition.project_identifiers import ProjectIdentifier, load_project_identifiers
from hunter.discovery.capacity import (
    CapacityInventory,
    CapacityLedger,
    DuplicateCapacityRequest,
    load_capacity_inventory,
)
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
    ScanBudget,
    contract_slug,
    normalize_contract_address,
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
        ledger: CapacityLedger | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.capacity_inventory: CapacityInventory = load_capacity_inventory(config.capacity_inventory_path)
        self.ledger = ledger or (CapacityLedger(repository, self.capacity_inventory) if providers is None else None)
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
        ledger_before = dict(self.ledger.summary()["totals"]) if self.ledger else None
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
        next_cursor: int | None = None
        source = self.providers[provider]
        scan_status = "succeeded"
        scan_message = ""
        try:
            provider_config = self.config.provider(provider)
            budget = limit or provider_config.limit or 250
            max_requests = getattr(provider_config, "max_requests_per_cycle", 8)
            min_interval = getattr(provider_config, "min_interval_seconds", 0.0)
            if not isinstance(max_requests, int):
                max_requests = 8
            if not isinstance(min_interval, (int, float)):
                min_interval = 0.0
            scan_budget = ScanBudget(max_requests=max_requests, min_interval_seconds=float(min_interval))
            if isinstance(source, GeckoTerminalDiscoveryProvider):
                result = source.discover_scan(
                    state=self.repository.scan_state(provider),
                    budget=scan_budget,
                    observed_at=started_at.isoformat(),
                    max_candidates=limit,
                )
                raw_candidates = result.candidates
                self.repository.save_network_observations(
                    tuple(
                        {
                            "network_id": network_id,
                            "name": network_id,
                            "source_url": f"{source.base_url.rstrip('/')}/networks",
                            "observed_at": result.observed_at,
                        }
                        for network_id in result.network_ids
                    )
                )
                new_pools = self.repository.save_pool_observations(result.pool_observations)
                result.state["new_pools"] = new_pools
                self.repository.save_scan_state(provider, result.state, result.status)
                scan_status = result.status
                scan_message = (
                    f"requests={result.requests} networks={len(result.network_ids)} "
                    f"pools={len(result.pool_observations)} new_pools={new_pools}"
                )
                if result.error:
                    scan_message = f"{scan_message} error={result.error}"
            elif isinstance(source, CoinGeckoDiscoveryProvider) and limit is None:
                raw_candidates, next_cursor = source.discover_batch(
                    limit=budget,
                    cursor=self.repository.discovery_page_cursor(provider),
                    budget=scan_budget,
                )
            else:
                raw_candidates = source.discover(limit=budget)
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
                message=f"{exc}{self._capacity_suffix(ledger_before)}",
            )
            self.repository.save_run(run)
            return run
        candidates = tuple(self._provider_candidate(item, observed_at=started_at) for item in raw_candidates)
        created, updated = self.repository.upsert_many(candidates)
        message = (scan_message or f"synced {len(candidates)} candidates") + self._capacity_suffix(ledger_before)
        run = DiscoveryRun(
            run_id=identity("candidate-discovery-run", {"provider": provider, "started_at": started_at}),
            provider=provider,
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
            candidates_seen=len(candidates),
            candidates_created=created,
            candidates_updated=updated,
            status=scan_status,
            message=message,
        )
        self.repository.save_run(run)
        if next_cursor is not None:
            self.repository.save_checkpoint(provider, f"page:{next_cursor}", run.status)
        elif not isinstance(source, (CoinGeckoDiscoveryProvider, GeckoTerminalDiscoveryProvider)):
            self.repository.save_checkpoint(provider, str(len(candidates)), run.status)
        return run

    def enumerate_universe(self, provider: str = "coingecko") -> dict[str, object]:
        """Persist the accessible provider universe used as a real coverage denominator."""
        source = self.providers.get(provider)
        if not isinstance(source, CoinGeckoDiscoveryProvider):
            return {
                "provider": provider,
                "status": "unsupported",
                "reason": "provider has no accessible global asset universe endpoint",
                "observed_at": datetime.now(tz=UTC).isoformat(),
            }
        try:
            payload = source.enumerate_universe()
        except ProviderUnavailableError as exc:
            return {
                "provider": provider,
                "status": "unavailable",
                "reason": str(exc),
                "observed_at": datetime.now(tz=UTC).isoformat(),
            }
        self.repository.save_universe_assets(
            provider, tuple(str(value) for value in payload["asset_ids"]), str(payload["observed_at"])
        )
        return {
            "provider": provider,
            "status": "succeeded",
            "scope": payload["scope"],
            "asset_count": payload["asset_count"],
            "observed_at": payload["observed_at"],
            "source_url": payload["source_url"],
        }

    def enrich_dex_contracts(self, *, limit: int | None = None, mode: str | None = None) -> DiscoveryRun:
        """Enrich already-discovered contracts via DexScreener's bounded token-v1 batches."""
        selected_mode = (
            self.config.normalized_mode()
            if mode is None
            else ("backfill" if mode.strip().lower() == "backfill" else "incremental")
        )
        started_at = datetime.now(tz=UTC)
        ledger_before = dict(self.ledger.summary()["totals"]) if self.ledger else None
        source = self.providers.get("dexscreener")
        if not isinstance(source, DexScreenerDiscoveryProvider):
            run = DiscoveryRun(
                run_id=identity("candidate-discovery-run", {"provider": "dexscreener-batch", "started_at": started_at}),
                provider="dexscreener-batch",
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                candidates_seen=0,
                candidates_created=0,
                candidates_updated=0,
                status="unsupported",
                message="DexScreener batch enrichment provider is not configured",
            )
            self.repository.save_run(run)
            return run

        groups = self.repository.identifier_groups()
        assets: list[tuple[str, str]] = []
        for (namespace, value), candidate_ids in sorted(groups.items()):
            if not namespace.startswith("contract:"):
                continue
            chain = namespace.split(":", 1)[1]
            if selected_mode == "incremental" and any(
                self._has_current_market_measure(candidate_id) for candidate_id in candidate_ids
            ):
                continue  # incremental: never re-fetch a contract that already has market evidence
            assets.append((chain, value))
            if limit is not None and len(assets) >= max(1, limit):
                break
        if limit is not None:
            assets = assets[: max(1, limit)]

        config = self.config.provider("dexscreener")
        budget = ScanBudget(
            max_requests=max(1, config.max_requests_per_cycle),
            min_interval_seconds=max(0.0, config.min_interval_seconds),
        )
        try:
            result = source.enrich_contracts(tuple(assets), budget=budget, dedup=selected_mode != "backfill")
        except (ProviderUnavailableError, DuplicateCapacityRequest) as exc:
            status = "partial" if isinstance(exc, DuplicateCapacityRequest) else "unavailable"
            run = DiscoveryRun(
                run_id=identity("candidate-discovery-run", {"provider": "dexscreener-batch", "started_at": started_at}),
                provider="dexscreener-batch",
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                candidates_seen=0,
                candidates_created=0,
                candidates_updated=0,
                status=status,
                message=(
                    f"mode={selected_mode} contracts={len(assets)} error={exc}"
                    f"{self._capacity_suffix(ledger_before)}"
                ),
            )
            self.repository.save_run(run)
            return run
        candidates = tuple(self._provider_candidate(item, observed_at=started_at) for item in result.candidates)
        created, updated = self.repository.upsert_many(candidates)
        status = "succeeded" if result.assets and not result.truncated else "partial"
        message = (
            f"mode={selected_mode} contracts={result.assets} processed={result.processed} "
            f"batches={result.batches} rows={len(candidates)}"
        )
        if result.truncated:
            message += f" unprocessed={result.assets - result.processed}"
        run = DiscoveryRun(
            run_id=identity("candidate-discovery-run", {"provider": "dexscreener-batch", "started_at": started_at}),
            provider="dexscreener-batch",
            started_at=started_at,
            finished_at=datetime.now(tz=UTC),
            candidates_seen=len(candidates),
            candidates_created=created,
            candidates_updated=updated,
            status=status,
            message=message + self._capacity_suffix(ledger_before),
        )
        self.repository.save_run(run)
        return run

    def _has_current_market_measure(self, candidate_id: str) -> bool:
        candidate = self.repository.get(candidate_id)
        return candidate is not None and _has_market_or_protocol_measure(candidate)

    def global_coverage(self) -> dict[str, object]:
        """Report coverage from measured denominators only; unmeasured values stay null."""
        universe_ids = set(self.repository.universe_assets("coingecko"))
        discovered_ids = set(self.repository.identifier_values("coingecko"))
        matched = universe_ids & discovered_ids
        universe_coverage = round(len(matched) / len(universe_ids), 6) if universe_ids else None
        scan = self.repository.scan_state("geckoterminal") or {}
        catalogue_ids = {str(value) for value in (scan.get("network_ids") or [])}
        pools = self.repository.pool_observations()
        scanned_networks = {str(item["network"]) for item in pools}
        network_coverage = (
            round(len(catalogue_ids & scanned_networks) / len(catalogue_ids), 6) if catalogue_ids else None
        )
        candidates = self.repository.list_candidates(limit=100_000)
        observations = [
            str(source.observed_at) for candidate in candidates for source in candidate.sources if source.observed_at
        ]
        fresh_cutoff = datetime.now(tz=UTC) - timedelta(hours=24)
        fresh_market_evidence = sum(
            1
            for candidate in candidates
            if _has_market_or_protocol_measure(candidate)
            and (observed_at := _market_observation_at(candidate)) is not None
            and observed_at >= fresh_cutoff
        )
        identity_verified = sum(1 for result in self.repository.identity_results() if result.outcome == "exact")
        ledger_summary = self.ledger.summary() if self.ledger else None
        capacity = self._capacity_telemetry(ledger_summary, universe_size=len(universe_ids), matched=len(matched))
        return {
            "scope": "global_market_enumeration",
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "universe": {
                "provider": "coingecko",
                "scope": "global_asset_universe",
                "accessible_assets": len(universe_ids),
                "discovered_assets": len(discovered_ids),
                "matched_assets": len(matched),
                "coverage": universe_coverage,
                "unmeasured_reason": (
                    None
                    if universe_ids
                    else "no verified accessible universe enumeration recorded; coverage is unmeasured"
                ),
                "observed_at": self.repository.universe_observed_at("coingecko"),
                "provenance": "CoinGecko /coins/list snapshot persisted in the discovery registry",
            },
            "networks": {
                "provider": "geckoterminal",
                "scope": "network_catalogue_enumeration",
                "catalogued_networks": len(catalogue_ids),
                "scanned_networks": len(scanned_networks),
                "coverage": network_coverage,
                "unmeasured_reason": (
                    None if catalogue_ids else "no network catalogue page has been enumerated; coverage is unmeasured"
                ),
            },
            "pools": {
                "provider": "geckoterminal",
                "scope": "per_network_pool_pages",
                "observed_pools": len(pools),
                "new_pools": int(scan.get("new_pools") or 0),
                "coverage": None,
                "unmeasured_reason": "GeckoTerminal public API exposes no global pool denominator",
            },
            "evidence": {
                "identity_verified_assets": identity_verified,
                "fresh_market_evidence_assets_24h": fresh_market_evidence,
                "criteria": (
                    "identity_verified_assets counts exact identity outcomes; "
                    "fresh_market_evidence_assets_24h counts candidates with a market/protocol measure and a "
                    "provider-observed market timestamp within 24 hours; generic source sync timestamps do not "
                    "qualify; the fields are reported separately"
                ),
            },
            "capacity": capacity,
            "freshness": {
                "oldest_observation": min(observations) if observations else None,
                "newest_observation": max(observations) if observations else None,
                "universe_observed_at": self.repository.universe_observed_at("coingecko"),
                "scan_observed_at": scan.get("updated_at"),
            },
            "provenance": {
                "registry_path": self.config.registry_path,
                "source_urls": {
                    "universe": "https://api.coingecko.com/api/v3/coins/list",
                    "networks": "https://api.geckoterminal.com/api/v2/networks",
                },
            },
            "scan_progress": {
                "coingecko_page": self.repository.discovery_page_cursor("coingecko"),
                "geckoterminal": {
                    key: scan.get(key)
                    for key in (
                        "catalogue_page",
                        "catalogue_complete",
                        "network_index",
                        "pool_page",
                        "passes",
                        "observed_pools",
                        "new_pools",
                        "last_error",
                        "inaccessible_scope",
                        "updated_at",
                    )
                },
            },
            "unsupported_scope": [
                *(
                    {
                        "provider": "geckoterminal",
                        "scope": str(item.get("scope") or "unspecified"),
                        "reason": str(item.get("reason") or "inaccessible"),
                        "page": item.get("page"),
                    }
                    for item in (scan.get("inaccessible_scope") or [])
                    if isinstance(item, dict)
                ),
                {
                    "provider": "dexscreener",
                    "scope": "token_profile_and_boost_feed",
                    "reason": "public profiles and paid boosts are not a global asset universe",
                },
                {
                    "provider": "defillama",
                    "scope": "protocol_catalogue",
                    "reason": "protocol listings are not an exhaustive asset universe",
                },
                {
                    "provider": "geckoterminal",
                    "scope": "trending_pools",
                    "reason": "popularity feed is distinct from network catalogue enumeration",
                },
            ],
            "trending_feeds": ("geckoterminal:trending_pools", "dexscreener:token-boosts/top/v1"),
        }

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

    def run_market_discovery(self, *, limit: int | None = None, mode: str | None = None) -> dict[str, object]:
        selected_mode = (
            self.config.normalized_mode()
            if mode is None
            else ("backfill" if mode.strip().lower() == "backfill" else "incremental")
        )
        seed_run = self.sync_configured_universe()
        universe = self.enumerate_universe()
        provider_run = self.sync(provider="all", limit=limit)
        dex_run = self.enrich_dex_contracts(limit=limit, mode=selected_mode)
        screening = self.screen_candidates()
        queue = self.refresh_queue()
        stats = self.repository.stats()
        return {
            "mode": selected_mode,
            "seed_run": seed_run.run_id,
            "provider_run": provider_run.run_id,
            "dex_enrichment_run": dex_run.run_id,
            "universe": universe,
            "coverage": self.global_coverage(),
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
        chain = str(metadata.get("chain") or candidate.primary_chain or "").strip().lower()
        contract_address = normalize_contract_address(metadata.get("contract_address"), chain)
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
            slug=contract_slug(chain, contract_address) if chain and contract_address else candidate.slug,
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

    def _capacity_telemetry(
        self,
        summary: dict[str, Any] | None,
        *,
        universe_size: int,
        matched: int,
    ) -> dict[str, Any]:
        inventory = self.ledger.inventory if self.ledger is not None else self.capacity_inventory
        endpoints = list(summary["endpoints"]) if summary else []
        totals = (
            dict(summary["totals"])
            if summary
            else {
                "requests": 0,
                "retries": 0,
                "throttles": 0,
                "failures": 0,
                "successes": 0,
                "cooldowns": 0,
            }
        )
        rates = {f"{row['provider']}:{row['endpoint']}": _observed_rate_per_second(row) for row in endpoints}
        coin_rate = rates.get("coingecko:coins_markets")
        remaining_pages = math.ceil(max(0, universe_size - matched) / 250) if universe_size else None
        universe_eta = round(remaining_pages / coin_rate, 1) if coin_rate and remaining_pages else None
        gecko_rate = rates.get("geckoterminal:network_pools")
        return {
            "inventory": inventory.source_path,
            "documented": inventory.as_dict(),
            "observed": summary,
            "api_calls": int(totals["requests"]),
            "retries": int(totals["retries"]),
            "throttles": int(totals["throttles"]),
            "cooldowns": int(totals["cooldowns"]),
            "failures": int(totals["failures"]),
            "remaining_quota": {
                f"{row['provider']}:{row['endpoint']}": row["remaining_quota"]
                for row in endpoints
                if row["remaining_quota"] is not None
            },
            "observed_requests_per_second": {key: value for key, value in rates.items() if value is not None},
            "eta_seconds": {
                "universe": universe_eta,
                "network_pools": None,
                "reason": (
                    "universe ETA uses observed CoinGecko /coins/markets throughput and a 250-asset page; "
                    "pool ETA is unmeasured because GeckoTerminal exposes no global pool denominator"
                ),
                "geckoterminal_observed_requests_per_second": gecko_rate,
            },
            "unsupported_segments": [
                {
                    "provider": "geckoterminal",
                    "scope": "global_pool_denominator",
                    "reason": "public API exposes no global pool denominator; pool coverage stays null",
                },
                {
                    "provider": "defillama",
                    "scope": "numeric_free_request_ceiling",
                    "reason": "free API is available without authentication but no numeric ceiling is documented",
                },
                {
                    "provider": "dexscreener",
                    "scope": "market_rate_and_batch_limit",
                    "reason": "300 requests/min and 30 addresses/batch are owner-specified, not published in fetched docs",
                },
            ],
        }

    def _capacity_suffix(self, before: dict[str, Any] | None) -> str:
        if self.ledger is None or before is None:
            return ""
        totals = self.ledger.summary()["totals"]
        return (
            f" api_calls={int(totals['requests']) - int(before.get('requests', 0))}"
            f" retries={int(totals['retries']) - int(before.get('retries', 0))}"
            f" throttles={int(totals['throttles']) - int(before.get('throttles', 0))}"
            f" cooldowns={int(totals['cooldowns']) - int(before.get('cooldowns', 0))}"
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
                ledger=self.ledger,
            )
        defillama = config.provider("defillama")
        if defillama.enabled:
            providers["defillama"] = DefiLlamaDiscoveryProvider(
                base_url=defillama.base_url or "https://api.llama.fi",
                timeout_seconds=defillama.timeout_seconds,
                max_attempts=defillama.max_attempts,
                backoff_seconds=defillama.backoff_seconds,
                ledger=self.ledger,
            )
        geckoterminal = config.provider("geckoterminal")
        if geckoterminal.enabled:
            providers["geckoterminal"] = GeckoTerminalDiscoveryProvider(
                base_url=geckoterminal.base_url or "https://api.geckoterminal.com/api/v2",
                timeout_seconds=geckoterminal.timeout_seconds,
                max_attempts=geckoterminal.max_attempts,
                backoff_seconds=geckoterminal.backoff_seconds,
                ledger=self.ledger,
            )
        dexscreener = config.provider("dexscreener")
        if dexscreener.enabled:
            providers["dexscreener"] = DexScreenerDiscoveryProvider(
                base_url=dexscreener.base_url or "https://api.dexscreener.com",
                timeout_seconds=dexscreener.timeout_seconds,
                max_attempts=dexscreener.max_attempts,
                backoff_seconds=dexscreener.backoff_seconds,
                ledger=self.ledger,
            )
        return providers

    def _candidate_id_for_provider_candidate(self, candidate: DiscoveredCandidate) -> str:
        metadata = candidate.metadata or {}
        chain = str(metadata.get("chain") or candidate.primary_chain or "").strip().lower()
        contract_address = normalize_contract_address(metadata.get("contract_address"), chain)
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
    }
    return any(candidate.metadata.get(key) not in {None, ""} for key in keys)


def _market_observation_at(candidate: CandidateRecord) -> datetime | None:
    """Return the newest provider-reported market observation time, if parseable."""
    observations: list[datetime] = []
    for key in ("market_evidence_observed_at", "last_updated"):
        value = (candidate.metadata or {}).get(key)
        if not value:
            continue
        try:
            observations.append(_parse_iso(str(value)))
        except (TypeError, ValueError):
            continue
    return max(observations) if observations else None


def _has_rejection_marker(candidate: CandidateRecord) -> bool:
    markers = ("spam", "scam", "impersonation", "blocked")
    return any(bool(candidate.metadata.get(marker)) for marker in markers)


def _observed_rate_per_second(row: dict[str, Any]) -> float | None:
    first = row.get("first_attempt_at")
    last = row.get("last_attempt_at")
    if not first or not last:
        return None
    span = (_parse_iso(str(last)) - _parse_iso(str(first))).total_seconds()
    attempts = int(row.get("attempts") or 0)
    return round(attempts / span, 6) if span > 0 and attempts else None


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
