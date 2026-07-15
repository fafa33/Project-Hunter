from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.sufficiency.models import (
    AVAILABILITY_STATES,
    DEGRADED_MODE_OUTCOMES,
    PROXY_SIGNAL_TYPES,
    AvailabilityState,
    BlockingLevel,
    DataAvailability,
    DataRequirement,
    DegradedModeOutcome,
    ProxySignalType,
)

DEFAULT_SUFFICIENCY_POLICY_ID = "data-sufficiency-default-policy"
DEFAULT_SUFFICIENCY_POLICY_VERSION = "data-sufficiency-policy-v1"
DEFAULT_SUFFICIENCY_SCHEMA_VERSION = "data-sufficiency-v1"


@dataclass(frozen=True)
class ProxySignalPolicy:
    policy_id: str
    policy_version: str
    proxy_type: ProxySignalType
    allowed_requirement_kinds: tuple[str, ...]
    limitation_text: str
    confidence_impact: float
    may_satisfy_direct_observation: bool
    effective_at: datetime
    recorded_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("policy_id", "policy_version", "limitation_text", "schema_version"):
            _text(name, getattr(self, name))
        if self.proxy_type not in PROXY_SIGNAL_TYPES or self.proxy_type == "none":
            msg = f"proxy_type must be one of {sorted(PROXY_SIGNAL_TYPES - {'none'})}"
            raise ValueError(msg)
        if not self.allowed_requirement_kinds:
            msg = "allowed_requirement_kinds is required"
            raise ValueError(msg)
        for kind in self.allowed_requirement_kinds:
            _text("allowed_requirement_kinds", kind)
        if not 0.0 <= self.confidence_impact <= 1.0:
            msg = "confidence_impact must be between 0 and 1"
            raise ValueError(msg)
        if self.may_satisfy_direct_observation:
            msg = "proxy signals may not satisfy direct observations"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "confidence_impact", round(float(self.confidence_impact), 4))


@dataclass(frozen=True)
class DegradedModeDecision:
    outcome: DegradedModeOutcome
    reason: str
    blocks_output: bool
    preserves_score: bool = True
    treats_missing_as_negative: bool = False


@dataclass(frozen=True)
class DegradedModePolicy:
    policy_id: str
    policy_version: str
    unavailable_required_outcome: DegradedModeOutcome
    partial_required_outcome: DegradedModeOutcome
    stale_required_outcome: DegradedModeOutcome
    proxy_for_direct_outcome: DegradedModeOutcome
    optional_missing_outcome: DegradedModeOutcome
    effective_at: datetime
    recorded_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("policy_id", "policy_version", "schema_version"):
            _text(name, getattr(self, name))
        for name in (
            "unavailable_required_outcome",
            "partial_required_outcome",
            "stale_required_outcome",
            "proxy_for_direct_outcome",
            "optional_missing_outcome",
        ):
            if getattr(self, name) not in DEGRADED_MODE_OUTCOMES:
                msg = f"{name} must be one of {sorted(DEGRADED_MODE_OUTCOMES)}"
                raise ValueError(msg)
        if self.proxy_for_direct_outcome == "normal":
            msg = "proxy_for_direct_outcome cannot be normal"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)

    def decide(self, *, requirement: DataRequirement, availability: DataAvailability) -> DegradedModeDecision:
        if requirement.requirement_id != availability.requirement_id:
            msg = "availability requirement_id does not match requirement"
            raise ValueError(msg)

        if availability.directness == "proxy_signal" and requirement.direct_observation_required:
            return DegradedModeDecision(
                outcome=self.proxy_for_direct_outcome,
                reason="proxy_signal_cannot_satisfy_required_direct_observation",
                blocks_output=self.proxy_for_direct_outcome == "blocked_insufficient_evidence",
            )

        if availability.availability_state == "available":
            if availability.directness == "proxy_signal":
                outcome = "degraded_non_blocking" if requirement.proxy_allowed else self.proxy_for_direct_outcome
                return DegradedModeDecision(
                    outcome=outcome,
                    reason="proxy_signal_available_only_as_limited_context",
                    blocks_output=outcome == "blocked_insufficient_evidence",
                )
            return DegradedModeDecision(
                outcome="normal", reason="direct_or_derived_data_available", blocks_output=False
            )

        if availability.availability_state == "stale":
            outcome = (
                self.stale_required_outcome if _material(requirement.blocking_level) else self.optional_missing_outcome
            )
            return DegradedModeDecision(
                outcome=outcome,
                reason="required_data_stale",
                blocks_output=outcome == "blocked_insufficient_evidence",
            )

        if availability.availability_state == "partial":
            outcome = (
                self.partial_required_outcome
                if _material(requirement.blocking_level)
                else self.optional_missing_outcome
            )
            return DegradedModeDecision(
                outcome=outcome,
                reason="required_data_partial",
                blocks_output=outcome == "blocked_insufficient_evidence",
            )

        if availability.availability_state == "unavailable":
            outcome = (
                self.unavailable_required_outcome
                if _material(requirement.blocking_level)
                else self.optional_missing_outcome
            )
            return DegradedModeDecision(
                outcome=outcome,
                reason="required_data_unavailable",
                blocks_output=outcome == "blocked_insufficient_evidence",
            )

        _unreachable_availability(availability.availability_state)


def default_degraded_mode_policy(*, effective_at: datetime, recorded_at: datetime) -> DegradedModePolicy:
    return DegradedModePolicy(
        policy_id=DEFAULT_SUFFICIENCY_POLICY_ID,
        policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
        unavailable_required_outcome="blocked_insufficient_evidence",
        partial_required_outcome="degraded_material_limitation",
        stale_required_outcome="degraded_material_limitation",
        proxy_for_direct_outcome="blocked_insufficient_evidence",
        optional_missing_outcome="degraded_non_blocking",
        effective_at=effective_at,
        recorded_at=recorded_at,
        schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
    )


def _material(blocking_level: BlockingLevel) -> bool:
    return blocking_level in {"required_for_output", "required_for_high_confidence", "required_for_full_report"}


def _unreachable_availability(value: AvailabilityState) -> None:
    if value not in AVAILABILITY_STATES:
        msg = f"availability_state must be one of {sorted(AVAILABILITY_STATES)}"
        raise ValueError(msg)
    msg = f"unhandled availability_state: {value}"
    raise AssertionError(msg)


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)
