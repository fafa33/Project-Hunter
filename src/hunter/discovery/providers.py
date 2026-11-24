from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.discovery.capacity import CapacityLedger, endpoint_for_url

# GeckoTerminal's free public API rejects page numbers above 10 with HTTP 401
# ("Upgrade to Analyst plan or above"). This is a documented-by-response free
# plan restriction, not a rate limit: requests above the cap are never retried.
GECKOTERMINAL_FREE_MAX_PAGE = 10


@dataclass(frozen=True)
class DiscoveredCandidate:
    provider: str
    provider_id: str
    slug: str
    name: str
    symbol: str | None = None
    sector: str | None = None
    primary_chain: str | None = None
    candidate_type: str = "unknown"
    source_url: str | None = None
    metadata: dict[str, Any] | None = None


class DiscoveryProvider(Protocol):
    @property
    def name(self) -> str:
        raise NotImplementedError

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class ScanBudget:
    """Bounded request allowance and pacing for one provider scan cycle."""

    max_requests: int = 8
    min_interval_seconds: float = 0.0


@dataclass(frozen=True)
class ProviderScanResult:
    """Evidence from one bounded scan cycle, including resumable state."""

    candidates: tuple[DiscoveredCandidate, ...]
    state: dict[str, Any]
    status: str
    requests: int
    network_ids: tuple[str, ...] = ()
    pool_observations: tuple[dict[str, Any], ...] = ()
    observed_at: str = ""
    error: str | None = None


@dataclass(frozen=True)
class DexEnrichmentResult:
    """Bounded batch-enrichment outcome, including the assets actually requested."""

    candidates: tuple[DiscoveredCandidate, ...]
    assets: int
    processed: int
    batches: int
    truncated: bool


class _RequestBudgetExhausted(Exception):
    pass


class _RequestMeter:
    def __init__(self, budget: ScanBudget) -> None:
        self.budget = budget
        self.count = 0
        self._last: float | None = None

    def consume(self, sleeper: Callable[[float], None]) -> None:
        if self.count >= max(1, self.budget.max_requests):
            raise _RequestBudgetExhausted
        if self.budget.min_interval_seconds > 0 and self._last is not None:
            remaining = self.budget.min_interval_seconds - (time.monotonic() - self._last)
            if remaining > 0:
                sleeper(remaining)
        self._last = time.monotonic()
        self.count += 1


@dataclass(frozen=True)
class CoinGeckoDiscoveryProvider:
    base_url: str = "https://api.coingecko.com/api/v3"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    per_page: int = 250
    name: str = "coingecko"
    sleeper: Callable[[float], None] = time.sleep
    ledger: CapacityLedger | None = None

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        return self.discover_batch(limit=limit, cursor=0)[0][:limit]

    def discover_batch(
        self, *, limit: int, cursor: int, budget: ScanBudget | None = None
    ) -> tuple[tuple[DiscoveredCandidate, ...], int]:
        rows: list[DiscoveredCandidate] = []
        meter = _RequestMeter(budget) if budget else None
        pages = max(1, (max(1, limit) + self.per_page - 1) // self.per_page)
        for page in range(cursor + 1, cursor + pages + 1):
            try:
                payload = self._get(
                    "/coins/markets",
                    {
                        "vs_currency": "usd",
                        "order": "market_cap_desc",
                        "per_page": self.per_page,
                        "page": page,
                        "sparkline": "false",
                    },
                    meter=meter,
                )
            except _RequestBudgetExhausted:
                # Stop at the last completed page; the cursor remains on this page.
                break
            if not isinstance(payload, list):
                raise ProviderUnavailableError("CoinGecko discovery response must be a list")
            if not payload:
                return tuple(rows), 0
            for item in payload:
                if not isinstance(item, dict):
                    continue
                coingecko_id = str(item.get("id") or "").strip()
                name = str(item.get("name") or "").strip()
                if not coingecko_id or not name:
                    continue
                rows.append(
                    DiscoveredCandidate(
                        provider=self.name,
                        provider_id=coingecko_id,
                        slug=coingecko_id,
                        name=name,
                        symbol=str(item.get("symbol") or "").upper() or None,
                        candidate_type="token",
                        source_url=f"https://www.coingecko.com/en/coins/{urllib.parse.quote(coingecko_id)}",
                        metadata={
                            "market_cap_rank": item.get("market_cap_rank"),
                            "market_cap": item.get("market_cap"),
                            "last_updated": item.get("last_updated"),
                            "discovery_mode": "global_market_enumeration",
                            "asset_scope": "coingecko_asset",
                        },
                    )
                )
            if len(payload) < self.per_page:
                return tuple(rows), 0
        return tuple(rows), cursor + pages

    def enumerate_universe(self) -> dict[str, Any]:
        """Enumerate the accessible CoinGecko asset universe for a real coverage denominator."""
        payload = self._get("/coins/list", {"include_platform": "false"})
        if not isinstance(payload, list):
            raise ProviderUnavailableError("CoinGecko universe response must be a list")
        asset_ids = sorted(
            {
                str(item.get("id", "")).strip()
                for item in payload
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
        )
        if not asset_ids:
            raise ProviderUnavailableError("CoinGecko universe enumeration returned no assets")
        return {
            "provider": self.name,
            "scope": "global_asset_universe",
            "asset_ids": asset_ids,
            "asset_count": len(asset_ids),
            "observed_at": datetime.now(UTC).isoformat(),
            "source_url": f"{self.base_url.rstrip('/')}/coins/list?include_platform=false",
        }

    def _get(self, path: str, params: dict[str, object], *, meter: _RequestMeter | None = None) -> object:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        return _request_json(
            url,
            provider="CoinGecko",
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleeper=self.sleeper,
            meter=meter,
            ledger=self.ledger,
            endpoint=endpoint_for_url("CoinGecko", url),
        )


@dataclass(frozen=True)
class DefiLlamaDiscoveryProvider:
    base_url: str = "https://api.llama.fi"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "defillama"
    sleeper: Callable[[float], None] = time.sleep
    ledger: CapacityLedger | None = None

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        payload = self._get("/protocols")
        if not isinstance(payload, list):
            raise ProviderUnavailableError("DefiLlama discovery response must be a list")
        rows: list[DiscoveredCandidate] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            name = str(item.get("name") or "").strip()
            if not slug or not name:
                continue
            rows.append(
                DiscoveredCandidate(
                    provider=self.name,
                    provider_id=slug,
                    slug=slug,
                    name=name,
                    symbol=str(item.get("symbol") or "").upper() or None,
                    sector=str(item.get("category") or "") or None,
                    primary_chain=str(item.get("chain") or "") or None,
                    candidate_type="protocol",
                    source_url=f"https://defillama.com/protocol/{urllib.parse.quote(slug)}",
                    metadata={
                        "category": item.get("category"),
                        "chain": item.get("chain"),
                        "chains": item.get("chains"),
                        "tvl": item.get("tvl"),
                    },
                )
            )
            if len(rows) >= limit:
                return tuple(rows)
        return tuple(rows)

    def _get(self, path: str) -> object:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        return _request_json(
            url,
            provider="DefiLlama",
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleeper=self.sleeper,
            ledger=self.ledger,
            endpoint=endpoint_for_url("DefiLlama", url),
        )


@dataclass(frozen=True)
class GeckoTerminalDiscoveryProvider:
    base_url: str = "https://api.geckoterminal.com/api/v2"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "geckoterminal"
    sleeper: Callable[[float], None] = time.sleep
    ledger: CapacityLedger | None = None
    seen_contract_limit: int = 20_000

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        # Trending pools are a popularity feed, not global enumeration.
        rows, _ = self._pool_page("/networks/trending_pools", 1, discovery_mode="trending_feed", seen_contracts=set())
        return rows[:limit]

    def discover_scan(
        self,
        *,
        state: dict[str, Any] | None,
        budget: ScanBudget,
        observed_at: str | None = None,
        max_candidates: int | None = None,
    ) -> ProviderScanResult:
        """Enumerate the network catalogue and per-network pools with a durable cursor."""
        scan = self._normalise_state(state)
        meter = _RequestMeter(budget)
        timestamp = observed_at or datetime.now(UTC).isoformat()
        rows: list[DiscoveredCandidate] = []
        pool_observations: list[dict[str, Any]] = []
        seen_contracts = {
            (str(key[0]), str(key[1]))
            for key in scan["seen_contracts"]
            if isinstance(key, (list, tuple)) and len(key) == 2
        }
        status = "succeeded"
        error: str | None = None
        successful_pages = 0

        while True:
            try:
                if not scan["network_ids"] or scan["network_index"] >= len(scan["network_ids"]):
                    if not scan["started"]:
                        scan["started"] = True
                    elif scan["catalogue_complete"]:
                        scan["passes"] = int(scan["passes"]) + 1
                        scan["rotation_seed"] = int(scan["rotation_seed"]) + 1
                        scan["catalogue_page"] = 1
                        scan["catalogue_complete"] = False
                        scan["catalogue_fetched"] = False
                    elif scan["catalogue_fetched"]:
                        if int(scan["catalogue_page"]) >= GECKOTERMINAL_FREE_MAX_PAGE:
                            scan["catalogue_complete"] = True
                            catalogue_record = {
                                "scope": "network_catalogue",
                                "page": int(scan["catalogue_page"]) + 1,
                                "reason": "free_api_page_cap",
                            }
                            if catalogue_record not in scan["inaccessible_scope"]:
                                scan["inaccessible_scope"].append(catalogue_record)
                        else:
                            scan["catalogue_page"] = int(scan["catalogue_page"]) + 1
                        scan["catalogue_fetched"] = False
                    scan["network_ids"] = []
                    scan["network_index"] = 0
                    scan["pool_page"] = 1
                    catalogue = self._get("/networks", {"page": scan["catalogue_page"]}, meter=meter)
                    scan["catalogue_fetched"] = True
                    networks = catalogue.get("data") if isinstance(catalogue, dict) else None
                    if not isinstance(networks, list):
                        raise ProviderUnavailableError("GeckoTerminal network catalogue unavailable")
                    scan["network_ids"] = [
                        str(item.get("id", "")).strip()
                        for item in networks
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    ]
                    rotation = int(scan["rotation_seed"]) % len(scan["network_ids"]) if scan["network_ids"] else 0
                    if rotation:
                        scan["network_ids"] = scan["network_ids"][rotation:] + scan["network_ids"][:rotation]
                    links = catalogue.get("links") if isinstance(catalogue, dict) else None
                    if not isinstance(links, dict) or not links.get("next"):
                        scan["catalogue_complete"] = True
                    if not scan["network_ids"]:
                        continue

                network_id = str(scan["network_ids"][scan["network_index"]])
                path = f"/networks/{urllib.parse.quote(network_id, safe='')}/pools"
                page_rows, page_pools = self._pool_page(
                    path,
                    int(scan["pool_page"]),
                    discovery_mode="network_pool_enumeration",
                    seen_contracts=seen_contracts,
                    meter=meter,
                    observed_at=timestamp,
                )
                successful_pages += 1
                rows.extend(page_rows)
                pool_observations.extend(page_pools)
                for item in page_rows:
                    metadata = item.metadata or {}
                    key = (str(metadata.get("chain") or ""), str(metadata.get("contract_address") or ""))
                    if all(key):
                        seen_contracts.add(key)
                if page_pools and int(scan["pool_page"]) < GECKOTERMINAL_FREE_MAX_PAGE:
                    scan["pool_page"] = int(scan["pool_page"]) + 1
                else:
                    if page_pools:
                        record = {
                            "scope": "network_pools",
                            "network_id": network_id,
                            "page": GECKOTERMINAL_FREE_MAX_PAGE + 1,
                            "reason": "free_api_page_cap",
                        }
                        if record not in scan["inaccessible_scope"]:
                            scan["inaccessible_scope"].append(record)
                    # Empty page or the free pagination cap ends this network.
                    scan["network_index"] = int(scan["network_index"]) + 1
                    scan["pool_page"] = 1
                if max_candidates is not None and len(rows) >= max(1, max_candidates):
                    break
            except _RequestBudgetExhausted:
                status = "partial"
                break
            except ProviderUnavailableError as exc:
                error = str(exc)
                scan["last_error"] = error
                status = "partial"
                break

        scan["seen_contracts"] = [list(key) for key in sorted(seen_contracts)[: max(0, self.seen_contract_limit)]]
        scan["inaccessible_scope"] = list(scan["inaccessible_scope"])[-200:]
        scan["observed_pools"] = int(scan["observed_pools"]) + len(pool_observations)
        scan["updated_at"] = timestamp
        return ProviderScanResult(
            candidates=tuple(rows),
            state=scan,
            status=status,
            requests=meter.count,
            network_ids=tuple(str(value) for value in scan["network_ids"]),
            pool_observations=tuple(pool_observations),
            observed_at=timestamp,
            error=error,
        )

    @staticmethod
    def _normalise_state(state: dict[str, Any] | None) -> dict[str, Any]:
        scan = dict(state or {})
        scan.setdefault("catalogue_page", 1)
        scan.setdefault("catalogue_complete", False)
        scan.setdefault("catalogue_fetched", False)
        scan.setdefault("started", False)
        scan.setdefault("network_ids", [])
        scan.setdefault("network_index", 0)
        scan.setdefault("pool_page", 1)
        scan.setdefault("seen_contracts", [])
        scan.setdefault("observed_pools", 0)
        scan.setdefault("passes", 0)
        scan.setdefault("rotation_seed", 0)
        scan.setdefault("last_error", None)
        scan.setdefault("inaccessible_scope", [])
        try:
            catalogue_page = int(scan.get("catalogue_page", 1))
        except (TypeError, ValueError):
            catalogue_page = 1
        if catalogue_page > GECKOTERMINAL_FREE_MAX_PAGE:
            scan["catalogue_page"] = 1
            scan["catalogue_complete"] = True
            scan["catalogue_fetched"] = False
        try:
            pool_page = int(scan.get("pool_page", 1))
        except (TypeError, ValueError):
            pool_page = 1
        if pool_page > GECKOTERMINAL_FREE_MAX_PAGE:
            # A pre-cap checkpoint must never re-issue the page that returned 401.
            scan["network_index"] = int(scan.get("network_index", 0)) + 1
            scan["pool_page"] = 1
            record = {
                "scope": "network_pools",
                "network_id": "resumed_checkpoint",
                "page": pool_page,
                "reason": "free_api_page_cap",
            }
            if record not in scan["inaccessible_scope"]:
                scan["inaccessible_scope"].append(record)
        return scan

    def _pool_page(
        self,
        path: str,
        page: int,
        *,
        discovery_mode: str,
        seen_contracts: set[tuple[str, str]],
        meter: _RequestMeter | None = None,
        observed_at: str | None = None,
    ) -> tuple[tuple[DiscoveredCandidate, ...], tuple[dict[str, Any], ...]]:
        payload = self._get(path, {"include": "base_token,quote_token", "page": page}, meter=meter)
        if not isinstance(payload, dict):
            raise ProviderUnavailableError("GeckoTerminal discovery response must be an object")
        pools_data = payload.get("data")
        if not isinstance(pools_data, list):
            raise ProviderUnavailableError("GeckoTerminal discovery response missing pool data")
        included = payload.get("included")
        if not isinstance(included, list):
            raise ProviderUnavailableError("GeckoTerminal discovery response missing included tokens")
        timestamp = observed_at or datetime.now(UTC).isoformat()
        pools = _geckoterminal_pool_index(pools_data)
        observations: list[dict[str, Any]] = []
        for pool in pools_data:
            if not isinstance(pool, dict):
                continue
            attributes = pool.get("attributes")
            relationships = pool.get("relationships")
            if not isinstance(attributes, dict) or not isinstance(relationships, dict):
                continue
            address = str(attributes.get("address") or "").strip()
            if not address:
                continue
            token_ids = [
                value
                for value in (
                    _relationship_id(relationships.get("base_token")),
                    _relationship_id(relationships.get("quote_token")),
                )
                if value
            ]
            network = next(
                (_geckoterminal_network(token_id, None) for token_id in token_ids), None
            ) or _network_from_path(path)
            if not network:
                continue
            observations.append(
                {
                    "network": network,
                    "pool_address": normalize_contract_address(address, network),
                    "contract_addresses": tuple(
                        normalize_contract_address(token_id.split("_", 1)[-1], network) for token_id in token_ids
                    ),
                    "source_url": f"{self.base_url.rstrip('/')}{path}?page={page}",
                    "observed_at": timestamp,
                }
            )
        rows: list[DiscoveredCandidate] = []
        for item in included:
            if not isinstance(item, dict) or item.get("type") not in {"token", "tokens"}:
                continue
            attributes = item.get("attributes")
            relationships = item.get("relationships")
            if not isinstance(attributes, dict):
                continue
            token_id = str(item.get("id") or "").strip()
            name = str(attributes.get("name") or "").strip()
            network = _geckoterminal_network(token_id, relationships)
            address = normalize_contract_address(attributes.get("address"), network or "")
            if not token_id or not name or not address or not network:
                continue
            key = (network, address)
            if key in seen_contracts:
                continue
            seen_contracts.add(key)
            pool_metadata = pools.get(token_id, {})
            rows.append(
                DiscoveredCandidate(
                    provider=self.name,
                    provider_id=token_id,
                    slug=contract_slug(network, address),
                    name=name,
                    symbol=str(attributes.get("symbol") or "").upper() or None,
                    sector=str(pool_metadata.get("dex")) if pool_metadata.get("dex") else "dex_liquidity",
                    primary_chain=network,
                    candidate_type="token",
                    source_url=f"https://www.geckoterminal.com/{urllib.parse.quote(network)}/tokens/{address}",
                    metadata={
                        "chain": network,
                        "contract_address": address,
                        "asset_scope": "dex_token",
                        "discovery_mode": discovery_mode,
                        "reserve_usd": pool_metadata.get("reserve_usd"),
                        "volume_usd": pool_metadata.get("volume_usd"),
                        "pool_address": pool_metadata.get("pool_address"),
                        "pool_name": pool_metadata.get("pool_name"),
                        "dex": pool_metadata.get("dex"),
                        "observed_at": timestamp,
                        "source_url": f"{self.base_url.rstrip('/')}{path}?page={page}",
                    },
                )
            )
        return tuple(rows), tuple(observations)

    def _get(self, path: str, params: dict[str, object], *, meter: _RequestMeter | None = None) -> object:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        return _request_json(
            url,
            provider="GeckoTerminal",
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleeper=self.sleeper,
            meter=meter,
            ledger=self.ledger,
            endpoint=endpoint_for_url("GeckoTerminal", url),
        )


@dataclass(frozen=True)
class DexScreenerDiscoveryProvider:
    base_url: str = "https://api.dexscreener.com"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "dexscreener"
    sleeper: Callable[[float], None] = time.sleep
    ledger: CapacityLedger | None = None

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        rows: list[DiscoveredCandidate] = []
        seen: set[tuple[str, str]] = set()
        for path, source_type in (
            ("/token-profiles/latest/v1", "token_profile"),
            ("/token-boosts/top/v1", "token_boost"),
        ):
            payload = self._get(path)
            if not isinstance(payload, list):
                raise ProviderUnavailableError("DexScreener discovery response must be a list")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                chain = str(item.get("chainId") or "").strip()
                address = normalize_contract_address(item.get("tokenAddress"), chain)
                if not chain or not address:
                    continue
                key = (chain, address)
                if key in seen:
                    continue
                seen.add(key)
                links = item.get("links") if isinstance(item.get("links"), list) else []
                market = self._market_evidence(chain, address)
                if market.get("market_evidence_status") != "observed" or market.get("market_pair_side") == "quote":
                    fallback = self._gecko_evidence(chain, address)
                    if fallback:
                        market = fallback
                claimed = tuple(_dexscreener_links(links))
                verified = self._corroborated_links(chain, address, claimed) if claimed else {}
                rows.append(
                    DiscoveredCandidate(
                        provider=self.name,
                        provider_id=f"{chain}:{address}",
                        slug=contract_slug(chain, address),
                        name=market.pop("token_name", None) or _dexscreener_name(item, chain, address),
                        symbol=market.pop("token_symbol", None),
                        sector="dex_market",
                        primary_chain=chain,
                        candidate_type="token",
                        source_url=str(item.get("url") or "") or f"https://dexscreener.com/{chain}/{address}",
                        metadata={
                            "chain": chain,
                            "contract_address": address,
                            "asset_scope": "dex_token",
                            "source_type": source_type,
                            "profile_url": item.get("url"),
                            "description": item.get("description"),
                            "icon": item.get("icon"),
                            "boost_amount": item.get("amount"),
                            "boost_total_amount": item.get("totalAmount"),
                            "claimed_links": claimed,
                            **verified,
                            **market,
                        },
                    )
                )
                if len(rows) >= limit:
                    return tuple(rows)
        return tuple(rows)

    def _market_evidence(self, chain: str, address: str) -> dict[str, Any]:
        path = f"/token-pairs/v1/{urllib.parse.quote(chain, safe='')}/{urllib.parse.quote(address, safe='')}"
        try:
            payload = self._get(path)
        except (ProviderUnavailableError, ValueError):
            return {"market_evidence_status": "unavailable"}
        if not isinstance(payload, list):
            return {"market_evidence_status": "unavailable"}
        matches = []
        for pair in payload:
            if not isinstance(pair, dict) or pair.get("chainId") != chain:
                continue
            side = next(
                (
                    side
                    for side in ("base", "quote")
                    if isinstance(pair.get(f"{side}Token"), dict)
                    and normalize_contract_address(pair[f"{side}Token"].get("address"), chain) == address
                ),
                None,
            )
            if side is None:
                continue
            liquidity = pair.get("liquidity")
            amount = _market_number(liquidity.get("usd")) if isinstance(liquidity, dict) else None
            if amount is not None and amount > 0 and pair.get("pairAddress"):
                matches.append((side == "base", amount, str(pair["pairAddress"]), side, pair))
        if not matches:
            return {"market_evidence_status": "no_matching_liquid_pair"}
        _, liquidity_usd, pair_address, side, pair = max(matches, key=lambda item: item[:3])
        token = pair[f"{side}Token"]
        volume = pair.get("volume")
        return {
            "market_evidence_status": "observed",
            "market_evidence_url": self.base_url.rstrip("/") + path,
            "market_pair_address": pair_address,
            "market_pair_side": side,
            "market_evidence_provider": "dexscreener",
            "market_evidence_observed_at": datetime.now(UTC).isoformat(),
            "token_name": str(token.get("name") or "").strip() or None,
            "token_symbol": str(token.get("symbol") or "").strip().upper() or None,
            "liquidity_usd": liquidity_usd,
            "volume_usd": _market_number(volume.get("h24")) if isinstance(volume, dict) else None,
            "market_cap": _market_number(pair.get("marketCap")) if side == "base" else None,
            "fdv": _market_number(pair.get("fdv")) if side == "base" else None,
            "price_usd": _market_number(pair.get("priceUsd")) if side == "base" else None,
        }

    def _corroborated_links(self, chain: str, address: str, claimed: tuple[str, ...]) -> dict[str, Any]:
        platform = {
            "eth": "ethereum",
            "ethereum": "ethereum",
            "solana": "solana",
            "base": "base",
            "bsc": "binance-smart-chain",
            "arbitrum": "arbitrum-one",
            "polygon": "polygon-pos",
            "optimism": "optimistic-ethereum",
        }.get(chain)
        if platform is None:
            return {}
        provider = CoinGeckoDiscoveryProvider(
            timeout_seconds=self.timeout_seconds, max_attempts=1, sleeper=self.sleeper, ledger=self.ledger
        )
        path = f"/coins/{platform}/contract/{urllib.parse.quote(address, safe='')}"
        try:
            payload = provider._get(path, {})
        except (ProviderUnavailableError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        platforms = payload.get("platforms")
        if not isinstance(platforms, dict) or normalize_contract_address(platforms.get(platform), chain) != address:
            return {}
        links = payload.get("links")
        homepages = links.get("homepage") if isinstance(links, dict) else None
        if not isinstance(homepages, list):
            return {}
        domains = {_homepage_domain(url) for url in homepages}
        matched = tuple(
            url for url in claimed if _homepage_domain(url) is not None and _homepage_domain(url) in domains
        )
        if not matched:
            return {}
        return {
            "verified_official_links": matched,
            "official_link_verification_method": "contract_bound_provider_homepage_corroboration",
            "official_link_evidence_url": provider.base_url + path,
            "official_link_observed_at": datetime.now(UTC).isoformat(),
        }

    def _gecko_evidence(self, chain: str, address: str) -> dict[str, Any]:
        network = {
            "ethereum": "eth",
            "eth": "eth",
            "solana": "solana",
            "base": "base",
            "bsc": "bsc",
            "arbitrum": "arbitrum",
            "polygon": "polygon_pos",
            "optimism": "optimism",
        }.get(chain)
        if network is None:
            return {}
        path = f"/networks/{network}/tokens/{urllib.parse.quote(address, safe='')}"
        provider = GeckoTerminalDiscoveryProvider(
            timeout_seconds=self.timeout_seconds, max_attempts=1, sleeper=self.sleeper, ledger=self.ledger
        )
        try:
            payload = provider._get(path, {})
        except (ProviderUnavailableError, ValueError):
            return {}
        data = payload.get("data") if isinstance(payload, dict) else None
        attrs = data.get("attributes") if isinstance(data, dict) else None
        if not isinstance(attrs, dict) or normalize_contract_address(attrs.get("address"), chain) != address:
            return {}
        reserve = _market_number(attrs.get("total_reserve_in_usd"))
        if reserve is None or reserve <= 0:
            return {}
        volume = attrs.get("volume_usd")
        return {
            "market_evidence_status": "observed",
            "market_evidence_provider": "geckoterminal",
            "market_evidence_url": provider.base_url + path,
            "market_evidence_observed_at": datetime.now(UTC).isoformat(),
            "token_name": attrs.get("name"),
            "token_symbol": attrs.get("symbol"),
            "reserve_usd": reserve,
            "volume_usd": _market_number(volume.get("h24")) if isinstance(volume, dict) else None,
            "market_cap": _market_number(attrs.get("market_cap_usd")),
            "fdv": _market_number(attrs.get("fdv_usd")),
            "price_usd": _market_number(attrs.get("price_usd")),
        }

    def enrich_contracts(
        self,
        assets: tuple[tuple[str, str], ...] | list[tuple[str, str]],
        *,
        budget: ScanBudget | None = None,
        max_batch_size: int = 30,
        dedup: bool = True,
    ) -> DexEnrichmentResult:
        """Enrich already-discovered contracts with DexScreener's 30-address token endpoint."""
        meter = _RequestMeter(budget) if budget else None
        groups: dict[str, tuple[str, list[str]]] = {}
        seen: set[tuple[str, str]] = set()
        batch_limit = max(1, min(30, max_batch_size))
        for platform, address in assets:
            chain_id = _dex_chain_id(platform)
            registry_chain = _registry_chain(platform) or platform.strip().lower()
            normalized = normalize_contract_address(address, registry_chain)
            if not chain_id or not normalized or (chain_id, normalized) in seen:
                continue
            seen.add((chain_id, normalized))
            if chain_id not in groups:
                groups[chain_id] = (registry_chain, [])
            groups[chain_id][1].append(normalized)

        asset_count = sum(len(addresses) for _chain, addresses in groups.values())
        observed_at = datetime.now(UTC).isoformat()
        rows: list[DiscoveredCandidate] = []
        processed = 0
        batches = 0
        truncated = False
        for chain_id in sorted(groups):
            registry_chain, addresses = groups[chain_id]
            for start in range(0, len(addresses), batch_limit):
                chunk = addresses[start : start + batch_limit]
                try:
                    if meter is not None:
                        meter.consume(self.sleeper)
                except _RequestBudgetExhausted:
                    truncated = True
                    break
                path = f"/tokens/v1/{urllib.parse.quote(chain_id, safe='')}/" + ",".join(
                    urllib.parse.quote(address, safe="") for address in chunk
                )
                fingerprint = f"{chain_id}|{','.join(chunk)}"
                payload = self._get(
                    path,
                    dedup_key=hashlib.sha256(fingerprint.encode()).hexdigest() if dedup else None,
                    batch_size=len(chunk),
                )
                processed += len(chunk)
                batches += 1
                rows.extend(
                    _dexscreener_batch_rows(
                        payload,
                        chain_id=chain_id,
                        chain=registry_chain,
                        base_url=self.base_url,
                        path=path,
                        batch_size=len(chunk),
                        batch_index=start // batch_limit,
                        observed_at=observed_at,
                    )
                )
            if truncated:
                break
        return DexEnrichmentResult(tuple(rows), asset_count, processed, batches, truncated)

    def _get(
        self,
        path: str,
        *,
        meter: _RequestMeter | None = None,
        dedup_key: str | None = None,
        batch_size: int | None = None,
    ) -> object:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        return _request_json(
            url,
            provider="DexScreener",
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleeper=self.sleeper,
            ledger=self.ledger,
            endpoint=endpoint_for_url("DexScreener", url),
            meter=meter,
            dedup_key=dedup_key,
            batch_size=batch_size,
        )


_REGISTRY_CHAIN_BY_PLATFORM = {
    "eth": "eth",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "polygon": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "optimistic-ethereum": "optimism",
    "solana": "solana",
    "avax": "avax",
    "avalanche": "avax",
}

# DexScreener's token-v1 path and response chainId use these full chain IDs;
# the shorter registry chain names above are kept for contract identity merging.
_DEX_CHAIN_BY_PLATFORM = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "polygon": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "optimistic-ethereum": "optimism",
    "solana": "solana",
    "avax": "avalanche",
    "avalanche": "avalanche",
}


def _registry_chain(platform: str) -> str | None:
    return _REGISTRY_CHAIN_BY_PLATFORM.get(platform.strip().lower())


def _dex_chain_id(platform: str) -> str | None:
    return _DEX_CHAIN_BY_PLATFORM.get(platform.strip().lower())


def _dexscreener_batch_rows(
    payload: object,
    *,
    chain_id: str,
    chain: str,
    base_url: str,
    path: str,
    batch_size: int,
    batch_index: int,
    observed_at: str,
) -> tuple[DiscoveredCandidate, ...]:
    if not isinstance(payload, list):
        return ()
    by_address: dict[str, tuple[bool, float, dict[str, Any]]] = {}
    for pair in payload:
        if not isinstance(pair, dict) or str(pair.get("chainId") or "").strip().lower() != chain_id:
            continue
        liquidity_obj = pair.get("liquidity")
        liquidity = _market_number(liquidity_obj.get("usd")) if isinstance(liquidity_obj, dict) else None
        if liquidity is None or liquidity <= 0 or not pair.get("pairAddress"):
            continue
        for side in ("base", "quote"):
            token = pair.get(f"{side}Token")
            if not isinstance(token, dict):
                continue
            token_address = normalize_contract_address(token.get("address"), chain_id)
            if not token_address:
                continue
            previous = by_address.get(token_address)
            if previous is None or liquidity > previous[1]:
                by_address[token_address] = (side == "base", liquidity, pair)

    rows: list[DiscoveredCandidate] = []
    for address, (is_base, _liquidity, pair) in sorted(by_address.items()):
        side = "base" if is_base else "quote"
        token = pair.get(f"{side}Token")
        if not isinstance(token, dict):
            continue
        liquidity_obj = pair.get("liquidity")
        volume_obj = pair.get("volume")
        rows.append(
            DiscoveredCandidate(
                provider="dexscreener",
                provider_id=f"{chain}:{address}",
                slug=contract_slug(chain, address),
                name=str(token.get("name") or "").strip() or address,
                symbol=str(token.get("symbol") or "").strip().upper() or None,
                sector="dex_market",
                primary_chain=chain,
                candidate_type="token",
                source_url=f"{base_url.rstrip('/')}{path}",
                metadata={
                    "chain": chain,
                    "dex_chain_id": chain_id,
                    "contract_address": address,
                    "asset_scope": "dex_token",
                    "discovery_mode": "global_market_enumeration",
                    "market_evidence_status": "observed",
                    "market_evidence_provider": "dexscreener",
                    "market_evidence_url": f"{base_url.rstrip('/')}{path}",
                    "market_evidence_observed_at": observed_at,
                    "market_pair_address": str(pair.get("pairAddress") or ""),
                    "market_pair_side": side,
                    "batch_endpoint": "tokens/v1",
                    "batch_size": batch_size,
                    "batch_index": batch_index,
                    "chain_mapping_provenance": "verified_dexscreener_chain_id_map",
                    "liquidity_usd": (
                        _market_number(liquidity_obj.get("usd")) if isinstance(liquidity_obj, dict) else None
                    ),
                    "volume_usd": _market_number(volume_obj.get("h24")) if isinstance(volume_obj, dict) else None,
                    "market_cap": _market_number(pair.get("marketCap")) if is_base else None,
                    "fdv": _market_number(pair.get("fdv")) if is_base else None,
                    "price_usd": _market_number(pair.get("priceUsd")) if is_base else None,
                    "observed_at": observed_at,
                },
            )
        )
    return tuple(rows)


def _request_json(
    url: str,
    *,
    provider: str,
    timeout_seconds: int,
    max_attempts: int,
    backoff_seconds: float,
    sleeper: Callable[[float], None],
    meter: _RequestMeter | None = None,
    max_retry_after_seconds: float = 60.0,
    ledger: CapacityLedger | None = None,
    endpoint: str | None = None,
    dedup_key: str | None = None,
    batch_size: int | None = None,
) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "Project-Hunter discovery/2.8.0",
        },
    )
    last_error: Exception | None = None
    endpoint_name = endpoint or endpoint_for_url(provider, url)
    for attempt in range(1, max(1, max_attempts) + 1):
        reservation = None
        try:
            if meter is not None:
                meter.consume(sleeper)
            if ledger is not None:
                reservation = ledger.reserve(
                    provider,
                    endpoint_name,
                    url,
                    dedup_key=dedup_key,
                    batch_size=batch_size,
                    retry=attempt > 1,
                    sleeper=sleeper,
                )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if reservation is not None and ledger is not None:
                ledger.complete(reservation.reservation_id, status_code=200, outcome="success")
            return payload
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_seconds(exc.headers, max_retry_after_seconds) if exc.code == 429 else None
            if reservation is not None and ledger is not None:
                ledger.complete(
                    reservation.reservation_id,
                    status_code=exc.code,
                    outcome="throttled" if exc.code == 429 else "failure",
                    retry_after_seconds=retry_after,
                    error=str(exc),
                )
            # Persist a 429 cooldown even for single-attempt helper calls.
            # Otherwise a helper with max_attempts=1 raises before this branch
            # and the next cycle can retry inside the provider's Retry-After.
            if exc.code == 429 and retry_after is not None and ledger is not None:
                ledger.cooldown(provider, endpoint_name, seconds=retry_after, reason="HTTP 429 Retry-After")
            if exc.code in {408, 425, 429, 500, 502, 503, 504} and attempt < max_attempts:
                if exc.code == 429 and retry_after is not None:
                    if ledger is None:
                        sleeper(retry_after)
                else:
                    _sleep(attempt, backoff_seconds, sleeper)
                last_error = exc
                continue
            raise ProviderUnavailableError(f"{provider} discovery unavailable: HTTP {exc.code}") from exc
        except TimeoutError as exc:
            if reservation is not None and ledger is not None:
                ledger.complete(reservation.reservation_id, outcome="failure", error=str(exc))
            last_error = exc
            if attempt < max_attempts:
                _sleep(attempt, backoff_seconds, sleeper)
                continue
            raise ProviderUnavailableError(f"{provider} discovery timed out") from exc
        except urllib.error.URLError as exc:
            if reservation is not None and ledger is not None:
                ledger.complete(reservation.reservation_id, outcome="failure", error=str(exc))
            last_error = exc
            if attempt < max_attempts:
                _sleep(attempt, backoff_seconds, sleeper)
                continue
            raise ProviderUnavailableError(f"{provider} discovery unavailable: {exc}") from exc
    raise ProviderUnavailableError(f"{provider} discovery unavailable: {last_error}")


def _sleep(attempt: int, backoff_seconds: float, sleeper: Callable[[float], None]) -> None:
    if backoff_seconds > 0:
        sleeper(backoff_seconds * (2 ** (attempt - 1)))


def _retry_after_seconds(headers: object, cap_seconds: float) -> float | None:
    """Read a bounded Retry-After window; HTTP dates and seconds are both accepted."""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter("Retry-After")
    if value is None:
        return None
    text = str(value).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    return max(0.0, min(seconds, max(0.0, cap_seconds)))


def _network_from_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "networks" and parts[1] != "trending_pools":
        return urllib.parse.unquote(parts[1])
    return None


def _geckoterminal_network(token_id: str, relationships: object) -> str | None:
    if "_" in token_id:
        return token_id.split("_", 1)[0]
    if not isinstance(relationships, dict):
        return None
    network = relationships.get("network")
    if not isinstance(network, dict):
        return None
    data = network.get("data")
    if not isinstance(data, dict):
        return None
    value = str(data.get("id") or "").strip()
    return value or None


def _geckoterminal_pool_index(data: object) -> dict[str, dict[str, object]]:
    if not isinstance(data, list):
        return {}
    index: dict[str, dict[str, object]] = {}
    for pool in data:
        if not isinstance(pool, dict):
            continue
        attributes = pool.get("attributes")
        relationships = pool.get("relationships")
        if not isinstance(attributes, dict) or not isinstance(relationships, dict):
            continue
        pool_metadata = {
            "pool_address": attributes.get("address"),
            "pool_name": attributes.get("name"),
            "reserve_usd": attributes.get("reserve_in_usd"),
            "volume_usd": _pool_volume(attributes.get("volume_usd")),
            "dex": _relationship_id(relationships.get("dex")),
        }
        for relationship in ("base_token", "quote_token"):
            token_id = _relationship_id(relationships.get(relationship))
            if token_id:
                index[token_id] = pool_metadata
    return index


def _relationship_id(relationship: object) -> str | None:
    if not isinstance(relationship, dict):
        return None
    data = relationship.get("data")
    if not isinstance(data, dict):
        return None
    value = str(data.get("id") or "").strip()
    return value or None


def _pool_volume(payload: object) -> object:
    if isinstance(payload, dict):
        return payload.get("h24") or payload.get("24h")
    return None


def _homepage_domain(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    host = host.lower().removeprefix("www.")
    if host in {
        "x.com",
        "twitter.com",
        "t.me",
        "telegram.me",
        "discord.com",
        "discord.gg",
        "github.com",
        "linktr.ee",
        "medium.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
    }:
        return None
    return host


def _market_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _dexscreener_links(links: list[object]) -> tuple[str, ...]:
    values: list[str] = []
    for link in links:
        if isinstance(link, dict):
            value = str(link.get("url") or "").strip()
            if value:
                values.append(value)
    return tuple(values)


def _dexscreener_name(item: dict[str, object], chain: str, address: str) -> str:
    description = str(item.get("description") or "").strip()
    if description:
        return description[:80]
    return f"{chain}:{address[:10]}"


def _slug(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in normalized.split("-") if part)


def normalize_contract_address(value: object, chain: str) -> str:
    address = str(value or "").strip()
    # Hex EVM addresses are case-insensitive; other identifiers are not assumed to be.
    if chain.strip().lower() != "solana" and address.startswith(("0x", "0X")):
        return address.lower()
    return address


def contract_slug(chain: str, address: str) -> str:
    if chain.strip().lower() != "solana" and address.startswith("0x"):
        return _slug(f"{chain}-{address}")
    # A case-folded slug is not an identity for base58 addresses.
    digest = hashlib.sha256(address.encode()).hexdigest()
    return f"{_slug(chain)}-address-{digest}"
