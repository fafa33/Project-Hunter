"""Fail-closed reconciliation of indexed Sky swaps and exact treasury balances.

Balances are chain facts; indexed swap amounts are candidates until each receipt
has been verified.  Unexplained residuals are NOT costs, revenue, or burn inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SkyTreasuryReconciliation:
    start_block: int
    end_block: int
    start_balance_sky: Decimal
    end_balance_sky: Decimal
    indexed_swap_sky: Decimal
    independently_verified_swap_sky: Decimal
    verified_swap_count: int
    indexed_swap_count: int
    unexplained_other_net_sky: Decimal
    evidence_class: str = "partial_chain_balance_and_indexed_swaps_no_revenue_attribution"

    def require_complete_economic_attribution(self) -> None:
        if self.indexed_swap_count != self.verified_swap_count or self.indexed_swap_sky != self.independently_verified_swap_sky:
            raise ValueError("indexed swap receipts are not exhaustively verified")
        if self.unexplained_other_net_sky:
            raise ValueError("treasury balance has unexplained transfers or protocol operations")
        raise ValueError("USDS revenue source and burn cost allocation not proven")


def reconcile_treasury_window(*, start_block: int, end_block: int,
                              start_balance_sky: Decimal, end_balance_sky: Decimal,
                              indexed_swap_sky: Decimal, independently_verified_swap_sky: Decimal,
                              indexed_swap_count: int, verified_swap_count: int) -> SkyTreasuryReconciliation:
    if not (0 <= start_block < end_block and 0 <= verified_swap_count <= indexed_swap_count):
        raise ValueError("invalid treasury window or receipt coverage")
    values = (start_balance_sky, end_balance_sky, indexed_swap_sky, independently_verified_swap_sky)
    if not all(isinstance(v, Decimal) and v.is_finite() and v >= 0 for v in values):
        raise ValueError("invalid treasury amounts")
    if independently_verified_swap_sky > indexed_swap_sky:
        raise ValueError("verified swap amount exceeds indexed swaps")
    return SkyTreasuryReconciliation(start_block, end_block, start_balance_sky, end_balance_sky,
                                     indexed_swap_sky, independently_verified_swap_sky,
                                     verified_swap_count, indexed_swap_count,
                                     end_balance_sky - start_balance_sky - indexed_swap_sky)
