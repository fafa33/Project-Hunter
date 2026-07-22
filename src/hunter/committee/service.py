from __future__ import annotations

from dataclasses import replace

from hunter.committee.authority import CommitteeInputPolicyError, validate_authoritative_input
from hunter.committee.engine import InvestmentCommitteeEngine, rank_committee_assessments
from hunter.committee.models import CommitteeInputSet, CycleChampionSnapshot, InvestmentCommitteeAssessment
from hunter.committee.repository import InvestmentCommitteeRepository, persist_cycle
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver


class CommitteeAuthorityError(ValueError):
    pass


_UNAVAILABLE_SNAPSHOT_METRICS = frozenset({"valuation", "comparative_valuation", "mispricing", "mispricing_quality", "asymmetry"})


class AuthoritativeInvestmentCommitteeService:
    """Service-owned authority boundary for evaluation, ranking, and persistence."""

    def __init__(
        self,
        *,
        repository: InvestmentCommitteeRepository,
        input_resolver: RepositoryBackedCommitteeInputResolver,
        engine: InvestmentCommitteeEngine | None = None,
    ) -> None:
        if not isinstance(input_resolver, RepositoryBackedCommitteeInputResolver):
            raise CommitteeAuthorityError("authoritative committee service requires the approved repository-backed resolver")
        self.repository = repository
        self.input_resolver = input_resolver
        self.engine = engine or InvestmentCommitteeEngine()

    def evaluate_cycle(
        self,
        inputs: tuple[CommitteeInputSet, ...],
    ) -> tuple[CycleChampionSnapshot, tuple[InvestmentCommitteeAssessment, ...]]:
        if not inputs:
            raise CommitteeAuthorityError("committee cycle requires at least one candidate")
        self._validate_inputs(inputs)

        raw = tuple(self.engine.evaluate(item) for item in inputs)
        ordered = rank_committee_assessments(raw)
        ranked = tuple(replace(item, rank=index) for index, item in enumerate(ordered, start=1))
        champion = _champion_from_ranked(self.engine, inputs, ranked)
        persist_cycle(self.repository, champion, ranked)
        return champion, ranked

    def _validate_inputs(self, inputs: tuple[CommitteeInputSet, ...]) -> None:
        project_ids = tuple(item.project_id for item in inputs)
        if len(project_ids) != len(set(project_ids)):
            raise CommitteeAuthorityError("duplicate project_id in one committee cycle")
        effective_at = inputs[0].effective_at
        if any(item.effective_at != effective_at for item in inputs):
            raise CommitteeAuthorityError("all candidates in a cycle must share effective_at")
        for item in inputs:
            self._validate_sources(item)

    def _validate_sources(self, item: CommitteeInputSet) -> None:
        if item.authority_identity is None:
            raise CommitteeAuthorityError("authoritative committee input requires typed candidate identity")
        if item.alerts:
            raise CommitteeAuthorityError(
                "critical alerts cannot affect authoritative decisions until a persisted typed alert authority exists"
            )

        record_groups = (
            ("intelligence", item.intelligence),
            ("fused_intelligence", item.fused_intelligence),
            ("evidence", item.evidence),
            ("snapshot", item.snapshots),
        )
        for family, records in record_groups:
            for record in records:
                if family == "snapshot":
                    self._reject_unavailable_snapshot_metrics(record)
                self._resolve_and_validate(record, item, family, derived=False)

        assessments = (
            ("opportunity", item.opportunity),
            ("probability", item.probability),
            ("pattern", item.pattern),
            ("necessity", item.necessity),
        )
        for family, assessment in assessments:
            if assessment is not None:
                self._resolve_and_validate(assessment, item, family, derived=True)

    @staticmethod
    def _reject_unavailable_snapshot_metrics(record: object) -> None:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            return
        forbidden = _UNAVAILABLE_SNAPSHOT_METRICS.intersection(str(key) for key in payload)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise CommitteeAuthorityError(
                f"unavailable valuation-family snapshot metrics cannot affect authoritative scoring: {names}"
            )

    def _resolve_and_validate(
        self,
        value: object,
        item: CommitteeInputSet,
        family: str,
        *,
        derived: bool,
    ) -> None:
        record_id = str(getattr(value, "assessment_id", getattr(value, "id", ""))).strip()
        if not record_id:
            kind = "assessment" if derived else "record"
            raise CommitteeAuthorityError(f"all committee inputs must reference persisted {kind} IDs")

        try:
            resolved = self.input_resolver.resolve(record_id=record_id, family=family, known_at=item.effective_at)
        except ValueError as exc:
            raise CommitteeAuthorityError(str(exc)) from exc
        if resolved is None:
            raise CommitteeAuthorityError("committee input was not known by Hunter at the cycle cutoff")
        try:
            validate_authoritative_input(
                value,
                resolved,
                family=family,
                expected_identity=item.authority_identity,
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
