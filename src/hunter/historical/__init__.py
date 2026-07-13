from hunter.historical.configuration import HistoricalValidationConfig, load_historical_validation_config
from hunter.historical.models import (
    HistoricalBacktestRun,
    HistoricalBiasValidation,
    HistoricalChallengeResult,
    HistoricalDecisionOutcomeRecord,
    HistoricalEvidenceSnapshot,
    HistoricalPerformanceMetrics,
    HistoricalReplayExplanation,
    HistoricalValidationCase,
)


def __getattr__(name: str) -> object:
    if name == "HistoricalPointInTimeValidationEngine":
        from hunter.historical.replay import HistoricalPointInTimeValidationEngine

        return HistoricalPointInTimeValidationEngine
    if name == "HistoricalValidationRenderer":
        from hunter.historical.renderer import HistoricalValidationRenderer

        return HistoricalValidationRenderer
    if name == "HistoricalValidationRepository":
        from hunter.historical.repository import HistoricalValidationRepository

        return HistoricalValidationRepository
    raise AttributeError(name)


__all__ = [
    "HistoricalBacktestRun",
    "HistoricalBiasValidation",
    "HistoricalChallengeResult",
    "HistoricalDecisionOutcomeRecord",
    "HistoricalEvidenceSnapshot",
    "HistoricalPerformanceMetrics",
    "HistoricalPointInTimeValidationEngine",
    "HistoricalReplayExplanation",
    "HistoricalValidationCase",
    "HistoricalValidationConfig",
    "HistoricalValidationRenderer",
    "HistoricalValidationRepository",
    "load_historical_validation_config",
]
