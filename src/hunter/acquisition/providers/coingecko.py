from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.acquisition.models import (
    AcquisitionRequest,
    NormalizedEvidence,
    ProviderHealth,
    ProviderMetadata,
    RateLimit,
    RawEvidence,
)
from hunter.execution.identity import identity

COINGECKO_MANDATORY_FIELDS: tuple[str, ...] = (
    "id",
    "symbol",
    "name",
    "market_cap",
    "price",
    "volume",
    "market_cap_rank",
    "last_updated",
)

COINGECKO_OPTIONAL_FIELDS: tuple[str, ...] = (
    "ath",
    "atl",
    "categories",
    "circulating_supply",
    "developer_links",
    "fdv",
    "fully_diluted_valuation",
    "homepage",
    "github_links",
    "max_supply",
    "total_supply",
)

COINGECKO_MARKET_FIELDS: tuple[str, ...] = (
    *COINGECKO_MANDATORY_FIELDS,
    *COINGECKO_OPTIONAL_FIELDS,
)


class CoinGeckoTransport(Protocol):
    def get_json(self, path: str, params: dict[str, object]) -> object:
        raise NotImplementedError


@dataclass(frozen=True)
class CoinGeckoProviderConfig:
    base_url: str = "https://api.coingecko.com/api/v3"
    api_key: str | None = None
    request_timeout_seconds: int = 30
    per_page: int = 250
    max_pages: int = 20
    max_attempts: int = 3
    detail_max_attempts: int = 1
    detail_metadata_ttl_seconds: int = 604_800
    detail_rate_limit_threshold: int = 3
    backoff_seconds: float = 1.0
    jitter_seconds: float = 0.25
    min_interval_seconds: float = 0.0
    vs_currency: str = "usd"

    def __post_init__(self) -> None:
        if self.per_page < 1 or self.per_page > 250:
            msg = "CoinGecko per_page must be between 1 and 250"
            raise ValueError(msg)
        if self.max_pages < 1:
            msg = "CoinGecko max_pages must be positive"
            raise ValueError(msg)
        if self.max_attempts < 1:
            msg = "CoinGecko max_attempts must be positive"
            raise ValueError(msg)
        if self.detail_max_attempts < 1:
            msg = "CoinGecko detail_max_attempts must be positive"
            raise ValueError(msg)
        if self.detail_metadata_ttl_seconds < 0 or self.detail_rate_limit_threshold < 1:
            msg = "CoinGecko detail cache and health settings must be non-negative"
            raise ValueError(msg)
        if self.backoff_seconds < 0 or self.jitter_seconds < 0 or self.min_interval_seconds < 0:
            msg = "CoinGecko timing settings must be non-negative"
            raise ValueError(msg)


@dataclass
class CoinGeckoProviderStatistics:
    request_count: int = 0
    success_count: int = 0
    retry_count: int = 0
    rate_limit_count: int = 0
    consecutive_rate_limit_count: int = 0
    http_error_count: int = 0
    market_success_count: int = 0
    detail_success_count: int = 0
    detail_failure_count: int = 0
    deferred_detail_count: int = 0
    cached_detail_count: int = 0
    accepted_record_count: int = 0
    accepted_detail_count: int = 0
    rejected_record_count: int = 0
    total_backoff_seconds: float = 0.0
    rejection_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def success_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return round(self.success_count / self.request_count, 4)


class CoinGeckoClient:
    def __init__(self, config: CoinGeckoProviderConfig) -> None:
        self.config = config

    def get_json(self, path: str, params: dict[str, object]) -> object:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        headers = {"accept": "application/json"}
        if self.config.api_key:
            headers["x-cg-demo-api-key"] = self.config.api_key
            headers["x-cg-pro-api-key"] = self.config.api_key
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise CoinGeckoHTTPError(exc.code, str(exc), retry_after=_retry_after(exc)) from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"CoinGecko request failed: {exc}") from exc
        return json.loads(payload)


class CoinGeckoHTTPError(ProviderUnavailableError):
    def __init__(self, status_code: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class CoinGeckoRateLimiter:
    def __init__(self, *, min_interval_seconds: float = 0.0) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.next_allowed_at: datetime | None = None
        self.delays: list[float] = []

    def before_request(self, now: datetime) -> float:
        delay = 0.0
        if self.next_allowed_at is not None and now < self.next_allowed_at:
            delay = (self.next_allowed_at - now).total_seconds()
            self.delays.append(delay)
        self.next_allowed_at = now + timedelta(seconds=self.min_interval_seconds)
        return delay


class CoinGeckoProvider:
    def __init__(
        self,
        config: CoinGeckoProviderConfig | None = None,
        *,
        transport: CoinGeckoTransport | None = None,
        rate_limiter: CoinGeckoRateLimiter | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or CoinGeckoProviderConfig()
        self.transport = transport or CoinGeckoClient(self.config)
        self.rate_limiter = rate_limiter or CoinGeckoRateLimiter(min_interval_seconds=self.config.min_interval_seconds)
        self.sleeper = sleeper or time.sleep
        self.statistics = CoinGeckoProviderStatistics()
        self._last_sync: datetime | None = None
        self._detail_degraded = False

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="coingecko",
            capabilities=("market", "developer", "github"),
            supported_metrics=("coingecko_market_profile",),
            rate_limits=(RateLimit(requests=1, window_seconds=max(1, int(self.config.min_interval_seconds or 1))),),
            last_sync=self._last_sync,
            availability="degraded" if self._detail_degraded else "available",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.metadata.name,
            availability="degraded" if self._detail_degraded else "available",
            checked_at=datetime.now(tz=UTC),
            last_sync=self._last_sync,
            message=(
                f"configured success_rate={self.statistics.success_rate:.4f} "
                f"requests={self.statistics.request_count} retries={self.statistics.retry_count} "
                f"rate_limits={self.statistics.rate_limit_count} deferred={self.statistics.deferred_detail_count}"
            ),
        )

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        project_ids = _project_ids(request)
        retrieved: list[RawEvidence] = []
        requested_details: set[str] = set()
        if project_ids:
            target_map = _target_map(request)
            for page_index, chunk in enumerate(_chunks(project_ids, self.config.per_page), start=1):
                if request.checkpoint and page_index < _checkpoint_page(request.checkpoint):
                    continue
                try:
                    market_rows = self._markets(ids=chunk, page=page_index)
                except ProviderUnavailableError as exc:
                    self.statistics.rejection_reasons[f"markets_unavailable:{type(exc).__name__}"] += 1
                    break
                details, detail_raw, pending_raw = self._detail_metadata(
                    market_rows,
                    request,
                    page_index,
                    requested_details,
                    target_map,
                )
                retrieved.extend(self._raw_rows(market_rows, details, request, page_index, target_map))
                retrieved.extend(detail_raw)
                retrieved.extend(pending_raw)
        else:
            start_page = _checkpoint_page(request.checkpoint)
            for page in range(start_page, self.config.max_pages + 1):
                try:
                    market_rows = self._markets(ids=(), page=page)
                except ProviderUnavailableError as exc:
                    self.statistics.rejection_reasons[f"markets_unavailable:{type(exc).__name__}"] += 1
                    break
                if not market_rows:
                    break
                details, detail_raw, pending_raw = self._detail_metadata(
                    market_rows, request, page, requested_details, {}
                )
                retrieved.extend(self._raw_rows(market_rows, details, request, page, {}))
                retrieved.extend(detail_raw)
                retrieved.extend(pending_raw)
        self._last_sync = request.requested_at
        return tuple(retrieved)

    def _markets(self, *, ids: tuple[str, ...], page: int) -> tuple[dict[str, Any], ...]:
        params: dict[str, object] = {
            "vs_currency": self.config.vs_currency,
            "order": "market_cap_desc",
            "per_page": self.config.per_page,
            "page": page,
            "sparkline": "false",
        }
        if ids:
            params["ids"] = ",".join(ids)
        payload = self._request("/coins/markets", params)
        if not isinstance(payload, list):
            raise ProviderUnavailableError("CoinGecko markets response must be a list")
        self.statistics.market_success_count += 1
        return tuple(item for item in payload if isinstance(item, dict))

    def _coin_detail(self, coin_id: str) -> dict[str, Any]:
        payload = self._request(
            f"/coins/{urllib.parse.quote(coin_id)}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            max_attempts=self.config.detail_max_attempts,
        )
        if not isinstance(payload, dict):
            raise ProviderUnavailableError("CoinGecko coin detail response must be an object")
        return payload

    def _detail_metadata(
        self,
        market_rows: tuple[dict[str, Any], ...],
        request: AcquisitionRequest,
        page: int,
        requested: set[str],
        target_map: dict[str, str],
    ) -> tuple[dict[str, dict[str, Any]], tuple[RawEvidence, ...], tuple[RawEvidence, ...]]:
        details: dict[str, dict[str, Any]] = {}
        detail_rows: list[RawEvidence] = []
        pending_rows: list[RawEvidence] = []
        for row in market_rows:
            coin_id = str(row.get("id", "")).strip()
            if not coin_id:
                continue
            target_id = target_map.get(coin_id, coin_id)
            cached = _cached_detail(coin_id, request, self.config.detail_metadata_ttl_seconds)
            if cached is not None:
                details[coin_id] = cached
                self.statistics.cached_detail_count += 1
                continue
            if coin_id in requested or self._detail_degraded:
                pending_rows.append(self._pending_detail_row(coin_id, target_id, request, page))
                self.statistics.deferred_detail_count += 1
                continue
            requested.add(coin_id)
            try:
                metadata = _detail_metadata_payload(self._coin_detail(coin_id), coin_id=coin_id)
                details[coin_id] = metadata
                detail_rows.append(self._detail_row(coin_id, target_id, metadata, request, page))
                self.statistics.detail_success_count += 1
                self.statistics.accepted_detail_count += 1
            except ProviderUnavailableError:
                self.statistics.detail_failure_count += 1
                self.statistics.rejection_reasons["detail_unavailable"] += 1
                pending_rows.append(self._pending_detail_row(coin_id, target_id, request, page))
                self.statistics.deferred_detail_count += 1
                if self.statistics.consecutive_rate_limit_count >= self.config.detail_rate_limit_threshold:
                    self._detail_degraded = True
        return details, tuple(detail_rows), tuple(pending_rows)

    def _request(self, path: str, params: dict[str, object], *, max_attempts: int | None = None) -> object:
        errors: list[Exception] = []
        attempts = max_attempts or self.config.max_attempts
        for attempt in range(attempts):
            try:
                pacing_delay = self.rate_limiter.before_request(datetime.now(tz=UTC))
                if pacing_delay > 0:
                    self.sleeper(pacing_delay)
                self.statistics.request_count += 1
                payload = self.transport.get_json(path, params)
                self.statistics.success_count += 1
                self.statistics.consecutive_rate_limit_count = 0
                return payload
            except CoinGeckoHTTPError as exc:
                self.statistics.http_error_count += 1
                if exc.status_code == 429:
                    self.statistics.rate_limit_count += 1
                    self.statistics.consecutive_rate_limit_count += 1
                    if self.statistics.consecutive_rate_limit_count >= self.config.detail_rate_limit_threshold:
                        self._detail_degraded = True
                if exc.status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise
                errors.append(exc)
            except ProviderUnavailableError as exc:
                errors.append(exc)
            if attempt < attempts - 1:
                self.statistics.retry_count += 1
                delay = _retry_delay(errors[-1], attempt=attempt, config=self.config, path=path)
                self.statistics.total_backoff_seconds += delay
                if delay > 0:
                    self.sleeper(delay)
        raise errors[-1]

    def _raw_rows(
        self,
        market_rows: tuple[dict[str, Any], ...],
        details: dict[str, dict[str, Any]],
        request: AcquisitionRequest,
        page: int,
        target_map: dict[str, str],
    ) -> tuple[RawEvidence, ...]:
        rows = []
        for market in market_rows:
            coin_id = str(market.get("id", ""))
            if not coin_id:
                self.statistics.rejected_record_count += 1
                self.statistics.rejection_reasons["missing_coin_id"] += 1
                continue
            target_id = target_map.get(coin_id, coin_id)
            detail = details.get(coin_id, {})
            payload = _payload(market, detail, page=page)
            if _completeness(payload, fields=COINGECKO_MANDATORY_FIELDS) < 1.0:
                self.statistics.rejected_record_count += 1
                for field_name in _missing_fields(payload, COINGECKO_MANDATORY_FIELDS):
                    self.statistics.rejection_reasons[f"missing_mandatory:{field_name}"] += 1
                continue
            self.statistics.accepted_record_count += 1
            rows.append(
                RawEvidence(
                    provider="coingecko",
                    collector="coingecko-api",
                    raw_source_id=coin_id,
                    domain="market",
                    metric="coingecko_market_profile",
                    target_id=target_id,
                    retrieved_at=request.requested_at,
                    payload=payload,
                    source_url=f"https://www.coingecko.com/en/coins/{coin_id}",
                    repository_id=f"coingecko:{target_id}",
                )
            )
        return tuple(rows)

    def _detail_row(
        self,
        coin_id: str,
        target_id: str,
        payload: dict[str, Any],
        request: AcquisitionRequest,
        page: int,
    ) -> RawEvidence:
        return RawEvidence(
            provider="coingecko",
            collector="coingecko-api",
            raw_source_id=coin_id,
            domain="market",
            metric="coingecko_detail_metadata",
            target_id=target_id,
            retrieved_at=request.requested_at,
            payload={**payload, "page": page},
            source_url=f"https://www.coingecko.com/en/coins/{coin_id}",
            repository_id=f"coingecko-detail:{target_id}",
        )

    def _pending_detail_row(self, coin_id: str, target_id: str, request: AcquisitionRequest, page: int) -> RawEvidence:
        return RawEvidence(
            provider="coingecko",
            collector="coingecko-api",
            raw_source_id=coin_id,
            domain="market",
            metric="coingecko_pending_detail_enrichment",
            target_id=target_id,
            retrieved_at=request.requested_at,
            payload={"id": coin_id, "reason": "detail_deferred", "page": page},
            source_url=f"https://www.coingecko.com/en/coins/{coin_id}",
            repository_id=f"coingecko-pending-detail:{target_id}",
        )


class CoinGeckoEvidenceNormalizer:
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        evidence = []
        for item in raw:
            if item.metric == "coingecko_detail_metadata":
                detail_completeness = _completeness(item.payload, fields=("id", "last_updated"))
                evidence.append(
                    NormalizedEvidence(
                        evidence_id=identity(
                            "coingecko-detail-evidence",
                            {
                                "coin_id": item.raw_source_id,
                                "last_updated": item.payload.get("last_updated"),
                                "retrieved_at": item.retrieved_at,
                            },
                        ),
                        repository_id=item.repository_id,
                        provider=item.provider,
                        collector=item.collector,
                        raw_source_id=item.raw_source_id,
                        domain=item.domain,
                        metric=item.metric,
                        target_id=item.target_id,
                        value=item.raw_source_id,
                        raw_metrics=dict(item.payload),
                        normalized_metrics={"detail_metadata_completeness": detail_completeness},
                        source_url=item.source_url,
                        retrieved_at=item.retrieved_at,
                        normalized_at=request.requested_at,
                        confidence=detail_completeness,
                        freshness=_freshness(item.payload, request.requested_at),
                        raw_evidence_id=item.raw_source_id,
                    )
                )
                continue
            if item.metric == "coingecko_pending_detail_enrichment":
                evidence.append(
                    NormalizedEvidence(
                        evidence_id=identity(
                            "coingecko-pending-detail",
                            {"coin_id": item.raw_source_id, "retrieved_at": item.retrieved_at},
                        ),
                        repository_id=item.repository_id,
                        provider=item.provider,
                        collector=item.collector,
                        raw_source_id=item.raw_source_id,
                        domain=item.domain,
                        metric=item.metric,
                        target_id=item.target_id,
                        value=item.raw_source_id,
                        raw_metrics=dict(item.payload),
                        normalized_metrics={"pending_detail_enrichment": 1.0},
                        source_url=item.source_url,
                        retrieved_at=item.retrieved_at,
                        normalized_at=request.requested_at,
                        confidence=1.0,
                        freshness=1.0,
                        raw_evidence_id=item.raw_source_id,
                    )
                )
                continue
            mandatory_completeness = _completeness(item.payload, fields=COINGECKO_MANDATORY_FIELDS)
            optional_completeness = _completeness(item.payload, fields=COINGECKO_OPTIONAL_FIELDS)
            schema_completeness = _completeness(item.payload, fields=COINGECKO_MARKET_FIELDS)
            evidence_id = identity(
                "coingecko-evidence",
                {
                    "coin_id": item.raw_source_id,
                    "last_updated": item.payload.get("last_updated"),
                    "retrieved_at": item.retrieved_at,
                },
            )
            evidence.append(
                NormalizedEvidence(
                    evidence_id=evidence_id,
                    repository_id=item.repository_id,
                    provider=item.provider,
                    collector=item.collector,
                    raw_source_id=item.raw_source_id,
                    domain=item.domain,
                    metric=item.metric,
                    target_id=item.target_id,
                    value=item.raw_source_id,
                    raw_metrics=dict(item.payload),
                    normalized_metrics={
                        "mandatory_completeness": mandatory_completeness,
                        "optional_completeness": optional_completeness,
                        "schema_completeness": schema_completeness,
                    },
                    source_url=item.source_url,
                    retrieved_at=item.retrieved_at,
                    normalized_at=request.requested_at,
                    confidence=mandatory_completeness,
                    freshness=_freshness(item.payload, request.requested_at),
                    raw_evidence_id=item.raw_source_id,
                )
            )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))


def _payload(market: dict[str, Any], detail: dict[str, Any], *, page: int) -> dict[str, Any]:
    return {
        "id": market.get("id"),
        "symbol": market.get("symbol"),
        "name": market.get("name"),
        "categories": (
            tuple(str(value) for value in detail.get("categories", ()) if str(value).strip())
            if isinstance(detail.get("categories"), list | tuple)
            else ()
        ),
        "market_cap": market.get("market_cap"),
        "fdv": market.get("fully_diluted_valuation"),
        "price": market.get("current_price"),
        "ath": market.get("ath"),
        "atl": market.get("atl"),
        "circulating_supply": market.get("circulating_supply"),
        "total_supply": market.get("total_supply"),
        "max_supply": market.get("max_supply"),
        "volume": market.get("total_volume"),
        "market_cap_rank": market.get("market_cap_rank"),
        "fully_diluted_valuation": market.get("fully_diluted_valuation"),
        "developer_links": tuple(str(value) for value in detail.get("github_links", ()) if str(value).strip()),
        "homepage": tuple(str(value) for value in detail.get("homepage", ()) if str(value).strip()),
        "github_links": tuple(str(value) for value in detail.get("github_links", ()) if str(value).strip()),
        "last_updated": market.get("last_updated") or detail.get("last_updated"),
        "detail_available": bool(detail),
        "page": page,
    }


def _detail_metadata_payload(detail: dict[str, Any], *, coin_id: str) -> dict[str, Any]:
    links = detail.get("links") if isinstance(detail.get("links"), dict) else {}
    repos = links.get("repos_url") if isinstance(links.get("repos_url"), dict) else {}
    homepage = links.get("homepage") if isinstance(links.get("homepage"), list) else []
    github = repos.get("github") if isinstance(repos.get("github"), list) else []
    return {
        "id": detail.get("id") or coin_id,
        "categories": (
            tuple(str(value) for value in detail.get("categories", ()) if str(value).strip())
            if isinstance(detail.get("categories"), list | tuple)
            else ()
        ),
        "homepage": tuple(str(value) for value in homepage if str(value).strip()),
        "github_links": tuple(str(value) for value in github if str(value).strip()),
        "developer_links": tuple(str(value) for value in github if str(value).strip()),
        "last_updated": detail.get("last_updated"),
    }


def _project_ids(request: AcquisitionRequest) -> tuple[str, ...]:
    raw = request.parameters.get("project_ids")
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    if isinstance(raw, tuple | list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def _target_map(request: AcquisitionRequest) -> dict[str, str]:
    raw = request.parameters.get("target_map")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if str(key).strip() and str(value).strip()}


def _chunks(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(values[index : index + size]) for index in range(0, len(values), size))


def _checkpoint_page(checkpoint: str | None) -> int:
    if not checkpoint:
        return 1
    try:
        return max(1, int(checkpoint.split(":", 1)[-1]))
    except ValueError:
        return 1


def _cached_detail(coin_id: str, request: AcquisitionRequest, ttl_seconds: int) -> dict[str, Any] | None:
    raw_cache = request.parameters.get("detail_cache")
    if not isinstance(raw_cache, dict):
        return None
    cached = raw_cache.get(coin_id)
    if not isinstance(cached, dict):
        return None
    retrieved_at = cached.get("retrieved_at")
    payload = cached.get("payload")
    if not isinstance(retrieved_at, str) or not isinstance(payload, dict):
        return None
    try:
        cached_at = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
    if request.requested_at - cached_at > timedelta(seconds=ttl_seconds):
        return None
    return dict(payload)


def _completeness(payload: dict[str, Any], *, fields: tuple[str, ...]) -> float:
    if not fields:
        return 1.0
    present = sum(1 for field in fields if field in payload and _present(payload.get(field)))
    return round(present / len(fields), 4)


def _missing_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(field for field in fields if field not in payload or not _present(payload.get(field)))


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple | list | dict):
        return bool(value)
    return True


def _freshness(payload: dict[str, Any], as_of: datetime) -> float:
    last_updated = payload.get("last_updated")
    if not isinstance(last_updated, str) or not last_updated:
        return 0.0
    try:
        updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return 0.0
    age_days = max((as_of - updated).total_seconds() / 86_400, 0.0)
    return round(max(0.0, min(1.0, 1.0 - (age_days / 30.0))), 4)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed.astimezone(UTC) - datetime.now(tz=UTC)).total_seconds())


def _retry_delay(
    error: Exception,
    *,
    attempt: int,
    config: CoinGeckoProviderConfig,
    path: str,
) -> float:
    if isinstance(error, CoinGeckoHTTPError) and error.retry_after is not None:
        return error.retry_after
    base = config.backoff_seconds * (2**attempt)
    if base == 0:
        return 0.0
    jitter_seed = sum(ord(char) for char in f"{path}:{attempt}") % 1000
    jitter = (jitter_seed / 1000) * config.jitter_seconds
    return round(base + jitter, 4)
