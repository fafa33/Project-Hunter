from __future__ import annotations

from datetime import UTC, datetime

from hunter.committee.models import CommitteeVote, CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.committee.sql_output import GenericSQLCommitteeOutput
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine

NOW = datetime(2026, 7, 22, 20, 0, tzinfo=UTC)


def test_generic_sql_output_persists_votes_assessment_and_champion_atomically() -> None:
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        vote = CommitteeVote(
            id="committee-vote:alpha:signal",
            assessment_id="committee-assessment:alpha",
            engine_name="signal",
            vote="APPROVE",
            normalized_contribution=0.8,
            source_score=0.8,
            source_confidence=0.9,
            source_timestamp=NOW,
            freshness_state="fresh",
            explanation="authoritative signal",
            supporting_references=("snapshot:alpha",),
        )
        assessment = InvestmentCommitteeAssessment(
            id="committee-assessment:alpha",
            project_id="alpha",
            created_at=NOW,
            eligibility_state="ELIGIBLE",
            decision="QUALIFIED_CANDIDATE",
            approval_score=0.8,
            opposition_score=0.1,
            consensus_score=0.8,
            conflict_score=0.1,
            evidence_robustness=0.8,
            committee_confidence=0.8,
            thesis_fragility=0.2,
            rank=1,
            votes=(vote,),
            positive_drivers=("signal",),
            negative_drivers=(),
            conflicts=(),
            abstentions=(),
            risks=(),
            invalidation_conditions=(),
            runner_up_comparison="no runner up",
            explanation=("qualified",),
            source_record_ids=("snapshot:alpha",),
        )
        champion = CycleChampionSnapshot(
            id="cycle-champion:alpha",
            created_at=NOW,
            selected_project_id="alpha",
            runner_up_project_id=None,
            decision="QUALIFIED_CANDIDATE",
            committee_confidence=0.8,
            consensus_score=0.8,
            lead_margin=0.8,
            selection_reason="highest ranked candidate",
        )

        GenericSQLCommitteeOutput(repositories, "pipeline-run:alpha").persist_cycle(champion, (assessment,))
        session.commit()

        assert repositories.committee_votes().load(vote.id) is not None
        persisted = repositories.investment_committee_assessments().load(assessment.id)
        assert persisted is not None
        assert persisted.source_record_ids == ("snapshot:alpha",)
        assert repositories.cycle_champion_snapshots().load(champion.id) is not None
    finally:
        session.close()
        engine.dispose()
