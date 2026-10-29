from decimal import Decimal as D

import pytest

from hunter.comparative_valuation.sky_historical_treasury import reconcile_treasury_window


def test_treasury_residual_keeps_economic_claim_closed():
    result = reconcile_treasury_window(
        start_block=25968583,
        end_block=26176858,
        start_balance_sky=D("30266702.841839996572247763"),
        end_balance_sky=D("27996757.438455110116489638"),
        indexed_swap_sky=D("42029405.165221595025723356"),
        independently_verified_swap_sky=D("94974.404003267058861541"),
        indexed_swap_count=954,
        verified_swap_count=2,
    )
    assert result.unexplained_other_net_sky == D("-44299350.568606481481481481")
    with pytest.raises(ValueError, match="not exhaustively"):
        result.require_complete_economic_attribution()


def test_no_economic_promotion_even_if_balance_and_swaps_match():
    result = reconcile_treasury_window(
        start_block=1,
        end_block=2,
        start_balance_sky=D(1),
        end_balance_sky=D(3),
        indexed_swap_sky=D(2),
        independently_verified_swap_sky=D(2),
        indexed_swap_count=1,
        verified_swap_count=1,
    )
    with pytest.raises(ValueError, match="revenue source"):
        result.require_complete_economic_attribution()


@pytest.mark.parametrize("bad", [D("NaN"), D("-1"), D("Infinity")])
def test_reject_invalid_balances(bad):
    with pytest.raises(ValueError):
        reconcile_treasury_window(
            start_block=1,
            end_block=2,
            start_balance_sky=bad,
            end_balance_sky=D(1),
            indexed_swap_sky=D(0),
            independently_verified_swap_sky=D(0),
            indexed_swap_count=0,
            verified_swap_count=0,
        )
