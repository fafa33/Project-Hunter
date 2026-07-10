from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

WHALE_SIGNAL_TYPES = (
    "accumulation",
    "distribution",
    "exchange_flow",
    "smart_money",
    "treasury_movement",
    "vc_activity",
    "foundation_activity",
    "liquidity_rotation",
    "cross_chain_flow",
    "long_term_holder_activity",
)


@dataclass(frozen=True)
class WhaleEvent:
    id: str
    asset: str
    event_type: str
    amount: float | None
    direction: str
    source: str
    timestamp: datetime
    reliability: float
    wallet_attribution_quality: float
    confirmation: float
    reference: str
    raw_data: Any = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WhaleDataset:
    events: tuple[WhaleEvent, ...] = ()

    def by_type(self) -> dict[str, tuple[WhaleEvent, ...]]:
        grouped: dict[str, list[WhaleEvent]] = {}
        for event in self.events:
            grouped.setdefault(event.event_type, []).append(event)
        return {key: tuple(values) for key, values in grouped.items()}


@dataclass(frozen=True)
class WhaleSignal:
    name: str
    event_type: str
    strength: float
    direction: str
    confidence: float
    event_count: int


@dataclass(frozen=True)
class WhaleAnalysis:
    signals: tuple[WhaleSignal, ...]
    accumulating_assets: tuple[str, ...] = ()
    distributing_assets: tuple[str, ...] = ()
    exchange_flow: str = "unknown"
    smart_money_activity: str = "unknown"
    notable_events: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

