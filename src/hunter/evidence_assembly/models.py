from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from hunter.valuation_methodology.models import MethodologyEvidenceInputContract as MethodologyEvidenceInputContract
from hunter.value_capture.models import EconomicClaimIdentity, FundamentalEvidenceRecord

ASSEMBLED_EVIDENCE_SCHEMA_VERSION = "assembled-fundamental-evidence-v1"
ASSEMBLY_RULE_VERSION = "lossless-exact-coverage-v1"
DETERMINISTIC_ORDER = "accounting_period_start-then-record_id"

AccountingMeaning = Literal["period_specific", "cumulative", "event"]
EvidenceMarker = Literal["assembled"]
AssemblyQualityState = Literal["accepted", "unavailable"]
AssemblyConflictState = Literal["none", "open", "resolved"]


@dataclass(frozen=True)
class EvidenceShape:
    shape_id: str
    registry_version: str
    evidence_type: str
    accounting_meaning: AccountingMeaning
    cadence: str
    composition_operation: Literal["exact_sum", "none"]
    active: bool

    def __post_init__(self) -> None:
        _required(self, "shape_id", "registry_version", "evidence_type", "cadence")
        if self.accounting_meaning not in {"period_specific", "cumulative", "event"}:
            raise ValueError("unknown accounting meaning")
        if self.composition_operation not in {"exact_sum", "none"}:
            raise ValueError("unknown composition operation")


@dataclass(frozen=True)
class AssemblyConstituent:
    record: FundamentalEvidenceRecord
    shape_id: str
    currency: str
    raw_unit: str
    accounting_meaning: AccountingMeaning
    supply_basis_id: str
    pathway_id: str
    representation_continuity_proof_id: str = ""
    pathway_continuity_proof_id: str = ""
    supply_basis_continuity_proof_id: str = ""

    def __post_init__(self) -> None:
        _required(
            self,
            "shape_id",
            "currency",
            "raw_unit",
            "supply_basis_id",
            "pathway_id",
        )
        if self.record.unit is None or self.record.amount is None:
            raise ValueError("constituent must carry an amount and unit")


@dataclass(frozen=True)
class AuthoritativeEvidenceSemantics:
    evidence_record_id: str
    evidence_record_version: str
    shape_id: str
    currency: str
    raw_unit: str
    accounting_meaning: AccountingMeaning
    supply_basis_id: str
    pathway_id: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: Literal["accepted"]
    conflict_state: Literal["none", "resolved"]
    content_hash: str

    def __post_init__(self) -> None:
        _required(
            self,
            "evidence_record_id",
            "evidence_record_version",
            "shape_id",
            "currency",
            "raw_unit",
            "supply_basis_id",
            "pathway_id",
            "content_hash",
        )
        object.__setattr__(self, "effective_at", _utc(self.effective_at))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))
        object.__setattr__(self, "known_at", _utc(self.known_at))


@dataclass(frozen=True)
class AssemblyConflictRecord:
    record_id: str
    entity_id: str
    economic_claim_id: str
    accounting_window_start: datetime
    accounting_window_end: datetime
    candidate_record_ids: tuple[str, ...]
    reason: str
    recorded_at: datetime
    known_at: datetime
    conflict_state: Literal["open"] = "open"
    content_hash: str = ""

    def __post_init__(self) -> None:
        _required(self, "record_id", "entity_id", "economic_claim_id", "reason", "content_hash")
        if not self.candidate_record_ids:
            raise ValueError("conflict candidates must not be empty")
        object.__setattr__(self, "accounting_window_start", _utc(self.accounting_window_start))
        object.__setattr__(self, "accounting_window_end", _utc(self.accounting_window_end))
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))
        object.__setattr__(self, "known_at", _utc(self.known_at))


@dataclass(frozen=True)
class AssembledFundamentalEvidenceRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    assembly_rule_version: str
    evidence_shape_registry_version: str
    methodology_contract_id: str
    methodology_contract_version: str
    identity: EconomicClaimIdentity
    representation_continuity_proof_id: str
    value_capture_pathway_id: str
    pathway_continuity_proof_id: str
    supply_basis_id: str
    supply_basis_continuity_proof_id: str
    currency: str
    unit: str
    accounting_meaning: AccountingMeaning
    accounting_window_start: datetime
    accounting_window_end: datetime
    accounting_period_days: int
    amount: str
    constituent_record_ids: tuple[str, ...]
    constituent_logical_ids: tuple[str, ...]
    constituent_versions: tuple[str, ...]
    constituent_content_hashes: tuple[str, ...]
    constituent_source_ids: tuple[str, ...]
    constituent_source_references: tuple[str, ...]
    constituent_shape_ids: tuple[str, ...]
    deterministic_constituent_order: str
    source_count: int
    assembly_content_hash: str
    aggregation_lineage: str
    evidence_marker: EvidenceMarker
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: AssemblyQualityState
    confidence_state: str
    conflict_state: AssemblyConflictState
    completeness_state: Literal["exact_complete"]
    continuity_proof_state: str
    non_overlap_proof_state: Literal["gap_free_non_overlapping"]
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    supersession_state: Literal["active", "superseded"] = "active"
    content_hash: str = ""

    def __post_init__(self) -> None:
        _required(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "assembly_rule_version",
            "evidence_shape_registry_version",
            "methodology_contract_id",
            "methodology_contract_version",
            "value_capture_pathway_id",
            "supply_basis_id",
            "currency",
            "unit",
            "amount",
            "deterministic_constituent_order",
            "assembly_content_hash",
            "aggregation_lineage",
            "confidence_state",
            "continuity_proof_state",
            "content_hash",
        )
        start = _utc(self.accounting_window_start)
        end = _utc(self.accounting_window_end)
        effective = _utc(self.effective_at)
        recorded = _utc(self.recorded_at)
        known = _utc(self.known_at)
        if start >= end:
            raise ValueError("accounting window must have positive duration")
        if effective != end:
            raise ValueError("effective_at must equal accounting_window_end")
        if recorded < effective:
            raise ValueError("recorded_at must not precede effective_at (accounting_window_end)")
        if known < recorded:
            raise ValueError("known_at must not precede recorded_at")
        if self.accounting_period_days != (end - start).days:
            raise ValueError("accounting_period_days does not match accounting window")
        if self.evidence_marker != "assembled":
            raise ValueError("assembled evidence marker is mandatory")
        if self.completeness_state != "exact_complete":
            raise ValueError("partial assembly is prohibited")
        if self.non_overlap_proof_state != "gap_free_non_overlapping":
            raise ValueError("non-overlap proof is mandatory")
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise ValueError("only authoritative conflict-free assembled records may be persisted")
        lengths = {
            len(self.constituent_record_ids),
            len(self.constituent_logical_ids),
            len(self.constituent_versions),
            len(self.constituent_content_hashes),
            len(self.constituent_source_ids),
            len(self.constituent_source_references),
            len(self.constituent_shape_ids),
            self.source_count,
        }
        if len(lengths) != 1 or self.source_count < 2:
            raise ValueError("constituent provenance fields must have equal lengths and contain at least two records")
        if len(set(self.constituent_record_ids)) != self.source_count:
            raise ValueError("constituent record IDs must be unique")
        if self.supersedes_record_id is None and self.correction_reason:
            raise ValueError("initial assembly cannot carry a correction reason")
        if self.supersedes_record_id is not None and not self.correction_reason.strip():
            raise ValueError("correction reason is required")
        try:
            value = Decimal(self.amount)
        except InvalidOperation as exc:
            raise ValueError("amount must be decimal") from exc
        if not value.is_finite():
            raise ValueError("amount must be finite")
        object.__setattr__(self, "accounting_window_start", start)
        object.__setattr__(self, "accounting_window_end", end)
        object.__setattr__(self, "effective_at", effective)
        object.__setattr__(self, "recorded_at", recorded)
        object.__setattr__(self, "known_at", known)


def _required(instance: object, *names: str) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)
