from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from io import StringIO
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from hunter.macro.configuration import MacroProviderConfig
from hunter.macro.models import MacroMetric, MacroProviderFailure

Transport = Callable[[str], str]


class MacroProvider(Protocol):
    name: str

    def collect(self) -> tuple[MacroMetric, ...]: ...


class MacroProviderError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "REQUEST_FAILED") -> None:
        super().__init__(message)
        self.reason = reason


class CsvSeriesProvider:
    def __init__(self, config: MacroProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[MacroProviderFailure, ...] = ()

    def collect(self) -> tuple[MacroMetric, ...]:
        rows = []
        failures = []
        for metric, series_id in self.config.metrics.items():
            url = f"{self.config.base_url}?id={urllib.parse.quote(series_id)}"
            try:
                rows.append(_metric_from_csv(metric, self.name, url, self.transport(url), series_id=series_id))
            except Exception as exc:
                failures.append(_failure(self.name, metric, url, exc))
        self.failures = tuple(failures)
        return tuple(rows)


class CboeVixProvider:
    def __init__(self, config: MacroProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[MacroProviderFailure, ...] = ()

    def collect(self) -> tuple[MacroMetric, ...]:
        try:
            payload = self.transport(self.config.base_url)
            reader = csv.DictReader(StringIO(payload))
            rows = [row for row in reader if row.get("CLOSE")]
            if not rows:
                raise MacroProviderError("CBOE VIX response did not contain CLOSE rows", reason="SCHEMA_MISMATCH")
        except Exception as exc:
            self.failures = (_failure(self.name, "vix", self.config.base_url, exc),)
            return ()
        row = rows[-1]
        return (
            MacroMetric(
                name="vix",
                provider=self.name,
                source_url=self.config.base_url,
                timestamp=_parse_date(str(row.get("DATE") or row.get("Date"))),
                value=float(str(row["CLOSE"]).replace(",", "")),
                raw_payload=dict(row),
            ),
        )


class JsonMacroProvider:
    def __init__(self, config: MacroProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[MacroProviderFailure, ...] = ()

    def collect(self) -> tuple[MacroMetric, ...]:
        try:
            payload = json.loads(self.transport(self.config.base_url))
        except Exception as exc:
            self.failures = tuple(
                _failure(self.name, metric, self.config.base_url, exc) for metric in self.config.metrics
            )
            return ()
        if self.name == "coingecko_global":
            data = payload.get("data", {})
            market_cap = data.get("total_market_cap", {}).get("usd")
            dominance = data.get("market_cap_percentage", {}).get("btc")
            return tuple(
                item
                for item in (
                    _json_metric("bitcoin_dominance", self.name, self.config.base_url, dominance, payload),
                    _json_metric("total_crypto_market_cap", self.name, self.config.base_url, market_cap, payload),
                )
                if item is not None
            )
        if self.name == "defillama_stablecoins":
            value = payload.get("totalCirculatingUSD", {}).get("peggedUSD")
            if value is None and isinstance(payload.get("peggedAssets"), list):
                value = sum(
                    float(asset.get("circulating", {}).get("peggedUSD", 0.0) or 0.0)
                    for asset in payload["peggedAssets"]
                    if isinstance(asset, dict)
                )
            metric = _json_metric("stablecoin_market_cap", self.name, self.config.base_url, value, payload)
            return (metric,) if metric is not None else ()
        if self.name == "alternative_me":
            rows = payload.get("data", [])
            value = rows[0].get("value") if rows else None
            timestamp = (
                datetime.fromtimestamp(int(rows[0]["timestamp"]), tz=UTC) if rows and rows[0].get("timestamp") else None
            )
            metric = _json_metric("fear_greed_index", self.name, self.config.base_url, value, payload, timestamp)
            return (metric,) if metric is not None else ()
        raise MacroProviderError(f"unsupported JSON macro provider: {self.name}")


class MacroProviderRegistry:
    def __init__(self, configs: tuple[MacroProviderConfig, ...], *, transport: Transport | None = None) -> None:
        self.configs = configs
        self.transport = transport

    def providers(self) -> tuple[MacroProvider, ...]:
        rows: list[MacroProvider] = []
        for config in self.configs:
            if not config.enabled:
                continue
            if config.name == "cboe":
                rows.append(CboeVixProvider(config, transport=self.transport))
            elif config.name == "stooq":
                rows.append(StooqCsvProvider(config, transport=self.transport))
            elif config.name in {"coingecko_global", "defillama_stablecoins", "alternative_me"}:
                rows.append(JsonMacroProvider(config, transport=self.transport))
            elif config.name == "fred":
                rows.append(CsvSeriesProvider(config, transport=self.transport))
            elif config.name == "ecb":
                rows.append(CsvSeriesProvider(config, transport=self.transport))
        return tuple(rows)


class StooqCsvProvider:
    def __init__(self, config: MacroProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[MacroProviderFailure, ...] = ()

    def collect(self) -> tuple[MacroMetric, ...]:
        rows = []
        failures = []
        for metric, symbol in self.config.metrics.items():
            url = f"{self.config.base_url}?s={urllib.parse.quote(symbol)}&d1=20200101&d2=20300101&i=d"
            try:
                rows.append(_metric_from_csv(metric, self.name, url, self.transport(url), series_id=symbol))
            except Exception as exc:
                failures.append(_failure(self.name, metric, url, exc))
        self.failures = tuple(failures)
        return tuple(rows)


def _metric_from_csv(metric: str, provider: str, url: str, payload: str, *, series_id: str) -> MacroMetric:
    reader = csv.DictReader(StringIO(payload))
    rows = [row for row in reader if row]
    if not rows:
        raise MacroProviderError(f"{metric} response was empty")
    value_key = _value_key(rows[-1])
    valid = [row for row in rows if row.get(value_key) not in {None, "", "."}]
    if not value_key or not valid:
        raise MacroProviderError(f"{metric} response did not contain numeric observations")
    row = valid[-1]
    date_value = str(row.get("DATE") or row.get("Date") or row.get("observation_date") or row.get("TIME_PERIOD"))
    return MacroMetric(
        name=metric,
        provider=provider,
        source_url=url,
        timestamp=_parse_date(date_value),
        value=float(str(row[value_key]).replace(",", "")),
        raw_payload={**dict(row), "series_id": series_id},
    )


def _json_metric(
    metric: str,
    provider: str,
    url: str,
    value: object,
    payload: dict[str, Any],
    timestamp: datetime | None = None,
) -> MacroMetric | None:
    if value is None:
        return None
    return MacroMetric(
        name=metric,
        provider=provider,
        source_url=url,
        timestamp=timestamp or datetime.now(tz=UTC),
        value=float(value),
        raw_payload=payload,
    )


def _parse_date(value: str) -> datetime:
    text = value.strip()
    if "/" in text:
        return datetime.strptime(text[:10], "%m/%d/%Y").replace(tzinfo=UTC)
    return datetime.fromisoformat(text[:10]).replace(tzinfo=UTC)


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Project-Hunter/3.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _value_key(row: dict[str, str]) -> str:
    for preferred in ("Close", "CLOSE", "Value", "value"):
        if preferred in row:
            return preferred
    return next((key for key in row if key and key.upper() not in {"DATE", "OBSERVATION_DATE", "TIME_PERIOD"}), "")


def _failure(provider: str, metric: str, url: str, exc: Exception) -> MacroProviderFailure:
    reason = getattr(exc, "reason", "REQUEST_FAILED")
    message = str(exc)
    if isinstance(exc, HTTPError):
        if exc.code == 429:
            reason = "RATE_LIMITED"
        elif exc.code in {401, 403}:
            reason = "AUTH_REQUIRED"
        elif exc.code == 404:
            reason = "MISCONFIGURED"
        else:
            reason = "PROVIDER_BLOCKED"
    elif isinstance(exc, URLError):
        reason = "PROVIDER_BLOCKED"
    elif "404" in message:
        reason = "MISCONFIGURED"
    elif "429" in message:
        reason = "RATE_LIMITED"
    elif isinstance(exc, (KeyError, ValueError, json.JSONDecodeError)):
        reason = "SCHEMA_MISMATCH"
    return MacroProviderFailure(
        provider=provider,
        metric=metric,
        reason=str(reason),
        message=message,
        source_url=url,
        occurred_at=datetime.now(tz=UTC),
    )
