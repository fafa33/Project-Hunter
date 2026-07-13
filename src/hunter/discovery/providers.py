from __future__ import annotations

import json
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


def _request_json(
    url: str,
    *,
    provider: str,
    timeout_seconds: int,
    max_attempts: int,
    backoff_seconds: float,
    sleeper: Callable[[float], None],
) -> object:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
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
