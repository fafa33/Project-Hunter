"""Bounded reconciliation of Sky-reported settlement USD with verified token burns.

Reported USD is NOT independent proof of payment, funding origin, or revenue.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hunter.comparative_valuation.sky_index_attestation import SkyVerifiedTokenBurn

USD_PRECISION_TOLERANCE = Decimal("0.000000000000000001")


@dataclass(frozen=True)
class SkyReportedSettlement:
    settlement: str
    tx_hash: str
    block_number: int
    log_index: int
    sky_burned: Decimal
    reported_burn_usd: Decimal
    reported_buyback_usd: Decimal
    msc_source: str
    monthly_source: str
    evidence_class: str = "reported_usd_correlated_not_economic_attribution"

    def require_verified_economic_attribution(self) -> None:
        raise ValueError("USD funding/payment provenance is not independently verified")


def reconcile_reported_sky_settlement(
    burn: SkyVerifiedTokenBurn, *, settlement: str, msc: dict, monthly: dict
) -> SkyReportedSettlement:
    """Cross-check two publisher views against a separately verified burn log."""
    if (
        msc.get("slug") != settlement
        or monthly.get("msc_slug") != settlement
        or msc.get("exec_tx_hash", "").lower() != burn.tx_hash
        or monthly.get("burn_tx_hash", "").lower() != burn.tx_hash
    ):
        raise ValueError("settlement identity/transaction mismatch")
    try:
        amount = Decimal(monthly["burned_amount"])
        usd = Decimal(msc["burn_usd"])
        monthly_usd = Decimal(monthly["burn_usd"])
        buyback = Decimal(msc["buyback_usd"])
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError("invalid settlement numeric evidence") from exc
    if (
        amount != burn.sky_amount
        or not all(v.is_finite() for v in (usd, monthly_usd, buyback))
        or usd <= 0
        or monthly_usd <= 0
        or buyback < 0
        or abs(usd - monthly_usd) > USD_PRECISION_TOLERANCE
    ):
        raise ValueError("settlement SKY/USD mismatch")
    return SkyReportedSettlement(
        settlement=settlement,
        tx_hash=burn.tx_hash,
        block_number=burn.block_number,
        log_index=burn.log_index,
        sky_burned=burn.sky_amount,
        reported_burn_usd=usd,
        reported_buyback_usd=buyback,
        msc_source="https://sky.data.blockanalitica.com/internal/msc/",
        monthly_source="https://sky.data.blockanalitica.com/internal/buyback/burns/monthly/",
    )
