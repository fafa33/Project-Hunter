"""Verify one Sky buyback token-payment path from immutable Ethereum receipt logs.

A proven USDS mint/payment and SKY treasury receipt are transaction facts.
They do not alone establish revenue origin, cost basis, or allocation to burns.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC

USDS_TOKEN = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
BUYBACK_TRADER = "0x374d9c3d5134052bc558f432afa1df6575f07407"
BUYBACK_POOL = "0x2621cc0b3f3c079c1db0e80794aa24976f0b9e3c"
SKY_TREASURY = "0xbe8e3e3618f7474f8cb1d074a26affef007e98fb"
ZERO = "0x" + "0" * 40


@dataclass(frozen=True)
class SkyVerifiedBuybackPayment:
    tx_hash: str
    block_hash: str
    block_number: int
    usds_paid: Decimal
    sky_received: Decimal
    payment_log_index: int
    sky_receipt_log_index: int
    source_endpoint: str
    evidence_class: str = "onchain_buyback_payment_not_burn_cost_basis"

    def require_burn_cost_basis(self) -> None:
        raise ValueError("buyback payment not uniquely linked to a particular SKY burn allocation")


def attest_sky_buyback_payment(receipt: dict, *, source_endpoint: str) -> SkyVerifiedBuybackPayment:
    """Match exact USDS mint -> trader -> pool and SKY pool -> treasury in one receipt."""
    if receipt.get("status") != "0x1" or not isinstance(receipt.get("logs"), list):
        raise ValueError("successful receipt required")
    tx = receipt.get("transactionHash")
    block = receipt.get("blockHash")
    if not isinstance(tx, str) or len(tx) != 66 or not isinstance(block, str) or len(block) != 66:
        raise ValueError("transaction and block provenance required")
    try:
        number = int(receipt["blockNumber"], 16)
        transfers: list[tuple[str, str, str, Decimal, int]] = []
        for log in receipt["logs"]:
            token = log["address"].lower()
            if token not in {USDS_TOKEN, SKY_TOKEN}:
                continue
            topics = log["topics"]
            if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
                continue
            if (
                log["transactionHash"].lower() != tx.lower()
                or log["blockHash"].lower() != block.lower()
                or int(log["blockNumber"], 16) != number
            ):
                raise ValueError("payment log provenance mismatch")
            transfers.append((token, "0x" + topics[1][-40:].lower(), "0x" + topics[2][-40:].lower(), Decimal(int(log["data"], 16)) / Decimal(10**18), int(log["logIndex"], 16)))
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise ValueError("invalid onchain payment transfer") from exc

    def matching(token: str, sender: str, recipient: str) -> list[tuple[Decimal, int]]:
        return [(amt, idx) for t, s, r, amt, idx in transfers if (t, s, r) == (token, sender, recipient)]

    mints = matching(USDS_TOKEN, ZERO, BUYBACK_TRADER)
    payments = matching(USDS_TOKEN, BUYBACK_TRADER, BUYBACK_POOL)
    receipts = matching(SKY_TOKEN, BUYBACK_POOL, SKY_TREASURY)
    if len(mints) != 1 or len(payments) != 1 or len(receipts) != 1:
        raise ValueError("unique USDS mint, USDS payment and SKY treasury transfer required")
    mint, payment, sky = mints[0], payments[0], receipts[0]
    if mint[0] <= 0 or mint[0] != payment[0] or sky[0] <= 0 or not (mint[1] < payment[1] < sky[1]):
        raise ValueError("invalid buyback payment order or amounts")
    return SkyVerifiedBuybackPayment(tx.lower(), block.lower(), number, payment[0], sky[0], payment[1], sky[1], source_endpoint)
