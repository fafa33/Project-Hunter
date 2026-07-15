from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hunter.sufficiency.registry import default_data_requirement_registry
from hunter.sufficiency.repository import DataSufficiencyRepository


@dataclass(frozen=True)
class SufficiencyReportContext:
    cutoff_at: datetime | None = None
    strict_known_by_hunter: bool = False
    reconstructed_after_cutoff: bool = False

    @property
    def replay_mode(self) -> str:
        if self.reconstructed_after_cutoff:
            return "reconstructed_after_cutoff"
        if self.strict_known_by_hunter:
            return "historical_strict_known_by_hunter"
        return "current"


class DataSufficiencyReporter:
    def __init__(self, repository: DataSufficiencyRepository) -> None:
        self.repository = repository

    def requirements(self, context: SufficiencyReportContext | None = None) -> tuple[dict[str, Any], ...]:
        context = context or SufficiencyReportContext()
        persisted = self._requirements(context)
        if persisted:
            return tuple(_requirement_with_source_types(row, self.repository) for row in persisted)
        registry = default_data_requirement_registry(created_at=_fallback_created_at())
        return tuple(
            {
                "requirement_id": requirement.requirement_id,
                "engine_id": requirement.engine_id,
                "analysis_purpose": requirement.analysis_purpose,
                "output_field": requirement.output_field,
                "requirement_kind": requirement.requirement_kind,
                "evidence_domain": requirement.evidence_domain,
                "required_source_types": requirement.required_source_types,
                "direct_observation_required": requirement.direct_observation_required,
                "proxy_allowed": requirement.proxy_allowed,
                "accepted_proxy_types": requirement.accepted_proxy_types,
                "blocking_level": requirement.blocking_level,
                "policy_id": requirement.policy_id,
                "policy_version": requirement.policy_version,
                "schema_version": requirement.schema_version,
                "source": "default_registry",
            }
            for requirement in registry.requirements
            if _visible_at(requirement.effective_at, requirement.recorded_at, context)
        )

    def coverage(self, context: SufficiencyReportContext) -> dict[str, Any]:
        assessments = self._assessments(context)
        candidates = {str(row["candidate_id"]) for row in assessments}
        availability_rows = self._availability_rows(context)
        availability_by_state = Counter(str(row["availability_state"]) for row in availability_rows)
        direct = _ratio(
            sum(1 for row in availability_rows if row["directness"] == "direct_observation"),
            len(availability_rows),
        )
        proxy = _ratio(
            sum(1 for row in availability_rows if row["directness"] == "proxy_signal"), len(availability_rows)
        )
        degraded = Counter(str(row["degraded_mode"]) for row in assessments)
        lineage_complete = _ratio(
            sum(1 for row in availability_rows if bool(row["lineage_complete"])),
            len(availability_rows),
        )
        return {
            "data_sufficiency_only": True,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at.isoformat() if context.cutoff_at else None,
            "requirements": len(self.requirements(context)),
            "candidates_assessed": len(candidates),
            "availability_by_state": dict(availability_by_state),
            "coverage_score": _average(float(row["coverage_score"]) for row in assessments),
            "direct_observation_coverage": direct,
            "proxy_signal_coverage": proxy,
            "stale_evidence": availability_by_state.get("stale", 0),
            "partial_evidence": availability_by_state.get("partial", 0),
            "unavailable_evidence": availability_by_state.get("unavailable", 0),
            "degraded_mode_outcomes": dict(degraded),
            "lineage_completeness": lineage_complete,
            "confidence_limits": _confidence_limits(assessments),
        }

    def assess(self, candidate_id: str, context: SufficiencyReportContext) -> dict[str, Any]:
        assessments = self._candidate_assessments(candidate_id, context)
        availability_rows = self._candidate_availability(candidate_id, context)
        disagreements = self.disagreements(context, candidate_id=candidate_id)
        return {
            "data_sufficiency_only": True,
            "candidate_id": candidate_id,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at.isoformat() if context.cutoff_at else None,
            "assessments": assessments,
            "availability_by_state": dict(Counter(str(row["availability_state"]) for row in availability_rows)),
            "required_data": _required_data(self.requirements(context), availability_rows),
            "source_disagreements": disagreements,
            "degraded_mode_limitations": _limitations(assessments),
        }

    def missing(self, candidate_id: str, context: SufficiencyReportContext) -> dict[str, Any]:
        rows = tuple(
            row
            for row in self._candidate_availability(candidate_id, context)
            if row["availability_state"] in {"unavailable", "stale", "partial"}
        )
        return {
            "data_sufficiency_only": True,
            "candidate_id": candidate_id,
            "replay_mode": context.replay_mode,
            "missing_or_degraded": rows,
            "material_missing_count": sum(1 for row in rows if row["availability_state"] == "unavailable"),
        }

    def disagreements(
        self,
        context: SufficiencyReportContext | None = None,
        *,
        candidate_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        context = context or SufficiencyReportContext()
        if context.cutoff_at is None:
            return self.repository.disagreements(candidate_id=candidate_id)
        if candidate_id is not None:
            return self.repository._rows_at(  # noqa: SLF001
                "data_disagreement_records",
                "disagreement_id",
                context.cutoff_at,
                "candidate_id = ?",
                (candidate_id,),
                strict_known_by_hunter=context.strict_known_by_hunter,
                order_by="candidate_id, engine_id, analysis_purpose, disagreement_id",
            )
        return self.repository._rows_at(  # noqa: SLF001
            "data_disagreement_records",
            "disagreement_id",
            context.cutoff_at,
            "1 = 1",
            (),
            strict_known_by_hunter=context.strict_known_by_hunter,
            order_by="candidate_id, engine_id, analysis_purpose, disagreement_id",
        )

    def disagreement_report(self, context: SufficiencyReportContext) -> dict[str, Any]:
        return {
            "data_sufficiency_only": True,
            "replay_mode": context.replay_mode,
            "cutoff_at": context.cutoff_at.isoformat() if context.cutoff_at else None,
            "disagreements": self.disagreements(context),
        }

    def report(self, context: SufficiencyReportContext) -> dict[str, Any]:
        return {
            "data_sufficiency_only": True,
            "coverage": self.coverage(context),
            "requirements": self.requirements(context),
            "disagreements": self.disagreements(context),
            "limitations": (
                "Data Sufficiency is advisory metadata only.",
                "Scoring, ranking, valuation, committee decisions, Opportunity Timing, and Market Validation are unchanged.",
                f"Replay mode: {context.replay_mode}.",
            ),
        }

    def _availability_rows(self, context: SufficiencyReportContext) -> tuple[dict[str, Any], ...]:
        if context.cutoff_at is None:
            return self._current_table_rows("data_availability", "availability_id")
        candidate_ids = {
            str(row["candidate_id"]) for row in self._current_table_rows("data_availability", "availability_id")
        }
        return tuple(
            row
            for candidate_id in candidate_ids
            for row in self.repository.availability_for_candidate_at(
                candidate_id,
                context.cutoff_at,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        )

    def _candidate_availability(
        self, candidate_id: str, context: SufficiencyReportContext
    ) -> tuple[dict[str, Any], ...]:
        if context.cutoff_at is not None:
            return self.repository.availability_for_candidate_at(
                candidate_id,
                context.cutoff_at,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        return self.repository.availability_for_candidate(candidate_id)

    def _assessments(self, context: SufficiencyReportContext) -> tuple[dict[str, Any], ...]:
        if context.cutoff_at is None:
            return self._current_table_rows("data_sufficiency_assessments", "assessment_id")
        candidate_ids = {
            str(row["candidate_id"])
            for row in self._current_table_rows("data_sufficiency_assessments", "assessment_id")
        }
        return tuple(
            row
            for candidate_id in candidate_ids
            for row in self.repository.assessments_for_candidate_at(
                candidate_id,
                context.cutoff_at,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        )

    def _candidate_assessments(
        self, candidate_id: str, context: SufficiencyReportContext
    ) -> tuple[dict[str, Any], ...]:
        if context.cutoff_at is not None:
            return self.repository.assessments_for_candidate_at(
                candidate_id,
                context.cutoff_at,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        return self.repository.assessments_for_candidate(candidate_id)

    def _current_table_rows(self, table: str, identity_column: str) -> tuple[dict[str, Any], ...]:
        return self.repository._current_rows(table, identity_column)  # noqa: SLF001

    def _requirements(self, context: SufficiencyReportContext) -> tuple[dict[str, Any], ...]:
        if context.cutoff_at is None:
            return self.repository.requirements()
        return self.repository._rows_at(  # noqa: SLF001
            "data_requirements",
            "requirement_id",
            context.cutoff_at,
            "1 = 1",
            (),
            strict_known_by_hunter=context.strict_known_by_hunter,
            order_by="engine_id, analysis_purpose, output_field, requirement_id",
        )


def _required_data(
    requirements: tuple[dict[str, Any], ...],
    availability_rows: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    by_requirement = {str(row["requirement_id"]): row for row in availability_rows}
    return tuple(
        {
            "requirement_id": row["requirement_id"],
            "engine_id": row["engine_id"],
            "analysis_purpose": row["analysis_purpose"],
            "output_field": row["output_field"],
            "direct_observation_required": row["direct_observation_required"],
            "proxy_allowed": row["proxy_allowed"],
            "availability_state": by_requirement.get(str(row["requirement_id"]), {}).get(
                "availability_state", "unavailable"
            ),
            "directness": by_requirement.get(str(row["requirement_id"]), {}).get("directness", "unavailable"),
            "proxy_type": by_requirement.get(str(row["requirement_id"]), {}).get("proxy_type"),
            "missing_reason": by_requirement.get(str(row["requirement_id"]), {}).get("missing_reason", ""),
        }
        for row in requirements
    )


def _requirement_with_source_types(row: dict[str, Any], repository: DataSufficiencyRepository) -> dict[str, Any]:
    value = dict(row)
    schema_version = str(value["schema_version"])
    requirement_id = str(value["requirement_id"])
    effective_at = datetime.fromisoformat(str(value["effective_at"]))
    recorded_at = datetime.fromisoformat(str(value["recorded_at"]))
    value["required_source_types"] = repository.requirement_source_types(
        requirement_id,
        schema_version,
        effective_at=effective_at,
        recorded_at=recorded_at,
    )
    value["accepted_proxy_types"] = repository.requirement_proxy_types(
        requirement_id,
        schema_version,
        effective_at=effective_at,
        recorded_at=recorded_at,
    )
    value["source"] = "repository"
    return value


def _visible_at(effective_at: datetime, recorded_at: datetime, context: SufficiencyReportContext) -> bool:
    cutoff = context.cutoff_at
    if cutoff is None:
        return True
    if effective_at > cutoff:
        return False
    if context.strict_known_by_hunter and recorded_at > cutoff:
        return False
    return True


def _limitations(assessments: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(str(row["limitations_summary"]) for row in assessments if str(row.get("limitations_summary", "")))


def _confidence_limits(assessments: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(
        str(row["limitations_summary"])
        for row in assessments
        if row["sufficiency_state"] in {"degraded", "insufficient", "unavailable"}
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _average(values: Iterable[float]) -> float:
    rows = tuple(float(value) for value in values)
    return round(sum(rows) / len(rows), 4) if rows else 0.0


def _fallback_created_at() -> datetime:
    return datetime.fromisoformat("2026-01-01T00:00:00+00:00")
