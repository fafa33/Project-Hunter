from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final


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


def validate_authoritative_input(
    value: object,
    *,
    family: str,
    project_id: str,
    cycle_effective_at: datetime,
) -> None:
    _validate_authority_class(value)
    _validate_identity(value, project_id)
    _validate_correction_state(value)
    _validate_freshness(value, family, cycle_effective_at)


def _validate_authority_class(value: object) -> None:
    authority = str(getattr(value, "authority_class", PRODUCTION_AUTHORITY)).strip().lower()
    if authority in NON_SCORING_AUTHORITIES:
        raise CommitteeInputPolicyError(f"{authority} committee input cannot affect authoritative scoring")
    if authority != PRODUCTION_AUTHORITY:
        raise CommitteeInputPolicyError("unknown committee input authority class")


def _validate_identity(value: object, project_id: str) -> None:
    source_project_id = getattr(value, "project_id", None)
    if source_project_id is None:
        return
    if str(source_project_id).strip() != project_id:
        raise CommitteeInputPolicyError("committee input project identity mismatch")


def _validate_correction_state(value: object) -> None:
    if bool(getattr(value, "is_superseded", False)):
        raise CommitteeInputPolicyError("superseded committee input cannot be scored")
    if bool(getattr(value, "is_invalidated", False)):
        raise CommitteeInputPolicyError("invalidated committee input cannot be scored")
    lifecycle = str(getattr(value, "lifecycle_state", "active")).strip().lower()
    if lifecycle in {"superseded", "invalidated", "retracted"}:
        raise CommitteeInputPolicyError("inactive correction lineage cannot be scored")


def _validate_freshness(value: object, family: str, cycle_effective_at: datetime) -> None:
    policy = _FRESHNESS.get(family)
    if policy is None:
        raise CommitteeInputPolicyError("committee input family has no freshness policy")
    source_time = getattr(
        value,
        "effective_at",
        getattr(value, "recorded_at", getattr(value, "created_at", None)),
    )
    if not isinstance(source_time, datetime):
        raise CommitteeInputPolicyError("committee input must expose a freshness timestamp")
    if cycle_effective_at - source_time > policy:
        raise CommitteeInputPolicyError("stale committee input cannot enter authoritative scoring")
