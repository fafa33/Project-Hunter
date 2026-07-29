from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

VALUATION_METHODOLOGY_SCHEMA_VERSION = "valuation-methodology-v1"

QualityState = Literal["accepted", "stale", "partial", "ambiguous", "unavailable", "unsupported"]
ConflictState = Literal["none", "open", "contested", "resolved"]

QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]

# ADR 0022 "Permitted methodology": the sole permitted model family for the first
# supported entity class. Fixed by the accepted ADR, never caller-parameterized.
PERMITTED_MODEL_IDENTIFIER = "discounted-value-capture-flow-v1"

# ADR 0021's default horizon, reaffirmed fixed by ADR 0022 for the first supported
# entity class ("a fixed 365-day horizon (per ADR 0021's default, unless a future
# amendment to this ADR authorizes another)"). Fixed, never caller-parameterized.
REQUIRED_HORIZON_DAYS = 365

# ADR 0021 "Anti-double-counting and correlation policy": valuation and mispricing
# are one correlation group by construction. Fixed, never caller-parameterized.
REQUIRED_CORRELATION_GROUP = "valuation-mispricing"

# ADR 0022 "Persistence requirements": normalization-policy ID is explicitly
# "unassigned until Milestone 4 in the accompanying implementation issue." Milestone 2
# must not assign one; this constant documents that the only legal value is None.
MILESTONE_2_NORMALIZATION_POLICY_ID: None = None

# The only ADR currently authorizing this record family. Fixed, never
# caller-parameterized; extending authorization to another ADR is a future
# governance decision, not a Milestone 2 runtime choice.
AUTHORIZING_ADR_REFERENCE = "ADR-0022"

# Identifies the sole canonical authority permitted to produce this record family.
# Persisted on every record as an auditable marker that it passed through
# CanonicalValuationMethodologyAuthority and not through some other code path.
CANONICAL_AUTHORITY_ID = "canonical-valuation-methodology-authority-v1"


@dataclass(frozen=True)
class ValuationMethodologySnapshot:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    entity_class_criteria_id: str
    entity_class_criteria_version: str
    permitted_model_identifier: str
    horizon_days: int
    currency: str
    discount_rate_policy_id: str
    discount_rate_policy_version: str
    sensitivity_policy_id: str
    sensitivity_policy_version: str
    supply_basis_selection_rule: str
    normalization_policy_id: str | None
    correlation_group: str
    authorizing_adr_reference: str
    authorized_by: str
    accepts_assembled_evidence: bool = False
    accepted_evidence_shape_ids: tuple[str, ...] = ()
    accepted_assembly_rule_versions: tuple[str, ...] = ()
    assembled_evidence_granularity_override: str | None = None
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "content_hash",
            "entity_class_criteria_id",
            "entity_class_criteria_version",
            "permitted_model_identifier",
            "currency",
            "discount_rate_policy_id",
            "discount_rate_policy_version",
            "sensitivity_policy_id",
            "sensitivity_policy_version",
            "supply_basis_selection_rule",
            "correlation_group",
            "authorizing_adr_reference",
            "authorized_by",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        # Defense-in-depth: CanonicalValuationMethodologyAuthority never accepts these
        # as caller input (they are injected as fixed constants), so this branch should
        # be unreachable through the approved service. It still guards any other
        # construction path (e.g. direct reconstruction from a persisted payload) so an
        # ADR-fixed invariant can never silently drift, matching the same
        # belt-and-suspenders pattern used for supply-coherence in hunter.value_capture.
        if self.permitted_model_identifier != PERMITTED_MODEL_IDENTIFIER:
            raise ValueError(
                f"permitted_model_identifier must be {PERMITTED_MODEL_IDENTIFIER!r} per ADR 0022's "
                "Permitted methodology section"
            )
        if self.horizon_days != REQUIRED_HORIZON_DAYS:
            raise ValueError(f"horizon_days must be {REQUIRED_HORIZON_DAYS} per ADR 0021/ADR 0022")
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        if self.normalization_policy_id is not None:
            raise ValueError(
                "normalization_policy_id must remain unavailable (None) until Milestone 4, per ADR 0022's "
                "Persistence requirements section"
            )
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise ValueError(f"authorizing_adr_reference must be {AUTHORIZING_ADR_REFERENCE!r} for Milestone 2")
        _normalize_chronology(self)
        if self.accepts_assembled_evidence:
            if not self.accepted_evidence_shape_ids or not self.accepted_assembly_rule_versions:
                raise ValueError("assembled evidence opt-in requires governed shape and assembly-rule versions")
        elif (
            self.accepted_evidence_shape_ids
            or self.accepted_assembly_rule_versions
            or self.assembled_evidence_granularity_override is not None
        ):
            raise ValueError("assembled evidence instructions require explicit opt-in")
        object.__setattr__(self, "accepted_evidence_shape_ids", tuple(self.accepted_evidence_shape_ids))
        object.__setattr__(self, "accepted_assembly_rule_versions", tuple(self.accepted_assembly_rule_versions))
        _validate_correction(self)


def _required_text(instance: object, *fields: str) -> None:
    for field in fields:
        value = getattr(instance, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be blank")


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"unsupported {name}: {value}")


def _normalize_chronology(instance: object) -> None:
    effective = _utc("effective_at", instance.effective_at)
    recorded = _utc("recorded_at", instance.recorded_at)
    known = _utc("known_at", instance.known_at)
    if effective > recorded:
        raise ValueError("effective_at must be <= recorded_at")
    if recorded > known:
        raise ValueError("recorded_at must be <= known_at")
    object.__setattr__(instance, "effective_at", effective)
    object.__setattr__(instance, "recorded_at", recorded)
    object.__setattr__(instance, "known_at", known)


def _validate_correction(instance: object) -> None:
    predecessor = instance.supersedes_record_id
    reason = instance.correction_reason
    if bool(predecessor) != bool(reason.strip()):
        raise ValueError("supersedes_record_id and correction_reason must be provided together")


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
