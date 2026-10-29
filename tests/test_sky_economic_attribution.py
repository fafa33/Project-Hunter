from __future__ import annotations

from decimal import Decimal as D

import pytest

from hunter.comparative_valuation.sky_economic_attribution import (
    BURNS,
    MONTHS,
    digest_record,
    reconcile_month,
)


def payload(settlement: str) -> tuple[dict, dict]:
    tx, amount, month = BURNS[settlement]
    row = MONTHS[settlement]
    msc = {
        "slug": settlement,
        "exec_tx_hash": tx,
        "net_revenue": row["net_revenue"],
        "buyback_usd": row["published_buyback_usd"],
        "burn_usd": row["reported_burn_usd"],
    }
    monthly = {
        "msc_slug": settlement,
        "burn_tx_hash": tx,
        "month": month + "-01",
        "burned_amount": amount,
        "burn_usd": row["reported_burn_usd"],
    }
    return msc, monthly


@pytest.mark.parametrize("settlement", ["msc-12", "msc-13"])
def test_attribution_does_not_promote_published_revenue_or_spend(settlement: str) -> None:
    tx, amount, _ = BURNS[settlement]
    msc, monthly = payload(settlement)
    report = reconcile_month(
        settlement=settlement,
        receipt_burn_tx=tx,
        receipt_burn_sky=D(amount),
        official_msc=msc,
        official_monthly=monthly,
        independently_verified_spend_usds=D(MONTHS[settlement]["window_flapper_usds"]),
    )
    assert not report.revenue_to_payment_proven
    assert not report.burn_allocation_proven
    assert report.revenue_source_class.startswith("pinned_issuer_")
    assert digest_record(report) == digest_record(report)
    with pytest.raises(ValueError, match="not independently proven"):
        report.require_independent_economic_proof()


def test_august_reported_cost_differs_from_exact_share() -> None:
    msc, monthly = payload("msc-12")
    report = reconcile_month(
        settlement="msc-12",
        receipt_burn_tx=BURNS["msc-12"][0],
        receipt_burn_sky=D("2860943.76"),
        official_msc=msc,
        official_monthly=monthly,
        independently_verified_spend_usds=D("1026300"),
    )
    assert report.rule_allocated_spend_usds == "186600.0000000000000000000000"
    assert report.reconciliation_gap_usds == "-46.1934604904617527450000"
    assert not report.costs_match_within_tolerance


@pytest.mark.parametrize("mutation", ["tx", "month", "revenue", "spend", "amount"])
def test_economic_mismatch_fails_closed(mutation: str) -> None:
    msc, monthly = payload("msc-13")
    tx = BURNS["msc-13"][0]
    month_spend = D("2791800")
    burn = D("7372288")
    if mutation == "tx":
        tx = "0x" + "a" * 64
    elif mutation == "month":
        monthly["month"] = "2026-08-01"
    elif mutation == "revenue":
        msc["net_revenue"] = "1"
    elif mutation == "spend":
        month_spend = D("2")
    elif mutation == "amount":
        burn = D("0")
    with pytest.raises(ValueError):
        reconcile_month(
            settlement="msc-13",
            receipt_burn_tx=tx,
            receipt_burn_sky=burn,
            official_msc=msc,
            official_monthly=monthly,
            independently_verified_spend_usds=month_spend,
        )


def test_reject_nonfinite_money() -> None:
    msc, monthly = payload("msc-13")
    with pytest.raises(ValueError):
        reconcile_month(
            settlement="msc-13",
            receipt_burn_tx=BURNS["msc-13"][0],
            receipt_burn_sky=D("7372288"),
            official_msc=msc,
            official_monthly=monthly,
            independently_verified_spend_usds=D("NaN"),
        )
