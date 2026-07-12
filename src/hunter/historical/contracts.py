from __future__ import annotations

from typing import Protocol

from hunter.historical.models import HistoricalBacktestRun


class HistoricalValidationRunner(Protocol):
    def run(self) -> HistoricalBacktestRun: ...
