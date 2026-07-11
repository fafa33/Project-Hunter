from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MACRO_DOMAINS = (
    "global_liquidity",
    "interest_rates",
    "inflation",
    "stablecoin_supply",
    "bitcoin_dominance",
    "eth_btc_ratio",
    "etf_flows",
    "regulatory_environment",
    "institutional_adoption",
    "layer_1_ecosystem",
    "layer_2_ecosystem",
    "ai_sector",
    "depin_sector",
    "rwa_sector",
    "defi_sector",
    "gaming_sector",
    "infrastructure_sector",
    "privacy_sector",
    "interoperability_sector",
    "oracle_sector",
)


@dataclass(frozen=True)
class MacroDataPoint:
    domain: str
    value: float | None
    previous_value: float | None
    source: str
    timestamp: datetime
    reliability: float
    reference: str
    raw_data: Any = None


@dataclass(frozen=True)
class MacroDataset:
    points: tuple[MacroDataPoint, ...] = ()

    def by_domain(self) -> dict[str, MacroDataPoint]:
        return {point.domain: point for point in self.points}


@dataclass(frozen=True)
class MacroIndicator:
    name: str
    domain: str
    value: float
    direction: str
    confidence: float


@dataclass(frozen=True)
class MacroAnalysis:
    indicators: tuple[MacroIndicator, ...]
    strengthening_domains: tuple[str, ...] = ()
    weakening_domains: tuple[str, ...] = ()
    risk_regime: str = "unknown"
    liquidity_flow: str = "unknown"
    notable_events: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
