"""Point checks linking indexed Sky settlements to finalized Ethereum spell transactions.

Transaction inclusion does not demonstrate an actual SKY burn or attributable USD value.
This deliberately produces a settlement-inclusion observation, not SkyEconomicEvent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC, EthereumRpcReader
from hunter.comparative_valuation.sky_burn_index import SkyIndexedBurn

EXECUTE_SELECTOR = "0x96d373e5"


@dataclass(frozen=True)
class SkySpellInclusion:
    tx_hash: str
    block_hash: str
    block_number: int
    block_time: datetime
    spell_address: str
    sky_amount_indexed: str
    source_digest: str
    receipt_status: str
    evidence_class: str = "verified_spell_inclusion_only"

    def require_verified_economic_burn(self) -> None:
        """Refuse economic promotion of a governance call without burn and USD proof.

        Settlement inclusion, even with a successful receipt, does not attest to
        SKY supply destruction, funding provenance, or attributable USD flows.
        """
        raise ValueError(
            "SKY economic attribution unavailable: independently verified burn "
            "logs, supply effect, USD settlement, and point-in-time coverage required"
        )


SKY_BURN_SINK = "0x" + "0" * 40
SKY_TOKEN_DECIMALS = 18


@dataclass(frozen=True)
class SkyVerifiedTokenBurn:
    """Verified SKY destruction only; NOT verified attributable USD economics."""

    tx_hash: str
    block_hash: str
    block_number: int
    log_index: int
    sky_amount: Decimal
    source_digest: str
    source_endpoint: str
    evidence_class: str = "verified_sky_token_burn_only"


def attest_indexed_sky_token_burn(
    indexed: SkyIndexedBurn, *, spell_address: str, rpc: EthereumRpcReader
) -> SkyVerifiedTokenBurn:
    """Require successful finalized spell and exact SKY-to-zero receipt log."""
    inclusion = attest_sky_spell_inclusion(indexed, spell_address=spell_address, rpc=rpc)
    if inclusion.receipt_status != "success":
        raise ValueError("successful matching receipt required for SKY burn")
    receipt = rpc.call("eth_getTransactionReceipt", [indexed.tx_hash])
    if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != indexed.tx_hash:
        raise ValueError("missing or inconsistent SKY burn receipt")
    if receipt.get("blockHash") != inclusion.block_hash or receipt.get("status") != "0x1":
        raise ValueError("SKY burn receipt block/status mismatch")
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        raise ValueError("SKY burn receipt logs unavailable")
    burns = []
    for log in logs:
        if not isinstance(log, dict) or str(log.get("address", "")).lower() != SKY_TOKEN:
            continue
        topics = log.get("topics")
        if (
            not isinstance(topics, list)
            or len(topics) != 3
            or not all(isinstance(t, str) and t.startswith("0x") and len(t) == 66 for t in topics)
        ):
            raise ValueError("malformed SKY receipt log topics")
        if topics[0].lower() != TRANSFER_TOPIC:
            continue
        if int(topics[2], 16) != 0:
            continue
        if (
            log.get("transactionHash", "").lower() != indexed.tx_hash
            or log.get("blockHash") != inclusion.block_hash
            or int(log.get("blockNumber", "-1"), 16) != indexed.block_number
        ):
            raise ValueError("SKY burn log provenance mismatch")
        raw = log.get("data")
        if not isinstance(raw, str) or not raw.startswith("0x") or len(raw) != 66:
            raise ValueError("invalid SKY burn amount")
        value = int(raw, 16)
        if value <= 0:
            raise ValueError("nonpositive SKY burn log")
        burns.append((int(log["logIndex"], 16), value))
    if len(burns) != 1:
        raise ValueError("exactly one SKY token burn log required")
    log_index, raw_value = burns[0]
    amount = Decimal(raw_value) / (Decimal(10) ** SKY_TOKEN_DECIMALS)
    if amount != indexed.sky_amount:
        raise ValueError("indexed SKY amount disagrees with onchain burn")
    return SkyVerifiedTokenBurn(
        tx_hash=indexed.tx_hash,
        block_hash=inclusion.block_hash,
        block_number=indexed.block_number,
        log_index=log_index,
        sky_amount=amount,
        source_digest=indexed.response_digest,
        source_endpoint=rpc.source_endpoint,
    )


def attest_sky_spell_inclusion(
    indexed: SkyIndexedBurn, *, spell_address: str, rpc: EthereumRpcReader
) -> SkySpellInclusion:
    """Verify exact-head inclusion without promoting aggregator claims to onchain economics."""
    if not spell_address.startswith("0x") or len(spell_address) != 42:
        raise ValueError("invalid expected spell address")
    if indexed.block_number > rpc.finalized_block_number():
        raise ValueError("index entry beyond finalized Ethereum head")
    block = rpc.call("eth_getBlockByNumber", [hex(indexed.block_number), True])
    if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
        raise ValueError("Ethereum full block unavailable")
    if int(block["number"], 16) != indexed.block_number:
        raise ValueError("indexed block number mismatch")
    block_time = datetime.fromtimestamp(int(block["timestamp"], 16), tz=UTC)
    if block_time != indexed.block_time.astimezone(UTC):
        raise ValueError("indexed block time mismatch")
    matching = [
        tx for tx in block["transactions"] if isinstance(tx, dict) and tx.get("hash", "").lower() == indexed.tx_hash
    ]
    if len(matching) != 1:
        raise ValueError("indexed spell transaction not uniquely included in block")
    tx = matching[0]
    if (
        tx.get("blockHash") != block.get("hash")
        or str(tx.get("to", "")).lower() != spell_address.lower()
        or tx.get("input") != EXECUTE_SELECTOR
    ):
        raise ValueError("spell address, execute selector or block hash mismatch")
    receipt = rpc.call("eth_getTransactionReceipt", [indexed.tx_hash])
    if receipt is not None:
        if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != indexed.tx_hash:
            raise ValueError("inconsistent Ethereum transaction receipt")
        if receipt.get("blockHash") != block["hash"] or receipt.get("status") != "0x1":
            raise ValueError("receipt not successful or not matching finalized block")
        receipt_status = "success"
    else:
        receipt_status = "unavailable"
    return SkySpellInclusion(
        tx_hash=indexed.tx_hash,
        block_hash=block["hash"],
        block_number=indexed.block_number,
        block_time=block_time,
        spell_address=spell_address.lower(),
        sky_amount_indexed=str(indexed.sky_amount),
        source_digest=indexed.response_digest,
        receipt_status=receipt_status,
    )
