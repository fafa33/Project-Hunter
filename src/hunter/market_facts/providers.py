from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Protocol

from hunter.execution.canonicalization import canonicalize
from hunter.market_facts.models import (
    MarketFactAcquisitionResult,
    MarketFactRequest,
    MarketFactStatus,
    NormalizedMarketFact,
)
from hunter.market_facts.registry import MarketFactSourceRegistry


class JsonTransport(Protocol):
    def get_json(self, url: str, timeout_seconds: float) -> object: ...


class UrllibJsonTransport:
    def get_json(self, url: str, timeout_seconds: float) -> object:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "Project-Hunter/3.4"}
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class CoinGeckoObservedMarketFactProvider:
    provider_id = "coingecko"

    def __init__(
        self,
        registry: MarketFactSourceRegistry | None = None,
        transport: JsonTransport | None = None,
        *,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry or MarketFactSourceRegistry.from_file()
        self.transport = transport or UrllibJsonTransport()
        self.timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect(self, request: MarketFactRequest) -> MarketFactAcquisitionResult:
        acquired_at = _aware_now(self._clock())
        try:
            source = self.registry.require(request.source_id)
        except ValueError as exc:
            return self._unavailable(request, acquired_at, "unsupported", str(exc), "unregistered")
        if source.provider_id != self.provider_id or request.provider_id != self.provider_id:
            return self._unavailable(request, acquired_at, "unsupported", "provider_mismatch", source.fingerprint)
        requested = set(request.requested_fact_types)
        if not requested.issubset(set(source.capabilities)):
            return self._unavailable(request, acquired_at, "unsupported", "capability_mismatch", source.fingerprint)
        if request.quote_currency not in source.quote_currencies:
            return self._unavailable(request, acquired_at, "unsupported", "quote_currency_mismatch", source.fingerprint)
        endpoint = source.endpoint_for(request.identity.provider_listing_id)
        try:
            payload = self.transport.get_json(endpoint, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status: MarketFactStatus = "rate_limited" if exc.code == 429 else "unavailable"
            return self._unavailable(request, acquired_at, status, f"http_{exc.code}", source.fingerprint, endpoint)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return self._unavailable(
                request, acquired_at, "unavailable", type(exc).__name__, source.fingerprint, endpoint
            )
        if not isinstance(payload, Mapping):
            return self._unavailable(
                request, acquired_at, "malformed", "payload_not_mapping", source.fingerprint, endpoint
            )
        raw_hash = f"sha256:{sha256(canonicalize(payload)).hexdigest()}"
        try:
            facts = self._parse(payload, request, source.units, acquired_at)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            return MarketFactAcquisitionResult(
                source_id=source.source_id,
                provider_id=self.provider_id,
                endpoint=endpoint,
                parser_version=source.parser_version,
                registry_fingerprint=source.fingerprint,
                request=request,
                status="malformed",
                acquired_at=acquired_at,
                known_at=acquired_at,
                raw_payload_hash=raw_hash,
                failure_reason=f"parse_error:{type(exc).__name__}",
            )
        if not facts:
            return MarketFactAcquisitionResult(
                source_id=source.source_id,
                provider_id=self.provider_id,
                endpoint=endpoint,
                parser_version=source.parser_version,
                registry_fingerprint=source.fingerprint,
                request=request,
                status="unavailable",
                acquired_at=acquired_at,
                known_at=acquired_at,
                raw_payload_hash=raw_hash,
                failure_reason="requested_facts_unavailable",
            )
        return MarketFactAcquisitionResult(
            source_id=source.source_id,
            provider_id=self.provider_id,
            endpoint=endpoint,
            parser_version=source.parser_version,
            registry_fingerprint=source.fingerprint,
            request=request,
            status="success",
            acquired_at=acquired_at,
            known_at=acquired_at,
            raw_payload_hash=raw_hash,
            facts=tuple(facts),
        )

    def _parse(
        self,
        payload: Mapping[str, object],
        request: MarketFactRequest,
        units: Mapping[str, str],
        acquired_at: datetime,
    ) -> list[NormalizedMarketFact]:
        market_data = payload.get("market_data")
        if not isinstance(market_data, Mapping):
            raise ValueError("market_data_missing")
        effective_at = _parse_time(payload.get("last_updated")) or acquired_at
        quote = request.quote_currency
        fields: dict[str, tuple[str, bool]] = {
            "spot_price": ("current_price", True),
            "circulating_supply": ("circulating_supply", False),
            "total_supply": ("total_supply", False),
            "max_supply": ("max_supply", False),
            "market_capitalization": ("market_cap", True),
            "fully_diluted_valuation": ("fully_diluted_valuation", True),
            "trading_volume": ("total_volume", True),
        }
        facts: list[NormalizedMarketFact] = []
        for fact_type in request.requested_fact_types:
            field, quoted = fields[fact_type]
            raw = market_data.get(field)
            if quoted:
                if not isinstance(raw, Mapping):
                    continue
                raw = raw.get(quote)
            if raw is None:
                continue
            value = _decimal_text(raw)
            facts.append(
                NormalizedMarketFact(
                    fact_type=fact_type,
                    value=value,
                    unit=units[fact_type],
                    quote_currency=quote if quoted else None,
                    effective_at=effective_at,
                    observed_at=acquired_at,
                )
            )
        return facts

    def _unavailable(
        self,
        request: MarketFactRequest,
        acquired_at: datetime,
        status: MarketFactStatus,
        reason: str,
        fingerprint: str,
        endpoint: str = "https://api.coingecko.com/",
    ) -> MarketFactAcquisitionResult:
        return MarketFactAcquisitionResult(
            source_id=request.source_id,
            provider_id=self.provider_id,
            endpoint=endpoint,
            parser_version="unresolved" if fingerprint in {"unregistered", ""} else "coingecko-coin-v1",
            registry_fingerprint=fingerprint or "unregistered",
            request=request,
            status=status,
            acquired_at=acquired_at,
            known_at=acquired_at,
            raw_payload_hash="sha256:" + ("0" * 64),
            failure_reason=reason,
        )


def _decimal_text(value: object) -> str:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("non-finite market fact")
    return format(number, "f")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _aware_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider clock must return timezone-aware datetime")
    return value.astimezone(UTC)
