from __future__ import annotations

from dataclasses import replace

from hunter.committee.engine import InvestmentCommitteeEngine, rank_committee_assessments
from hunter.committee.models import CommitteeInputSet, CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.committee.repository import InvestmentCommitteeRepository, persist_cycle


class CommitteeAuthorityError(ValueError):
    pass


class AuthoritativeInvestmentCommitteeService:
    """Service-owned authority boundary for evaluation, ranking, and persistence."""

    def __init__(
        self,
        *,
        repository: InvestmentCommitteeRepository,
        engine: InvestmentCommitteeEngine | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine or InvestmentCommitteeEngine()

    def evaluate_cycle(
        self,
        inputs: tuple[CommitteeInputSet, ...],
    ) -> tuple[CycleChampionSnapshot, tuple[InvestmentCommitteeAssessment, ...]]:
        if not inputs:
            raise CommitteeAuthorityError("committee cycle requires at least one candidate")
        _validate_inputs(inputs)

        raw = tuple(self.engine.evaluate(item) for item in inputs)
        ordered = rank_committee_assessments(raw)
        ranked = tuple(replace(item, rank=index) for index, item in enumerate(ordered, start=1))
        champion = _champion_from_ranked(self.engine, inputs, ranked)
        persist_cycle(self.repository, champion, ranked)
        return champion, ranked


def _validate_inputs(inputs: tuple[CommitteeInputSet, ...]) -> None:
    project_ids = tuple(item.project_id for item in inputs)
    if len(project_ids) != len(set(project_ids)):
        raise CommitteeAuthorityError("duplicate project_id in one committee cycle")
    effective_at = inputs[0].effective_at
    if any(item.effective_at != effective_at for item in inputs):
        raise CommitteeAuthorityError("all candidates in a cycle must share effective_at")
    for item in inputs:
        _validate_source_ids(item)


def _validate_source_ids(item: CommitteeInputSet) -> None:
    records = (*item.intelligence, *item.fused_intelligence, *item.evidence, *item.snapshots)
    for record in records:
        record_id = str(getattr(record, "id", "")).strip()
        if not record_id:
            raise CommitteeAuthorityError("all committee inputs must reference persisted record IDs")
        recorded_at = getattr(record, "recorded_at", getattr(record, "created_at", None))
        if recorded_at is not None and recorded_at > item.effective_at:
            raise CommitteeAuthorityError("future-known input cannot enter committee evaluation")


def _champion_from_ranked(
    engine: InvestmentCommitteeEngine,
    inputs: tuple[CommitteeInputSet, ...],
    ranked: tuple[InvestmentCommitteeAssessment, ...],
) -> CycleChampionSnapshot:
    original_champion, _ = engine.select_champion(inputs)
    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    selected = original_champion.selected_project_id
    if selected is not None and selected != winner.project_id:
        raise CommitteeAuthorityError("engine champion does not match authoritative ranking")
    return replace(
        original_champion,
        created_at=winner.created_at,
        runner_up_project_id=runner_up.project_id if runner_up else None,
        committee_confidence=winner.committee_confidence,
        consensus_score=winner.consensus_score,
        lead_margin=max(
            0.0,
            winner.committee_confidence - (runner_up.committee_confidence if runner_up else 0.0),
        ),
    )
