from unittest.mock import Mock

import pytest

from hunter.comparative_valuation.sky_burn_index import parse_burn_index
from hunter.comparative_valuation.sky_index_attestation import attest_sky_spell_inclusion

HASH = "0x" + "a" * 64
BLOCK_HASH = "0x" + "b" * 64
SPELL = "0x" + "c" * 40


def candidate():
    import json

    return parse_burn_index(
        json.dumps(
            {
                "data": [
                    {
                        "datetime": "2026-10-12T14:01:47+00:00",
                        "block_number": 26176859,
                        "tx_hash": HASH,
                        "amount": "7372288",
                        "month": "2026-09-01",
                        "msc_slug": "msc-13",
                    }
                ]
            }
        ).encode()
    )[0]


def rpc(*, tx_to=SPELL, selector="0x96d373e5", receipt=None):
    from datetime import UTC, datetime

    block = {
        "number": hex(26176859),
        "hash": BLOCK_HASH,
        "timestamp": hex(int(datetime(2026, 10, 12, 14, 1, 47, tzinfo=UTC).timestamp())),
        "transactions": [{"hash": HASH, "blockHash": BLOCK_HASH, "to": tx_to, "input": selector}],
    }
    r = Mock()
    r.finalized_block_number.return_value = 26293133
    r.call.side_effect = lambda m, p: block if m == "eth_getBlockByNumber" else receipt
    return r


def test_inclusion_does_not_claim_economic_burn():
    result = attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=rpc())
    assert result.receipt_status == "unavailable"
    assert result.evidence_class == "verified_spell_inclusion_only"
    assert not hasattr(result, "amount_usd")
    assert not hasattr(result, "verified_attribution")


def test_inclusion_cannot_be_promoted_to_economic_burn():
    inclusion = attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=rpc())
    with pytest.raises(ValueError, match="economic attribution unavailable"):
        inclusion.require_verified_economic_burn()


def test_successful_receipt_still_cannot_prove_economic_burn():
    receipt = {"transactionHash": HASH, "blockHash": BLOCK_HASH, "status": "0x1"}
    inclusion = attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=rpc(receipt=receipt))
    with pytest.raises(ValueError, match="independently verified burn"):
        inclusion.require_verified_economic_burn()


def test_verified_burn_requires_matching_sky_to_zero_receipt_log():
    from decimal import Decimal

    from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC
    from hunter.comparative_valuation.sky_index_attestation import attest_indexed_sky_token_burn

    log = {
        "address": SKY_TOKEN,
        "topics": [TRANSFER_TOPIC, "0x" + "1" * 64, "0x" + "0" * 64],
        "data": hex(7372288 * 10**18).replace("0x", "0x" + "0" * (64 - len(hex(7372288 * 10**18)[2:]))),
        "transactionHash": HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": hex(26176859),
        "logIndex": "0x2",
    }
    receipt = {
        "transactionHash": HASH,
        "blockHash": BLOCK_HASH,
        "status": "0x1",
        "logs": [log],
    }
    reader = rpc(receipt=receipt)
    reader.source_endpoint = "https://eth-mainnet.g.alchemy.com"
    result = attest_indexed_sky_token_burn(candidate(), spell_address=SPELL, rpc=reader)
    assert result.sky_amount == Decimal("7372288")
    assert result.log_index == 2
    assert result.evidence_class == "verified_sky_token_burn_only"
    assert not hasattr(result, "amount_usd")

    for bad in (
        {**log, "topics": [TRANSFER_TOPIC, "0x" + "1" * 64, "0x" + "2" * 64]},
        {**log, "data": "0x" + "0" * 63 + "1"},
        {**log, "blockHash": "0x" + "f" * 64},
    ):
        corrupted = rpc(receipt={**receipt, "logs": [bad]})
        corrupted.source_endpoint = "https://eth-mainnet.g.alchemy.com"
        with pytest.raises(ValueError):
            attest_indexed_sky_token_burn(candidate(), spell_address=SPELL, rpc=corrupted)


def test_verified_burn_rejects_missing_receipt_and_multiple_burns():
    from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC
    from hunter.comparative_valuation.sky_index_attestation import attest_indexed_sky_token_burn

    reader = rpc()
    with pytest.raises(ValueError, match="successful matching receipt"):
        attest_indexed_sky_token_burn(candidate(), spell_address=SPELL, rpc=reader)
    log = {
        "address": SKY_TOKEN,
        "topics": [TRANSFER_TOPIC, "0x" + "1" * 64, "0x" + "0" * 64],
        "data": "0x" + "0" * 63 + "1",
        "transactionHash": HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": hex(26176859),
        "logIndex": "0x2",
    }
    reader = rpc(
        receipt={
            "transactionHash": HASH,
            "blockHash": BLOCK_HASH,
            "status": "0x1",
            "logs": [log, {**log, "logIndex": "0x3"}],
        }
    )
    with pytest.raises(ValueError, match="exactly one"):
        attest_indexed_sky_token_burn(candidate(), spell_address=SPELL, rpc=reader)


def test_successful_receipt_is_linked():
    r = rpc(receipt={"transactionHash": HASH, "blockHash": BLOCK_HASH, "status": "0x1"})
    assert attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=r).receipt_status == "success"


@pytest.mark.parametrize(
    "change",
    [
        {"tx_to": "0x" + "f" * 40},
        {"selector": "0x00000000"},
        {"receipt": {"transactionHash": HASH, "blockHash": BLOCK_HASH, "status": "0x0"}},
    ],
)
def test_rejects_wrong_destination_selector_or_failed_receipt(change):
    with pytest.raises(ValueError):
        attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=rpc(**change))


def test_rejects_unfinalized_index():
    r = rpc()
    r.finalized_block_number.return_value = 26000000
    with pytest.raises(ValueError, match="finalized"):
        attest_sky_spell_inclusion(candidate(), spell_address=SPELL, rpc=r)
