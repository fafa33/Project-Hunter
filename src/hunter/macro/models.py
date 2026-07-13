from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from hunter.execution.canonicalization import normalize


@dataclass(frozen=True)
class MacroMetric:
    name: str
    provider: str
    source_url: str
    timestamp: datetime
    value: float
    raw_payload: dict[str, Any]
    confidence: float = 1.0
    freshness: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("name", "provider", "source_url"):
            if not str(getattr(self, field_name)).strip():
                msg = f"{field_name} is required"
                raise ValueError(msg)
        object.__setattr__(self, "timestamp", _aware(self.timestamp))
        object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "freshness", _clamp(self.freshness))
        normalize(self.raw_payload)
        object.__setattr__(self, "raw_payload", MappingProxyType(dict(self.raw_payload)))


@dataclass(frozen=True)
class MacroEvidence:
    evidence_id: str
    repository_id: str
    metric: MacroMetric
    normalized_value: float
    validation_status: str
    validation_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_value", _clamp(self.normalized_value))
        object.__setattr__(self, "validation_status", self.validation_status.upper())
        object.__setattr__(self, "validation_errors", tuple(sorted(str(item) for item in self.validation_errors)))


@dataclass(frozen=True)
class MacroSnapshot:
    snapshot_id: str
    generated_at: datetime
    evidence: tuple[MacroEvidence, ...]
    liquidity_score: float
    inflation_score: float
    monetary_policy_score: float
    recession_probability: float
    risk_on_score: float
    risk_off_score: float
    crypto_liquidity_score: float
    macro_confidence: float
    freshness: float
    evidence_quality: float
    raw_metrics: dict[str, float] = field(default_factory=dict)
    normalized_metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _aware(self.generated_at))
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence, key=lambda item: item.metric.name)))
        for field_name in (
            "liquidity_score",
            "inflation_score",
            "monetary_policy_score",
            "recession_probability",
            "risk_on_score",
            "risk_off_score",
            "crypto_liquidity_score",
            "macro_confidence",
            "freshness",
            "evidence_quality",
        ):
            object.__setattr__(self, field_name, _clamp(float(getattr(self, field_name))))
        object.__setattr__(self, "raw_metrics", MappingProxyType(dict(sorted(self.raw_metrics.items()))))
        object.__setattr__(self, "normalized_metrics", MappingProxyType(dict(sorted(self.normalized_metrics.items()))))


@dataclass(frozen=True)
class MacroProviderFailure:
    provider: str
    metric: str
    reason: str
    message: str
    source_url: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", _aware(self.occurred_at))
        object.__setattr__(self, "reason", self.reason.upper())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
