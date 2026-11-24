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
from typing import Any, Protocol

from hunter.acquisition.exceptions import ProviderUnavailableError


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
class CoinGeckoDiscoveryProvider:
    base_url: str = "https://api.coingecko.com/api/v3"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    per_page: int = 250
    name: str = "coingecko"
    sleeper: Callable[[float], None] = time.sleep

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        return self.discover_batch(limit=limit, cursor=0)[0][:limit]

    def discover_batch(self, *, limit: int, cursor: int) -> tuple[tuple[DiscoveredCandidate, ...], int]:
        rows: list[DiscoveredCandidate] = []
        pages = max(1, (max(1, limit) + self.per_page - 1) // self.per_page)
        for page in range(cursor + 1, cursor + pages + 1):
            payload = self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": self.per_page,
                    "page": page,
                    "sparkline": "false",
                },
            )
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
                        },
                    )
                )
            if len(payload) < self.per_page:
                return tuple(rows), 0
        return tuple(rows), cursor + pages

    def _get(self, path: str, params: dict[str, object]) -> object:
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
        )


@dataclass(frozen=True)
class DefiLlamaDiscoveryProvider:
    base_url: str = "https://api.llama.fi"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "defillama"
    sleeper: Callable[[float], None] = time.sleep

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
        )


@dataclass(frozen=True)
class GeckoTerminalDiscoveryProvider:
    base_url: str = "https://api.geckoterminal.com/api/v2"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "geckoterminal"
    sleeper: Callable[[float], None] = time.sleep

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        return self._pool_page("/networks/trending_pools", 1)[:limit]

    def discover_batch(self, *, limit: int, cursor: int) -> tuple[tuple[DiscoveredCandidate, ...], int]:
        # Rotate across the provider's network catalogue, not only trending pools.
        catalogue_page, network_index, pool_page = cursor // 1000000 + 1, (cursor // 1000) % 1000, cursor % 1000 + 1
        catalogue = self._get("/networks", {"page": catalogue_page})
        networks = catalogue.get("data") if isinstance(catalogue, dict) else None
        if not isinstance(networks, list):
            raise ProviderUnavailableError("GeckoTerminal network catalogue unavailable")
        if not networks:
            return (), 0
        if network_index >= len(networks):
            return (), catalogue_page * 1000000
        network = networks[network_index]
        network_id = network.get("id") if isinstance(network, dict) else None
        if not isinstance(network_id, str) or not network_id:
            raise ProviderUnavailableError("GeckoTerminal network identity unavailable")
        path = f"/networks/{urllib.parse.quote(network_id, safe='')}/pools"
        rows = self._pool_page(path, pool_page)
        # Public endpoint pagination is bounded; do not claim exhaustive coverage.
        next_cursor = (
            (catalogue_page - 1) * 1000000 + (network_index + 1) * 1000 if not rows or pool_page >= 10 else cursor + 1
        )
        return rows, next_cursor

    def _pool_page(self, path: str, page: int) -> tuple[DiscoveredCandidate, ...]:
        payload = self._get(path, {"include": "base_token,quote_token", "page": page})
        if not isinstance(payload, dict):
            raise ProviderUnavailableError("GeckoTerminal discovery response must be an object")
        if payload.get("data") == [] and not payload.get("included"):
            return ()
        included = payload.get("included")
        if not isinstance(included, list):
            raise ProviderUnavailableError("GeckoTerminal discovery response missing included tokens")
        pools = _geckoterminal_pool_index(payload.get("data"))
        rows: list[DiscoveredCandidate] = []
        seen: set[tuple[str, str]] = set()
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
            if key in seen:
                continue
            seen.add(key)
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
                        "reserve_usd": pool_metadata.get("reserve_usd"),
                        "volume_usd": pool_metadata.get("volume_usd"),
                        "pool_address": pool_metadata.get("pool_address"),
                        "pool_name": pool_metadata.get("pool_name"),
                        "dex": pool_metadata.get("dex"),
                    },
                )
            )
        return tuple(rows)

    def _get(self, path: str, params: dict[str, object]) -> object:
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
        )


@dataclass(frozen=True)
class DexScreenerDiscoveryProvider:
    base_url: str = "https://api.dexscreener.com"
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    name: str = "dexscreener"
    sleeper: Callable[[float], None] = time.sleep

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
            timeout_seconds=self.timeout_seconds, max_attempts=1, sleeper=self.sleeper
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
            timeout_seconds=self.timeout_seconds, max_attempts=1, sleeper=self.sleeper
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

    def _get(self, path: str) -> object:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        return _request_json(
            url,
            provider="DexScreener",
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_seconds=self.backoff_seconds,
            sleeper=self.sleeper,
        )


def _request_json(
    url: str,
    *,
    provider: str,
    timeout_seconds: int,
    max_attempts: int,
    backoff_seconds: float,
    sleeper: Callable[[float], None],
) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "Project-Hunter discovery/2.8.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {408, 425, 429, 500, 502, 503, 504} and attempt < max_attempts:
                _sleep(attempt, backoff_seconds, sleeper)
                last_error = exc
                continue
            raise ProviderUnavailableError(f"{provider} discovery unavailable: HTTP {exc.code}") from exc
        except TimeoutError as exc:
            last_error = exc
            if attempt < max_attempts:
                _sleep(attempt, backoff_seconds, sleeper)
                continue
            raise ProviderUnavailableError(f"{provider} discovery timed out") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < max_attempts:
                _sleep(attempt, backoff_seconds, sleeper)
                continue
            raise ProviderUnavailableError(f"{provider} discovery unavailable: {exc}") from exc
    raise ProviderUnavailableError(f"{provider} discovery unavailable: {last_error}")


def _sleep(attempt: int, backoff_seconds: float, sleeper: Callable[[float], None]) -> None:
    if backoff_seconds > 0:
        sleeper(backoff_seconds * (2 ** (attempt - 1)))


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
