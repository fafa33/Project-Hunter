"""Bounded checkpointed SKY buyback receipt verification; no revenue promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

from hunter.comparative_valuation.sky_payment_provenance import attest_sky_buyback_payment

EVENTS_URL = "https://sky.data.blockanalitica.com/internal/buyback/events/"
START_BLOCK = 25968583
END_BLOCK = 26176859


def fetch_page(page: int) -> dict:
    if page < 1 or page > 500:
        raise ValueError("page outside bounded allowance")
    req = Request(
        f"{EVENTS_URL}?page={page}&limit=100",
        headers={"Accept": "application/json", "User-Agent": "Project-Hunter/1.0"},
    )
    with urlopen(req, timeout=20) as response:
        value = json.load(response)
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise ValueError("invalid source envelope")
    return value["data"]


def rpc_receipts(tx_hashes: list[str]) -> dict[str, dict]:
    """One bounded batch, exact transaction hashes, fail on any RPC error."""
    if not 1 <= len(tx_hashes) <= 10:
        raise ValueError("bounded batch of 1..10 required")
    endpoint = os.environ["HUNTER_ETHEREUM_RPC_URL"]
    request_body = [
        {"jsonrpc": "2.0", "id": i, "method": "eth_getTransactionReceipt", "params": [tx]}
        for i, tx in enumerate(tx_hashes)
    ]
    req = Request(
        endpoint, data=json.dumps(request_body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urlopen(req, timeout=30) as response:
        returned = json.load(response)
    if not isinstance(returned, list) or len(returned) != len(tx_hashes):
        raise ValueError("incomplete Ethereum receipt batch")
    results = {}
    for item in returned:
        if not isinstance(item, dict) or item.get("error") or type(item.get("id")) is not int:
            raise ValueError("RPC receipt batch error")
        i = item["id"]
        if i < 0 or i >= len(tx_hashes) or tx_hashes[i] in results:
            raise ValueError("duplicate or invalid RPC response ID")
        receipt = item.get("result")
        if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != tx_hashes[i].lower():
            raise ValueError("receipt hash mismatch")
        results[tx_hashes[i]] = receipt
    return results


def build_candidates(
    *, start_block: int = START_BLOCK, end_block: int = END_BLOCK, max_pages: int = 40
) -> tuple[list[dict], list[dict]]:
    """Require stable source pagination and contiguous event ordering in selected window."""
    events = []
    snapshots = []
    previous_last = None
    for page in range(1, max_pages + 1):
        payload = fetch_page(page)
        pagination = payload.get("pagination", {})
        rows = payload.get("results")
        if not isinstance(rows, list) or not rows or pagination.get("page") != page or pagination.get("limit") != 100:
            raise ValueError("incomplete event page")
        if len(rows) != 100 and page < pagination.get("pages", 0):
            raise ValueError("truncated event page")
        indices = [row.get("order_index") for row in rows]
        if any(not isinstance(index, str) for index in indices) or indices != sorted(indices, reverse=True):
            raise ValueError("non-monotonic event order")
        if previous_last is not None and indices[0] >= previous_last:
            raise ValueError("overlapping or changing indexed pagination")
        previous_last = indices[-1]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        snapshots.append({"page": page, "digest": digest, "first": indices[0], "last": indices[-1]})
        for row in rows:
            number = row.get("block_number")
            if type(number) is not int:
                raise ValueError("invalid event block")
            if start_block < number < end_block and row.get("event") == "Swap":
                events.append(row)
        if rows[-1]["block_number"] <= start_block:
            break
    else:
        raise ValueError("page budget exhausted before history complete")
    keys = [(row["tx_hash"].lower(), row["order_index"]) for row in events]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate swap events")
    return sorted(events, key=lambda r: (r["block_number"], r["order_index"])), snapshots


def update_checkpoint(path: Path, state: dict) -> None:
    """Atomic replacement with deterministic digest and no private RPC endpoint."""
    serialized = json.dumps(state, sort_keys=True, indent=2) + "\n"
    temp = path.with_suffix(".tmp")
    temp.write_text(serialized)
    os.replace(temp, path)


def execute(*, path: Path, max_batches: int = 5, start_block: int = START_BLOCK, end_block: int = END_BLOCK) -> dict:
    if not 1 <= max_batches <= 500:
        raise ValueError("bounded execution budget required")
    candidates, pages = build_candidates(start_block=start_block, end_block=end_block)
    if path.exists():
        state = json.loads(path.read_text())
        if (
            state.get("start_block") != start_block
            or state.get("end_block") != end_block
            or state.get("pages") != pages
        ):
            raise ValueError("source changed since checkpoint; refusing stale continuation")
    else:
        state = {
            "schema": "sky-buyback-receipt-reconciliation/v1",
            "start_block": start_block,
            "end_block": end_block,
            "pages": pages,
            "verified": {},
            "rejected": {},
            "total_candidates": len(candidates),
            "evidence_class": "onchain_payment_only_revenue_and_burn_allocation_unproven",
        }
    batches = 0
    for offset in range(0, len(candidates), 10):
        portion = candidates[offset : offset + 10]
        unseen = [
            row
            for row in portion
            if row["order_index"] not in state["verified"] and row["order_index"] not in state["rejected"]
        ]
        if not unseen:
            continue
        if batches >= max_batches:
            break
        hashes = list(dict.fromkeys(row["tx_hash"].lower() for row in unseen))
        try:
            receipt_by_hash = rpc_receipts(hashes)
        except (ValueError, OSError, TimeoutError) as exc:
            state["last_error"] = type(exc).__name__ + ": " + str(exc)[:160]
            update_checkpoint(path, state)
            break
        for row in unseen:
            key = row["order_index"]
            try:
                proof = attest_sky_buyback_payment(
                    receipt_by_hash[row["tx_hash"].lower()], source_endpoint="ethereum-mainnet"
                )
                if (
                    proof.block_number != row["block_number"]
                    or proof.usds_paid != Decimal(row["usds_amount"])
                    or proof.sky_received != Decimal(row["sky_amount"])
                ):
                    raise ValueError("indexed-to-receipt mismatch")
                state["verified"][key] = {
                    "tx_hash": proof.tx_hash,
                    "block_hash": proof.block_hash,
                    "block_number": proof.block_number,
                    "usds_paid": str(proof.usds_paid),
                    "sky_received": str(proof.sky_received),
                    "payment_log_index": proof.payment_log_index,
                    "sky_receipt_log_index": proof.sky_receipt_log_index,
                }
            except (ValueError, KeyError, TypeError) as exc:
                state["rejected"][key] = {"tx_hash": row["tx_hash"], "reason": str(exc)[:180]}
        batches += 1
        state.pop("last_error", None)
        update_checkpoint(path, state)
    state["verified_count"] = len(state["verified"])
    state["rejected_count"] = len(state["rejected"])
    state["remaining_count"] = len(candidates) - state["verified_count"] - state["rejected_count"]
    state["verified_usds"] = str(sum((Decimal(item["usds_paid"]) for item in state["verified"].values()), Decimal(0)))
    state["verified_sky"] = str(sum((Decimal(item["sky_received"]) for item in state["verified"].values()), Decimal(0)))
    update_checkpoint(path, state)
    return {
        k: state[k]
        for k in (
            "total_candidates",
            "verified_count",
            "rejected_count",
            "remaining_count",
            "verified_usds",
            "verified_sky",
        )
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--max-batches", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(execute(path=args.checkpoint, max_batches=args.max_batches), sort_keys=True))
