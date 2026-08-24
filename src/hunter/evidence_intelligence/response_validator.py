"""ADR 0035 Phase A — provider-independent ResponseValidator foundation.

This module implements only the authority and allocation contracts authorized by
Issue #332:

* immutable validation-profile publication and strict-known historical
  resolution through a dedicated ``ResponseValidationProfileAuthority``;
* atomic base-validation and explicit re-validation event allocation through a
  ``ResponseValidatorFoundation``; and
* the closed top-level validation-state vocabulary and its exact precedence.

It deliberately does not inspect response bytes, execute semantic validation,
issue validation authorizations or attestations, persist terminal validation
records, allocate corrections, invoke a provider, or promote downstream state.
Requested-output contracts and Source Handling remain exact upstream identities;
this boundary references them and does not duplicate either authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from hunter.execution import Clock, SystemClock

VALIDATION_PROFILE_SCHEMA_VERSION = "response-validation-profile-v1"
VALIDATION_VOCABULARY_VERSION = "adr-0035-validation-state-v1"
BASE_VALIDATION_PURPOSE = "BASE_RESPONSE_VALIDATION"


class ResponseValidatorFoundationError(RuntimeError):
    """Base class for Phase A authority and allocation failures."""


class ResponseValidationProfileError(ResponseValidatorFoundationError):
    """Raised when canonical profile publication or history is invalid."""


class ResponseValidationProfileResolutionError(ResponseValidationProfileError):
    """Raised when strict-known profile history cannot resolve exactly once."""


class ValidationEventAllocationError(ResponseValidatorFoundationError):
    """Raised when canonical validation-event allocation fails closed."""


class UnknownValidationStateError(ResponseValidatorFoundationError, ValueError):
    """Raised when a value is outside ADR 0035's closed state vocabulary."""


class ValidationState(StrEnum):
    VALID = "VALID"
    INVALID_SYNTAX = "INVALID_SYNTAX"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_OUTPUT_CONTRACT = "INVALID_OUTPUT_CONTRACT"
    INVALID_LINEAGE = "INVALID_LINEAGE"
    INVALID_EVIDENCE_REFERENCE_STRUCTURE = "INVALID_EVIDENCE_REFERENCE_STRUCTURE"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
    RULE_UNAVAILABLE = "RULE_UNAVAILABLE"
    VALIDATOR_CAPABILITY_UNKNOWN = "VALIDATOR_CAPABILITY_UNKNOWN"
    EVIDENCE_AMBIGUOUS = "EVIDENCE_AMBIGUOUS"
    SOURCE_HANDLING_BLOCKED = "SOURCE_HANDLING_BLOCKED"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    VALIDATOR_ERROR = "VALIDATOR_ERROR"


VALIDATION_STATE_PRECEDENCE: tuple[ValidationState, ...] = (
    ValidationState.SECURITY_BLOCKED,
    ValidationState.SOURCE_HANDLING_BLOCKED,
    ValidationState.VALIDATOR_ERROR,
    ValidationState.VALIDATOR_CAPABILITY_UNKNOWN,
    ValidationState.INPUT_UNAVAILABLE,
    ValidationState.RULE_UNAVAILABLE,
    ValidationState.EVIDENCE_AMBIGUOUS,
    ValidationState.INVALID_LINEAGE,
    ValidationState.INVALID_SYNTAX,
    ValidationState.INVALID_SCHEMA,
    ValidationState.INVALID_OUTPUT_CONTRACT,
    ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
    ValidationState.PARTIAL_RESPONSE,
    ValidationState.VALID,
)

_VALIDATION_STATE_RANK = {state: rank for rank, state in enumerate(VALIDATION_STATE_PRECEDENCE)}


def canonical_validation_state(value: ValidationState | str) -> ValidationState:
    """Return a canonical state and reject every unknown value fail-closed."""
    try:
        return value if isinstance(value, ValidationState) else ValidationState(value)
    except (TypeError, ValueError) as error:
        raise UnknownValidationStateError(f"unknown canonical validation state: {value!r}") from error


def highest_precedence_validation_state(states: Iterable[ValidationState | str]) -> ValidationState:
    """Reduce simultaneous states using ADR 0035's exact highest-first order."""
    canonical = tuple(canonical_validation_state(state) for state in states)
    if not canonical:
        raise UnknownValidationStateError("at least one canonical validation state is required")
    return min(canonical, key=_VALIDATION_STATE_RANK.__getitem__)


@dataclass(frozen=True)
class ResponseValidationProfileSpec:
    """A publication proposal containing policy identities, never authority.

    The dedicated profile authority supplies publication/version/history and
    applicability time. The proposal only names exact upstream and
    validation-specific contracts that the canonical profile composes.
    """

    profile_selector: str
    requested_output_contract_identity: str
    requested_output_contract_version: str
    validator_contract_identity: str
    validator_contract_version: str
    syntax_schema_rule_identity: str
    syntax_schema_rule_version: str
    parser_canonicalization_identity: str
    parser_canonicalization_version: str
    evidence_reference_rule_identity: str
    evidence_reference_rule_version: str
    resource_policy_identity: str
    resource_policy_version: str
    required_dimensions: tuple[str, ...]
    security_rule_identity: str | None = None
    security_rule_version: str | None = None
    vocabulary_version: str = VALIDATION_VOCABULARY_VERSION
    schema_version: str = VALIDATION_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = (
            "profile_selector",
            "requested_output_contract_identity",
            "requested_output_contract_version",
            "validator_contract_identity",
            "validator_contract_version",
            "syntax_schema_rule_identity",
            "syntax_schema_rule_version",
            "parser_canonicalization_identity",
            "parser_canonicalization_version",
            "evidence_reference_rule_identity",
            "evidence_reference_rule_version",
            "resource_policy_identity",
            "resource_policy_version",
            "vocabulary_version",
            "schema_version",
        )
        for name in required:
            _required_text(name, getattr(self, name))
        if (self.security_rule_identity is None) != (self.security_rule_version is None):
            raise ResponseValidationProfileError("security rule identity and version must be supplied together")
        if self.security_rule_identity is not None:
            _required_text("security_rule_identity", self.security_rule_identity)
            _required_text("security_rule_version", self.security_rule_version)
        if self.vocabulary_version != VALIDATION_VOCABULARY_VERSION:
            raise ResponseValidationProfileError("profile must use ADR 0035's canonical validation vocabulary")
        if self.schema_version != VALIDATION_PROFILE_SCHEMA_VERSION:
            raise ResponseValidationProfileError("unknown response-validation profile schema version")
        if not self.required_dimensions:
            raise ResponseValidationProfileError("profile requires at least one validation dimension")
        for dimension in self.required_dimensions:
            _required_text("required_dimension", dimension)
        object.__setattr__(self, "required_dimensions", tuple(sorted(set(self.required_dimensions))))

    @property
    def applicability_key(self) -> str:
        return _identity(
            "response-validation-profile-applicability",
            {
                "profile_selector": self.profile_selector,
                "requested_output_contract_identity": self.requested_output_contract_identity,
                "requested_output_contract_version": self.requested_output_contract_version,
            },
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(_canonical_json(_jsonable(asdict(self)))).hexdigest()


@dataclass(frozen=True)
class ResponseValidationProfile:
    """One immutable, authority-published profile version."""

    spec: ResponseValidationProfileSpec
    profile_version: int
    applicable_from: datetime
    published_at: datetime
    known_at: datetime
    supersedes_publication_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        if self.profile_version < 1:
            raise ResponseValidationProfileError("profile version starts at 1")
        applicable_from = _aware_utc("applicable_from", self.applicable_from)
        published_at = _aware_utc("published_at", self.published_at)
        known_at = _aware_utc("known_at", self.known_at)
        if published_at > known_at:
            raise ResponseValidationProfileError("profile published_at must not follow known_at")
        if applicable_from > known_at:
            raise ResponseValidationProfileError("profile applicability cannot begin after it is known")
        if self.profile_version == 1:
            if self.supersedes_publication_id is not None or self.correction_reason:
                raise ResponseValidationProfileError("genesis profile has no supersession lineage")
        else:
            if not self.supersedes_publication_id or not self.correction_reason.strip():
                raise ResponseValidationProfileError("successor profile requires exact predecessor and reason")
        object.__setattr__(self, "applicable_from", applicable_from)
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "known_at", known_at)

    @property
    def publication_id(self) -> str:
        return _identity(
            "response-validation-profile-publication",
            {
                "spec": _jsonable(asdict(self.spec)),
                "profile_version": self.profile_version,
                "applicable_from": self.applicable_from.isoformat(),
                "published_at": self.published_at.isoformat(),
                "known_at": self.known_at.isoformat(),
                "supersedes_publication_id": self.supersedes_publication_id,
                "correction_reason": self.correction_reason,
            },
        )


@dataclass(frozen=True)
class ResponseValidationProfileResolution:
    """Exact strict-known profile/version lineage at one trusted cutoff."""

    validation_cutoff: datetime
    profile: ResponseValidationProfile
    publication_lineage: tuple[str, ...]

    def __post_init__(self) -> None:
        cutoff = _aware_utc("validation_cutoff", self.validation_cutoff)
        if not self.publication_lineage or self.publication_lineage[-1] != self.profile.publication_id:
            raise ResponseValidationProfileResolutionError("profile resolution must preserve exact publication lineage")
        object.__setattr__(self, "validation_cutoff", cutoff)

    @property
    def resolution_id(self) -> str:
        return _identity(
            "response-validation-profile-resolution",
            {
                "validation_cutoff": self.validation_cutoff.isoformat(),
                "profile_publication_id": self.profile.publication_id,
                "profile_version": self.profile.profile_version,
                "publication_lineage": self.publication_lineage,
            },
        )


@dataclass(frozen=True)
class BaseValidationKey:
    """Stable base-event key; intentionally excludes any per-run cutoff."""

    response_capture_identity: str
    requested_output_contract_identity: str
    requested_output_contract_version: str
    requested_profile_selector: str
    purpose: str = BASE_VALIDATION_PURPOSE
    schema_version: str = "base-validation-key-v1"

    def __post_init__(self) -> None:
        for name in (
            "response_capture_identity",
            "requested_output_contract_identity",
            "requested_output_contract_version",
            "requested_profile_selector",
            "schema_version",
        ):
            _required_text(name, getattr(self, name))
        if self.purpose != BASE_VALIDATION_PURPOSE:
            raise ValidationEventAllocationError("base-validation purpose is closed")

    @property
    def base_validation_key_id(self) -> str:
        return _identity("base-validation-key", asdict(self))


@dataclass(frozen=True)
class ValidationEventAllocation:
    """Canonical event identity/cutoff pair minted only by the validator boundary."""

    base_validation_key: BaseValidationKey
    validation_cutoff: datetime
    revalidation_generation: int = 0
    predecessor_validation_event_id: str | None = None
    schema_version: str = "validation-event-allocation-v1"

    def __post_init__(self) -> None:
        cutoff = _aware_utc("validation_cutoff", self.validation_cutoff)
        if self.revalidation_generation < 0:
            raise ValidationEventAllocationError("re-validation generation cannot be negative")
        if self.revalidation_generation == 0 and self.predecessor_validation_event_id is not None:
            raise ValidationEventAllocationError("base event has no predecessor")
        if self.revalidation_generation > 0 and not self.predecessor_validation_event_id:
            raise ValidationEventAllocationError("re-validation requires exact predecessor lineage")
        _required_text("schema_version", self.schema_version)
        object.__setattr__(self, "validation_cutoff", cutoff)

    @property
    def validation_event_id(self) -> str:
        return _identity(
            "response-validation-event",
            {
                "base_validation_key_id": self.base_validation_key.base_validation_key_id,
                "validation_cutoff": self.validation_cutoff.isoformat(),
                "revalidation_generation": self.revalidation_generation,
                "predecessor_validation_event_id": self.predecessor_validation_event_id,
                "schema_version": self.schema_version,
            },
        )


class ResponseValidationProfileAuthority:
    """Dedicated owner of canonical profile publication/history/resolution."""

    def __init__(self, repository: Any, *, clock: Clock | None = None) -> None:
        self._repository = repository
        self._clock = clock or SystemClock()
        self.__persistence_capability: object | None = None
        self._repository._bind_profile_authority(  # noqa: SLF001 - one-time authority capability binding
            self,
            self.__install_persistence_capability,
        )

    def __install_persistence_capability(self, capability: object) -> None:
        if self.__persistence_capability is not None:
            raise ResponseValidationProfileError("profile persistence capability is immutable once installed")
        self.__persistence_capability = capability

    def publish_profile(self, spec: ResponseValidationProfileSpec) -> ResponseValidationProfile:
        """Publish a genesis profile; authority supplies identity, version, and time."""
        if not isinstance(spec, ResponseValidationProfileSpec):
            raise ResponseValidationProfileError("canonical publication requires a profile specification")

        def build(predecessor: ResponseValidationProfile | None) -> ResponseValidationProfile:
            if predecessor is not None:
                raise ResponseValidationProfileError("profile history already exists; publish a successor")
            cutoff = _clock_now(self._clock, "profile publication")
            return ResponseValidationProfile(
                spec=spec,
                profile_version=1,
                applicable_from=cutoff,
                published_at=cutoff,
                known_at=cutoff,
            )

        return self._repository._publish_profile_authorized(  # noqa: SLF001 - authority-only persistence seam
            authority_capability=self.__persistence_capability,
            applicability_key=spec.applicability_key,
            factory=build,
        )

    def supersede_profile(
        self,
        *,
        predecessor_publication_id: str,
        spec: ResponseValidationProfileSpec,
        correction_reason: str,
    ) -> ResponseValidationProfile:
        """Append one exact non-branching successor; history is never rewritten."""
        _required_text("predecessor_publication_id", predecessor_publication_id)
        _required_text("correction_reason", correction_reason)
        if not isinstance(spec, ResponseValidationProfileSpec):
            raise ResponseValidationProfileError("canonical publication requires a profile specification")

        def build(predecessor: ResponseValidationProfile | None) -> ResponseValidationProfile:
            if predecessor is None or predecessor.publication_id != predecessor_publication_id:
                raise ResponseValidationProfileError("successor must name the exact canonical profile head")
            if predecessor.spec.applicability_key != spec.applicability_key:
                raise ResponseValidationProfileError("successor cannot change profile applicability scope")
            cutoff = _clock_now(self._clock, "profile publication")
            if cutoff <= predecessor.known_at:
                raise ResponseValidationProfileError("profile publication clock must advance beyond predecessor")
            return ResponseValidationProfile(
                spec=spec,
                profile_version=predecessor.profile_version + 1,
                applicable_from=cutoff,
                published_at=cutoff,
                known_at=cutoff,
                supersedes_publication_id=predecessor.publication_id,
                correction_reason=correction_reason,
            )

        return self._repository._publish_profile_authorized(  # noqa: SLF001 - authority-only persistence seam
            authority_capability=self.__persistence_capability,
            applicability_key=spec.applicability_key,
            factory=build,
        )

    def profile_history(
        self, *, profile_selector: str, requested_output_contract_identity: str, requested_output_contract_version: str
    ) -> tuple[ResponseValidationProfile, ...]:
        key = _profile_applicability_key(
            profile_selector,
            requested_output_contract_identity,
            requested_output_contract_version,
        )
        return self._repository.profile_history(key)

    def resolve_strict_known(
        self,
        *,
        profile_selector: str,
        requested_output_contract_identity: str,
        requested_output_contract_version: str,
        trusted_cutoff: datetime,
    ) -> ResponseValidationProfileResolution:
        """Resolve exactly one profile using only state knowable at ``trusted_cutoff``."""
        cutoff = _aware_utc("trusted_cutoff", trusted_cutoff)
        history = self.profile_history(
            profile_selector=profile_selector,
            requested_output_contract_identity=requested_output_contract_identity,
            requested_output_contract_version=requested_output_contract_version,
        )
        eligible = tuple(
            profile
            for profile in history
            if profile.applicable_from <= cutoff and profile.published_at <= cutoff and profile.known_at <= cutoff
        )
        if not eligible:
            raise ResponseValidationProfileResolutionError("canonical profile history is unavailable at cutoff")

        by_id = {profile.publication_id: profile for profile in eligible}
        if len(by_id) != len(eligible):
            raise ResponseValidationProfileResolutionError("canonical profile history contains duplicate identity")
        superseded_ids = {
            profile.supersedes_publication_id for profile in eligible if profile.supersedes_publication_id is not None
        }
        heads = tuple(profile for profile in eligible if profile.publication_id not in superseded_ids)
        if len(heads) != 1:
            raise ResponseValidationProfileResolutionError("canonical profile history is ambiguous at cutoff")

        lineage_reversed: list[str] = []
        cursor: ResponseValidationProfile | None = heads[0]
        seen: set[str] = set()
        while cursor is not None:
            if cursor.publication_id in seen:
                raise ResponseValidationProfileResolutionError("canonical profile history contains a cycle")
            seen.add(cursor.publication_id)
            lineage_reversed.append(cursor.publication_id)
            predecessor_id = cursor.supersedes_publication_id
            if predecessor_id is None:
                if cursor.profile_version != 1:
                    raise ResponseValidationProfileResolutionError("canonical profile lineage has no genesis")
                cursor = None
            else:
                predecessor = by_id.get(predecessor_id)
                if predecessor is None or predecessor.profile_version + 1 != cursor.profile_version:
                    raise ResponseValidationProfileResolutionError("canonical profile predecessor is unresolvable")
                cursor = predecessor
        if seen != set(by_id):
            raise ResponseValidationProfileResolutionError("canonical profile history contains disconnected branches")

        return ResponseValidationProfileResolution(
            validation_cutoff=cutoff,
            profile=heads[0],
            publication_lineage=tuple(reversed(lineage_reversed)),
        )


class ResponseValidatorFoundation:
    """Trusted owner of base/re-validation event identity and cutoff allocation."""

    def __init__(
        self,
        repository: Any,
        profile_authority: ResponseValidationProfileAuthority,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._profile_authority = profile_authority
        self._clock = clock or SystemClock()
        self.__persistence_capability: object | None = None
        self._repository._bind_event_allocator(  # noqa: SLF001 - one-time allocator capability binding
            self,
            self.__install_persistence_capability,
        )

    def __install_persistence_capability(self, capability: object) -> None:
        if self.__persistence_capability is not None:
            raise ValidationEventAllocationError("event-allocation persistence capability is immutable once installed")
        self.__persistence_capability = capability

    def allocate_base_validation(self, key: BaseValidationKey) -> ValidationEventAllocation:
        """Atomically create or join the one canonical base event for ``key``."""
        if not isinstance(key, BaseValidationKey):
            raise ValidationEventAllocationError("base validation requires the canonical key contract")

        def build() -> ValidationEventAllocation:
            return ValidationEventAllocation(
                base_validation_key=key,
                validation_cutoff=_clock_now(self._clock, "validation allocation"),
            )

        return self._repository._allocate_base_event_authorized(  # noqa: SLF001 - authority-only persistence seam
            authority_capability=self.__persistence_capability,
            key=key,
            factory=build,
        )

    def allocate_revalidation(self, *, predecessor_validation_event_id: str) -> ValidationEventAllocation:
        """Create or join the next event after the exact predecessor."""
        _required_text("predecessor_validation_event_id", predecessor_validation_event_id)

        def build(predecessor: ValidationEventAllocation) -> ValidationEventAllocation:
            cutoff = _clock_now(self._clock, "re-validation allocation")
            if cutoff <= predecessor.validation_cutoff:
                raise ValidationEventAllocationError("re-validation cutoff must advance beyond predecessor")
            return ValidationEventAllocation(
                base_validation_key=predecessor.base_validation_key,
                validation_cutoff=cutoff,
                revalidation_generation=predecessor.revalidation_generation + 1,
                predecessor_validation_event_id=predecessor.validation_event_id,
            )

        return self._repository._allocate_revalidation_event_authorized(  # noqa: SLF001
            authority_capability=self.__persistence_capability,
            predecessor_validation_event_id=predecessor_validation_event_id,
            factory=build,
        )

    def resolve_profile_for_event(
        self,
        allocation: ValidationEventAllocation,
    ) -> ResponseValidationProfileResolution:
        """Resolve at the persisted allocator-owned cutoff, rejecting substitution."""
        canonical = self._repository.validation_event(allocation.validation_event_id)
        if canonical is None or canonical != allocation:
            raise ValidationEventAllocationError("validation allocation is unknown or does not match canonical state")
        key = canonical.base_validation_key
        return self._profile_authority.resolve_strict_known(
            profile_selector=key.requested_profile_selector,
            requested_output_contract_identity=key.requested_output_contract_identity,
            requested_output_contract_version=key.requested_output_contract_version,
            trusted_cutoff=canonical.validation_cutoff,
        )


def _profile_applicability_key(
    profile_selector: str,
    requested_output_contract_identity: str,
    requested_output_contract_version: str,
) -> str:
    spec = {
        "profile_selector": _required_text("profile_selector", profile_selector),
        "requested_output_contract_identity": _required_text(
            "requested_output_contract_identity", requested_output_contract_identity
        ),
        "requested_output_contract_version": _required_text(
            "requested_output_contract_version", requested_output_contract_version
        ),
    }
    return _identity("response-validation-profile-applicability", spec)


def _clock_now(clock: Clock, context: str) -> datetime:
    return _aware_utc(f"{context} trusted clock", clock.now())


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ResponseValidatorFoundationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ResponseValidatorFoundationError(f"{name} must be non-blank canonical text")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _identity(kind: str, value: object) -> str:
    return f"{kind}:{hashlib.sha256(_canonical_json(_jsonable(value))).hexdigest()}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
