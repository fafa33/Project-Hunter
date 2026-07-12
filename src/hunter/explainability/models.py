from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ContributionBreakdown:
    engine: str
    raw_score: float
    normalized_score: float
    applied_weight: float
    final_score_contribution: float


@dataclass(frozen=True)
class EvidenceTrace:
    engine: str
    input_evidence: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    timestamp: datetime
    confidence: float
    freshness: float
    missing_evidence: tuple[str, ...]
    stale_evidence: tuple[str, ...]
    validation_warnings: tuple[str, ...]


@dataclass(frozen=True)
class EngineExplanation:
    engine: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityItem:
    engine: str
    final_score_decrease_if_removed: float


@dataclass(frozen=True)
class DecisionAudit:
    project_id: str
    project_name: str
    final_score: float
    rank: int
    committee_decision: str
    committee_confidence: float
    contributions: tuple[ContributionBreakdown, ...]
    evidence_trace: tuple[EvidenceTrace, ...]
    explanations: tuple[EngineExplanation, ...]
    decision_tree: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    top_positive_contributors: tuple[ContributionBreakdown, ...]
    top_negative_contributors: tuple[ContributionBreakdown, ...]
    sensitivity: tuple[SensitivityItem, ...]


@dataclass(frozen=True)
class ScoreDifference:
    engine: str
    left_score: float
    right_score: float
    difference: float
    preferred_project_id: str


@dataclass(frozen=True)
class RankComparison:
    left_project_id: str
    right_project_id: str
    left_rank: int
    right_rank: int
    final_ranking_difference: int
    engine_preferences: tuple[ScoreDifference, ...]
    largest_score_differences: tuple[ScoreDifference, ...]
    largest_confidence_difference: float
    largest_risk_difference: float
    largest_valuation_difference: float
    largest_macro_difference: float
    largest_future_demand_difference: float
    largest_committee_difference: float
