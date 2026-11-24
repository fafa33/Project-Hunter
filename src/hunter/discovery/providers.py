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
        rows: list[DiscoveredCandidate] = []
        pages = max(1, (max(1, limit) + self.per_page - 1) // self.per_page)
        for page in range(1, pages + 1):
            payload = self._get(
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": min(self.per_page, max(1, limit)),
                    "page": page,
                    "sparkline": "false",
                },
            )
            if not isinstance(payload, list):
                raise ProviderUnavailableError("CoinGecko discovery response must be a list")
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
                if len(rows) >= limit:
                    return tuple(rows)
        return tuple(rows)

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
        payload = self._get("/networks/trending_pools", {"include": "base_token,quote_token", "page": 1})
        if not isinstance(payload, dict):
            raise ProviderUnavailableError("GeckoTerminal discovery response must be an object")
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
            if len(rows) >= limit:
                return tuple(rows)
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
                            "claimed_links": tuple(_dexscreener_links(links)),
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
            token = pair.get("baseToken")
            if not isinstance(token, dict) or normalize_contract_address(token.get("address"), chain) != address:
                continue
            liquidity = pair.get("liquidity")
            amount = _market_number(liquidity.get("usd")) if isinstance(liquidity, dict) else None
            if amount is not None and amount > 0 and pair.get("pairAddress"):
                matches.append((amount, str(pair["pairAddress"]), pair))
        if not matches:
            return {"market_evidence_status": "no_matching_liquid_base_pair"}
        liquidity_usd, pair_address, pair = max(matches, key=lambda item: (item[0], item[1]))
        token = pair["baseToken"]
        volume = pair.get("volume")
        return {
            "market_evidence_status": "observed",
            "market_evidence_url": self.base_url.rstrip("/") + path,
            "market_pair_address": pair_address,
            "token_name": str(token.get("name") or "").strip() or None,
            "token_symbol": str(token.get("symbol") or "").strip().upper() or None,
            "liquidity_usd": liquidity_usd,
            "volume_usd": _market_number(volume.get("h24")) if isinstance(volume, dict) else None,
            "market_cap": _market_number(pair.get("marketCap")),
            "fdv": _market_number(pair.get("fdv")),
            "price_usd": _market_number(pair.get("priceUsd")),
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
