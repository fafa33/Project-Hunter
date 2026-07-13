from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from hunter.whale.configuration import WhaleProviderConfig
from hunter.whale.models import WhaleMetric, WhaleProviderFailure

Transport = Callable[[str], str]


class WhaleProvider(Protocol):
    name: str
    failures: tuple[WhaleProviderFailure, ...]

    def collect(self) -> tuple[WhaleMetric, ...]: ...


class WhaleProviderError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "REQUEST_FAILED") -> None:
        super().__init__(message)
        self.reason = reason


class BinanceDerivativesProvider:
    def __init__(self, config: WhaleProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[WhaleProviderFailure, ...] = ()

    def collect(self) -> tuple[WhaleMetric, ...]:
        metrics = []
        failures = []
        retrieval = datetime.now(tz=UTC)
        for asset, symbol in self.config.assets.items():
            for metric in self.config.metrics:
                url = _binance_url(self.config.base_url, metric, symbol)
                try:
                    metrics.append(_binance_metric(metric, asset, self.name, url, self.transport(url), retrieval))
                except Exception as exc:
                    failures.append(_failure(self.name, metric, url, exc))
        self.failures = tuple(failures)
        return tuple(metrics)


class BybitDerivativesProvider:
    def __init__(self, config: WhaleProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[WhaleProviderFailure, ...] = ()

    def collect(self) -> tuple[WhaleMetric, ...]:
        metrics = []
        failures = []
        retrieval = datetime.now(tz=UTC)
        for asset, symbol in self.config.assets.items():
            for metric in self.config.metrics:
                url = _bybit_url(self.config.base_url, metric, symbol)
                try:
                    metrics.append(_bybit_metric(metric, asset, self.name, url, self.transport(url), retrieval))
                except Exception as exc:
                    failures.append(_failure(self.name, metric, url, exc))
        self.failures = tuple(failures)
        return tuple(metrics)


class OkxDerivativesProvider:
    def __init__(self, config: WhaleProviderConfig, *, transport: Transport | None = None) -> None:
        self.name = config.name
        self.config = config
        self.transport = transport or _fetch
        self.failures: tuple[WhaleProviderFailure, ...] = ()

    def collect(self) -> tuple[WhaleMetric, ...]:
        metrics = []
        failures = []
        retrieval = datetime.now(tz=UTC)
        for asset, symbol in self.config.assets.items():
            for metric in self.config.metrics:
                url = _okx_url(self.config.base_url, metric, symbol)
                try:
                    metrics.append(_okx_metric(metric, asset, self.name, url, self.transport(url), retrieval))
                except Exception as exc:
                    failures.append(_failure(self.name, metric, url, exc))
        self.failures = tuple(failures)
        return tuple(metrics)


class UnavailableWhaleProvider:
    def __init__(self, config: WhaleProviderConfig) -> None:
        self.name = config.name
        self.config = config
        self.failures: tuple[WhaleProviderFailure, ...] = ()

    def collect(self) -> tuple[WhaleMetric, ...]:
        now = datetime.now(tz=UTC)
        self.failures = tuple(
            WhaleProviderFailure(
                provider=self.name,
                metric=metric,
                reason="NO_DOCUMENTED_FREE_SOURCE",
                message="no documented public provider configured for this whale metric",
                source_url=self.config.base_url,
                occurred_at=now,
            )
            for metric in self.config.metrics
        )
        return ()


class WhaleProviderRegistry:
    def __init__(self, configs: tuple[WhaleProviderConfig, ...], *, transport: Transport | None = None) -> None:
        self.configs = configs
        self.transport = transport

    def providers(self) -> tuple[WhaleProvider, ...]:
        rows: list[WhaleProvider] = []
        for config in self.configs:
            if config.name == "unavailable_public_sources":
                rows.append(UnavailableWhaleProvider(config))
            elif config.enabled and config.name == "binance_derivatives":
                rows.append(BinanceDerivativesProvider(config, transport=self.transport))
            elif config.enabled and config.name == "bybit_derivatives":
                rows.append(BybitDerivativesProvider(config, transport=self.transport))
            elif config.enabled and config.name == "okx_derivatives":
                rows.append(OkxDerivativesProvider(config, transport=self.transport))
        return tuple(rows)


def _binance_url(base_url: str, metric: str, symbol: str) -> str:
    path = "/fapi/v1/openInterest" if metric == "open_interest" else "/fapi/v1/premiumIndex"
    return f"{base_url.rstrip('/')}{path}?symbol={urllib.parse.quote(symbol)}"


def _bybit_url(base_url: str, metric: str, symbol: str) -> str:
    query = {"category": "linear", "symbol": symbol}
    path = "/v5/market/open-interest" if metric == "open_interest" else "/v5/market/funding/history"
    if metric == "open_interest":
        query.update({"intervalTime": "5min", "limit": "1"})
    else:
        query.update({"limit": "1"})
    return f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"


def _okx_url(base_url: str, metric: str, symbol: str) -> str:
    path = "/api/v5/public/open-interest" if metric == "open_interest" else "/api/v5/public/funding-rate"
    query = {"instId": symbol}
    if metric == "open_interest":
        query["instType"] = "SWAP"
    return f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(query)}"


def _binance_metric(
    metric: str,
    asset: str,
    provider: str,
    url: str,
    payload: str,
    retrieval: datetime,
) -> WhaleMetric:
    row = json.loads(payload)
    if not isinstance(row, dict):
        raise WhaleProviderError(f"{metric} response must be an object", reason="SCHEMA_MISMATCH")
    if metric == "open_interest":
        value = _required_float(row, "openInterest", metric)
        timestamp = retrieval
        raw = {**row, "normalized_unit": "base_asset_contracts"}
    elif metric == "funding_rate":
        value = _required_float(row, "lastFundingRate", metric)
        timestamp_ms = row.get("time")
        timestamp = datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=UTC) if timestamp_ms else retrieval
        raw = {**row, "normalized_unit": "rate"}
    else:
        raise WhaleProviderError(f"unsupported Binance whale metric: {metric}", reason="MISCONFIGURED")
    return WhaleMetric(
        name=metric,
        provider=provider,
        source_url=url,
        asset=asset,
        timestamp=timestamp,
        retrieval_time=retrieval,
        value=value,
        raw_payload=raw,
    )


def _bybit_metric(
    metric: str,
    asset: str,
    provider: str,
    url: str,
    payload: str,
    retrieval: datetime,
) -> WhaleMetric:
    row = json.loads(payload)
    if not isinstance(row, dict) or int(row.get("retCode", -1)) != 0:
        raise WhaleProviderError(f"{metric} Bybit response returned non-zero retCode", reason="SCHEMA_MISMATCH")
    rows = row.get("result", {}).get("list", [])
    if not rows:
        raise WhaleProviderError(f"{metric} Bybit response missing rows", reason="SCHEMA_MISMATCH")
    item = rows[0]
    if metric == "open_interest":
        value = _required_float(item, "openInterest", metric)
        timestamp = _timestamp_from_ms(item.get("timestamp"), retrieval)
        raw = {**row, "normalized_unit": "base_asset_contracts"}
    elif metric == "funding_rate":
        value = _required_float(item, "fundingRate", metric)
        timestamp = _timestamp_from_ms(item.get("fundingRateTimestamp"), retrieval)
        raw = {**row, "normalized_unit": "rate"}
    else:
        raise WhaleProviderError(f"unsupported Bybit whale metric: {metric}", reason="MISCONFIGURED")
    return WhaleMetric(
        name=metric,
        provider=provider,
        source_url=url,
        asset=asset,
        timestamp=timestamp,
        retrieval_time=retrieval,
        value=value,
        raw_payload=raw,
    )


def _okx_metric(
    metric: str,
    asset: str,
    provider: str,
    url: str,
    payload: str,
    retrieval: datetime,
) -> WhaleMetric:
    row = json.loads(payload)
    if not isinstance(row, dict) or str(row.get("code")) != "0":
        raise WhaleProviderError(f"{metric} OKX response returned non-zero code", reason="SCHEMA_MISMATCH")
    rows = row.get("data", [])
    if not rows:
        raise WhaleProviderError(f"{metric} OKX response missing rows", reason="SCHEMA_MISMATCH")
    item = rows[0]
    if metric == "open_interest":
        value = _required_float(item, "oiCcy", metric)
        timestamp = _timestamp_from_ms(item.get("ts"), retrieval)
        raw = {**row, "normalized_unit": "base_asset_contracts"}
    elif metric == "funding_rate":
        value = _required_float(item, "fundingRate", metric)
        timestamp = retrieval
        raw = {**row, "normalized_unit": "rate"}
    else:
        raise WhaleProviderError(f"unsupported OKX whale metric: {metric}", reason="MISCONFIGURED")
    return WhaleMetric(
        name=metric,
        provider=provider,
        source_url=url,
        asset=asset,
        timestamp=timestamp,
        retrieval_time=retrieval,
        value=value,
        raw_payload=raw,
    )


def _required_float(row: dict[str, Any], key: str, metric: str) -> float:
    value = row.get(key)
    if value in {None, ""}:
        raise WhaleProviderError(f"{metric} response missing {key}", reason="SCHEMA_MISMATCH")
    return float(value)


def _timestamp_from_ms(value: object, fallback: datetime) -> datetime:
    return datetime.fromtimestamp(float(value) / 1000, tz=UTC) if value not in {None, ""} else fallback


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Project-Hunter/3.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _failure(provider: str, metric: str, url: str, exc: Exception) -> WhaleProviderFailure:
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
    elif isinstance(exc, (KeyError, ValueError, json.JSONDecodeError)):
        reason = "SCHEMA_MISMATCH"
    return WhaleProviderFailure(
        provider=provider,
        metric=metric,
        reason=str(reason),
        message=message,
        source_url=url,
        occurred_at=datetime.now(tz=UTC),
    )
