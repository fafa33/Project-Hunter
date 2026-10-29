"""Verify exact historical spend windows from the immutable receipt checkpoint.

An indexed swap is only eligible after the full receipt attestation has
already passed. This source must not infer revenue origin from USDS minting.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def verified_spend_for_window(checkpoint: dict, *, after_block: int, through_block: int) -> tuple[int, Decimal]:
    if not 0 <= after_block < through_block:
        raise ValueError("invalid window bounds")
    if checkpoint.get("schema") != "sky-buyback-receipt-reconciliation/v1":
        raise ValueError("not a verified receipt checkpoint")
    if checkpoint.get("remaining_count") != 0 or checkpoint.get("rejected_count") != 0:
        raise ValueError("buyback receipt verification incomplete")
    rows = checkpoint.get("verified")
    if not isinstance(rows, dict) or len(rows) != checkpoint.get("verified_count"):
        raise ValueError("receipt checkpoint count mismatch")
    if not checkpoint.get("start_block", -1) <= after_block < through_block <= checkpoint.get("end_block", -1):
        raise ValueError("requested window outside verified scope")
    total = Decimal(0)
    count = 0
    for item in rows.values():
        if not isinstance(item, dict):
            raise ValueError("invalid receipt item")
        tx, block_hash = item.get("tx_hash"), item.get("block_hash")
        if not isinstance(tx, str) or len(tx) != 66 or not isinstance(block_hash, str) or len(block_hash) != 66:
            raise ValueError("receipt provenance missing")
        number = item.get("block_number")
        if type(number) is not int:
            raise ValueError("invalid block number")
        try:
            money = Decimal(item["usds_paid"])
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid USDS payment") from exc
        if not money.is_finite() or money <= 0:
            raise ValueError("untrusted USDS payment")
        if after_block < number <= through_block:
            count += 1
            total += money
    return count, total
