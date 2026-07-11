from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENGINE_WEIGHTS = {
    "valuation": 0.06,
    "mispricing": 0.07,
    "asymmetry": 0.06,
    "whale": 0.06,
    "macro": 0.07,
    "future_demand": 0.08,
    "opportunity": 0.1,
    "probability": 0.12,
    "pattern": 0.08,
    "technology_necessity": 0.1,
    "capital_rotation": 0.07,
    "validation": 0.06,
    "backtesting": 0.04,
    "risk": 0.02,
    "evidence_quality": 0.01,
}


@dataclass(frozen=True)
class EligibilityThresholds:
    minimum_evidence_completeness: float = 0.5
    minimum_evidence_freshness: float = 0.5
    minimum_validation_health: float = 0.45
    minimum_confidence: float = 0.45
    maximum_missing_evidence_ratio: float = 0.35
    maximum_risk: float = 0.7
    minimum_available_engines: int = 5
    maximum_critical_alert_count: int = 0
    minimum_probability_reliability: float = 0.45
    minimum_opportunity_confidence: float = 0.45
    minimum_necessity_confidence: float = 0.45


@dataclass(frozen=True)
class WinnerMinimums:
    committee_confidence: float = 0.68
    consensus: float = 0.62
    evidence_robustness: float = 0.6
    probability: float = 0.58
    opportunity: float = 0.55
    necessity: float = 0.55
    maximum_conflict: float = 0.35
    maximum_risk: float = 0.65
    lead_margin: float = 0.05


@dataclass(frozen=True)
class InvestmentCommitteeConfig:
    engine_weights: tuple[tuple[str, float], ...] = field(default_factory=lambda: tuple(sorted(ENGINE_WEIGHTS.items())))
    eligibility: EligibilityThresholds = field(default_factory=EligibilityThresholds)
    winner_minimums: WinnerMinimums = field(default_factory=WinnerMinimums)
    approve_threshold: float = 0.6
    strong_approve_threshold: float = 0.78
    oppose_threshold: float = 0.4
    strong_oppose_threshold: float = 0.22
    stale_after_days: int = 30
    low_confidence_threshold: float = 0.35
    maximum_displayed_contributors: int = 5
    tie_breaking_order: tuple[str, ...] = (
        "committee_confidence",
        "consensus_score",
        "evidence_robustness",
        "probability",
        "opportunity",
        "technology_necessity",
        "project_id",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "engine_weights", _weights(self.engine_weights))
        for value in (
            self.approve_threshold,
            self.strong_approve_threshold,
            self.oppose_threshold,
            self.strong_oppose_threshold,
            self.low_confidence_threshold,
        ):
            if value < 0.0 or value > 1.0:
                msg = "committee thresholds must be between 0.0 and 1.0"
                raise ValueError(msg)
        if (
            self.strong_oppose_threshold > self.oppose_threshold
            or self.approve_threshold > self.strong_approve_threshold
        ):
            msg = "committee vote thresholds are incoherent"
            raise ValueError(msg)


def load_investment_committee_config(
    path: str | Path = "configs/investment_committee.yaml",
) -> InvestmentCommitteeConfig:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    payload = payload or {}
    eligibility = payload.get("eligibility", {})
    winner = payload.get("winner_minimums", {})
    return InvestmentCommitteeConfig(
        engine_weights=_float_items(payload.get("engine_weights", {})) or tuple(sorted(ENGINE_WEIGHTS.items())),
        eligibility=EligibilityThresholds(**eligibility),
        winner_minimums=WinnerMinimums(**winner),
        approve_threshold=float(payload.get("approve_threshold", 0.6)),
        strong_approve_threshold=float(payload.get("strong_approve_threshold", 0.78)),
        oppose_threshold=float(payload.get("oppose_threshold", 0.4)),
        strong_oppose_threshold=float(payload.get("strong_oppose_threshold", 0.22)),
        stale_after_days=int(payload.get("stale_after_days", 30)),
        low_confidence_threshold=float(payload.get("low_confidence_threshold", 0.35)),
        maximum_displayed_contributors=int(payload.get("maximum_displayed_contributors", 5)),
        tie_breaking_order=tuple(str(item) for item in payload.get("tie_breaking_order", ())),
    )


def _weights(values: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
    normalized = tuple(sorted((str(name), max(0.0, float(weight))) for name, weight in values))
    total = sum(weight for _, weight in normalized)
    if total <= 0.0:
        msg = "engine weights must include positive weight"
        raise ValueError(msg)
    return tuple((name, round(weight / total, 6)) for name, weight in normalized)


def _float_items(value: Any) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), float(item)) for key, item in value.items())
