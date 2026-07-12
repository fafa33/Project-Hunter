from __future__ import annotations

import math
from statistics import pstdev

from hunter.historical.models import HistoricalOutcome, HistoricalOutcomeWindow, HistoricalValidationCase, SuccessLabel


def build_outcome(case: HistoricalValidationCase, observations: dict[int, tuple[float, ...]]) -> HistoricalOutcome:
    windows = tuple(_window(days, observations.get(days, ()), case) for days in sorted(observations))
    label = classify_success(windows, case)
    return HistoricalOutcome(
        case_id=case.case_id, project_id=case.project_id, windows=windows, final_success_label=label
    )


def classify_success(windows: tuple[HistoricalOutcomeWindow, ...], case: HistoricalValidationCase) -> SuccessLabel:
    if case.token_lifecycle_state == "delisted":
        return "DELISTED"
    if case.project_lifecycle_state in {"collapsed", "failed"}:
        return "COLLAPSED"
    returns = tuple(window.simple_return for window in windows if window.simple_return is not None)
    if not returns:
        return "INSUFFICIENT_OUTCOME_DATA"
    best = max(returns)
    worst = min(returns)
    if best >= 5.0:
        return "MAJOR_WINNER"
    if best >= 1.0:
        return "MODERATE_WINNER"
    if worst <= -0.8:
        return "SEVERE_UNDERPERFORMER"
    if worst < 0.0:
        return "UNDERPERFORMER"
    return "NEUTRAL"


def _window(days: int, prices: tuple[float, ...], case: HistoricalValidationCase) -> HistoricalOutcomeWindow:
    if len(prices) < 2:
        return HistoricalOutcomeWindow(
            window_days=days,
            start_price=None,
            end_price=None,
            maximum_price=None,
            minimum_price=None,
            simple_return=None,
            log_return=None,
            maximum_drawdown=None,
            maximum_favorable_excursion=None,
            maximum_adverse_excursion=None,
            volatility=None,
            liquidity_change=None,
            market_cap_change=None,
            fdv_change=None,
            rank_change=None,
            survival_status=case.project_lifecycle_state,
            delisting_status=case.token_lifecycle_state,
            exploit_status="unknown",
            collapse_status="collapsed" if case.project_lifecycle_state == "collapsed" else "none",
        )
    start = prices[0]
    end = prices[-1]
    returns = tuple((prices[index] / prices[index - 1]) - 1.0 for index in range(1, len(prices)) if prices[index - 1])
    simple = (end / start) - 1.0 if start else None
    return HistoricalOutcomeWindow(
        window_days=days,
        start_price=start,
        end_price=end,
        maximum_price=max(prices),
        minimum_price=min(prices),
        simple_return=round(simple, 6) if simple is not None else None,
        log_return=round(math.log(end / start), 6) if start and end > 0 else None,
        maximum_drawdown=maximum_drawdown(prices),
        maximum_favorable_excursion=round((max(prices) / start) - 1.0, 6) if start else None,
        maximum_adverse_excursion=round((min(prices) / start) - 1.0, 6) if start else None,
        volatility=round(pstdev(returns), 6) if len(returns) > 1 else 0.0,
        liquidity_change=None,
        market_cap_change=None,
        fdv_change=None,
        rank_change=None,
        survival_status=case.project_lifecycle_state,
        delisting_status=case.token_lifecycle_state,
        exploit_status="unknown",
        collapse_status="collapsed" if case.project_lifecycle_state == "collapsed" else "none",
    )


def maximum_drawdown(prices: tuple[float, ...]) -> float | None:
    if not prices:
        return None
    peak = prices[0]
    drawdown = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak:
            drawdown = min(drawdown, (price / peak) - 1.0)
    return round(drawdown, 6)
