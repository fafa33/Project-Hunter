from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from hunter.value_capture.models import EconomicClaimIdentity

ASSEMBLED_EVIDENCE_SCHEMA_VERSION = "assembled-fundamental-evidence-v1"
ASSEMBLY_RULE_VERSION = "lossless-exact-coverage-v1"
DETERMINISTIC_ORDER = "accounting_period_start-then-record_id"

AccountingMeaning = Literal["period_specific", "cumulative", "event"]
Cadence = Literal["daily", "monthly", "quarterly", "event_driven", "irregular", "epoch_based"]


@dataclass(frozen=True)
class EvidenceShape:
    shape_id: str
    evidence_type: str
    source_methodology: str
    accounting_meaning: AccountingMeaning
    cadence: Cadence
    composition_operation: Literal["exact_sum", "none"]
    active: bool
    currency: str
    unit: str
    compatible_shape_ids: tuple[str, ...] = ()
    compatible_cadences: tuple[Cadence, ...] = ()

    def __post_init__(self) -> None:
        _required(self, "shape_id", "evidence_type", "source_methodology", "cadence", "currency", "unit")
        if self.accounting_meaning not in {"period_specific", "cumulative", "event"}:
            raise ValueError("unknown accounting meaning")
        if self.composition_operation not in {"exact_sum", "none"}:
            raise ValueError("unknown composition operation")
        object.__setattr__(self, "compatible_shape_ids", tuple(sorted(self.compatible_shape_ids)))
        object.__setattr__(self, "compatible_cadences", tuple(sorted(self.compatible_cadences)))


@dataclass(frozen=True)
class AssemblyConstituent:
    evidence_record_id: str
    supply_basis_record_id: str
    pathway_rule_record_id: str

    def __post_init__(self) -> None:
        _required(
            self,
            "evidence_record_id",
            "supply_basis_record_id",
            "pathway_rule_record_id",
        )


@dataclass(frozen=True)
class AssemblyConflictRecord:
    record_id: str
    logical_id: str
    entity_id: str
    economic_claim_id: str
    accounting_window_start: datetime
    accounting_window_end: datetime
    candidate_record_ids: tuple[str, ...]
    candidate_logical_ids: tuple[str, ...]
    candidate_versions: tuple[str, ...]
    candidate_content_hashes: tuple[str, ...]
    candidate_source_ids: tuple[str, ...]
    candidate_effective_at: tuple[str, ...]
    candidate_recorded_at: tuple[str, ...]
    candidate_known_at: tuple[str, ...]
    registry_record_id: str
    registry_id: str
    registry_version: str
    registry_content_hash: str
    methodology_record_id: str
    methodology_logical_id: str
    methodology_version: str
    methodology_content_hash: str
    pathway_classifications: tuple[str, ...]
    shape_classifications: tuple[str, ...]
    assembly_request_id: str
    replay_cutoff: datetime
    conflict_category: str
    reason: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    conflict_state: Literal["open"] = "open"
    content_hash: str = ""

    def __post_init__(self) -> None:
        _required(
            self,
            "record_id",
            "logical_id",
            "entity_id",
            "economic_claim_id",
            "registry_record_id",
            "registry_id",
            "registry_version",
            "registry_content_hash",
            "methodology_record_id",
            "methodology_logical_id",
            "methodology_version",
            "methodology_content_hash",
            "assembly_request_id",
            "conflict_category",
            "reason",
            "content_hash",
        )
        size = len(self.candidate_record_ids)
        if size == 0 or any(
            len(values) != size
            for values in (
                self.candidate_logical_ids,
                self.candidate_versions,
                self.candidate_content_hashes,
                self.candidate_source_ids,
                self.candidate_effective_at,
                self.candidate_recorded_at,
                self.candidate_known_at,
                self.pathway_classifications,
                self.shape_classifications,
            )
        ):
            raise ValueError("conflict provenance must be complete and positionally aligned")
        _normalize_times(
            self,
            "accounting_window_start",
            "accounting_window_end",
            "replay_cutoff",
            "effective_at",
            "recorded_at",
            "known_at",
        )
        if self.known_at > self.replay_cutoff:
            raise ValueError("conflict must be known by replay cutoff")


@dataclass(frozen=True)
class AssembledFundamentalEvidenceRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    assembly_rule_version: str
    registry_record_id: str
    registry_id: str
    registry_version: str
    registry_content_hash: str
    methodology_record_id: str
    methodology_logical_id: str
    methodology_version: str
    methodology_content_hash: str
    identity: EconomicClaimIdentity
    value_capture_pathway_record_id: str
    value_capture_pathway_version: str
    value_capture_pathway_content_hash: str
    supply_basis_record_id: str
    supply_basis_version: str
    supply_basis_content_hash: str
    currency: str
    unit: str
    accounting_meaning: AccountingMeaning
    cadence: Cadence
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
    evidence_marker: Literal["assembled"]
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: Literal["accepted"]
    confidence_state: str
    conflict_state: Literal["none", "resolved"]
    completeness_state: Literal["exact_complete"]
    continuity_proof_state: Literal["same-canonical-identity-pathway-and-supply-basis"]
    non_overlap_proof_state: Literal["gap_free_non_overlapping"]
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        _required(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "assembly_rule_version",
            "registry_record_id",
            "registry_id",
            "registry_version",
            "registry_content_hash",
            "methodology_record_id",
            "methodology_logical_id",
            "methodology_version",
            "methodology_content_hash",
            "value_capture_pathway_record_id",
            "value_capture_pathway_version",
            "value_capture_pathway_content_hash",
            "supply_basis_record_id",
            "supply_basis_version",
            "supply_basis_content_hash",
            "currency",
            "unit",
            "amount",
            "assembly_content_hash",
            "aggregation_lineage",
            "confidence_state",
            "continuity_proof_state",
            "content_hash",
        )
        _normalize_times(
            self,
            "accounting_window_start",
            "accounting_window_end",
            "effective_at",
            "recorded_at",
            "known_at",
        )
        if self.accounting_window_start >= self.accounting_window_end:
            raise ValueError("accounting window must have positive duration")
        if self.effective_at != self.accounting_window_end:
            raise ValueError("effective_at must equal accounting window end")
        if self.known_at < self.recorded_at:
            raise ValueError("known_at must not precede recorded_at")
        if self.accounting_period_days != (self.accounting_window_end - self.accounting_window_start).days:
            raise ValueError("accounting period does not match window")
        if self.evidence_marker != "assembled" or self.completeness_state != "exact_complete":
            raise ValueError("partial or unmarked assembly is prohibited")
        if self.non_overlap_proof_state != "gap_free_non_overlapping":
            raise ValueError("gap-free non-overlap proof is mandatory")
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
            raise ValueError("constituent provenance must contain at least two aligned records")
        if len(set(self.constituent_record_ids)) != self.source_count:
            raise ValueError("constituent record IDs must be unique")
        if bool(self.supersedes_record_id) != bool(self.correction_reason.strip()):
            raise ValueError("correction predecessor and reason must be provided together")
        try:
            amount = Decimal(self.amount)
        except InvalidOperation as exc:
            raise ValueError("amount must be decimal") from exc
        if not amount.is_finite():
            raise ValueError("amount must be finite")


@dataclass(frozen=True)
class AssemblyLineageProjection:
    record: AssembledFundamentalEvidenceRecord
    supersession_state: Literal["active", "superseded"]


def _required(instance: object, *names: str) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")


def _normalize_times(instance: object, *names: str) -> None:
    for name in names:
        value = getattr(instance, name)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
        object.__setattr__(instance, name, value.astimezone(UTC))
