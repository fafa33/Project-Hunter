from __future__ import annotations

from hunter.historical.models import HistoricalBenchmarkOutcome, HistoricalOutcome


def benchmark_outcomes(
    outcome: HistoricalOutcome,
    benchmark_returns: dict[int, float],
    *,
    benchmark_id: str,
) -> tuple[HistoricalBenchmarkOutcome, ...]:
    rows = []
    for window in outcome.windows:
        benchmark_return = benchmark_returns.get(window.window_days)
        absolute = window.simple_return
        excess = None if absolute is None or benchmark_return is None else round(absolute - benchmark_return, 6)
        rows.append(
            HistoricalBenchmarkOutcome(
                case_id=outcome.case_id,
                benchmark_id=benchmark_id,
                window_days=window.window_days,
                absolute_return=absolute,
                excess_return=excess,
                benchmark_relative_return=excess,
                peer_relative_return=None,
                percentile_outcome=None,
                rank_improvement=None,
                rank_deterioration=None,
            )
        )
    return tuple(rows)


def peer_relative_return(project_return: float | None, peer_returns: tuple[float, ...]) -> float | None:
    if project_return is None or not peer_returns:
        return None
    median = sorted(peer_returns)[len(peer_returns) // 2]
    return round(project_return - median, 6)
