from __future__ import annotations

from dataclasses import dataclass

from hunter.committee.models import CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.persistence.records import (
    CommitteeVoteRecord,
    CycleChampionSnapshotRecord,
    InvestmentCommitteeAssessmentRecord,
)
from hunter.persistence.sql import RepositoryFactory


@dataclass(frozen=True)
class GenericSQLCommitteeOutput:
    """Persist one authoritative committee cycle through Hunter's canonical SQL repositories."""

    repositories: RepositoryFactory
    pipeline_run_id: str

    def persist_cycle(
        self,
        champion: CycleChampionSnapshot,
        assessments: tuple[InvestmentCommitteeAssessment, ...],
    ) -> None:
        if not assessments:
            raise ValueError("an authoritative cycle requires at least one assessment")
        ranks = tuple(item.rank for item in assessments)
        if ranks != tuple(range(1, len(assessments) + 1)):
            raise ValueError("assessment ranks must be contiguous and one-based")
        if champion.created_at != assessments[0].created_at:
            raise ValueError("champion chronology must match assessment cycle")

        metadata = {
            "pipeline_run_id": self.pipeline_run_id,
            "cycle_id": champion.id,
            "authority_class": "production-authoritative",
        }
        vote_records: list[CommitteeVoteRecord] = []
        assessment_records: list[InvestmentCommitteeAssessmentRecord] = []
        for assessment in assessments:
            for vote in assessment.votes:
                vote_records.append(
                    CommitteeVoteRecord(
                        id=vote.id,
                        created_at=assessment.created_at,
                        effective_at=assessment.created_at,
                        assessment_id=vote.assessment_id,
                        project_id=assessment.project_id,
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
                        metadata=metadata,
                    )
                )
            assessment_records.append(
                InvestmentCommitteeAssessmentRecord(
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
                    previous_assessment_id=None,
                    metadata=metadata,
                )
            )

        champion_record = CycleChampionSnapshotRecord(
            id=champion.id,
            created_at=champion.created_at,
            effective_at=champion.created_at,
            selected_project_id=champion.selected_project_id,
            runner_up_project_id=champion.runner_up_project_id,
            decision=champion.decision,
            committee_confidence=champion.committee_confidence,
            consensus_score=champion.consensus_score,
            lead_margin=champion.lead_margin,
            selection_reason=champion.selection_reason,
            no_selection_reason=champion.no_selection_reason,
            metadata=metadata,
        )

        self.repositories.committee_votes().save_many(tuple(vote_records))
        self.repositories.investment_committee_assessments().save_many(tuple(assessment_records))
        self.repositories.cycle_champion_snapshots().save(champion_record)
