from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from hunter.historical_acquisition.usd_valuation import UsdValuation
from hunter.onchain.models import RawOnChainObservation

FIFO_POLICY = "deterministic-fifo"
_UNAVAILABLE_FIELDS = ("fees", "unrealized_pnl", "opening_inventory")


@dataclass(frozen=True, slots=True)
class WalletPerformance:
    status: str
    wallet: str | None
    chain_id: int
    reason: str | None = None
    realized_pnl_usd: Decimal | None = None
    cost_basis_usd: Decimal | None = None
    proceeds_usd: Decimal | None = None
    realized_return: Decimal | None = None
    coverage: Decimal | None = None
    sample_size: int = 0
    win_rate: Decimal | None = None
    net_pnl_usd: Decimal | None = None
    unavailable_fields: tuple[str, ...] = _UNAVAILABLE_FIELDS


def wallet_performance_from_swaps(
    swaps: tuple[RawOnChainObservation, ...],
    valuations: Mapping[str, UsdValuation],
    *,
    as_of: object = None,
) -> tuple[WalletPerformance, ...]:
    groups: dict[tuple[int, str | None], list[RawOnChainObservation]] = defaultdict(list)
    for swap in swaps:
        groups[(swap.chain_id, swap.attributed_wallet)].append(swap)
    results: list[WalletPerformance] = []
    for (chain_id, wallet), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        results.append(_performance(chain_id, wallet, rows, valuations))
    return tuple(results)


def _performance(
    chain_id: int,
    wallet: str | None,
    rows: list[RawOnChainObservation],
    valuations: Mapping[str, UsdValuation],
) -> WalletPerformance:
    base = WalletPerformance(status="UNAVAILABLE", wallet=wallet, chain_id=chain_id)
    if wallet is None:
        return _unavailable(base, "wallet_attribution_unavailable", ("wallet_attribution",))
    if any(row.attribution_status != "AVAILABLE" or row.swap_status != "AVAILABLE" for row in rows):
        reason = (
            "multi_hop_ambiguous_swap"
            if any(row.swap_status == "AMBIGUOUS" for row in rows)
            else "wallet_attribution_unavailable"
        )
        return _unavailable(base, reason, ("attribution",))
    missing = [row.evidence_id for row in rows if _valuation_usd(valuations.get(row.evidence_id)) is None]
    if missing:
        return _unavailable(base, "usd_valuation_unavailable", ("usd_valuation",))
    lots: deque[list[Decimal]] = deque()
    cost_basis = Decimal(0)
    proceeds = Decimal(0)
    closed = 0
    wins = 0
    covered = Decimal(0)
    total_sold = Decimal(0)
    for row in sorted(rows, key=lambda item: (item.block_number, item.log_index or 0, item.evidence_id)):
        amount = row.normalized_amount or Decimal(0)
        value = _valuation_usd(valuations[row.evidence_id])
        if value is None or amount <= 0:
            return _unavailable(base, "usd_valuation_unavailable", ("usd_valuation",))
        if row.trade_direction == "buy":
            lots.append([amount, value])
            continue
        if row.trade_direction != "sell":
            return _unavailable(base, "unsupported_trade_direction", ("trade_direction",))
        total_sold += amount
        remaining = amount
        sale_cost = Decimal(0)
        while remaining > 0:
            if not lots:
                return _unavailable(base, "partial_inventory_history", ("partial_inventory",))
            lot = lots[0]
            taken = min(lot[0], remaining)
            cost = (lot[1] * taken) / lot[0] if lot[0] else Decimal(0)
            sale_cost += cost
            lot[0] -= taken
            lot[1] -= cost
            remaining -= taken
            covered += taken
            if lot[0] == 0:
                lots.popleft()
        cost_basis += sale_cost
        proceeds += value
        closed += 1
        if value - sale_cost > 0:
            wins += 1
    if closed == 0 or total_sold <= 0:
        return _unavailable(base, "insufficient_closed_inventory", ("sample_size",))
    with localcontext() as context:
        context.prec = 28
        realized = proceeds - cost_basis
        realized_return = realized / cost_basis if cost_basis else None
        win_rate = Decimal(wins) / Decimal(closed)
        coverage = covered / total_sold
    return WalletPerformance(
        status="AVAILABLE",
        wallet=wallet,
        chain_id=chain_id,
        realized_pnl_usd=realized,
        cost_basis_usd=cost_basis,
        proceeds_usd=proceeds,
        realized_return=realized_return,
        coverage=coverage,
        sample_size=closed,
        win_rate=win_rate,
    )


def _unavailable(base: WalletPerformance, reason: str, fields: tuple[str, ...]) -> WalletPerformance:
    return WalletPerformance(
        status="UNAVAILABLE",
        wallet=base.wallet,
        chain_id=base.chain_id,
        reason=reason,
        unavailable_fields=tuple(dict.fromkeys((*_UNAVAILABLE_FIELDS, *fields))),
    )


def _valuation_usd(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, UsdValuation):
        return value.value_usd if value.status == "AVAILABLE" else None
    if isinstance(value, Mapping):
        if str(value.get("status")) != "AVAILABLE":
            return None
        raw = value.get("value_usd")
        return None if raw is None else Decimal(str(raw))
    return None
