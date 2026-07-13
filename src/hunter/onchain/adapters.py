from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from hunter.onchain.models import ChainConfig, ProviderState


class EVMTransport(Protocol):
    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        raise NotImplementedError


class EVMProviderUnavailable(Exception):
    pass


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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise EVMProviderUnavailable(str(exc)) from exc
        if raw.get("error"):
            raise EVMProviderUnavailable(str(raw["error"]))
        return raw.get("result")


class EVMJsonRpcAdapter:
    def __init__(self, config: ChainConfig, *, transport: EVMTransport | None = None) -> None:
        self.config = config
        self.transport = transport or UrlLibEVMTransport(config.rpc_endpoint, config.timeout_seconds)

    @property
    def provider(self) -> str:
        return "evm-json-rpc"

    @property
    def endpoint_identity(self) -> str:
        parsed = urllib.parse.urlparse(self.config.rpc_endpoint)
        return f"{parsed.scheme}://{parsed.netloc}"

    def state(self) -> ProviderState:
        try:
            self.latest_finalized_block()
        except EVMProviderUnavailable as exc:
            return ProviderState(
                self.config.chain_id,
                self.config.network,
                self.provider,
                self.endpoint_identity,
                "unavailable",
                str(exc),
            )
        return ProviderState(
            self.config.chain_id,
            self.config.network,
            self.provider,
            self.endpoint_identity,
            "available",
            "latest finalized block available",
        )

    def latest_finalized_block(self) -> int:
        latest = self._rpc("eth_blockNumber", ())
        if not isinstance(latest, str):
            raise EVMProviderUnavailable("eth_blockNumber returned invalid payload")
        return max(int(latest, 16) - self.config.finality_depth, 0)

    def block(self, number: int) -> dict[str, Any]:
        result = self._rpc("eth_getBlockByNumber", (hex(number), False))
        if not isinstance(result, dict):
            raise EVMProviderUnavailable("eth_getBlockByNumber returned no block")
        return result

    def block_timestamp(self, number: int) -> datetime:
        block = self.block(number)
        timestamp = int(str(block["timestamp"]), 16)
        return datetime.fromtimestamp(timestamp, tz=UTC)

    def native_balance(self, address: str, block_number: int) -> int:
        result = self._rpc("eth_getBalance", (address, hex(block_number)))
        if not isinstance(result, str):
            raise EVMProviderUnavailable("eth_getBalance returned invalid payload")
        return int(result, 16)

    def logs(
        self, *, address: str | None, from_block: int, to_block: int, topics: tuple[str, ...]
    ) -> tuple[dict[str, Any], ...]:
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
            raise EVMProviderUnavailable("eth_getLogs returned invalid payload")
        return tuple(item for item in result if isinstance(item, dict))

    def _rpc(self, method: str, params: tuple[object, ...]) -> object:
        errors: list[Exception] = []
        for attempt in range(self.config.retry_limit + 1):
            try:
                return self.transport.rpc(method, params)
            except EVMProviderUnavailable as exc:
                errors.append(exc)
                if attempt < self.config.retry_limit:
                    time.sleep(min(2**attempt, 2))
        raise EVMProviderUnavailable(str(errors[-1]) if errors else "provider unavailable")
