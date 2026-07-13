from __future__ import annotations

from statistics import mean, median, pstdev

from hunter.historical.models import HistoricalChallengeResult, HistoricalPerformanceMetrics

POSITIVE_OUTCOMES = {
    "MAJOR_WINNER",
    "MODERATE_WINNER",
    "OUTPERFORMED_BENCHMARK",
    "OUTPERFORMED_PEERS",
    "TOP_QUARTILE",
    "SURVIVED",
}

NEGATIVE_OUTCOMES = {
    "UNDERPERFORMER",
    "SEVERE_UNDERPERFORMER",
    "COLLAPSED",
    "DELISTED",
}

POSITIVE_DECISIONS = {"QUALIFIED_CANDIDATE", "APPROVE", "BUY", "ACCEPT"}


def performance_metrics(challenges: tuple[HistoricalChallengeResult, ...]) -> HistoricalPerformanceMetrics:
    samples = tuple(row for row in challenges if row.realized_outcome != "INSUFFICIENT_OUTCOME_DATA")
    labeled = tuple((row, _positive_decision(row), _positive_outcome(row)) for row in samples)
    if not labeled:
        return HistoricalPerformanceMetrics(
            metric_id="historical-performance",
            accuracy="INSUFFICIENT_SAMPLE_SIZE",
            precision="INSUFFICIENT_SAMPLE_SIZE",
            recall="INSUFFICIENT_SAMPLE_SIZE",
            f1="INSUFFICIENT_SAMPLE_SIZE",
            roc_auc="INSUFFICIENT_SAMPLE_SIZE",
            maximum_drawdown="INSUFFICIENT_SAMPLE_SIZE",
            annualized_return="INSUFFICIENT_SAMPLE_SIZE",
            sharpe_ratio="INSUFFICIENT_SAMPLE_SIZE",
            sortino_ratio="INSUFFICIENT_SAMPLE_SIZE",
            win_rate="INSUFFICIENT_SAMPLE_SIZE",
            average_return="INSUFFICIENT_SAMPLE_SIZE",
            median_return="INSUFFICIENT_SAMPLE_SIZE",
            best_trade="INSUFFICIENT_SAMPLE_SIZE",
            worst_trade="INSUFFICIENT_SAMPLE_SIZE",
            hit_rate="INSUFFICIENT_SAMPLE_SIZE",
            time_to_target="INSUFFICIENT_SAMPLE_SIZE",
            false_positive_rate="INSUFFICIENT_SAMPLE_SIZE",
            false_negative_rate="INSUFFICIENT_SAMPLE_SIZE",
            sample_count=0,
        )
    true_positive = sum(1 for _, decision, outcome in labeled if decision and outcome)
    true_negative = sum(1 for _, decision, outcome in labeled if not decision and not outcome)
    false_positive = sum(1 for _, decision, outcome in labeled if decision and not outcome)
    false_negative = sum(1 for _, decision, outcome in labeled if not decision and outcome)
    returns = tuple(row.excess_return for row, _, _ in labeled if row.excess_return is not None)
    drawdowns = tuple(row.maximum_drawdown for row, _, _ in labeled if row.maximum_drawdown is not None)
    return HistoricalPerformanceMetrics(
        metric_id="historical-performance",
        accuracy=_ratio(true_positive + true_negative, len(labeled)),
        precision=_ratio(true_positive, true_positive + false_positive),
        recall=_ratio(true_positive, true_positive + false_negative),
        f1=_f1(true_positive, false_positive, false_negative),
        roc_auc="INSUFFICIENT_SAMPLE_SIZE",
        maximum_drawdown=round(min(drawdowns), 6) if drawdowns else "INSUFFICIENT_SAMPLE_SIZE",
        annualized_return=_annualized_return(returns),
        sharpe_ratio=_sharpe(returns),
        sortino_ratio=_sortino(returns),
        win_rate=_ratio(sum(1 for value in returns if value > 0), len(returns)),
        average_return=round(mean(returns), 6) if returns else "INSUFFICIENT_SAMPLE_SIZE",
        median_return=round(median(returns), 6) if returns else "INSUFFICIENT_SAMPLE_SIZE",
        best_trade=round(max(returns), 6) if returns else "INSUFFICIENT_SAMPLE_SIZE",
        worst_trade=round(min(returns), 6) if returns else "INSUFFICIENT_SAMPLE_SIZE",
        hit_rate=_ratio(true_positive, true_positive + false_negative),
        time_to_target="INSUFFICIENT_SAMPLE_SIZE",
        false_positive_rate=_ratio(false_positive, false_positive + true_negative),
        false_negative_rate=_ratio(false_negative, false_negative + true_positive),
        sample_count=len(labeled),
    )


def _positive_decision(row: HistoricalChallengeResult) -> bool:
    decision = row.committee_decision.upper()
    return decision in POSITIVE_DECISIONS or "QUALIFIED" in decision and "NO_" not in decision


def _positive_outcome(row: HistoricalChallengeResult) -> bool:
    if row.realized_outcome in POSITIVE_OUTCOMES:
        return True
    if row.realized_outcome in NEGATIVE_OUTCOMES:
        return False
    return bool(row.excess_return is not None and row.excess_return > 0)


def _ratio(numerator: int, denominator: int) -> float | str:
    if denominator <= 0:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round(numerator / denominator, 6)


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float | str:
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    if isinstance(precision, str) or isinstance(recall, str) or precision + recall == 0:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round((2 * precision * recall) / (precision + recall), 6)


def _annualized_return(returns: tuple[float, ...]) -> float | str:
    if not returns:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round(mean(returns), 6)


def _sharpe(returns: tuple[float, ...]) -> float | str:
    if len(returns) < 2:
        return "INSUFFICIENT_SAMPLE_SIZE"
    deviation = pstdev(returns)
    if deviation == 0:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round(mean(returns) / deviation, 6)


def _sortino(returns: tuple[float, ...]) -> float | str:
    downside = tuple(value for value in returns if value < 0)
    if len(downside) < 2:
        return "INSUFFICIENT_SAMPLE_SIZE"
    deviation = pstdev(downside)
    if deviation == 0:
        return "INSUFFICIENT_SAMPLE_SIZE"
    return round(mean(returns) / deviation, 6)
