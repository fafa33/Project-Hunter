from hunter.backtest.models import BacktestRun, CalibrationReport, EngineBacktestMetric, ProjectBacktestMetric


def __getattr__(name: str) -> object:
    if name == "BacktestRepository":
        from hunter.backtest.repository import BacktestRepository

        return BacktestRepository
    if name in {"BacktestingCalibrationEngine", "compare_backtests"}:
        from hunter.backtest.engine import BacktestingCalibrationEngine, compare_backtests

        return {
            "BacktestingCalibrationEngine": BacktestingCalibrationEngine,
            "compare_backtests": compare_backtests,
        }[name]
    raise AttributeError(name)


__all__ = [
    "BacktestRepository",
    "BacktestRun",
    "BacktestingCalibrationEngine",
    "CalibrationReport",
    "EngineBacktestMetric",
    "ProjectBacktestMetric",
    "compare_backtests",
]
