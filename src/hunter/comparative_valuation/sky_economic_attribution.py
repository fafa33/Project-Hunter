"""Evidence-bound Sky treasury-management reconciliation for published MSC settlements.

The budget derives from independently published revenue *policy*, not from a
fungible USDS coin's transfer history.  No transfer is silently promoted to
revenue, and reported burn USD stays distinct from exact spend allocation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation

POLICY_COMMIT = "536f1492089ed82fbf6eaabb956f7a194b887d47"
POLICY_PREFIX = "https://raw.githubusercontent.com/soterlabs/settlement-cycle/" + POLICY_COMMIT
POLICY_GUIDE = POLICY_PREFIX + "/docs/tmf/README.md"
FORUM_12 = "https://forum.skyeco.com/t/msc-12-settlement-summary-august-2026/28217"
FORUM_13 = "https://forum.skyeco.com/t/msc-13-settlement-summary-september-2026/28274"
SOTER_SOURCES = {
    "msc-12": POLICY_PREFIX + "/settlements/tmf/2026-08/summary.md",
    "msc-13": POLICY_PREFIX + "/settlements/tmf/2026-09/summary.md",
}
BURNS = {
    "msc-12": ("0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3", "2860943.76", "2026-08"),
    "msc-13": ("0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275", "7372288", "2026-09"),
}
# Exact numbers as reported at the pinned historical source, not guessed from
# receipt mint logs. They remain independent of the live mutable aggregator.
MONTHS = {
    "msc-12": {
        "net_revenue": "15745296",
        "window_flapper_usds": "1026300",
        "window_sky_bought": "15735190.69",
        "allocated_sky": "2860943.76",
        "reported_burn_usd": "186553.806539509538247255",
        "published_buyback_usd": "1457746.193460490461752745",
    },
    "msc-13": {
        "net_revenue": "14635947",
        "window_flapper_usds": "2791800",
        "window_sky_bought": "40547581.67",
        "allocated_sky": "7372287.58",
        "reported_burn_usd": "507599.999999999999789964",
        "published_buyback_usd": "2284200.000000000000210036",
    },
}
SHARE_BURN = Decimal("10") / Decimal("55")
ROUNDING_SKY = Decimal("0.02")
ROUNDING_USDS = Decimal("0.000000000001")


def _value(value: object) -> Decimal:
    try:
        item = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid financial numeric value") from exc
    if not item.is_finite() or item < 0:
        raise ValueError("nonfinite or negative financial amount")
    return item


@dataclass(frozen=True)
class EconomicAttribution:
    settlement: str
    tx_hash: str
    burn_sky: str
    net_revenue_usds: str
    independently_verified_spend_usds: str
    rule_allocated_spend_usds: str
    reported_burn_cost_usds: str
    rule_allocated_burn_sky: str
    revenue_source_class: str
    burn_cost_source_class: str
    revenue_to_payment_proven: bool
    burn_allocation_proven: bool
    costs_match_within_tolerance: bool
    reconciliation_gap_usds: str
    policy_source: str
    reported_source: str

    def require_independent_economic_proof(self) -> None:
        if not (self.revenue_to_payment_proven and self.burn_allocation_proven and self.costs_match_within_tolerance):
            raise ValueError("economic revenue funding and burn cost not independently proven")


def reconcile_month(
    *,
    settlement: str,
    receipt_burn_tx: str,
    receipt_burn_sky: object,
    official_msc: dict,
    official_monthly: dict,
    independently_verified_spend_usds: object,
) -> EconomicAttribution:
    """Require matching burn identity; publish reconciliation and explicit proof gaps."""
    if settlement not in BURNS:
        raise ValueError("unknown Sky settlement")
    tx, expected_sky, month = BURNS[settlement]
    numbers = MONTHS[settlement]
    if receipt_burn_tx.lower() != tx or _value(receipt_burn_sky) != _value(expected_sky):
        raise ValueError("verified onchain burn identity mismatch")
    if official_msc.get("slug") != settlement or official_msc.get("exec_tx_hash", "").lower() != tx:
        raise ValueError("MSC settlement identity mismatch")
    if official_monthly.get("msc_slug") != settlement or official_monthly.get("burn_tx_hash", "").lower() != tx:
        raise ValueError("monthly settlement identity mismatch")
    if official_monthly.get("month") != month + "-01":
        raise ValueError("settlement attribution month mismatch")
    for key in ("net_revenue", "buyback_usd", "burn_usd"):
        expected = (
            "reported_burn_usd"
            if key == "burn_usd"
            else "published_buyback_usd" if key == "buyback_usd" else "net_revenue"
        )
        if abs(_value(official_msc[key]) - _value(numbers[expected])) > ROUNDING_USDS:
            raise ValueError("published MSC numbers have changed; reattestation required")
    if abs(_value(official_monthly["burn_usd"]) - _value(official_msc["burn_usd"])) > ROUNDING_USDS:
        raise ValueError("monthly vs MSC burn USD mismatch")
    if _value(official_monthly["burned_amount"]) != _value(expected_sky):
        raise ValueError("reported burn SKY amount mismatch")
    expected_spend = _value(numbers["window_flapper_usds"])
    actual_spend = _value(independently_verified_spend_usds)
    if actual_spend != expected_spend:
        raise ValueError("real payment receipts do not reconcile to the pinned month")
    allocated_usds = actual_spend * SHARE_BURN
    reported = _value(official_msc["burn_usd"])
    gap = reported - allocated_usds
    allocated_sky = _value(numbers["allocated_sky"])
    if abs(allocated_sky - (_value(numbers["window_sky_bought"]) * SHARE_BURN)) > ROUNDING_SKY:
        raise ValueError("policy's SKY allocation does not match source transactions")
    # The MSC burn moves a fungible SKY treasury inventory. Neither a USDS mint
    # nor the monthly net revenue proves the exact source of the allocated funds.
    return EconomicAttribution(
        settlement=settlement,
        tx_hash=tx,
        burn_sky=expected_sky,
        net_revenue_usds=str(_value(official_msc["net_revenue"])),
        independently_verified_spend_usds=str(actual_spend),
        rule_allocated_spend_usds=str(allocated_usds),
        reported_burn_cost_usds=str(reported),
        rule_allocated_burn_sky=str(allocated_sky),
        revenue_source_class="pinned_issuer_net_revenue_accounting_not_payment_origin",
        burn_cost_source_class="pinned_tmf_policy_and_verified_payment_not_fifo",
        revenue_to_payment_proven=False,
        burn_allocation_proven=False,
        costs_match_within_tolerance=abs(gap) <= ROUNDING_USDS,
        reconciliation_gap_usds=str(gap),
        policy_source=SOTER_SOURCES[settlement],
        reported_source=FORUM_12 if settlement == "msc-12" else FORUM_13,
    )


def digest_record(value: EconomicAttribution) -> str:
    return hashlib.sha256(json.dumps(asdict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
