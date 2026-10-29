"""Bounded Alchemy discovery, receipt-backed exact SKY treasury audit."""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from hunter.comparative_valuation.sky_checkpoint_security import checkpoint_path, read_checkpoint, write_checkpoint
from hunter.comparative_valuation.sky_treasury_audit import (
    BUYBACK_POOL,
    END,
    SKY_TOKEN,
    SKY_TREASURY,
    START,
    ZERO,
    rpc,
    token_balance,
)

SCALE = 10**18


def fetch_transfers(start: int, end: int, direction: str) -> list[dict]:
    if direction not in ("fromAddress", "toAddress"):
        raise ValueError("invalid direction")
    params = {
        "fromBlock": hex(start + 1),
        "toBlock": hex(end),
        direction: SKY_TREASURY,
        "contractAddresses": [SKY_TOKEN],
        "category": ["erc20"],
        "withMetadata": False,
        "maxCount": "0x3e8",
        "order": "asc",
    }
    result: list[dict] = []
    seen_pages: set[str] = set()
    for _ in range(10):
        page = rpc("alchemy_getAssetTransfers", [params])
        if not isinstance(page, dict) or not isinstance(page.get("transfers"), list):
            raise ValueError("invalid transfer page")
        result.extend(page["transfers"])
        key = page.get("pageKey")
        if not key:
            return result
        if key in seen_pages:
            raise ValueError("repeated page key")
        seen_pages.add(key)
        params["pageKey"] = key
    raise ValueError("transfer page budget exceeded")


def normalize(rows: list[dict], start: int, end: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        tx = row["hash"].lower()
        ident = row["uniqueId"].split(":")
        if len(ident) != 3 or ident[0].lower() != tx or ident[1] != "log":
            raise ValueError("invalid discovery identity")
        idx = int(ident[2])
        number = int(row["blockNum"], 16)
        sender, to = row["from"].lower(), row["to"].lower()
        contract = row.get("rawContract") or {}
        raw = contract.get("value")
        if not start < number <= end or not raw or contract.get("address", "").lower() != SKY_TOKEN:
            raise ValueError("untrusted or out-of-window transfer")
        if contract.get("decimal") != "0x12" or SKY_TREASURY not in (sender, to):
            raise ValueError("invalid SKY discovery entry")
        key = tx + ":" + str(idx)
        item = {"tx": tx, "block": number, "log_index": idx, "from": sender, "to": to, "raw": str(int(raw, 16))}
        if key in found and found[key] != item:
            raise ValueError("conflicting transfer discovery")
        found[key] = item
    return found


def receipt_log_match(row: dict, receipt: dict) -> dict:
    from hunter.comparative_valuation.ethereum_rpc import TRANSFER_TOPIC

    if int(receipt["blockNumber"], 16) != row["block"] or receipt["transactionHash"].lower() != row["tx"]:
        raise ValueError("receipt identity mismatch")
    if receipt.get("status") != "0x1":
        raise ValueError("failed receipt")
    logs = [x for x in receipt["logs"] if int(x["logIndex"], 16) == row["log_index"]]
    if len(logs) != 1:
        raise ValueError("missing indexed log")
    log = logs[0]
    if log["address"].lower() != SKY_TOKEN or len(log["topics"]) != 3 or log["topics"][0].lower() != TRANSFER_TOPIC:
        raise ValueError("not canonical ERC20 Transfer")
    sender = "0x" + log["topics"][1][-40:].lower()
    to = "0x" + log["topics"][2][-40:].lower()
    if (sender, to, int(log["data"], 16)) != (row["from"], row["to"], int(row["raw"])):
        raise ValueError("discovered transfer differs from receipt")
    return {**row, "block_hash": receipt["blockHash"].lower()}


def audit(start: int, end: int, checkpoint: Path, max_batches: int = 10) -> dict:
    checkpoint = checkpoint_path(checkpoint)
    if start < 0 or end <= start or not 1 <= max_batches <= 100:
        raise ValueError("invalid audit bounds")
    if rpc("eth_chainId", []) != "0x1":
        raise ValueError("Ethereum mainnet required")
    finalized_block = rpc("eth_getBlockByNumber", ["finalized", False])
    if not isinstance(finalized_block, dict):
        raise ValueError("invalid finalized block response")
    if int(finalized_block["number"], 16) < end:
        raise ValueError("unfinalized audit range")
    if checkpoint.exists():
        state = read_checkpoint(checkpoint)
        if (state["start"], state["end"]) != (start, end):
            raise ValueError("checkpoint scope mismatch")
    else:
        outgoing = fetch_transfers(start, end, "fromAddress")
        incoming = fetch_transfers(start, end, "toAddress")
        discovery = normalize(outgoing + incoming, start, end)
        state = {
            "schema": "sky-fast-treasury/v1",
            "start": start,
            "end": end,
            "candidate_count": len(discovery),
            "candidates": discovery,
            "verified": {},
            "evidence_class": "token_receipt_ledger_not_revenue_or_burn_cost",
        }
        save(checkpoint, state)
    keys = sorted(set(state["candidates"]) - set(state["verified"]))
    for begin in range(0, min(len(keys), max_batches * 10), 10):
        batch = keys[begin : begin + 10]
        receipts = rpc_batch_receipts(sorted({state["candidates"][key]["tx"] for key in batch}))
        for key in batch:
            row = state["candidates"][key]
            state["verified"][key] = receipt_log_match(row, receipts[row["tx"]])
        save(checkpoint, state)
    complete = len(state["verified"]) == len(state["candidates"])
    events = list(state["verified"].values())
    incoming_raw = sum(int(x["raw"]) for x in events if x["to"] == SKY_TREASURY and x["from"] != SKY_TREASURY)
    outgoing_raw = sum(int(x["raw"]) for x in events if x["from"] == SKY_TREASURY and x["to"] != SKY_TREASURY)
    burn_raw = sum(int(x["raw"]) for x in events if x["from"] == SKY_TREASURY and x["to"] == ZERO)
    pool_raw = sum(int(x["raw"]) for x in events if x["from"] == BUYBACK_POOL and x["to"] == SKY_TREASURY)

    def fmt(n: int) -> str:
        return str(Decimal(n) / SCALE)

    result = {
        "complete": complete,
        "candidates": len(state["candidates"]),
        "verified": len(events),
        "incoming_sky": fmt(incoming_raw),
        "outgoing_sky": fmt(outgoing_raw),
        "burn_sky": fmt(burn_raw),
        "pool_buyback_sky": fmt(pool_raw),
        "other_incoming_sky": fmt(incoming_raw - pool_raw),
        "other_outgoing_sky": fmt(outgoing_raw - burn_raw),
    }
    if complete:
        before, after = token_balance(start), token_balance(end)
        result.update(
            {
                "start_balance_sky": fmt(before),
                "end_balance_sky": fmt(after),
                "residual_sky": fmt(after - before - incoming_raw + outgoing_raw),
            }
        )
        if after - before != incoming_raw - outgoing_raw:
            raise ValueError("treasury balance residual nonzero; discovery incomplete")
    return result


def rpc_batch_receipts(hashes: list[str]) -> dict[str, dict]:
    import urllib.request

    endpoint = os.environ["HUNTER_ETHEREUM_RPC_URL"]
    body = [
        {"jsonrpc": "2.0", "id": i, "method": "eth_getTransactionReceipt", "params": [h]} for i, h in enumerate(hashes)
    ]
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as response:
        data = json.load(response)
    if not isinstance(data, list) or len(data) != len(hashes):
        raise ValueError("incomplete receipt batch")
    found = {}
    for item in data:
        if item.get("error") or not isinstance(item.get("id"), int) or item["id"] not in range(len(hashes)):
            raise ValueError("receipt RPC error")
        receipt = item.get("result")
        if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != hashes[item["id"]]:
            raise ValueError("missing or conflicting receipt")
        found[hashes[item["id"]]] = receipt
    if len(found) != len(hashes):
        raise ValueError("duplicate receipt")
    return found


def save(path: Path, state: dict) -> None:
    write_checkpoint(path, state)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--checkpoint", type=Path, required=True)
    cli.add_argument("--start", type=int, default=START)
    cli.add_argument("--end", type=int, default=END)
    cli.add_argument("--max-batches", type=int, default=10)
    opt = cli.parse_args()
    print(json.dumps(audit(opt.start, opt.end, opt.checkpoint, opt.max_batches), sort_keys=True))
