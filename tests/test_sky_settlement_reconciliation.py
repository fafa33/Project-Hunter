from decimal import Decimal

import pytest

from hunter.comparative_valuation.sky_index_attestation import SkyVerifiedTokenBurn
from hunter.comparative_valuation.sky_settlement_reconciliation import (
    reconcile_reported_sky_settlement,
)


def test_reconcile_reported_usd_never_promotes_to_economic_event():
    burn = SkyVerifiedTokenBurn(
        tx_hash="0x" + "a" * 64,
        block_hash="0x" + "b" * 64,
        block_number=25968583,
        log_index=328,
        sky_amount=Decimal("2860943.76"),
        source_digest="digest",
        source_endpoint="https://eth-mainnet.g.alchemy.com",
    )
    msc = {
        "slug": "msc-12",
        "exec_tx_hash": burn.tx_hash,
        "burn_usd": "186553.806539509538247255",
        "buyback_usd": "1457746.193460490461752745",
    }
    monthly = {
        "msc_slug": "msc-12",
        "burn_tx_hash": burn.tx_hash,
        "burned_amount": "2860943.760000000000000000",
        "burn_usd": "186553.806539509538247254714813920850249512",
    }
    result = reconcile_reported_sky_settlement(burn, settlement="msc-12", msc=msc, monthly=monthly)
    assert result.reported_burn_usd == Decimal(msc["burn_usd"])
    assert not hasattr(result, "amount_usd")
    with pytest.raises(ValueError, match="not independently verified"):
        result.require_verified_economic_attribution()
    for wrong in (
        {**monthly, "burn_tx_hash": "0x" + "f" * 64},
        {**monthly, "burned_amount": "1"},
        {**monthly, "burn_usd": "180000"},
    ):
        with pytest.raises(ValueError):
            reconcile_reported_sky_settlement(burn, settlement="msc-12", msc=msc, monthly=wrong)
