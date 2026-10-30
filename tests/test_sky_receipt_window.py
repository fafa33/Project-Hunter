from __future__ import annotations

from copy import deepcopy

import pytest

from hunter.comparative_valuation.sky_receipt_window import verified_spend_for_window


def fixture() -> dict:
    return {
        "schema": "sky-buyback-receipt-reconciliation/v1",
        "start_block": 10,
        "end_block": 20,
        "remaining_count": 0,
        "rejected_count": 0,
        "verified_count": 2,
        "verified": {
            "one": {"tx_hash": "0x" + "a" * 64, "block_hash": "0x" + "c" * 64, "block_number": 12, "usds_paid": "6000"},
            "two": {"tx_hash": "0x" + "b" * 64, "block_hash": "0x" + "d" * 64, "block_number": 20, "usds_paid": "800"},
        },
    }


def test_window_exact_boundaries() -> None:
    count, money = verified_spend_for_window(fixture(), after_block=12, through_block=20)
    assert count == 1
    assert str(money) == "800"


@pytest.mark.parametrize("modification", ["missing", "rejected", "count", "wrong_scope", "nan", "provenance"])
def test_corrupt_checkpoint_fails_closed(modification: str) -> None:
    data = deepcopy(fixture())
    start, end = 10, 20
    if modification == "missing":
        data["remaining_count"] = 1
    elif modification == "rejected":
        data["rejected_count"] = 1
    elif modification == "count":
        data["verified_count"] = 3
    elif modification == "wrong_scope":
        end = 21
    elif modification == "nan":
        data["verified"]["one"]["usds_paid"] = "NaN"
    elif modification == "provenance":
        data["verified"]["one"]["block_hash"] = ""
    with pytest.raises(ValueError):
        verified_spend_for_window(data, after_block=start, through_block=end)
