from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OpportunityTimingConfig:
    enabled: bool = False
    required_historical_depth: int = 3
    min_confirmation_groups: int = 2
    min_category_coverage: float = 0.5
    freshness_grace_days: int = 7
    freshness_window_days: int = 30
    required_categories: tuple[str, ...] = (
        "macro",
        "whale",
        "developer",
        "protocol",
        "news",
        "narrative",
        "social",
        "on-chain",
    )
    phase_thresholds: tuple[tuple[str, float], ...] = (
        ("too_early", 20.0),
        ("forming", 40.0),
        ("early_entry", 60.0),
        ("confirmed_entry", 75.0),
        ("expansion", 90.0),
    )
    window_thresholds: tuple[tuple[str, float], ...] = (
        ("closed", 20.0),
        ("watch", 40.0),
        ("opening", 60.0),
        ("open", 75.0),
        ("strengthening", 90.0),
    )
    risk_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: (
            ("contradiction", 0.25),
            ("missing_evidence", 0.2),
            ("dependency", 0.2),
            ("divergence", 0.2),
            ("insufficient_history", 0.15),
        )
    )
    confidence_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: (
            ("history", 0.2),
            ("diversity", 0.2),
            ("coverage", 0.2),
            ("independence", 0.15),
            ("freshness", 0.1),
            ("fusion", 0.15),
        )
    )
    contradiction_penalty: float = 18.0
    missing_evidence_penalty: float = 3.0
    divergence_penalty: float = 15.0
    acceleration_bonus: float = 12.0
    confirmation_bonus: float = 18.0
    persistence_bonus: float = 10.0
    acceleration_rules: tuple[tuple[str, float], ...] = (
        ("positive_acceleration", 0.08),
        ("negative_acceleration", -0.08),
        ("stalled_delta", 0.02),
        ("reversal_delta", -0.08),
    )
    divergence_rules: tuple[tuple[str, float], ...] = (
        ("social_high", 0.75),
        ("fundamental_low", 0.45),
        ("fundamental_high", 0.65),
        ("attention_low", 0.4),
        ("whale_high", 0.7),
        ("narrative_high", 0.7),
        ("macro_low", 0.4),
        ("social_saturation", 0.85),
    )
    horizon_rules: tuple[tuple[str, float], ...] = (
        ("weeks", 75.0),
        ("1-3 months", 75.0),
        ("3-6 months", 60.0),
        ("6-12 months", 40.0),
        ("12-24 months", 20.0),
    )
    invalidation_rules: tuple[str, ...] = (
        "loss_of_independent_confirmation",
        "material_increase_in_contradiction_severity",
        "sustained_negative_acceleration",
        "continued_absence_of_required_evidence",
        "risk_state_worsens",
        "divergence_remains_unresolved",
        "confirmation_threshold_not_maintained",
    )
    model_version: str = "opportunity-timing-model-v1"

    def __post_init__(self) -> None:
        for name in ("required_categories",):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        for name in ("phase_thresholds", "window_thresholds", "horizon_rules"):
            raw = getattr(self, name)
            values = raw.items() if isinstance(raw, Mapping) else raw
            object.__setattr__(self, name, tuple((str(key), float(value)) for key, value in values))
        for name in ("risk_weights", "confidence_weights", "acceleration_rules", "divergence_rules"):
            raw = getattr(self, name)
            values = raw.items() if isinstance(raw, Mapping) else raw
            object.__setattr__(self, name, tuple(sorted((str(key), float(value)) for key, value in values)))
        object.__setattr__(self, "invalidation_rules", tuple(str(item) for item in self.invalidation_rules))
        _validate_thresholds("phase_thresholds", self.phase_thresholds)
        _validate_thresholds("window_thresholds", self.window_thresholds)
        if not 0.0 <= self.min_category_coverage <= 1.0:
            msg = "min_category_coverage must be between 0 and 1"
            raise ValueError(msg)
        if self.freshness_grace_days < 0 or self.freshness_window_days <= 0:
            msg = "freshness windows must be positive"
            raise ValueError(msg)
        if self.required_historical_depth < 1 or self.min_confirmation_groups < 1:
            msg = "historical depth and confirmation groups must be positive"
            raise ValueError(msg)


@dataclass(frozen=True)
class OpportunityConfig:
    enabled: bool = False
    factor_weights: tuple[tuple[str, float], ...] = (
        ("valuation_discount", 0.11),
        ("relative_valuation", 0.08),
        ("historical_discount", 0.07),
        ("whale_accumulation", 0.1),
        ("smart_money_positioning", 0.07),
        ("developer_momentum", 0.07),
        ("macro_tailwinds", 0.1),
        ("future_demand", 0.1),
        ("sector_strength", 0.06),
        ("capital_formation", 0.05),
        ("validation_health", 0.08),
        ("evidence_freshness", 0.04),
        ("confidence", 0.04),
        ("backtesting_quality", 0.02),
        ("historical_opportunity_similarity", 0.01),
    )
    risk_weight: float = 0.18
    missing_evidence_weight: float = 0.12
    validation_gate_weight: float = 0.2
    label_thresholds: tuple[tuple[str, float], ...] = (
        ("Avoid", 0.1),
        ("Weak Opportunity", 0.22),
        ("Wait", 0.34),
        ("Neutral", 0.46),
        ("Watch Closely", 0.58),
        ("Accumulation", 0.68),
        ("Strong Opportunity", 0.78),
        ("High Conviction", 0.88),
        ("Exceptional Opportunity", 1.0),
    )
    window_thresholds_entry: tuple[tuple[str, float], ...] = (
        ("Unknown", 0.0),
        ("Very Early", 0.18),
        ("Early", 0.34),
        ("Developing", 0.52),
        ("Established", 0.7),
        ("Late", 0.84),
        ("Very Late", 1.0),
    )

    def __post_init__(self) -> None:
        for name in ("factor_weights", "label_thresholds", "window_thresholds_entry"):
            raw = getattr(self, name)
            values = raw.items() if isinstance(raw, Mapping) else raw
            object.__setattr__(self, name, tuple((str(key), float(value)) for key, value in values))
        if self.risk_weight < 0 or self.missing_evidence_weight < 0 or self.validation_gate_weight < 0:
            msg = "penalty weights must be non-negative"
            raise ValueError(msg)
        _validate_unit_thresholds("label_thresholds", self.label_thresholds)
        _validate_unit_thresholds("window_thresholds_entry", self.window_thresholds_entry)


def load_opportunity_timing_config(path: Path) -> OpportunityTimingConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Opportunity Timing configuration must be a mapping"
        raise ValueError(msg)
    return opportunity_timing_config_from_mapping(payload)


def load_opportunity_config(path: Path) -> OpportunityConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        msg = "Opportunity configuration must be a mapping"
        raise ValueError(msg)
    return opportunity_config_from_mapping(payload)


def opportunity_config_from_mapping(payload: dict[str, Any]) -> OpportunityConfig:
    return OpportunityConfig(
        enabled=bool(payload.get("enabled", False)),
        factor_weights=_mapping_tuple(payload.get("factor_weights"), OpportunityConfig().factor_weights),
        risk_weight=float(payload.get("risk_weight", 0.18)),
        missing_evidence_weight=float(payload.get("missing_evidence_weight", 0.12)),
        validation_gate_weight=float(payload.get("validation_gate_weight", 0.2)),
        label_thresholds=_mapping_tuple(payload.get("label_thresholds"), OpportunityConfig().label_thresholds),
        window_thresholds_entry=_mapping_tuple(
            payload.get("window_thresholds"), OpportunityConfig().window_thresholds_entry
        ),
    )


def opportunity_timing_config_from_mapping(payload: dict[str, Any]) -> OpportunityTimingConfig:
    return OpportunityTimingConfig(
        enabled=bool(payload.get("enabled", False)),
        required_historical_depth=int(payload.get("required_historical_depth", 3)),
        min_confirmation_groups=int(payload.get("min_confirmation_groups", 2)),
        min_category_coverage=float(payload.get("min_category_coverage", 0.5)),
        freshness_grace_days=int(payload.get("freshness_grace_days", 7)),
        freshness_window_days=int(payload.get("freshness_window_days", 30)),
        required_categories=tuple(
            str(item) for item in payload.get("required_categories", OpportunityTimingConfig().required_categories)
        ),
        phase_thresholds=_mapping_tuple(payload.get("phase_thresholds"), OpportunityTimingConfig().phase_thresholds),
        window_thresholds=_mapping_tuple(payload.get("window_thresholds"), OpportunityTimingConfig().window_thresholds),
        risk_weights=_mapping_tuple(payload.get("risk_weights"), OpportunityTimingConfig().risk_weights),
        confidence_weights=_mapping_tuple(
            payload.get("confidence_weights"), OpportunityTimingConfig().confidence_weights
        ),
        contradiction_penalty=float(payload.get("contradiction_penalty", 18.0)),
        missing_evidence_penalty=float(payload.get("missing_evidence_penalty", 3.0)),
        divergence_penalty=float(payload.get("divergence_penalty", 15.0)),
        acceleration_bonus=float(payload.get("acceleration_bonus", 12.0)),
        confirmation_bonus=float(payload.get("confirmation_bonus", 18.0)),
        persistence_bonus=float(payload.get("persistence_bonus", 10.0)),
        acceleration_rules=_mapping_tuple(
            payload.get("acceleration_rules"), OpportunityTimingConfig().acceleration_rules
        ),
        divergence_rules=_mapping_tuple(payload.get("divergence_rules"), OpportunityTimingConfig().divergence_rules),
        horizon_rules=_mapping_tuple(payload.get("horizon_rules"), OpportunityTimingConfig().horizon_rules),
        invalidation_rules=tuple(
            str(item) for item in payload.get("invalidation_rules", OpportunityTimingConfig().invalidation_rules)
        ),
        model_version=str(payload.get("model_version", "opportunity-timing-model-v1")),
    )


def _mapping_tuple(value: Any, default: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    if isinstance(value, dict):
        return tuple((str(key), float(item)) for key, item in value.items())
    return default


def _validate_thresholds(name: str, values: tuple[tuple[str, float], ...]) -> None:
    if not values:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    scores = [score for _, score in values]
    if any(score < 0 or score > 100 for score in scores):
        msg = f"{name} values must be between 0 and 100"
        raise ValueError(msg)
    if scores != sorted(scores):
        msg = f"{name} values must be ordered from low to high"
        raise ValueError(msg)


def _validate_unit_thresholds(name: str, values: tuple[tuple[str, float], ...]) -> None:
    if not values:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    scores = [score for _, score in values]
    if any(score < 0 or score > 1 for score in scores):
        msg = f"{name} values must be between 0 and 1"
        raise ValueError(msg)
    if scores != sorted(scores):
        msg = f"{name} values must be ordered from low to high"
        raise ValueError(msg)
