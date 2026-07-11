from __future__ import annotations

from hunter.committee.contracts import CommitteeInputRepository, InvestmentCommitteeAssessmentRepository
from hunter.committee.models import (
    CommitteeInputSet,
    CommitteeVote,
    CycleChampionSnapshot,
    InvestmentCommitteeAssessment,
)
from hunter.persistence.records import (
    CommitteeVoteRecord,
    CycleChampionSnapshotRecord,
    InvestmentCommitteeAssessmentRecord,
)


class InMemoryCommitteeInputRepository(CommitteeInputRepository):
    def __init__(self, inputs: tuple[CommitteeInputSet, ...] = ()) -> None:
        self._inputs = tuple(inputs)

    def latest_for_project(self, project_id: str) -> CommitteeInputSet | None:
        scoped = [item for item in self._inputs if item.project_id == project_id]
        scoped.sort(key=lambda item: item.effective_at, reverse=True)
        return scoped[0] if scoped else None

    def all_latest(self) -> tuple[CommitteeInputSet, ...]:
        latest: dict[str, CommitteeInputSet] = {}
        for item in sorted(self._inputs, key=lambda row: row.effective_at):
            latest[item.project_id] = item
        return tuple(latest[key] for key in sorted(latest))


class InMemoryInvestmentCommitteeAssessmentRepository(InvestmentCommitteeAssessmentRepository):
    def __init__(self) -> None:
        self._assessments: dict[str, InvestmentCommitteeAssessment] = {}

    def save(self, assessment: InvestmentCommitteeAssessment) -> InvestmentCommitteeAssessment:
        existing = self._assessments.get(assessment.id)
        if existing is not None:
            return existing
        self._assessments[assessment.id] = assessment
        return assessment

    def latest_for_project(self, project_id: str) -> InvestmentCommitteeAssessment | None:
        records = [item for item in self._assessments.values() if item.project_id == project_id]
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[0] if records else None

    def history_for_project(self, project_id: str) -> tuple[InvestmentCommitteeAssessment, ...]:
        records = [item for item in self._assessments.values() if item.project_id == project_id]
        return tuple(sorted(records, key=lambda item: item.created_at))


def vote_to_record(vote: CommitteeVote, *, project_id: str, effective_at) -> CommitteeVoteRecord:
    return CommitteeVoteRecord(
        id=vote.id,
        created_at=effective_at,
        effective_at=effective_at,
        assessment_id=vote.assessment_id,
        project_id=project_id,
        engine_name=vote.engine_name,
        vote=vote.vote,
        normalized_contribution=vote.normalized_contribution,
        source_score=vote.source_score,
        source_confidence=vote.source_confidence,
        source_timestamp=vote.source_timestamp,
        freshness_state=vote.freshness_state,
        explanation=vote.explanation,
        supporting_references=vote.supporting_references,
        opposing_references=vote.opposing_references,
        missing_fields=vote.missing_fields,
    )


def assessment_to_record(
    assessment: InvestmentCommitteeAssessment,
    *,
    previous_assessment_id: str | None = None,
) -> InvestmentCommitteeAssessmentRecord:
    return InvestmentCommitteeAssessmentRecord(
        id=assessment.id,
        created_at=assessment.created_at,
        effective_at=assessment.created_at,
        project_id=assessment.project_id,
        eligibility_state=assessment.eligibility_state,
        decision=assessment.decision,
        approval_score=assessment.approval_score,
        opposition_score=assessment.opposition_score,
        consensus_score=assessment.consensus_score,
        conflict_score=assessment.conflict_score,
        evidence_robustness=assessment.evidence_robustness,
        committee_confidence=assessment.committee_confidence,
        thesis_fragility=assessment.thesis_fragility,
        rank=assessment.rank,
        vote_ids=tuple(vote.id for vote in assessment.votes),
        positive_drivers=assessment.positive_drivers,
        negative_drivers=assessment.negative_drivers,
        conflicts=assessment.conflicts,
        abstentions=assessment.abstentions,
        risks=assessment.risks,
        invalidation_conditions=assessment.invalidation_conditions,
        runner_up_comparison=assessment.runner_up_comparison,
        explanation=assessment.explanation,
        source_record_ids=assessment.source_record_ids,
        previous_assessment_id=previous_assessment_id,
        metadata=dict(assessment.metadata),
    )


def champion_to_record(snapshot: CycleChampionSnapshot) -> CycleChampionSnapshotRecord:
    return CycleChampionSnapshotRecord(
        id=snapshot.id,
        created_at=snapshot.created_at,
        effective_at=snapshot.created_at,
        selected_project_id=snapshot.selected_project_id,
        runner_up_project_id=snapshot.runner_up_project_id,
        decision=snapshot.decision,
        committee_confidence=snapshot.committee_confidence,
        consensus_score=snapshot.consensus_score,
        lead_margin=snapshot.lead_margin,
        selection_reason=snapshot.selection_reason,
        no_selection_reason=snapshot.no_selection_reason,
    )
