from __future__ import annotations

import hashlib
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from hunter.acquisition.exceptions import ProviderUnavailableError

if TYPE_CHECKING:
    from hunter.discovery.repository import CandidateRegistryRepository

DEFAULT_CAPACITY_INVENTORY = "configs/discovery_capacity.yaml"


class DuplicateCapacityRequest(Exception):
    """A deduplicated request fingerprint is still inside its active window."""


class CapacityBudgetExhausted(ProviderUnavailableError):
    """A shared documented endpoint budget cannot be reserved within the wait ceiling."""


@dataclass(frozen=True)
class EndpointCapacity:
    provider: str
    endpoint: str
    rate_limit_per_minute: int | None = None
    rate_limit_provenance: str = "not_documented"
    batch_limit: int | None = None
    batch_limit_provenance: str = "not_documented"
    monthly_credits: int | None = None
    monthly_credits_provenance: str = "not_documented"
    authentication: str = "none"
    pagination: str = "not_documented"
    documented_source_url: str = ""
    notes: tuple[str, ...] = ()
    has_global_denominator: bool = False


@dataclass(frozen=True)
class CapacityInventory:
    endpoints: dict[str, EndpointCapacity]
    source_path: str = DEFAULT_CAPACITY_INVENTORY

    def get(self, provider: str, endpoint: str) -> EndpointCapacity:
        key = f"{provider.strip().lower()}:{endpoint.strip().lower()}"
        return self.endpoints.get(key, EndpointCapacity(provider=provider.strip().lower(), endpoint=endpoint))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "endpoints": {key: _capacity_dict(value) for key, value in sorted(self.endpoints.items())},
        }


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    provider: str
    endpoint: str
    url: str
    attempted_at: datetime


class CapacityLedger:
    """Cross-process request ledger with documented-rate slots and bounded cooldowns."""

    def __init__(
        self,
        repository: CandidateRegistryRepository,
        inventory: CapacityInventory,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        dedupe_window_seconds: int = 300,
        max_wait_seconds: float = 60.0,
        max_wait_cycles: int = 6,
    ) -> None:
        self.repository = repository
        self.inventory = inventory
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._sleeper = sleeper or time.sleep
        self.dedupe_window_seconds = max(0, dedupe_window_seconds)
        self.max_wait_seconds = max(0.0, max_wait_seconds)
        self.max_wait_cycles = max(1, max_wait_cycles)

    def reserve(
        self,
        provider: str,
        endpoint: str,
        url: str,
        *,
        dedup_key: str | None = None,
        batch_size: int | None = None,
        retry: bool = False,
        sleeper: Callable[[float], None] | None = None,
    ) -> Reservation:
        wait_sleeper = sleeper or self._sleeper
        provider_name = provider.strip().lower()
        endpoint_name = endpoint.strip().lower()
        capacity = self.inventory.get(provider_name, endpoint_name)
        fingerprint_source = dedup_key or url
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        limit = capacity.rate_limit_per_minute
        cooldown_waited = False

        for _ in range(self.max_wait_cycles):
            now = self._clock()
            cooldown_until_text = self.repository.capacity_cooldown_until(provider_name, endpoint_name)
            if cooldown_until_text and not cooldown_waited:
                cooldown_until = _parse_iso(cooldown_until_text)
                if cooldown_until > now:
                    wait = min((cooldown_until - now).total_seconds(), self.max_wait_seconds)
                    wait_sleeper(max(0.0, wait))
                    cooldown_waited = True
                    continue

            attempted_at = self._clock()
            reservation_id = hashlib.sha256(
                f"{fingerprint}|{attempted_at.isoformat()}|{time.monotonic_ns()}".encode()
            ).hexdigest()
            reservation, wait, duplicate = self.repository.try_reserve_capacity(
                provider=provider_name,
                endpoint=endpoint_name,
                fingerprint=fingerprint,
                dedup_key=dedup_key,
                attempted_at=_dt(attempted_at),
                window_start=_dt(attempted_at - timedelta(seconds=60)),
                dedupe_until=_dt(attempted_at + timedelta(seconds=self.dedupe_window_seconds)),
                rate_limit_per_minute=limit,
                retry=retry,
                batch_size=batch_size,
                reservation_id=reservation_id,
            )
            if duplicate:
                raise DuplicateCapacityRequest(
                    f"duplicate {provider_name}:{endpoint_name} request within dedupe window"
                )
            if reservation is not None:
                return Reservation(reservation_id, provider_name, endpoint_name, url, attempted_at)
            if wait > 0:
                wait_sleeper(min(wait, self.max_wait_seconds))

        raise CapacityBudgetExhausted(
            f"{provider_name}:{endpoint_name} capacity budget was not available within the wait ceiling"
        )

    def complete(
        self,
        reservation_id: str,
        *,
        status_code: int | None = None,
        outcome: str,
        retry_after_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        self.repository.complete_capacity_request(
            reservation_id,
            completed_at=_dt(self._clock()),
            status_code=status_code,
            outcome=outcome,
            retry_after_seconds=retry_after_seconds,
            error=error,
        )

    def cooldown(self, provider: str, endpoint: str, *, seconds: float, reason: str) -> None:
        if seconds <= 0:
            return
        bounded = min(seconds, self.max_wait_seconds)
        self.repository.set_capacity_cooldown(
            provider.strip().lower(),
            endpoint.strip().lower(),
            until=_dt(self._clock() + timedelta(seconds=bounded)),
            reason=reason,
        )

    def summary(self) -> dict[str, Any]:
        rows = self.repository.capacity_summary(window_start=_dt(self._clock() - timedelta(seconds=60)))
        endpoints: list[dict[str, Any]] = []
        totals = {
            "requests": 0,
            "retries": 0,
            "throttles": 0,
            "failures": 0,
            "successes": 0,
            "cooldowns": 0,
        }
        for row in rows:
            provider = str(row["provider"])
            endpoint = str(row["endpoint"])
            capacity = self.inventory.get(provider, endpoint)
            attempts = int(row["attempts"])
            recent = int(row["recent_attempts"])
            remaining = (
                max(0, capacity.rate_limit_per_minute - recent) if capacity.rate_limit_per_minute is not None else None
            )
            item = {
                "provider": provider,
                "endpoint": endpoint,
                "attempts": attempts,
                "retries": int(row["retries"]),
                "throttles": int(row["throttles"]),
                "failures": int(row["failures"]),
                "successes": int(row["successes"]),
                "cooldowns": int(row["cooldowns"]),
                "recent_attempts": recent,
                "remaining_quota": remaining,
                "rate_limit_per_minute": capacity.rate_limit_per_minute,
                "rate_limit_provenance": capacity.rate_limit_provenance,
                "monthly_credits": capacity.monthly_credits,
                "monthly_credits_provenance": capacity.monthly_credits_provenance,
                "max_batch_size": int(row["max_batch_size"] or 0) or None,
                "batch_limit": capacity.batch_limit,
                "batch_limit_provenance": capacity.batch_limit_provenance,
                "first_attempt_at": row["first_attempt_at"],
                "last_attempt_at": row["last_attempt_at"],
                "cooldown_until": row["cooldown_until"] or None,
                "documented_source_url": capacity.documented_source_url,
            }
            endpoints.append(item)
            totals["requests"] += attempts
            totals["retries"] += int(item["retries"])
            totals["throttles"] += int(item["throttles"])
            totals["failures"] += int(item["failures"])
            totals["successes"] += int(item["successes"])
            totals["cooldowns"] += int(item["cooldowns"])
        return {
            "generated_at": _dt(self._clock()),
            "inventory": self.inventory.source_path,
            "endpoints": endpoints,
            "totals": totals,
        }


def load_capacity_inventory(path: str | Path = DEFAULT_CAPACITY_INVENTORY) -> CapacityInventory:
    config_path = Path(path)
    if not config_path.exists():
        return CapacityInventory(endpoints={}, source_path=str(config_path))
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "discovery capacity inventory must be a mapping"
        raise ValueError(msg)
    endpoints: dict[str, EndpointCapacity] = {}
    for raw in payload.get("endpoints") or ():
        if not isinstance(raw, dict):
            continue
        item = EndpointCapacity(
            provider=str(raw.get("provider") or "").strip().lower(),
            endpoint=str(raw.get("endpoint") or "").strip().lower(),
            rate_limit_per_minute=_optional_int(raw.get("rate_limit_per_minute")),
            rate_limit_provenance=str(raw.get("rate_limit_provenance") or "not_documented"),
            batch_limit=_optional_int(raw.get("batch_limit")),
            batch_limit_provenance=str(raw.get("batch_limit_provenance") or "not_documented"),
            monthly_credits=_optional_int(raw.get("monthly_credits")),
            monthly_credits_provenance=str(raw.get("monthly_credits_provenance") or "not_documented"),
            authentication=str(raw.get("authentication") or "none"),
            pagination=str(raw.get("pagination") or "not_documented"),
            documented_source_url=str(raw.get("documented_source_url") or ""),
            notes=tuple(str(value) for value in (raw.get("notes") or ())),
            has_global_denominator=bool(raw.get("has_global_denominator", False)),
        )
        if item.provider and item.endpoint:
            endpoints[f"{item.provider}:{item.endpoint}"] = item
    return CapacityInventory(endpoints=endpoints, source_path=str(config_path))


def endpoint_for_url(provider: str, url: str) -> str:
    """Classify a provider request into a budgeted endpoint family."""
    path = urllib.parse.urlparse(url).path.lower()
    name = provider.strip().lower()
    if name == "coingecko":
        if path.endswith("/coins/list"):
            return "coins_list"
        if path.endswith("/coins/markets"):
            return "coins_markets"
        if "/contract/" in path:
            return "coins_contract"
        return "other"
    if name == "geckoterminal":
        if path.endswith("/networks/trending_pools"):
            return "trending_pools"
        if path.endswith("/networks"):
            return "networks"
        if path.endswith("/pools"):
            return "network_pools"
        if "/tokens/" in path:
            return "token"
        return "other"
    if name == "dexscreener":
        if "/tokens/v1/" in path:
            return "tokens_v1"
        if "/token-pairs/v1/" in path:
            return "token_pairs"
        if path.startswith(("/token-profiles/", "/token-boosts/", "/community-takeovers/", "/ads/")):
            return "profiles_boosts"
        if path.startswith("/latest/dex/"):
            return "market_search"
        return "other"
    if name == "defillama":
        if path.endswith("/protocols"):
            return "protocols"
        return "other"
    return "other"


def _capacity_dict(item: EndpointCapacity) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "endpoint": item.endpoint,
        "rate_limit_per_minute": item.rate_limit_per_minute,
        "rate_limit_provenance": item.rate_limit_provenance,
        "batch_limit": item.batch_limit,
        "batch_limit_provenance": item.batch_limit_provenance,
        "monthly_credits": item.monthly_credits,
        "monthly_credits_provenance": item.monthly_credits_provenance,
        "authentication": item.authentication,
        "pagination": item.pagination,
        "documented_source_url": item.documented_source_url,
        "notes": list(item.notes),
        "has_global_denominator": item.has_global_denominator,
    }


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
