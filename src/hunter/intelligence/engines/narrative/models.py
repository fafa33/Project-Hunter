from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.narrative.exceptions import NarrativeValidationError

NARRATIVE_CATEGORIES = (
    "ai",
    "depin",
    "rwa",
    "bitcoinfi",
    "layer_1",
    "layer_2",
    "rollups",
    "gaming",
    "oracle",
    "defi",
    "privacy",
    "interoperability",
    "stablecoins",
    "tokenization",
    "restaking",
    "modular_chains",
    "consumer_crypto",
    "identity",
    "payments",
    "infrastructure",
    "cross_chain",
    "prediction_markets",
    "data_availability",
)

LIFECYCLE_PHASES = (
    "unknown",
    "emerging",
    "early_expansion",
    "expansion",
    "acceleration",
    "mainstream",
    "crowded",
    "saturation",
    "decline",
    "obsolete",
)


@dataclass(frozen=True)
class NarrativeEvidence:
    id: str
    category: str
    source: str
    timestamp: datetime
    reliability: float
    strength: float
    text: str
    engine: str = "unknown"
    project: str = "global-crypto"
    reference: str = ""
    institutional: bool = False
    retail: bool = False
    duplicate_key: str = ""
    promotional: bool = False
    spam: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "evidence id")
        _require_text(self.category, "category")
        _require_text(self.source, "source")
        _require_text(self.text, "text")
        _require_datetime(self.timestamp, "timestamp")
        object.__setattr__(self, "category", _normalize_category(self.category))
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "strength", _clamp(self.strength))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class Narrative:
    id: str
    category: str
    name: str
    description: str
    created_at: datetime
    evidence_ids: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "narrative id")
        _require_text(self.category, "category")
        _require_text(self.name, "name")
        _require_datetime(self.created_at, "created_at")
        object.__setattr__(self, "category", _normalize_category(self.category))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class NarrativeSignal:
    narrative_id: str
    category: str
    signal_type: str
    strength: float
    confidence: float
    timestamp: datetime
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.narrative_id, "signal narrative id")
        _require_text(self.signal_type, "signal type")
        _require_datetime(self.timestamp, "signal timestamp")
        object.__setattr__(self, "category", _normalize_category(self.category))
        object.__setattr__(self, "strength", _clamp(self.strength))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class NarrativeCluster:
    id: str
    category: str
    evidence: tuple[NarrativeEvidence, ...]
    parent: str | None = None
    children: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeTrend:
    narrative_id: str
    category: str
    growth: float
    acceleration: float
    saturation: float
    persistence: float
    ignored: bool = False


@dataclass(frozen=True)
class NarrativeEvent:
    id: str
    narrative_id: str
    event_type: str
    timestamp: datetime
    strength: float
    confidence: float
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NarrativeLifecycle:
    narrative_id: str
    category: str
    phase: str
    previous_phase: str = "unknown"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.phase not in LIFECYCLE_PHASES:
            raise NarrativeValidationError(f"Invalid lifecycle phase: {self.phase}")
        if self.previous_phase not in LIFECYCLE_PHASES:
            raise NarrativeValidationError(f"Invalid previous lifecycle phase: {self.previous_phase}")


@dataclass(frozen=True)
class NarrativeRelationship:
    source_narrative_id: str
    target_narrative_id: str
    relationship_type: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp(self.confidence))


@dataclass(frozen=True)
class NarrativeDataset:
    project: str = "global-crypto"
    evidence: tuple[NarrativeEvidence, ...] = ()
    duplicates: tuple[str, ...] = ()
    filtered: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class NarrativeAnalysis:
    narratives: tuple[Narrative, ...]
    clusters: tuple[NarrativeCluster, ...]
    signals: tuple[NarrativeSignal, ...]
    trends: tuple[NarrativeTrend, ...]
    events: tuple[NarrativeEvent, ...]
    lifecycles: tuple[NarrativeLifecycle, ...]
    relationships: tuple[NarrativeRelationship, ...]
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


NarrativeRecord = NarrativeEvidence


def _normalize_category(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise NarrativeValidationError(f"Missing {field_name}")


def _require_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise NarrativeValidationError(f"Missing {field_name}")


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
