from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Protocol

from hunter.onchain.models import ChainConfig, ProviderState


class EVMTransport(Protocol):
    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        raise NotImplementedError


class EVMProviderUnavailable(Exception):
    def __init__(self, message: str, *, failure_type: str = "unavailable") -> None:
        super().__init__(message)
        self.failure_type = failure_type


@dataclass
class UrlLibEVMTransport:
    endpoint: str
    timeout_seconds: int

    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"content-type": "application/json", "user-agent": "Project-Hunter/2.5 onchain-acquisition"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise EVMProviderUnavailable(str(exc), failure_type="timed_out") from exc
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise EVMProviderUnavailable(str(exc), failure_type="forbidden") from exc
            if exc.code == 429:
                raise EVMProviderUnavailable(str(exc), failure_type="rate_limited") from exc
            raise EVMProviderUnavailable(str(exc), failure_type="unavailable") from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise EVMProviderUnavailable(str(exc), failure_type="invalid_response") from exc
        if raw.get("error"):
            raise EVMProviderUnavailable(str(raw["error"]), failure_type=_failure_type(str(raw["error"])))
        return raw.get("result")


class EVMJsonRpcAdapter:
    def __init__(
        self,
        config: ChainConfig,
        *,
        transport: EVMTransport | None = None,
        transport_factory: Callable[[str], EVMTransport] | None = None,
    ) -> None:
        self.config = config
        self._single_transport = transport
        self._transport_factory = transport_factory or (
            lambda endpoint: UrlLibEVMTransport(endpoint, config.timeout_seconds)
        )
        self._active_endpoint = config.rpc_endpoint
        self._cooldown_until: dict[str, datetime] = {}
        self._cooldown_failure_type: dict[str, str] = {}
        self._last_successful_provider: str | None = None
        self._last_successful_request: datetime | None = None

    @property
    def provider(self) -> str:
        return "evm-json-rpc"

    @property
    def endpoint_identity(self) -> str:
        return endpoint_identity(self._active_endpoint)

    def state(self) -> ProviderState:
        states = self.check_providers()
        healthy = next((state for state in states if state.status in {"healthy", "degraded"}), None)
        if healthy is not None:
            return healthy
        return (
            states[0]
            if states
            else _provider_state(self.config, self.provider, self.config.rpc_endpoint, "unavailable", "no providers")
        )

    def check_providers(self) -> tuple[ProviderState, ...]:
        return tuple(self._check_provider(endpoint) for endpoint in self._ordered_endpoints())

    def reset_cooldown(self) -> None:
        self._cooldown_until.clear()
        self._cooldown_failure_type.clear()

    def chain_id(self) -> int:
        result = self._rpc("eth_chainId", ())
        if not isinstance(result, str):
            raise EVMProviderUnavailable("eth_chainId returned invalid payload", failure_type="invalid_response")
        return int(result, 16)

    def latest_finalized_block(self) -> int:
        latest = self._rpc("eth_blockNumber", ())
        if not isinstance(latest, str):
            raise EVMProviderUnavailable("eth_blockNumber returned invalid payload", failure_type="invalid_response")
        return max(int(latest, 16) - self.config.finality_depth, 0)

    def block(self, number: int) -> dict[str, Any]:
        result = self._rpc("eth_getBlockByNumber", (hex(number), False))
        if not isinstance(result, dict):
            raise EVMProviderUnavailable("eth_getBlockByNumber returned no block", failure_type="invalid_response")
        return result

    def block_timestamp(self, number: int) -> datetime:
        block = self.block(number)
        timestamp = int(str(block["timestamp"]), 16)
        return datetime.fromtimestamp(timestamp, tz=UTC)

    def native_balance(self, address: str, block_number: int) -> int:
        result = self._rpc("eth_getBalance", (address, hex(block_number)))
        if not isinstance(result, str):
            raise EVMProviderUnavailable("eth_getBalance returned invalid payload", failure_type="invalid_response")
        return int(result, 16)

    def logs(
        self, *, address: str | None, from_block: int, to_block: int, topics: tuple[str, ...]
    ) -> tuple[dict[str, Any], ...]:
        if to_block - from_block > self.config.max_block_range:
            raise EVMProviderUnavailable(
                "requested block range exceeds configured maximum", failure_type="invalid_response"
            )
        result = self._rpc(
            "eth_getLogs",
            (
                {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    **({"address": address} if address else {}),
                    "topics": list(topics),
                },
            ),
        )
        if not isinstance(result, list):
            raise EVMProviderUnavailable("eth_getLogs returned invalid payload", failure_type="invalid_response")
        return tuple(item for item in result if isinstance(item, dict))

    def _check_provider(self, endpoint: str) -> ProviderState:
        now = datetime.now(tz=UTC)
        cooldown = self._cooldown_until.get(endpoint)
        if cooldown and cooldown > now:
            failure_type = self._cooldown_failure_type.get(endpoint, "rate_limited")
            return _provider_state(
                self.config,
                self.provider,
                endpoint,
                _status(failure_type),
                "provider in cooldown",
                failure_type=failure_type,
                cooldown_until=cooldown,
                last_successful_request=(
                    self._last_successful_request if endpoint == self._last_successful_provider else None
                ),
            )
        started = perf_counter()
        try:
            chain_id = self._rpc_endpoint(endpoint, "eth_chainId", ())
            latest = self._rpc_endpoint(endpoint, "eth_blockNumber", ())
        except EVMProviderUnavailable as exc:
            cooldown_until = _cooldown(now, exc.failure_type)
            if cooldown_until is not None:
                self._cooldown_until[endpoint] = cooldown_until
                self._cooldown_failure_type[endpoint] = exc.failure_type
            return ProviderState(
                chain_id=self.config.chain_id,
                network=self.config.network,
                provider=self.provider,
                endpoint_identity=endpoint_identity(endpoint),
                status=_status(exc.failure_type),
                message=str(exc),
                checked_at=now,
                latency_ms=int((perf_counter() - started) * 1000),
                failure_type=exc.failure_type,
                cooldown_until=cooldown_until,
            )
        latency = int((perf_counter() - started) * 1000)
        if not isinstance(chain_id, str) or not isinstance(latest, str):
            return _provider_state(
                self.config,
                self.provider,
                endpoint,
                "invalid_response",
                "invalid health-check payload",
                latency_ms=latency,
            )
        chain_id_int = int(chain_id, 16)
        latest_int = int(latest, 16)
        if chain_id_int != self.config.chain_id:
            return _provider_state(
                self.config,
                self.provider,
                endpoint,
                "wrong_chain",
                f"expected chain {self.config.chain_id}, got {chain_id_int}",
                latency_ms=latency,
                latest_block=latest_int,
                chain_id_response=chain_id_int,
                failure_type="wrong_chain",
            )
        self._last_successful_provider = endpoint
        self._last_successful_request = now
        return ProviderState(
            chain_id=self.config.chain_id,
            network=self.config.network,
            provider=self.provider,
            endpoint_identity=endpoint_identity(endpoint),
            status="healthy",
            message="latest finalized block available",
            checked_at=now,
            latency_ms=latency,
            latest_block=max(latest_int - self.config.finality_depth, 0),
            chain_id_response=chain_id_int,
            capabilities=("latest_block", "native_balance", "logs"),
            last_successful_request=now,
        )

    def _rpc(self, method: str, params: tuple[object, ...]) -> object:
        errors: list[Exception] = []
        for endpoint in self._ordered_endpoints():
            cooldown = self._cooldown_until.get(endpoint)
            if cooldown and cooldown > datetime.now(tz=UTC):
                continue
            try:
                result = self._rpc_endpoint(endpoint, method, params)
                self._active_endpoint = endpoint
                self._last_successful_provider = endpoint
                self._last_successful_request = datetime.now(tz=UTC)
                return result
            except EVMProviderUnavailable as exc:
                errors.append(exc)
                cooldown_until = _cooldown(datetime.now(tz=UTC), exc.failure_type)
                if cooldown_until is not None:
                    self._cooldown_until[endpoint] = cooldown_until
                    self._cooldown_failure_type[endpoint] = exc.failure_type
        raise EVMProviderUnavailable(str(errors[-1]) if errors else "provider unavailable")

    def _rpc_endpoint(self, endpoint: str, method: str, params: tuple[object, ...]) -> object:
        errors: list[EVMProviderUnavailable] = []
        for attempt in range(self.config.retry_limit + 1):
            try:
                transport = self._single_transport or self._transport_factory(endpoint)
                return transport.rpc(method, params)
            except EVMProviderUnavailable as exc:
                errors.append(exc)
                if attempt < self.config.retry_limit:
                    time.sleep(min(2**attempt, 2))
        raise errors[-1] if errors else EVMProviderUnavailable("provider unavailable")

    def _ordered_endpoints(self) -> tuple[str, ...]:
        endpoints = self.config.rpc_endpoints or (self.config.rpc_endpoint,)
        if self._last_successful_provider in endpoints:
            return (
                self._last_successful_provider,
                *(endpoint for endpoint in endpoints if endpoint != self._last_successful_provider),
            )
        return endpoints


def endpoint_identity(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"


def _provider_state(
    config: ChainConfig,
    provider: str,
    endpoint: str,
    status: str,
    message: str,
    *,
    latency_ms: int | None = None,
    latest_block: int | None = None,
    chain_id_response: int | None = None,
    failure_type: str | None = None,
    cooldown_until: datetime | None = None,
    last_successful_request: datetime | None = None,
) -> ProviderState:
    return ProviderState(
        chain_id=config.chain_id,
        network=config.network,
        provider=provider,
        endpoint_identity=endpoint_identity(endpoint),
        status=status,
        message=message,
        latency_ms=latency_ms,
        latest_block=latest_block,
        chain_id_response=chain_id_response,
        failure_type=failure_type,
        cooldown_until=cooldown_until,
        last_successful_request=last_successful_request,
    )


def _failure_type(message: str) -> str:
    lowered = message.lower()
    if "403" in lowered or "forbidden" in lowered:
        return "forbidden"
    if "429" in lowered or "rate" in lowered:
        return "rate_limited"
    if "timeout" in lowered or "timed out" in lowered:
        return "timed_out"
    if "chain" in lowered:
        return "wrong_chain"
    return "unavailable"


def _status(failure_type: str) -> str:
    if failure_type in {"forbidden", "rate_limited", "timed_out", "wrong_chain", "invalid_response"}:
        return failure_type
    return "unavailable"


def _cooldown(now: datetime, failure_type: str) -> datetime | None:
    if failure_type == "rate_limited":
        return now + timedelta(minutes=10)
    if failure_type in {"forbidden", "timed_out"}:
        return now + timedelta(minutes=5)
    return None
