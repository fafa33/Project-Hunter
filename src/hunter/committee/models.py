from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from hunter.committee.authority import CommitteeInputIdentity
from hunter.intelligence.fusion.models import FrozenFloatMap, FrozenScalarMap
from hunter.necessity.models import TechnologyNecessityAssessment
from hunter.opportunity.models import OpportunityTimingAssessment
from hunter.patterns.models import PatternMatchingAssessment
from hunter.persistence.records import EvidenceRecord, FusedIntelligenceRecord, IntelligenceRecord, SnapshotRecord
from hunter.probability.models import ProbabilityAssessment

EligibilityState = Literal["ELIGIBLE", "CONDITIONALLY_ELIGIBLE", "INELIGIBLE", "INSUFFICIENT_EVIDENCE"]
VoteState = Literal[
    "STRONG_APPROVE",
    "APPROVE",
    "NEUTRAL",
    "OPPOSE",
    "STRONG_OPPOSE",
    "ABSTAIN_MISSING",
    "ABSTAIN_STALE",
    "ABSTAIN_LOW_CONFIDENCE",
]
CommitteeDecision = Literal[
    "HIGHEST_CONVICTION_CANDIDATE",
    "STRONG_CANDIDATE",
    "QUALIFIED_CANDIDATE",
    "WATCH_CLOSELY",
    "WAIT",
    "REJECT",
    "INSUFFICIENT_EVIDENCE",
    "NO_QUALIFIED_CANDIDATE",
]


@dataclass(frozen=True)
class CommitteeInputSet:
    project_id: str
    effective_at: datetime
    authority_identity: CommitteeInputIdentity | None = None
    intelligence: tuple[IntelligenceRecord, ...] = ()
    fused_intelligence: tuple[FusedIntelligenceRecord, ...] = ()
    opportunity: OpportunityTimingAssessment | None = None
    probability: ProbabilityAssessment | None = None
    pattern: PatternMatchingAssessment | None = None
    necessity: TechnologyNecessityAssessment | None = None
    evidence: tuple[EvidenceRecord, ...] = ()
    snapshots: tuple[SnapshotRecord, ...] = ()
    alerts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text("project_id", self.project_id)
        if self.authority_identity is not None and self.authority_identity.project_id != self.project_id:
            raise ValueError("authority_identity project_id must match project_id")
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in ("intelligence", "fused_intelligence", "evidence", "snapshots", "alerts"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class CommitteeVote:
    id: str
    assessment_id: str
    engine_name: str
    vote: VoteState
    normalized_contribution: float
    source_score: float
    source_confidence: float
    source_timestamp: datetime | None
    freshness_state: str
    explanation: str
    supporting_references: tuple[str, ...] = ()
    opposing_references: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("id", "assessment_id", "engine_name", "vote", "freshness_state", "explanation"):
            _text(name, getattr(self, name))
        object.__setattr__(self, "normalized_contribution", _clamp(self.normalized_contribution))
        object.__setattr__(self, "source_score", _clamp(self.source_score))
        object.__setattr__(self, "source_confidence", _clamp(self.source_confidence))
        if self.source_timestamp is not None:
            object.__setattr__(self, "source_timestamp", self.source_timestamp.astimezone(UTC))
        for name in ("supporting_references", "opposing_references", "missing_fields"):
            object.__setattr__(self, name, tuple(sorted(str(item) for item in getattr(self, name))))


@dataclass(frozen=True)
class InvestmentCommitteeAssessment:
    id: str
    project_id: str
    created_at: datetime
    eligibility_state: EligibilityState
    decision: CommitteeDecision
    approval_score: float
    opposition_score: float
    consensus_score: float
    conflict_score: float
    evidence_robustness: float
    committee_confidence: float
    thesis_fragility: float
    rank: int
    votes: tuple[CommitteeVote, ...]
    positive_drivers: tuple[str, ...]
    negative_drivers: tuple[str, ...]
    conflicts: tuple[str, ...]
    abstentions: tuple[str, ...]
    risks: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    runner_up_comparison: str
    explanation: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    metadata: FrozenScalarMap | Mapping[str, object] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        for name in ("id", "project_id", "eligibility_state", "decision", "runner_up_comparison"):
            _text(name, str(getattr(self, name)))
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        for name in (
            "approval_score",
            "opposition_score",
            "consensus_score",
            "conflict_score",
            "evidence_robustness",
            "committee_confidence",
            "thesis_fragility",
        ):
            object.__setattr__(self, name, _clamp(getattr(self, name)))
        object.__setattr__(self, "votes", tuple(self.votes))
        for name in (
            "positive_drivers",
            "negative_drivers",
            "conflicts",
            "abstentions",
            "risks",
            "invalidation_conditions",
            "explanation",
            "source_record_ids",
        ):
            object.__setattr__(self, name, tuple(sorted(str(item) for item in getattr(self, name))))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))

    @property
    def scores(self) -> FrozenFloatMap:
        return FrozenFloatMap(
            {
                "approval": self.approval_score,
                "opposition": self.opposition_score,
                "consensus": self.consensus_score,
                "conflict": self.conflict_score,
                "evidence_robustness": self.evidence_robustness,
                "committee_confidence": self.committee_confidence,
                "thesis_fragility": self.thesis_fragility,
            }
        )


@dataclass(frozen=True)
class CycleChampionSnapshot:
    id: str
    created_at: datetime
    selected_project_id: str | None
    runner_up_project_id: str | None
    decision: CommitteeDecision
    committee_confidence: float
    consensus_score: float
    lead_margin: float
    selection_reason: str
    no_selection_reason: str | None = None

    def __post_init__(self) -> None:
        _text("id", self.id)
        _text("decision", self.decision)
        _text("selection_reason", self.selection_reason)
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        for name in ("committee_confidence", "consensus_score", "lead_margin"):
            object.__setattr__(self, name, _clamp(getattr(self, name)))


@dataclass(frozen=True)
class CommitteeBacktestSummary:
    candidate_count: int
    qualified_candidate_count: int
    no_selection_count: int
    decision_distribution: Mapping[str, int]
    champion_hit_rate: float
    false_positive_count: int
    false_negative_count: int
    notes: tuple[str, ...] = ()


def _text(name: str, value: str) -> None:
    if not value.strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
