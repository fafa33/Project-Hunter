from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from hunter.execution.hashing import stable_fingerprint
from hunter.opportunity.configuration import OpportunityConfig
from hunter.opportunity.metrics import GATING_FACTORS, NEGATIVE_FACTORS, POSITIVE_FACTORS, OpportunityMetricSnapshot
from hunter.persistence.records import MarketValidationProjectResultRecord

FactorState = Literal["available", "missing", "unavailable", "stale", "legacy_non_strict", "invalid"]
AuthorityStatus = Literal["approved_source", "unowned"]

CURRENT_OPPORTUNITY_FACTORS = (*POSITIVE_FACTORS, *GATING_FACTORS, *NEGATIVE_FACTORS)


@dataclass(frozen=True, slots=True)
class OpportunityFactorAuthority:
    factor: str
    status: AuthorityStatus
    semantic_owner: str
    record_type: str | None
    fields: tuple[str, ...]
    normalization: str
    confidence_rule: str
    time_rule: str
    missing_policy: str
    anti_substitution_rule: str


@dataclass(frozen=True, slots=True)
class OpportunityFactorDiagnostic:
    factor: str
    state: FactorState
    value: float | None
    reason: str
    record_id: str | None = None
    record_version: str | None = None
    source_record_ids: tuple[str, ...] = ()
    source_versions: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    confidence: float | None = None
    effective_at: datetime | None = None
    recorded_at: datetime | None = None
    known_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "effective_at": _time(self.effective_at),
            "evidence_references": list(self.evidence_references),
            "factor": self.factor,
            "known_at": _time(self.known_at),
            "reason": self.reason,
            "record_id": self.record_id,
            "record_version": self.record_version,
            "recorded_at": _time(self.recorded_at),
            "source_record_ids": list(self.source_record_ids),
            "source_versions": list(self.source_versions),
            "state": self.state,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class OpportunityAssemblyResult:
    snapshot: OpportunityMetricSnapshot
    diagnostics: tuple[OpportunityFactorDiagnostic, ...]
    effective_as_of: datetime
    known_by: datetime
    configuration_fingerprint: str
    authority_classification: str = "experimental"

    def as_dict(self) -> dict[str, object]:
        return {
            "authority_classification": self.authority_classification,
            "configuration_fingerprint": self.configuration_fingerprint,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "effective_as_of": _time(self.effective_as_of),
            "known_by": _time(self.known_by),
            "snapshot": {
                "effective_at": _time(self.snapshot.effective_at),
                "evidence_ids": list(self.snapshot.evidence_ids),
                "metadata": self.snapshot.metadata.as_dict(),
                "missing_evidence": list(self.snapshot.missing_evidence),
                "project_id": self.snapshot.project_id,
                "values": self.snapshot.values.as_dict(),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


class OpportunityAuthoritySource(Protocol):
    def market_validation_project_records(self, project_id: str) -> tuple[MarketValidationProjectResultRecord, ...]: ...


class OpportunitySourceUnavailableError(RuntimeError):
    pass


class EmptyOpportunityAuthoritySource:
    def market_validation_project_records(self, project_id: str) -> tuple[MarketValidationProjectResultRecord, ...]:
        return ()


class CanonicalMarketValidationOpportunityAdapter:
    """Read-only adapter over explicitly selected canonical run histories."""

    def __init__(self, repository: object, validation_run_ids: tuple[str, ...]) -> None:
        self._repository = repository
        self._validation_run_ids = tuple(validation_run_ids)

    def market_validation_project_records(self, project_id: str) -> tuple[MarketValidationProjectResultRecord, ...]:
        records = []
        for run_id in self._validation_run_ids:
            history = self._repository.project_history(run_id, project_id)  # type: ignore[attr-defined]
            records.extend(history)
        records.sort(key=lambda item: (item.effective_at, item.created_at, item.id))
        return tuple(records)


APPROVED_MARKET_VALIDATION_FIELDS: dict[str, str] = {
    "validation_health": "validation_health",
    "evidence_freshness": "data_freshness",
    "confidence": "confidence",
    "risk": "risk",
    "missing_evidence": "missing_evidence",
}


def opportunity_factor_authorities() -> tuple[OpportunityFactorAuthority, ...]:
    declarations = []
    for factor in CURRENT_OPPORTUNITY_FACTORS:
        field = APPROVED_MARKET_VALIDATION_FIELDS.get(factor)
        if field is None:
            declarations.append(
                OpportunityFactorAuthority(
                    factor=factor,
                    status="unowned",
                    semantic_owner="no approved persisted source",
                    record_type=None,
                    fields=(),
                    normalization="none; value is not assembled",
                    confidence_rule="none; factor remains missing",
                    time_rule="no selection is permitted",
                    missing_policy="mark missing and omit from values",
                    anti_substitution_rule="similarly named Market Validation, Dashboard, corpus, report, or current-state fields are forbidden",
                )
            )
            continue
        normalization = (
            "len(missing_evidence) / current factor count, clamped to [0,1]"
            if factor == "missing_evidence"
            else "identity mapping; persisted [0,1] value"
        )
        declarations.append(
            OpportunityFactorAuthority(
                factor=factor,
                status="approved_source",
                semantic_owner=f"Market Validation ProjectValidationResult.{field}",
                record_type="market-validation-project-result",
                fields=(field,),
                normalization=normalization,
                confidence_rule="preserve the canonical project-result confidence without fabrication",
                time_rule="service strict-known selection by effective_at, recorded_at, known_at, and supersession",
                missing_policy="fail closed; stale, legacy, invalid, unavailable, or absent records do not provide a value",
                anti_substitution_rule=f"only canonical persisted {field} is allowed; experimental or similarly named sources are forbidden",
            )
        )
    return tuple(declarations)


class OpportunityAssessmentService:
    """Assembles an experimental snapshot; it does not score or persist it."""

    def __init__(
        self,
        source: OpportunityAuthoritySource | None = None,
        config: OpportunityConfig | None = None,
    ) -> None:
        self.source = source or EmptyOpportunityAuthoritySource()
        self.config = config or OpportunityConfig()
        self.authorities = opportunity_factor_authorities()
        configured = {factor for factor, _ in self.config.factor_weights} | set(NEGATIVE_FACTORS)
        declared = {item.factor for item in self.authorities}
        if configured != declared:
            raise ValueError("Opportunity factor authority map does not match the current configuration")

    def assemble(
        self,
        project_id: str,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> OpportunityAssemblyResult:
        if not project_id.strip():
            raise ValueError("project_id is required")
        effective_cutoff = _aware("effective_as_of", effective_as_of)
        known_cutoff = _aware("known_by", known_by)
        config_fingerprint = stable_fingerprint(
            "experimental-opportunity-input-assembly",
            self.config,
            schema_version="opportunity-input-authority-v1",
        )
        try:
            records = self.source.market_validation_project_records(project_id)
        except Exception as exc:
            diagnostics = tuple(
                (
                    self._unavailable(item, f"approved persisted source unavailable: {type(exc).__name__}")
                    if item.status == "approved_source"
                    else self._unowned(item)
                )
                for item in self.authorities
            )
            return self._result(project_id, effective_cutoff, known_cutoff, config_fingerprint, diagnostics)

        selected, selection_state, selection_reason = _select_record(records, effective_cutoff, known_cutoff)
        diagnostics = []
        for authority in self.authorities:
            if authority.status == "unowned":
                diagnostics.append(self._unowned(authority))
            elif selected is None:
                diagnostics.append(
                    OpportunityFactorDiagnostic(
                        factor=authority.factor,
                        state=selection_state,
                        value=None,
                        reason=selection_reason,
                    )
                )
            else:
                diagnostics.append(self._from_record(authority, selected))
        return self._result(project_id, effective_cutoff, known_cutoff, config_fingerprint, tuple(diagnostics))

    def _from_record(
        self,
        authority: OpportunityFactorAuthority,
        record: MarketValidationProjectResultRecord,
    ) -> OpportunityFactorDiagnostic:
        field = authority.fields[0]
        if field in record.stale_evidence or authority.factor in record.stale_evidence:
            state: FactorState = "stale"
            value = None
            reason = f"canonical source declares {field} stale"
        else:
            raw = getattr(record, field, None)
            if field == "missing_evidence" and isinstance(raw, tuple):
                value = round(min(1.0, len(raw) / len(CURRENT_OPPORTUNITY_FACTORS)), 4)
            elif isinstance(raw, int | float) and not isinstance(raw, bool) and 0.0 <= float(raw) <= 1.0:
                value = round(float(raw), 4)
            else:
                value = None
            state = "available" if value is not None else "invalid"
            reason = "strict-known canonical field selected" if value is not None else "canonical field is invalid"
        return OpportunityFactorDiagnostic(
            factor=authority.factor,
            state=state,
            value=value,
            reason=reason,
            record_id=record.id,
            record_version=record.schema_version,
            source_record_ids=record.source_record_ids,
            source_versions=record.source_versions,
            evidence_references=record.evidence_references,
            confidence=record.confidence,
            effective_at=record.effective_at,
            recorded_at=record.created_at,
            known_at=record.known_at,
        )

    @staticmethod
    def _unowned(authority: OpportunityFactorAuthority) -> OpportunityFactorDiagnostic:
        return OpportunityFactorDiagnostic(
            authority.factor,
            "missing",
            None,
            "no approved persisted source; similarly named sources cannot substitute",
        )

    @staticmethod
    def _unavailable(authority: OpportunityFactorAuthority, reason: str) -> OpportunityFactorDiagnostic:
        return OpportunityFactorDiagnostic(authority.factor, "unavailable", None, reason)

    def _result(
        self,
        project_id: str,
        effective_as_of: datetime,
        known_by: datetime,
        config_fingerprint: str,
        diagnostics: tuple[OpportunityFactorDiagnostic, ...],
    ) -> OpportunityAssemblyResult:
        values = {
            item.factor: item.value for item in diagnostics if item.state == "available" and item.value is not None
        }
        if not any(item.factor == "validation_health" and item.state == "available" for item in diagnostics):
            values["validation_health"] = 0.0
        missing = tuple(sorted(item.factor for item in diagnostics if item.state != "available"))
        evidence = tuple(
            sorted(
                {
                    reference
                    for item in diagnostics
                    if item.state == "available"
                    for reference in item.evidence_references
                }
            )
        )
        snapshot = OpportunityMetricSnapshot(
            project_id=project_id,
            effective_at=effective_as_of,
            values=values,
            evidence_ids=evidence,
            missing_evidence=missing,
            metadata={
                "authority_classification": "experimental",
                "configuration_fingerprint": config_fingerprint,
                "known_by": known_by.isoformat(),
                "strict_known": True,
            },
        )
        return OpportunityAssemblyResult(snapshot, diagnostics, effective_as_of, known_by, config_fingerprint)


def _select_record(
    records: tuple[MarketValidationProjectResultRecord, ...],
    effective_as_of: datetime,
    known_by: datetime,
) -> tuple[MarketValidationProjectResultRecord | None, FactorState, str]:
    typed = tuple(record for record in records if isinstance(record, MarketValidationProjectResultRecord))
    if len(typed) != len(records):
        return None, "invalid", "source returned a non-canonical record type"
    scoped = tuple(
        record for record in typed if record.effective_at <= effective_as_of and record.created_at <= known_by
    )
    if not scoped:
        return None, "missing", "no canonical record exists at the requested cutoffs"
    strict = tuple(
        record
        for record in scoped
        if record.known_at is not None and record.known_time_limitation is None and record.known_at <= known_by
    )
    if not strict:
        return None, "legacy_non_strict", "eligible records do not have trustworthy known-time provenance"
    superseded = {record.supersedes_id for record in strict if record.supersedes_id is not None}
    current = tuple(record for record in strict if record.id not in superseded)
    if not current:
        return None, "invalid", "strict-known lineage has no current record"
    selected = max(current, key=lambda record: (record.effective_at, record.created_at, record.id))
    if selected.authorized_payload.get("authority_classification") != "production":
        return None, "invalid", "canonical production authority classification is absent"
    return selected, "available", "strict-known canonical record selected"


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _time(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
