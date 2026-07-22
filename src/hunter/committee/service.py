from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from hunter.committee.authority import CommitteeInputPolicyError, validate_authoritative_input
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
        _validate_sources(item)


def _validate_sources(item: CommitteeInputSet) -> None:
    record_groups = (
        ("intelligence", item.intelligence),
        ("fused_intelligence", item.fused_intelligence),
        ("evidence", item.evidence),
        ("snapshot", item.snapshots),
    )
    for family, records in record_groups:
        for record in records:
            _validate_persisted_input(record, item, family)

    assessments = (
        ("opportunity", item.opportunity),
        ("probability", item.probability),
        ("pattern", item.pattern),
        ("necessity", item.necessity),
    )
    for family, assessment in assessments:
        if assessment is not None:
            _validate_assessment_input(assessment, item, family)


def _validate_persisted_input(record: object, item: CommitteeInputSet, family: str) -> None:
    record_id = str(getattr(record, "id", "")).strip()
    if not record_id:
        raise CommitteeAuthorityError("all committee inputs must reference persisted record IDs")

    recorded_at = getattr(record, "recorded_at", getattr(record, "created_at", None))
    if isinstance(recorded_at, datetime) and recorded_at > item.effective_at:
        raise CommitteeAuthorityError("future-known input cannot enter committee evaluation")

    effective_at = getattr(record, "effective_at", None)
    if isinstance(effective_at, datetime) and effective_at > item.effective_at:
        raise CommitteeAuthorityError("future-effective input cannot enter committee evaluation")

    _apply_authority_policy(record, item, family)


def _validate_assessment_input(
    assessment: object,
    item: CommitteeInputSet,
    family: str,
) -> None:
    assessment_id = str(getattr(assessment, "assessment_id", getattr(assessment, "id", ""))).strip()
    if not assessment_id:
        raise CommitteeAuthorityError("all derived committee inputs must reference persisted assessment IDs")

    effective_at = getattr(assessment, "effective_at", None)
    if not isinstance(effective_at, datetime):
        raise CommitteeAuthorityError("derived committee inputs must define effective_at")
    if effective_at > item.effective_at:
        raise CommitteeAuthorityError("future-effective assessment cannot enter committee evaluation")

    recorded_at = getattr(assessment, "recorded_at", getattr(assessment, "created_at", None))
    if isinstance(recorded_at, datetime) and recorded_at > item.effective_at:
        raise CommitteeAuthorityError("future-known assessment cannot enter committee evaluation")

    _apply_authority_policy(assessment, item, family)


def _apply_authority_policy(value: object, item: CommitteeInputSet, family: str) -> None:
    try:
        validate_authoritative_input(
            value,
            family=family,
            project_id=item.project_id,
            cycle_effective_at=item.effective_at,
        )
    except CommitteeInputPolicyError as exc:
        raise CommitteeAuthorityError(str(exc)) from exc


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
