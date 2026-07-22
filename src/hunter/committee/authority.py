from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol


class CommitteeInputPolicyError(ValueError):
    pass


PRODUCTION_AUTHORITY: Final[str] = "production-authoritative"
NON_SCORING_AUTHORITIES: Final[frozenset[str]] = frozenset({"descriptive-only", "experimental", "unavailable"})

_FRESHNESS: Final[dict[str, timedelta]] = {
    "intelligence": timedelta(days=30),
    "fused_intelligence": timedelta(days=30),
    "evidence": timedelta(days=90),
    "snapshot": timedelta(days=7),
    "opportunity": timedelta(days=30),
    "probability": timedelta(days=30),
    "pattern": timedelta(days=90),
    "necessity": timedelta(days=180),
}


@dataclass(frozen=True)
class CommitteeInputIdentity:
    project_id: str
    entity_id: str
    representation_id: str
    chain_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "entity_id", "representation_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.chain_id is not None and not self.chain_id.strip():
            raise ValueError("chain_id must be non-empty when provided")


@dataclass(frozen=True)
class ResolvedCommitteeInput:
    record_id: str
    family: str
    value: object
    authority_class: str
    identity: CommitteeInputIdentity
    recorded_at: datetime
    effective_at: datetime
    lineage_id: str
    revision_id: str
    current_revision_id: str
    superseded_at: datetime | None = None
    invalidated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("record_id", "family", "authority_class", "lineage_id", "revision_id", "current_revision_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in ("recorded_at", "effective_at", "superseded_at", "invalidated_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{name} must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))


class CommitteeInputResolver(Protocol):
    def resolve(self, *, record_id: str, family: str, known_at: datetime) -> ResolvedCommitteeInput | None: ...


def validate_authoritative_input(
    supplied: object,
    resolved: ResolvedCommitteeInput,
    *,
    family: str,
    expected_identity: CommitteeInputIdentity,
    cycle_effective_at: datetime,
) -> None:
    if resolved.family != family:
        raise CommitteeInputPolicyError("resolved committee input family mismatch")
    supplied_id = str(getattr(supplied, "assessment_id", getattr(supplied, "id", ""))).strip()
    if supplied_id != resolved.record_id:
        raise CommitteeInputPolicyError("resolved committee input ID mismatch")
    if supplied != resolved.value:
        raise CommitteeInputPolicyError("caller-supplied committee input differs from authoritative persisted record")
    _validate_authority_class(resolved.authority_class)
    _validate_identity(resolved.identity, expected_identity)
    _validate_chronology(resolved, cycle_effective_at)
    _validate_correction_lineage(resolved, cycle_effective_at)
    _validate_freshness(resolved, family, cycle_effective_at)


def _validate_authority_class(authority_class: str) -> None:
    authority = authority_class.strip().lower()
    if not authority:
        raise CommitteeInputPolicyError("committee input requires explicit authority class")
    if authority in NON_SCORING_AUTHORITIES:
        raise CommitteeInputPolicyError(f"{authority} committee input cannot affect authoritative scoring")
    if authority != PRODUCTION_AUTHORITY:
        raise CommitteeInputPolicyError("unknown committee input authority class")


def _validate_identity(actual: CommitteeInputIdentity, expected: CommitteeInputIdentity) -> None:
    if actual != expected:
        raise CommitteeInputPolicyError("committee input identity mismatch")


def _validate_chronology(resolved: ResolvedCommitteeInput, cycle_effective_at: datetime) -> None:
    if resolved.recorded_at > cycle_effective_at:
        raise CommitteeInputPolicyError("future-known input cannot enter committee evaluation")
    if resolved.effective_at > cycle_effective_at:
        raise CommitteeInputPolicyError("future-effective input cannot enter committee evaluation")


def _validate_correction_lineage(resolved: ResolvedCommitteeInput, cycle_effective_at: datetime) -> None:
    if resolved.revision_id != resolved.current_revision_id:
        raise CommitteeInputPolicyError("superseded correction-lineage member cannot be scored")
    if resolved.superseded_at is not None and resolved.superseded_at <= cycle_effective_at:
        raise CommitteeInputPolicyError("superseded committee input cannot be scored")
    if resolved.invalidated_at is not None and resolved.invalidated_at <= cycle_effective_at:
        raise CommitteeInputPolicyError("invalidated committee input cannot be scored")


def _validate_freshness(
    resolved: ResolvedCommitteeInput,
    family: str,
    cycle_effective_at: datetime,
) -> None:
    policy = _FRESHNESS.get(family)
    if policy is None:
        raise CommitteeInputPolicyError("committee input family has no freshness policy")
    if cycle_effective_at - resolved.effective_at > policy:
        raise CommitteeInputPolicyError("stale committee input cannot enter authoritative scoring")
