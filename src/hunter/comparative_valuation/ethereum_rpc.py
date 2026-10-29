"""Read-only Ethereum RPC transport for bounded, verifiable SKY token logs.

Raw transfers are observations, never automatically classified as economic burns.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from hunter.value_capture.registry import ValueCaptureSourceRegistry

SKY_TOKEN = "0x56072c95faa701256059aa122697b133aded9279"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@dataclass(frozen=True)
class EthereumTransferObservation:
    tx_hash: str
    block_hash: str
    block_number: int
    log_index: int
    block_time: datetime
    from_address: str
    to_address: str
    raw_amount: int
    source_endpoint: str


class EthereumRpcReader:
    def __init__(self, endpoint: str | None = None) -> None:
        endpoint = endpoint or os.environ.get("HUNTER_ETHEREUM_RPC_URL", "https://rpc.flashbots.net")
        registry_path = Path(__file__).resolve().parents[3] / "configs" / "value_capture_sources.yaml"
        registry = ValueCaptureSourceRegistry.from_yaml(registry_path)
        registry.require("verified-onchain-value-capture").authorize(
            endpoint=endpoint,
            parser_version="evm-value-capture-v1",
            capability="evidence:onchain_observation",
        )
        parsed = urlparse(endpoint)
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("RPC endpoint must not contain credentials or query parameters")
        self.endpoint = endpoint
        self.source_endpoint = f"{parsed.scheme}://{parsed.hostname}"
        self.max_log_blocks = 10 if parsed.hostname == "eth-mainnet.g.alchemy.com" else 101

    def call(self, method: str, params: list[object]) -> object:
        request = Request(
            self.endpoint,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
        if not isinstance(result, dict) or "error" in result or "result" not in result:
            raise ValueError(f"Ethereum RPC failed: {result.get('error') if isinstance(result, dict) else 'invalid response'}")
        return result["result"]

    def finalized_block_number(self) -> int:
        """Use the finalized head, not the potentially inconsistent latest head."""
        if self.call("eth_chainId", []) != "0x1":
            raise ValueError("Ethereum mainnet chain ID mismatch")
        block = self.call("eth_getBlockByNumber", ["finalized", False])
        if not isinstance(block, dict) or not isinstance(block.get("number"), str):
            raise ValueError("missing finalized block")
        return int(block["number"], 16)

    def fetch_finalized_sky_transfers(self) -> tuple[EthereumTransferObservation, ...]:
        """Fetch a bounded finalized block; no economic classification implied."""
        number = self.finalized_block_number()
        return self.fetch_sky_transfers(from_block=number, to_block=number)

    def probe_log_range(self, *, block_number: int) -> bool:
        """Check actual log-query capability, not just reported finalized height."""
        if block_number < 0 or block_number > self.finalized_block_number():
            return False
        try:
            self.call("eth_getLogs", [{
                "fromBlock": hex(block_number),
                "toBlock": hex(block_number),
                "address": SKY_TOKEN,
                "topics": [TRANSFER_TOPIC],
            }])
        except (ValueError, OSError, TimeoutError):
            return False
        return True

    def scan_finalized_sky_transfers(
        self, *, from_block: int, to_block: int, max_batches: int = 10
    ) -> tuple[EthereumTransferObservation, ...]:
        """Bounded deterministic historical scan; never treats transfers as burns.

        Callers must persist a checkpoint and request successive ranges.  An
        incomplete range is rejected instead of silently truncating evidence.
        """
        if from_block < 0 or to_block < from_block or not 1 <= max_batches <= 100:
            raise ValueError("invalid historical scan bounds")
        count = (to_block - from_block) // self.max_log_blocks + 1
        if count > max_batches:
            raise ValueError("historical range exceeds batch budget")
        finalized = self.finalized_block_number()
        if to_block > finalized:
            raise ValueError("historical scan exceeds finalized Ethereum head")
        results: list[EthereumTransferObservation] = []
        seen: set[tuple[str, int]] = set()
        for start in range(from_block, to_block + 1, self.max_log_blocks):
            for event in self.fetch_sky_transfers(from_block=start, to_block=min(start + self.max_log_blocks - 1, to_block)):
                key = (event.tx_hash.lower(), event.log_index)
                if key in seen:
                    raise ValueError("duplicate Ethereum log across batches")
                seen.add(key)
                results.append(event)
        return tuple(sorted(results, key=lambda item: (item.block_number, item.log_index, item.tx_hash)))

    def fetch_sky_transfers(self, *, from_block: int, to_block: int) -> tuple[EthereumTransferObservation, ...]:
        if from_block < 0 or to_block < from_block or to_block - from_block >= self.max_log_blocks:
            raise ValueError("bounded Ethereum block range required")
        if self.call("eth_chainId", []) != "0x1":
            raise ValueError("Ethereum mainnet chain ID mismatch")
        finalized = self.finalized_block_number()
        if to_block > finalized:
            raise ValueError("requested block exceeds finalized Ethereum head")
        raw = self.call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": SKY_TOKEN,
            "topics": [TRANSFER_TOPIC],
        }])
        if not isinstance(raw, list):
            raise ValueError("invalid log response")
        events = []
        for log in raw:
            if log.get("removed") or log.get("address", "").lower() != SKY_TOKEN:
                raise ValueError("invalid or removed SKY log")
            topics = log["topics"]
            if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
                raise ValueError("invalid transfer topics")
            block_number = int(log["blockNumber"], 16)
            if not from_block <= block_number <= to_block:
                raise ValueError("log outside requested block range")
            block = self.call("eth_getBlockByNumber", [hex(block_number), False])
            if not isinstance(block, dict) or block.get("hash") != log["blockHash"]:
                raise ValueError("block hash provenance mismatch")
            events.append(EthereumTransferObservation(
                tx_hash=log["transactionHash"],
                block_hash=log["blockHash"],
                block_number=block_number,
                log_index=int(log["logIndex"], 16),
                block_time=datetime.fromtimestamp(int(block["timestamp"], 16), tz=UTC),
                from_address="0x" + topics[1][-40:],
                to_address="0x" + topics[2][-40:],
                raw_amount=int(log["data"], 16),
                source_endpoint=self.source_endpoint,
            ))
        return tuple(events)
