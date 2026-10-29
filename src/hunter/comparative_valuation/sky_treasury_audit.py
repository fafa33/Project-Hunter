"""Independent bounded SKY treasury transfer audit; no inferred burn cost or revenue."""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC
from hunter.comparative_valuation.sky_payment_provenance import BUYBACK_POOL, SKY_TREASURY, ZERO

START = 25968583
END = 26176859
TOPIC_TREASURY = "0x" + "0" * 24 + SKY_TREASURY[2:]
BURN_12 = "0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3"
BURN_13 = "0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275"


def rpc(method: str, params: list) -> object:
    endpoint = os.environ["HUNTER_ETHEREUM_RPC_URL"]
    req = Request(
        endpoint,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=40) as response:
        obj = json.load(response)
    if not isinstance(obj, dict) or obj.get("error") or "result" not in obj:
        raise ValueError("RPC failure: " + str(obj.get("error"))[:200])
    return obj["result"]


def token_balance(block: int) -> int:
    call = {"to": SKY_TOKEN, "data": "0x70a08231" + "0" * 24 + SKY_TREASURY[2:]}
    result = rpc("eth_call", [call, hex(block)])
    if not isinstance(result, str):
        raise ValueError("invalid balance response")
    return int(result, 16)


def audit_window(*, start: int, end: int, checkpoint: Path, chunk: int = 10, max_chunks: int = 200) -> dict:
    if not 0 <= start < end or not 1 <= chunk <= 10 or not 1 <= max_chunks <= 5000:
        raise ValueError("invalid bounded audit parameters")
    chain = rpc("eth_chainId", [])
    if chain != "0x1":
        raise ValueError("Ethereum mainnet required")
    finalized_block = rpc("eth_getBlockByNumber", ["finalized", False])
    if not isinstance(finalized_block, dict):
        raise ValueError("invalid finalized block response")
    finalized = int(finalized_block["number"], 16)
    if end > finalized:
        raise ValueError("non-finalized audit range")
    state: dict = (
        json.loads(checkpoint.read_text())
        if checkpoint.exists()
        else {
            "schema": "sky-treasury-audit/v1",
            "start": start,
            "end": end,
            "next": start + 1,
            "events": {},
            "evidence_class": "token_transfer_ledger_not_economic_cost_basis",
        }
    )
    if (state["start"], state["end"]) != (start, end):
        raise ValueError("checkpoint scope mismatch")
    completed = 0
    while state["next"] <= end and completed < max_chunks:
        lo = state["next"]
        hi = min(end, lo + chunk - 1)
        found = {}
        for topics in ([TRANSFER_TOPIC, TOPIC_TREASURY], [TRANSFER_TOPIC, None, TOPIC_TREASURY]):
            logs = rpc(
                "eth_getLogs", [{"fromBlock": hex(lo), "toBlock": hex(hi), "address": SKY_TOKEN, "topics": topics}]
            )
            if not isinstance(logs, list):
                raise ValueError("invalid log response")
            for item in logs:
                if (
                    item.get("removed")
                    or item.get("address", "").lower() != SKY_TOKEN
                    or len(item.get("topics", [])) != 3
                ):
                    raise ValueError("invalid SKY log")
                sender = "0x" + item["topics"][1][-40:].lower()
                recipient = "0x" + item["topics"][2][-40:].lower()
                if SKY_TREASURY not in (sender, recipient):
                    raise ValueError("non-treasury log")
                number = int(item["blockNumber"], 16)
                if not lo <= number <= hi:
                    raise ValueError("out of bounds log")
                key = item["transactionHash"].lower() + ":" + str(int(item["logIndex"], 16))
                found[key] = {
                    "tx": item["transactionHash"].lower(),
                    "block": number,
                    "block_hash": item["blockHash"].lower(),
                    "log_index": int(item["logIndex"], 16),
                    "from": sender,
                    "to": recipient,
                    "raw": str(int(item["data"], 16)),
                }
        for key, item in found.items():
            if key in state["events"] and state["events"][key] != item:
                raise ValueError("conflicting transfer identity")
            state["events"][key] = item
        state["next"] = hi + 1
        tmp = checkpoint.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n")
        os.replace(tmp, checkpoint)
        completed += 1
    events = list(state["events"].values())
    incoming = sum(int(e["raw"]) for e in events if e["to"] == SKY_TREASURY and e["from"] != SKY_TREASURY)
    outgoing = sum(int(e["raw"]) for e in events if e["from"] == SKY_TREASURY and e["to"] != SKY_TREASURY)
    burns = sum(int(e["raw"]) for e in events if e["from"] == SKY_TREASURY and e["to"] == ZERO)
    buybacks = sum(int(e["raw"]) for e in events if e["from"] == BUYBACK_POOL and e["to"] == SKY_TREASURY)
    summary = {
        "start": start,
        "end": end,
        "next": state["next"],
        "complete": state["next"] > end,
        "event_count": len(events),
        "incoming_sky": str(Decimal(incoming) / 10**18),
        "outgoing_sky": str(Decimal(outgoing) / 10**18),
        "burn_sky": str(Decimal(burns) / 10**18),
        "pool_incoming_sky": str(Decimal(buybacks) / 10**18),
        "other_incoming_sky": str(Decimal(incoming - buybacks) / 10**18),
        "other_outgoing_sky": str(Decimal(outgoing - burns) / 10**18),
    }
    if summary["complete"]:
        initial = token_balance(start)
        final = token_balance(end)
        summary["start_balance_sky"] = str(Decimal(initial) / 10**18)
        summary["end_balance_sky"] = str(Decimal(final) / 10**18)
        summary["residual_sky"] = str(Decimal(final - initial - incoming + outgoing) / 10**18)
        if final - initial != incoming - outgoing:
            raise ValueError("treasury balance fails complete transfer ledger reconciliation")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--start", type=int, default=START)
    parser.add_argument("--end", type=int, default=END)
    parser.add_argument("--max-chunks", type=int, default=200)
    args = parser.parse_args()
    print(
        json.dumps(
            audit_window(start=args.start, end=args.end, checkpoint=args.checkpoint, max_chunks=args.max_chunks),
            sort_keys=True,
        )
    )
