from hunter.market_validation.configuration import MarketValidationConfig, load_market_validation_config
from hunter.market_validation.models import (
    MarketValidationComparison,
    MarketValidationRun,
    ProjectValidationResult,
    ProjectValidationTarget,
)


def __getattr__(name: str) -> object:
    if name == "MarketValidationRenderer":
        from hunter.market_validation.renderer import MarketValidationRenderer

        return MarketValidationRenderer
    if name in {"EvidenceBackedProjectExecutor", "MarketValidationRunner", "compare_runs"}:
        from hunter.market_validation.runner import EvidenceBackedProjectExecutor, MarketValidationRunner, compare_runs

        return {
            "EvidenceBackedProjectExecutor": EvidenceBackedProjectExecutor,
            "MarketValidationRunner": MarketValidationRunner,
            "compare_runs": compare_runs,
        }[name]
    raise AttributeError(name)


__all__ = [
    "EvidenceBackedProjectExecutor",
    "MarketValidationComparison",
    "MarketValidationConfig",
    "MarketValidationRenderer",
    "MarketValidationRun",
    "MarketValidationRunner",
    "ProjectValidationResult",
    "ProjectValidationTarget",
    "compare_runs",
    "load_market_validation_config",
]
