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

DEFILLAMA_MANDATORY_FIELDS: tuple[str, ...] = (
    "protocol_name",
    "protocol_slug",
    "last_updated",
)


class DefiLlamaTransport(Protocol):
    def get_json(self, path: str, params: dict[str, object] | None = None) -> object:
        raise NotImplementedError


@dataclass(frozen=True)
class DefiLlamaProviderConfig:
    base_url: str = "https://api.llama.fi"
    request_timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    jitter_seconds: float = 0.25
    min_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "DefiLlama max_attempts must be positive"
            raise ValueError(msg)
        if self.backoff_seconds < 0 or self.jitter_seconds < 0 or self.min_interval_seconds < 0:
            msg = "DefiLlama timing settings must be non-negative"
            raise ValueError(msg)


@dataclass
class DefiLlamaProviderStatistics:
    request_count: int = 0
    success_count: int = 0
    retry_count: int = 0
    rate_limit_count: int = 0
    accepted_record_count: int = 0
    rejected_record_count: int = 0
    tvl_record_count: int = 0
    revenue_record_count: int = 0
    fee_record_count: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def success_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return round(self.success_count / self.request_count, 4)


class DefiLlamaHTTPError(ProviderUnavailableError):
    def __init__(self, status_code: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class DefiLlamaClient:
    def __init__(self, config: DefiLlamaProviderConfig) -> None:
        self.config = config

    def get_json(self, path: str, params: dict[str, object] | None = None) -> object:
        query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, headers={"accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise DefiLlamaHTTPError(exc.code, str(exc), retry_after=_retry_after(exc)) from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailableError(f"DefiLlama request failed: {exc}") from exc
        return json.loads(payload)


class DefiLlamaRateLimiter:
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


class DefiLlamaProvider:
    def __init__(
        self,
        config: DefiLlamaProviderConfig | None = None,
        *,
        transport: DefiLlamaTransport | None = None,
        rate_limiter: DefiLlamaRateLimiter | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or DefiLlamaProviderConfig()
        self.transport = transport or DefiLlamaClient(self.config)
        self.rate_limiter = rate_limiter or DefiLlamaRateLimiter(min_interval_seconds=self.config.min_interval_seconds)
        self.sleeper = sleeper or time.sleep
        self.statistics = DefiLlamaProviderStatistics()
        self._last_sync: datetime | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="defillama",
            capabilities=("protocol", "fees", "revenue"),
            supported_metrics=("defillama_protocol_profile",),
            rate_limits=(RateLimit(requests=1, window_seconds=max(1, int(self.config.min_interval_seconds or 1))),),
            last_sync=self._last_sync,
            availability="available",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name="defillama",
            availability="available",
            checked_at=datetime.now(tz=UTC),
            last_sync=self._last_sync,
            message=(
                f"configured success_rate={self.statistics.success_rate:.4f} "
                f"requests={self.statistics.request_count} retries={self.statistics.retry_count} "
                f"rate_limits={self.statistics.rate_limit_count}"
            ),
        )

    def protocols(self) -> tuple[dict[str, Any], ...]:
        payload = self._request("/protocols")
        if not isinstance(payload, list):
            raise ProviderUnavailableError("DefiLlama protocols response must be a list")
        return tuple(item for item in payload if isinstance(item, dict))

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        slugs = _project_ids(request)
        target_map = _target_map(request)
        rows = []
        for page, slug in enumerate(slugs, start=1):
            if request.checkpoint and page < _checkpoint_page(request.checkpoint):
                continue
            try:
                protocol = self._protocol(slug)
            except ProviderUnavailableError as exc:
                self.statistics.rejected_record_count += 1
                self.statistics.rejection_reasons[f"protocol_unavailable:{type(exc).__name__}"] += 1
                continue
            fees = self._summary("fees", slug)
            revenue = self._summary("fees", slug, {"dataType": "dailyRevenue"})
            payload = _payload(protocol, fees, revenue, slug=slug, page=page)
            if _completeness(payload) < 1.0:
                self.statistics.rejected_record_count += 1
                for field_name in _missing_fields(payload):
                    self.statistics.rejection_reasons[f"missing_mandatory:{field_name}"] += 1
                continue
            self.statistics.accepted_record_count += 1
            if payload["tvl"] is not None:
                self.statistics.tvl_record_count += 1
            if payload["revenue"] is not None or payload["daily_revenue"] is not None:
                self.statistics.revenue_record_count += 1
            if payload["fees"] is not None or payload["daily_fees"] is not None:
                self.statistics.fee_record_count += 1
            rows.append(
                RawEvidence(
                    provider="defillama",
                    collector="defillama-api",
                    raw_source_id=slug,
                    domain="protocol",
                    metric="defillama_protocol_profile",
                    target_id=target_map.get(slug, slug),
                    retrieved_at=request.requested_at,
                    payload=payload,
                    source_url=f"https://defillama.com/protocol/{slug}",
                    repository_id=f"defillama:{target_map.get(slug, slug)}",
                )
            )
        self._last_sync = request.requested_at
        return tuple(rows)

    def _protocol(self, slug: str) -> dict[str, Any]:
        payload = self._request(f"/protocol/{urllib.parse.quote(slug)}")
        if not isinstance(payload, dict):
            raise ProviderUnavailableError("DefiLlama protocol response must be an object")
        return payload

    def _summary(self, category: str, slug: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        try:
            payload = self._request(f"/summary/{category}/{urllib.parse.quote(slug)}", params or {})
        except ProviderUnavailableError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _request(self, path: str, params: dict[str, object] | None = None) -> object:
        errors: list[Exception] = []
        for attempt in range(self.config.max_attempts):
            try:
                pacing_delay = self.rate_limiter.before_request(datetime.now(tz=UTC))
                if pacing_delay > 0:
                    self.sleeper(pacing_delay)
                self.statistics.request_count += 1
                payload = self.transport.get_json(path, params or {})
                self.statistics.success_count += 1
                return payload
            except DefiLlamaHTTPError as exc:
                if exc.status_code == 429:
                    self.statistics.rate_limit_count += 1
                if exc.status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise
                errors.append(exc)
            except ProviderUnavailableError as exc:
                errors.append(exc)
            if attempt < self.config.max_attempts - 1:
                self.statistics.retry_count += 1
                delay = _retry_delay(errors[-1], attempt=attempt, config=self.config, path=path)
                if delay > 0:
                    self.sleeper(delay)
        raise errors[-1]


class DefiLlamaEvidenceNormalizer:
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        evidence = []
        for item in raw:
            confidence = _completeness(item.payload)
            evidence.append(
                NormalizedEvidence(
                    evidence_id=identity(
                        "defillama-evidence",
                        {
                            "slug": item.raw_source_id,
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
                    normalized_metrics={
                        "schema_completeness": confidence,
                        "has_tvl": 1.0 if item.payload.get("tvl") is not None else 0.0,
                        "has_revenue": (
                            1.0
                            if item.payload.get("revenue") is not None or item.payload.get("daily_revenue") is not None
                            else 0.0
                        ),
                        "has_fees": (
                            1.0
                            if item.payload.get("fees") is not None or item.payload.get("daily_fees") is not None
                            else 0.0
                        ),
                    },
                    source_url=item.source_url,
                    retrieved_at=item.retrieved_at,
                    normalized_at=request.requested_at,
                    confidence=confidence,
                    freshness=_freshness(item.payload, request.requested_at),
                    raw_evidence_id=item.raw_source_id,
                )
            )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))


def _payload(
    protocol: dict[str, Any],
    fees: dict[str, Any],
    revenue: dict[str, Any],
    *,
    slug: str,
    page: int,
) -> dict[str, Any]:
    tvl_history = _tvl_history(protocol)
    chains = protocol.get("chains") if isinstance(protocol.get("chains"), list) else []
    tvl = _number(protocol.get("tvl"))
    if tvl is None:
        tvl = _latest_tvl(tvl_history)
    return {
        "protocol_name": protocol.get("name"),
        "protocol_slug": protocol.get("slug") or slug,
        "tvl": tvl,
        "tvl_history": tvl_history,
        "revenue": (
            _number(revenue.get("totalAllTime") or revenue.get("totalDataChart", [[None, None]])[-1][-1])
            if revenue
            else None
        ),
        "fees": (
            _number(fees.get("totalAllTime") or fees.get("totalDataChart", [[None, None]])[-1][-1]) if fees else None
        ),
        "daily_fees": _number(fees.get("dailyFees")),
        "daily_revenue": _number(revenue.get("dailyRevenue")),
        "monthly_fees": _number(fees.get("monthlyFees")),
        "monthly_revenue": _number(revenue.get("monthlyRevenue")),
        "chain_list": tuple(str(item) for item in chains if str(item).strip()),
        "parent_protocol": protocol.get("parentProtocol"),
        "category": protocol.get("category"),
        "token_symbol": protocol.get("symbol"),
        "last_updated": _last_updated(protocol, tvl_history),
        "page": page,
    }


def _tvl_history(protocol: dict[str, Any]) -> tuple[dict[str, float], ...]:
    raw = protocol.get("tvl")
    if not isinstance(raw, list):
        return ()
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        date = item.get("date")
        total = _number(item.get("totalLiquidityUSD"))
        if date is None or total is None:
            continue
        rows.append({"date": float(date), "tvl": total})
    return tuple(rows)


def _latest_tvl(tvl_history: tuple[dict[str, float], ...]) -> float | None:
    if not tvl_history:
        return None
    return tvl_history[-1]["tvl"]


def _last_updated(protocol: dict[str, Any], tvl_history: tuple[dict[str, float], ...]) -> str:
    raw = protocol.get("lastUpdated") or protocol.get("last_updated")
    if isinstance(raw, str) and raw:
        return raw
    if tvl_history:
        return datetime.fromtimestamp(tvl_history[-1]["date"], tz=UTC).isoformat()
    return datetime.now(tz=UTC).isoformat()


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _checkpoint_page(checkpoint: str | None) -> int:
    if not checkpoint:
        return 1
    try:
        return max(1, int(checkpoint.split(":", 1)[-1]))
    except ValueError:
        return 1


def _completeness(payload: dict[str, Any]) -> float:
    present = sum(1 for field in DEFILLAMA_MANDATORY_FIELDS if field in payload and _present(payload.get(field)))
    return round(present / len(DEFILLAMA_MANDATORY_FIELDS), 4)


def _missing_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        field for field in DEFILLAMA_MANDATORY_FIELDS if field not in payload or not _present(payload.get(field))
    )


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
    config: DefiLlamaProviderConfig,
    path: str,
) -> float:
    if isinstance(error, DefiLlamaHTTPError) and error.retry_after is not None:
        return error.retry_after
    base = config.backoff_seconds * (2**attempt)
    if base == 0:
        return 0.0
    jitter_seed = sum(ord(char) for char in f"{path}:{attempt}") % 1000
    jitter = (jitter_seed / 1000) * config.jitter_seconds
    return round(base + jitter, 4)
