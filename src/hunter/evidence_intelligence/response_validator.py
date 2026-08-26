"""ADR 0035 Phases A and B — provider-independent ResponseValidator boundary.

This module implements only the authority and allocation contracts authorized by
Issue #332:

* immutable validation-profile publication and strict-known historical
  resolution through a dedicated ``ResponseValidationProfileAuthority``;
* atomic base-validation and explicit re-validation event allocation through a
  ``ResponseValidatorFoundation``; and
* the closed top-level validation-state vocabulary and its exact precedence.

Phase B adds strict authorization of an already allocated event, deterministic
semantic validation, and in-memory state-compatible attestations.  It still does
not persist terminal validation records, mint durable-acceptance chronology,
allocate corrections, invoke a provider, or promote downstream state.
Requested-output contracts, Source Handling, and Model Adapter lineage remain
upstream authority; this boundary resolves and binds them without duplicating
their ownership.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from typing import Any

from hunter.evidence_intelligence.model_adapter import (
    ModelAttemptOutcomeRecord,
    ModelAttemptRecord,
    ModelHandoffRecord,
    ProviderResponseArtifact,
    TransientResponseAccessError,
    TransientResponseHandoffVault,
)
from hunter.evidence_intelligence.pre_model import (
    EvidencePreModelSourceHandlingAuthority,
    resolve_pre_model_source_handling,
)
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError
from hunter.execution import Clock, SystemClock

VALIDATION_PROFILE_SCHEMA_VERSION = "response-validation-profile-v1"
VALIDATION_VOCABULARY_VERSION = "adr-0035-validation-state-v1"
BASE_VALIDATION_PURPOSE = "BASE_RESPONSE_VALIDATION"
RESPONSE_VALIDATION_AUTHORIZATION_SCHEMA_VERSION = "response-validation-authorization-v1"
RESPONSE_VALIDATION_OUTCOME_SCHEMA_VERSION = "response-semantic-validation-outcome-v1"
RESPONSE_VALIDATION_ATTESTATION_SCHEMA_VERSION = "response-validation-attestation-v1"


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


class ResponseValidationAuthorizationError(ResponseValidatorFoundationError):
    """Raised when asserted or presented authorization coordinates are not canonical."""


class ResponseValidationExecutionError(ResponseValidatorFoundationError):
    """Raised when semantic execution is attempted outside its authorization."""


class ResponseValidationRuleUnavailable(ResponseValidatorFoundationError):
    """Internal signal that an exact profile-required executable rule is unavailable."""


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


class ValidationInputMode(StrEnum):
    DURABLE = "DURABLE"
    TRANSIENT_NOT_RETAINED = "TRANSIENT_NOT_RETAINED"


class ValidationAttestationKind(StrEnum):
    SUCCESS = "SUCCESS"
    REFUSAL = "REFUSAL"


@dataclass(frozen=True)
class ResponseValidationAuthorityCoordinates:
    """Exact canonical authority and lineage bound into one authorization."""

    validation_event_id: str
    validation_cutoff: datetime
    profile_resolution_id: str
    profile_publication_id: str
    profile_version: int
    validator_contract_identity: str
    validator_contract_version: str
    requested_output_contract_identity: str
    requested_output_contract_version: str
    output_contract_hash: str
    source_handling_resolution_id: str
    source_handling_fact_record_id: str
    source_handling_policy_record_id: str
    source_handling_registry_id: str
    source_handling_authorization_rule_id: str
    response_capture_identity: str
    response_evidence_state: str
    attempt_id: str
    handoff_id: str
    outcome_id: str
    execution_profile_identity: str
    request_evidence_identity: str
    build_record_id: str
    prompt_artifact_id: str
    intent_id: str
    ledger_id: str
    allocation_id: str
    package_id: str
    evidence_input_identity: str
    input_mode: ValidationInputMode
    revalidation_generation: int
    predecessor_validation_event_id: str | None
    schema_version: str = RESPONSE_VALIDATION_AUTHORIZATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "validation_cutoff", _aware_utc("validation_cutoff", self.validation_cutoff))
        for name in (
            "validation_event_id",
            "profile_resolution_id",
            "profile_publication_id",
            "validator_contract_identity",
            "validator_contract_version",
            "requested_output_contract_identity",
            "requested_output_contract_version",
            "output_contract_hash",
            "source_handling_resolution_id",
            "source_handling_fact_record_id",
            "source_handling_policy_record_id",
            "source_handling_registry_id",
            "source_handling_authorization_rule_id",
            "response_capture_identity",
            "response_evidence_state",
            "attempt_id",
            "handoff_id",
            "outcome_id",
            "execution_profile_identity",
            "request_evidence_identity",
            "build_record_id",
            "prompt_artifact_id",
            "intent_id",
            "ledger_id",
            "allocation_id",
            "package_id",
            "evidence_input_identity",
            "schema_version",
        ):
            _required_text(name, getattr(self, name))
        if self.profile_version < 1 or self.revalidation_generation < 0:
            raise ResponseValidationAuthorizationError("authorization version/generation coordinates are invalid")

    @property
    def validation_subject_id(self) -> str:
        return _identity("response-validation-subject", _jsonable(asdict(self)))


_AUTHORIZATION_MINT = object()
_ATTESTATION_MINT = object()


@dataclass(frozen=True, init=False)
class ResponseValidationAuthorization:
    """Single-use validator-issued authorization; callers cannot construct one."""

    coordinates: ResponseValidationAuthorityCoordinates
    schema_version: str

    def __init__(
        self,
        mint: object,
        *,
        coordinates: ResponseValidationAuthorityCoordinates,
        schema_version: str = RESPONSE_VALIDATION_AUTHORIZATION_SCHEMA_VERSION,
    ) -> None:
        if mint is not _AUTHORIZATION_MINT:
            raise ResponseValidationAuthorizationError(
                "validation authorization is minted only by the canonical ResponseValidator"
            )
        if not isinstance(coordinates, ResponseValidationAuthorityCoordinates):
            raise ResponseValidationAuthorizationError("authorization requires canonical coordinates")
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "schema_version", _required_text("schema_version", schema_version))

    @property
    def authorization_id(self) -> str:
        return _identity(
            "response-validation-authorization",
            {"coordinates": _jsonable(asdict(self.coordinates)), "schema_version": self.schema_version},
        )


@dataclass(frozen=True)
class ResponseValidationFinding:
    dimension: str
    state: ValidationState
    reason_code: str

    def __post_init__(self) -> None:
        _required_text("dimension", self.dimension)
        _required_text("reason_code", self.reason_code)
        object.__setattr__(self, "state", canonical_validation_state(self.state))


@dataclass(frozen=True)
class ResponseSemanticValidationOutcome:
    """Canonical in-memory semantic decision for later mechanical persistence."""

    authorization_id: str
    coordinates: ResponseValidationAuthorityCoordinates
    state: ValidationState
    findings: tuple[ResponseValidationFinding, ...]
    executed: bool
    schema_version: str = RESPONSE_VALIDATION_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text("authorization_id", self.authorization_id)
        _required_text("schema_version", self.schema_version)
        state = canonical_validation_state(self.state)
        canonical_findings = tuple(_canonical_findings(self.findings))
        if not canonical_findings:
            raise ResponseValidationExecutionError("semantic outcome requires at least one finding")
        if highest_precedence_validation_state(item.state for item in canonical_findings) != state:
            raise ResponseValidationExecutionError("semantic outcome state does not match canonical precedence")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "findings", canonical_findings)

    @property
    def semantic_outcome_id(self) -> str:
        return _identity("response-semantic-validation-outcome", _jsonable(asdict(self)))


@dataclass(frozen=True)
class ResponseValidationRefusal:
    """Truthful pre-semantic refusal with only authority that actually resolved."""

    validation_event_id: str
    validation_cutoff: datetime
    state: ValidationState
    reason_code: str
    available_authority: tuple[tuple[str, str], ...]
    schema_version: str = "response-validation-refusal-v1"

    def __post_init__(self) -> None:
        _required_text("validation_event_id", self.validation_event_id)
        _required_text("reason_code", self.reason_code)
        _required_text("schema_version", self.schema_version)
        object.__setattr__(self, "validation_cutoff", _aware_utc("validation_cutoff", self.validation_cutoff))
        state = canonical_validation_state(self.state)
        if state not in {
            ValidationState.INPUT_UNAVAILABLE,
            ValidationState.RULE_UNAVAILABLE,
            ValidationState.VALIDATOR_CAPABILITY_UNKNOWN,
            ValidationState.SOURCE_HANDLING_BLOCKED,
            ValidationState.SECURITY_BLOCKED,
        }:
            raise ResponseValidationAuthorizationError("state is not a pre-semantic refusal state")
        normalized = tuple(
            sorted(
                (_required_text("authority name", key), _required_text(key, value))
                for key, value in self.available_authority
            )
        )
        if len({key for key, _ in normalized}) != len(normalized):
            raise ResponseValidationAuthorizationError("refusal authority coordinates must be unique")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "available_authority", normalized)

    @property
    def refusal_id(self) -> str:
        return _identity("response-validation-refusal", _jsonable(asdict(self)))


@dataclass(frozen=True, init=False)
class ResponseValidationAttestation:
    """Non-caller-mintable, state-compatible in-memory attestation."""

    kind: ValidationAttestationKind
    decision_id: str
    validation_event_id: str
    state: ValidationState
    authorization_id: str | None
    schema_version: str

    def __init__(
        self,
        mint: object,
        *,
        kind: ValidationAttestationKind,
        decision_id: str,
        validation_event_id: str,
        state: ValidationState,
        authorization_id: str | None,
        schema_version: str = RESPONSE_VALIDATION_ATTESTATION_SCHEMA_VERSION,
    ) -> None:
        if mint is not _ATTESTATION_MINT:
            raise ResponseValidationAuthorizationError(
                "validation attestation is minted only by the canonical ResponseValidator"
            )
        if kind is ValidationAttestationKind.SUCCESS and authorization_id is None:
            raise ResponseValidationAuthorizationError("success attestation requires exact authorization")
        if kind is ValidationAttestationKind.REFUSAL and authorization_id is not None:
            raise ResponseValidationAuthorizationError("refusal attestation cannot substitute a success authorization")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "decision_id", _required_text("decision_id", decision_id))
        object.__setattr__(self, "validation_event_id", _required_text("validation_event_id", validation_event_id))
        object.__setattr__(self, "state", canonical_validation_state(state))
        object.__setattr__(self, "authorization_id", authorization_id)
        object.__setattr__(self, "schema_version", _required_text("schema_version", schema_version))

    @property
    def attestation_id(self) -> str:
        return _identity("response-validation-attestation", _jsonable(asdict(self)))


@dataclass(frozen=True)
class ResponseValidationRefusalResult:
    refusal: ResponseValidationRefusal
    attestation: ResponseValidationAttestation


@dataclass(frozen=True)
class ResponseValidationAuthorizationResult:
    authorization: ResponseValidationAuthorization | None = None
    refusal: ResponseValidationRefusalResult | None = None

    def __post_init__(self) -> None:
        if (self.authorization is None) == (self.refusal is None):
            raise ResponseValidationAuthorizationError("authorization result must contain authorization or refusal")

    @property
    def authorized(self) -> bool:
        return self.authorization is not None


@dataclass(frozen=True)
class ResponseValidationExecutionResult:
    outcome: ResponseSemanticValidationOutcome
    attestation: ResponseValidationAttestation


_SUPPORTED_DIMENSIONS = frozenset(
    {
        "SYNTAX",
        "SCHEMA",
        "OUTPUT_CONTRACT",
        "LINEAGE",
        "EVIDENCE_REFERENCE_STRUCTURE",
        "PARTIAL_RESPONSE",
        "SECURITY",
    }
)

_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "tool_calls",
        "tools",
        "fetch",
        "fetch_requests",
        "http_requests",
        "repository_writes",
        "schema_changes",
        "configuration_changes",
        "config_changes",
    }
)


@dataclass(frozen=True)
class DeterministicJsonValidationRuntime:
    """Exact executable capability for one profile-selected JSON rule family."""

    validator_contract_identity: str = "response-validator-contract"
    validator_contract_version: str = "1"
    syntax_schema_rule_identity: str = "syntax-schema-rules"
    syntax_schema_rule_version: str = "1"
    parser_canonicalization_identity: str = "json-parser-contract"
    parser_canonicalization_version: str = "1"
    evidence_reference_rule_identity: str = "evidence-reference-structure"
    evidence_reference_rule_version: str = "1"
    resource_policy_identity: str = "bounded-validation-resources"
    resource_policy_version: str = "1"
    security_rule_identity: str = "validator-security-structure"
    security_rule_version: str = "1"
    maximum_response_bytes: int = 1_048_576
    maximum_json_depth: int = 64

    def __post_init__(self) -> None:
        if self.maximum_response_bytes <= 0 or self.maximum_json_depth <= 0:
            raise ResponseValidationRuleUnavailable("validator response bounds must be positive")

    def capability_matches(self, profile: ResponseValidationProfile) -> bool:
        spec = profile.spec
        return (
            spec.validator_contract_identity == self.validator_contract_identity
            and spec.validator_contract_version == self.validator_contract_version
        )

    def rules_match(self, profile: ResponseValidationProfile) -> bool:
        spec = profile.spec
        security_matches = (
            spec.security_rule_identity == self.security_rule_identity
            and spec.security_rule_version == self.security_rule_version
        )
        if "SECURITY" not in spec.required_dimensions and spec.security_rule_identity is None:
            security_matches = True
        return (
            spec.syntax_schema_rule_identity == self.syntax_schema_rule_identity
            and spec.syntax_schema_rule_version == self.syntax_schema_rule_version
            and spec.parser_canonicalization_identity == self.parser_canonicalization_identity
            and spec.parser_canonicalization_version == self.parser_canonicalization_version
            and spec.evidence_reference_rule_identity == self.evidence_reference_rule_identity
            and spec.evidence_reference_rule_version == self.evidence_reference_rule_version
            and spec.resource_policy_identity == self.resource_policy_identity
            and spec.resource_policy_version == self.resource_policy_version
            and security_matches
            and set(spec.required_dimensions).issubset(_SUPPORTED_DIMENSIONS)
        )

    def evaluate(
        self,
        *,
        response_text: str,
        coordinates: ResponseValidationAuthorityCoordinates,
        output_contract: Mapping[str, Any],
        evidence_inputs: tuple[tuple[str, str], ...],
        provider_status_metadata: tuple[tuple[str, str], ...],
        required_dimensions: tuple[str, ...],
    ) -> tuple[ResponseValidationFinding, ...]:
        if len(response_text.encode("utf-8")) > self.maximum_response_bytes:
            raise ResponseValidationRuleUnavailable("response exceeds the exact installed resource policy")
        if _json_nesting_exceeds(response_text, self.maximum_json_depth):
            raise ResponseValidationRuleUnavailable("response exceeds the exact installed JSON-depth policy")

        required = set(required_dimensions)
        findings: list[ResponseValidationFinding] = []
        try:
            decoded = json.loads(
                response_text,
                parse_float=Decimal,
                parse_int=_parse_json_integer,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except InvalidOperation as error:
            raise ResponseValidationRuleUnavailable(
                "response number exceeds the exact installed numeric capability"
            ) from error
        except (json.JSONDecodeError, UnicodeError, ValueError):
            if "SYNTAX" in required:
                findings.append(
                    ResponseValidationFinding("SYNTAX", ValidationState.INVALID_SYNTAX, "RESPONSE_JSON_INVALID")
                )
            return tuple(
                findings
                or (
                    ResponseValidationFinding(
                        "VALIDATOR", ValidationState.VALIDATOR_ERROR, "PARSER_FAILED_WITHOUT_SYNTAX_DIMENSION"
                    ),
                )
            )

        if "SECURITY" in required and _contains_forbidden_response_key(decoded):
            findings.append(
                ResponseValidationFinding(
                    "SECURITY",
                    ValidationState.SECURITY_BLOCKED,
                    "FORBIDDEN_CAPABILITY_STRUCTURE_PRESENT",
                )
            )

        if "SCHEMA" in required and _schema_type_invalid(decoded, output_contract):
            findings.append(
                ResponseValidationFinding("SCHEMA", ValidationState.INVALID_SCHEMA, "OUTPUT_SCHEMA_TYPE_MISMATCH")
            )

        if "OUTPUT_CONTRACT" in required and _output_contract_invalid(decoded, output_contract):
            findings.append(
                ResponseValidationFinding(
                    "OUTPUT_CONTRACT",
                    ValidationState.INVALID_OUTPUT_CONTRACT,
                    "REQUESTED_OUTPUT_CONTRACT_VIOLATION",
                )
            )

        if "LINEAGE" in required and not _response_lineage_matches(decoded, coordinates):
            findings.append(
                ResponseValidationFinding("LINEAGE", ValidationState.INVALID_LINEAGE, "RESPONSE_LINEAGE_MISMATCH")
            )

        if "EVIDENCE_REFERENCE_STRUCTURE" in required:
            evidence_state = _evidence_reference_state(decoded, evidence_inputs)
            if evidence_state is not None:
                findings.append(evidence_state)

        if "PARTIAL_RESPONSE" in required and _response_is_partial(decoded, provider_status_metadata):
            findings.append(
                ResponseValidationFinding(
                    "PARTIAL_RESPONSE",
                    ValidationState.PARTIAL_RESPONSE,
                    "RESPONSE_DECLARED_OR_TRANSPORT_TRUNCATED",
                )
            )

        if not findings:
            findings.append(
                ResponseValidationFinding("VALIDATION", ValidationState.VALID, "ALL_REQUIRED_DIMENSIONS_VALID")
            )
        return tuple(findings)


@dataclass(frozen=True)
class _CanonicalAuthorizationInputs:
    allocation: ValidationEventAllocation
    profile_resolution: ResponseValidationProfileResolution
    response_artifact: ProviderResponseArtifact
    outcome: ModelAttemptOutcomeRecord
    attempt: ModelAttemptRecord
    handoff: ModelHandoffRecord
    output_contract: Mapping[str, Any]
    evidence_inputs: tuple[tuple[str, str], ...]
    provider_status_metadata: tuple[tuple[str, str], ...]
    required_dimensions: tuple[str, ...]
    coordinates: ResponseValidationAuthorityCoordinates


class _AuthorizationRefusalSignal(RuntimeError):
    def __init__(
        self,
        state: ValidationState,
        reason_code: str,
        available_authority: tuple[tuple[str, str], ...] = (),
        transient_cleanup_coordinates: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.state = state
        self.reason_code = reason_code
        self.available_authority = available_authority
        self.transient_cleanup_coordinates = transient_cleanup_coordinates


class ResponseValidator:
    """ADR 0035 Phase B authorization and semantic execution owner.

    Every upstream object is reloaded strict-known at the allocator-owned cutoff.
    Caller assertions are compared and rejected on any difference; they are never
    used to select authority.  No method accepts response text.
    """

    def __init__(
        self,
        foundation: ResponseValidatorFoundation,
        *,
        model_adapter_repository: Any,
        pre_model_repository: Any,
        source_handling_store: Any,
        runtime: DeterministicJsonValidationRuntime,
        transient_response_vault: TransientResponseHandoffVault | None = None,
    ) -> None:
        if type(foundation) is not ResponseValidatorFoundation:
            raise ResponseValidationAuthorizationError("Phase B requires the canonical Phase A foundation")
        if not isinstance(runtime, DeterministicJsonValidationRuntime):
            raise ResponseValidationAuthorizationError("Phase B requires a deterministic validator runtime")
        self._foundation = foundation
        self.__reservation_persistence_capability: object | None = None
        foundation._repository._bind_validation_authority(  # noqa: SLF001 - authority capability binding
            self,
            self.__install_reservation_persistence_capability,
        )
        self._model_adapter_repository = model_adapter_repository
        self._pre_model_repository = pre_model_repository
        self._source_handling_store = source_handling_store
        self._runtime = runtime
        self._transient_response_vault = transient_response_vault
        if transient_response_vault is not None:
            transient_response_vault._bind_response_validator(  # noqa: SLF001 - protected-worker owner binding
                self,
                self.__install_transient_response_boundary,
            )
        self._issued_authorizations: dict[str, ResponseValidationAuthorization] = {}
        self._issued_inputs: dict[str, _CanonicalAuthorizationInputs] = {}
        self._execution_results: dict[str, ResponseValidationExecutionResult | ResponseValidationRefusalResult] = {}
        self._event_authorization_ids: dict[str, str] = {}
        self._state_lock = threading.Lock()

    def __install_reservation_persistence_capability(self, capability: object) -> None:
        if self.__reservation_persistence_capability is not None:
            raise ResponseValidationAuthorizationError("transient reservation persistence capability is immutable")
        self.__reservation_persistence_capability = capability

    def __install_transient_response_boundary(self, boundary: Any) -> None:
        if boundary is not self._transient_response_vault:
            raise ResponseValidationAuthorizationError("transient protected-worker boundary was substituted")

    def authorize_event(
        self,
        allocation: ValidationEventAllocation,
        *,
        asserted_coordinates: ResponseValidationAuthorityCoordinates | None = None,
    ) -> ResponseValidationAuthorizationResult:
        """Authorize one exact canonical event or issue a truthful refusal."""
        with self._state_lock:
            return self._authorize_event(
                allocation,
                asserted_coordinates=asserted_coordinates,
            )

    def _authorize_event(
        self,
        allocation: ValidationEventAllocation,
        *,
        asserted_coordinates: ResponseValidationAuthorityCoordinates | None,
    ) -> ResponseValidationAuthorizationResult:
        if not isinstance(allocation, ValidationEventAllocation):
            raise ResponseValidationAuthorizationError("authorization requires a canonical Phase A allocation")
        completed_id = self._event_authorization_ids.get(allocation.validation_event_id)
        if completed_id is not None and completed_id in self._execution_results:
            completed = self._issued_authorizations[completed_id]
            if self._issued_inputs[completed_id].allocation != allocation:
                raise ResponseValidationAuthorizationError(
                    "validation event identity or cutoff does not match the completed authorization"
                )
            self._require_asserted_coordinates(asserted_coordinates, completed.coordinates)
            return ResponseValidationAuthorizationResult(authorization=completed)
        try:
            inputs = self._canonical_authorization_inputs(allocation)
        except _AuthorizationRefusalSignal as signal:
            self._discard_transient_response(
                signal.transient_cleanup_coordinates,
                refusing_validation_event_id=allocation.validation_event_id,
            )
            return self._refusal_result(
                allocation=allocation,
                state=signal.state,
                reason_code=signal.reason_code,
                available_authority=signal.available_authority,
            )

        self._require_asserted_coordinates(asserted_coordinates, inputs.coordinates)

        if inputs.coordinates.input_mode is ValidationInputMode.TRANSIENT_NOT_RETAINED:
            repository = self._foundation._repository  # noqa: SLF001 - same owning ADR 0035 boundary
            reserved = repository._reserve_transient_capture_authorized(  # noqa: SLF001
                authority_capability=self.__reservation_persistence_capability,
                response_capture_identity=inputs.coordinates.response_capture_identity,
                validation_event_id=inputs.coordinates.validation_event_id,
            )
            if not reserved:
                return self._refusal_result(
                    allocation=allocation,
                    state=ValidationState.INPUT_UNAVAILABLE,
                    reason_code="TRANSIENT_RESPONSE_CAPTURE_RESERVED_BY_OTHER_EVENT",
                    available_authority=(
                        ("profile_publication_id", inputs.coordinates.profile_publication_id),
                        ("profile_resolution_id", inputs.coordinates.profile_resolution_id),
                        ("response_capture_identity", inputs.coordinates.response_capture_identity),
                        ("validation_event_id", inputs.coordinates.validation_event_id),
                    ),
                )

        authorization = ResponseValidationAuthorization(_AUTHORIZATION_MINT, coordinates=inputs.coordinates)
        authorization_id = authorization.authorization_id
        existing = self._issued_authorizations.get(authorization_id)
        if existing is not None:
            # Re-authorizing a retry joins the same in-memory single-use grant. If
            # it has not executed yet, preserve the already-bound transient lease;
            # a later caller cannot replace it.
            return ResponseValidationAuthorizationResult(authorization=existing)
        self._issued_authorizations[authorization_id] = authorization
        self._issued_inputs[authorization_id] = inputs
        self._event_authorization_ids[allocation.validation_event_id] = authorization_id
        return ResponseValidationAuthorizationResult(authorization=authorization)

    @staticmethod
    def _require_asserted_coordinates(
        asserted: ResponseValidationAuthorityCoordinates | None,
        canonical: ResponseValidationAuthorityCoordinates,
    ) -> None:
        if asserted is None:
            return
        if not isinstance(asserted, ResponseValidationAuthorityCoordinates):
            raise ResponseValidationAuthorizationError("asserted authority coordinates are not canonical")
        mismatches = tuple(
            item.name
            for item in fields(ResponseValidationAuthorityCoordinates)
            if getattr(asserted, item.name) != getattr(canonical, item.name)
        )
        if mismatches:
            raise ResponseValidationAuthorizationError(
                "asserted authority coordinates do not match canonical state: " + ", ".join(mismatches)
            )

    def execute(
        self, authorization: ResponseValidationAuthorization
    ) -> ResponseValidationExecutionResult | ResponseValidationRefusalResult:
        """Consume one issued authorization and produce one deterministic result.

        An ordinary retry after completion returns the exact cached outcome and
        attestation; it does not consume transient bytes again or reinterpret any
        authority using current state.
        """
        with self._state_lock:
            return self._execute_once(authorization)

    def _execute_once(
        self, authorization: ResponseValidationAuthorization
    ) -> ResponseValidationExecutionResult | ResponseValidationRefusalResult:
        canonical = self._require_issued_authorization(authorization)
        cached = self._execution_results.get(canonical.authorization_id)
        if cached is not None:
            return cached

        inputs = self._issued_inputs[canonical.authorization_id]
        try:
            rederived = self._canonical_authorization_inputs(inputs.allocation)
        except _AuthorizationRefusalSignal as signal:
            if signal.state is ValidationState.INPUT_UNAVAILABLE:
                return self._presemantic_execution_refusal(
                    canonical,
                    reason_code=signal.reason_code,
                )
            raise ResponseValidationExecutionError(
                f"authorization authority no longer rederives at its historical cutoff: {signal.reason_code}"
            ) from signal
        if rederived.coordinates != canonical.coordinates:
            raise ResponseValidationExecutionError("authorization coordinates no longer match canonical authority")

        def evaluate_text(response_text: str) -> tuple[ResponseValidationFinding, ...]:
            try:
                return tuple(
                    self._runtime.evaluate(
                        response_text=response_text,
                        coordinates=canonical.coordinates,
                        output_contract=rederived.output_contract,
                        evidence_inputs=rederived.evidence_inputs,
                        provider_status_metadata=rederived.provider_status_metadata,
                        required_dimensions=rederived.required_dimensions,
                    )
                )
            except ResponseValidationRuleUnavailable:
                return (
                    ResponseValidationFinding(
                        "RULE_AVAILABILITY",
                        ValidationState.RULE_UNAVAILABLE,
                        "EXECUTABLE_VALIDATION_RULE_UNAVAILABLE",
                    ),
                )
            except Exception:  # noqa: BLE001 - semantic failure maps to the closed validator state
                return (
                    ResponseValidationFinding(
                        "VALIDATOR_EXECUTION",
                        ValidationState.VALIDATOR_ERROR,
                        "VALIDATOR_EXECUTION_FAILED",
                    ),
                )

        if canonical.coordinates.input_mode is ValidationInputMode.DURABLE:
            content = rederived.response_artifact.content
            if content is None:
                raise ResponseValidationExecutionError("durable authorization has no canonical response content")
            findings = evaluate_text(content)
        else:
            boundary = self._transient_response_vault
            if boundary is None:
                refusal_result = self._refusal_result(
                    allocation=inputs.allocation,
                    state=ValidationState.INPUT_UNAVAILABLE,
                    reason_code="PROTECTED_TRANSIENT_WORKER_UNAVAILABLE",
                    available_authority=(("validation_event_id", inputs.allocation.validation_event_id),),
                )
                assert refusal_result.refusal is not None
                self._execution_results[canonical.authorization_id] = refusal_result.refusal
                return refusal_result.refusal
            execution_plan = {
                "authorization_id": canonical.authorization_id,
                "coordinates": asdict(canonical.coordinates),
                "output_contract": rederived.output_contract,
                "evidence_inputs": rederived.evidence_inputs,
                "provider_status_metadata": rederived.provider_status_metadata,
                "required_dimensions": rederived.required_dimensions,
                "runtime": asdict(self._runtime),
            }
            try:
                worker_result = boundary.execute_canonical_event(
                    response_capture_identity=canonical.coordinates.response_capture_identity,
                    validation_event_id=canonical.coordinates.validation_event_id,
                    execution_plan=execution_plan,
                )
            except TransientResponseAccessError:
                refusal_result = self._refusal_result(
                    allocation=inputs.allocation,
                    state=ValidationState.INPUT_UNAVAILABLE,
                    reason_code="TRANSIENT_RESPONSE_ACCESS_UNAVAILABLE",
                    available_authority=(("validation_event_id", inputs.allocation.validation_event_id),),
                )
                assert refusal_result.refusal is not None
                self._execution_results[canonical.authorization_id] = refusal_result.refusal
                return refusal_result.refusal
            if worker_result.get("kind") == "REFUSAL":
                try:
                    refusal_state = ValidationState(str(worker_result.get("state")))
                except ValueError as error:
                    raise ResponseValidationExecutionError(
                        "protected worker returned an unknown refusal state"
                    ) from error
                if refusal_state is not ValidationState.INPUT_UNAVAILABLE:
                    raise ResponseValidationExecutionError(
                        "protected worker returned a non-input refusal after authorization"
                    )
                refusal_result = self._refusal_result(
                    allocation=inputs.allocation,
                    state=refusal_state,
                    reason_code=str(worker_result.get("reason_code") or "TRANSIENT_RESPONSE_ACCESS_UNAVAILABLE"),
                    available_authority=(("validation_event_id", inputs.allocation.validation_event_id),),
                )
                assert refusal_result.refusal is not None
                self._execution_results[canonical.authorization_id] = refusal_result.refusal
                return refusal_result.refusal
            if worker_result.get("authorization_id") != canonical.authorization_id:
                raise ResponseValidationExecutionError("protected worker authorization identity mismatch")
            raw_findings = worker_result.get("findings")
            if not isinstance(raw_findings, list) or not raw_findings:
                raise ResponseValidationExecutionError("protected worker returned no semantic findings")
            findings = tuple(
                ResponseValidationFinding(
                    dimension=str(item["dimension"]),
                    state=ValidationState(str(item["state"])),
                    reason_code=str(item["reason_code"]),
                )
                for item in raw_findings
                if isinstance(item, dict)
            )
            if (
                not findings
                or str(worker_result.get("state"))
                != highest_precedence_validation_state(item.state for item in findings).value
            ):
                raise ResponseValidationExecutionError("protected worker semantic state mismatch")

        state = highest_precedence_validation_state(item.state for item in findings)
        outcome = ResponseSemanticValidationOutcome(
            authorization_id=canonical.authorization_id,
            coordinates=canonical.coordinates,
            state=state,
            findings=tuple(findings),
            executed=True,
        )
        attestation = ResponseValidationAttestation(
            _ATTESTATION_MINT,
            kind=ValidationAttestationKind.SUCCESS,
            decision_id=outcome.semantic_outcome_id,
            validation_event_id=canonical.coordinates.validation_event_id,
            state=state,
            authorization_id=canonical.authorization_id,
        )
        result = ResponseValidationExecutionResult(outcome=outcome, attestation=attestation)
        self._execution_results[canonical.authorization_id] = result
        return result

    def _presemantic_execution_refusal(
        self,
        canonical: ResponseValidationAuthorization,
        *,
        reason_code: str,
    ) -> ResponseValidationExecutionResult:
        finding = ResponseValidationFinding(
            "INPUT_AVAILABILITY",
            ValidationState.INPUT_UNAVAILABLE,
            reason_code,
        )
        outcome = ResponseSemanticValidationOutcome(
            authorization_id=canonical.authorization_id,
            coordinates=canonical.coordinates,
            state=ValidationState.INPUT_UNAVAILABLE,
            findings=(finding,),
            executed=False,
        )
        attestation = ResponseValidationAttestation(
            _ATTESTATION_MINT,
            kind=ValidationAttestationKind.REFUSAL,
            decision_id=outcome.semantic_outcome_id,
            validation_event_id=canonical.coordinates.validation_event_id,
            state=ValidationState.INPUT_UNAVAILABLE,
            authorization_id=None,
        )
        result = ResponseValidationExecutionResult(outcome=outcome, attestation=attestation)
        self._execution_results[canonical.authorization_id] = result
        return result

    def _require_issued_authorization(
        self,
        authorization: ResponseValidationAuthorization,
    ) -> ResponseValidationAuthorization:
        if type(authorization) is not ResponseValidationAuthorization:
            raise ResponseValidationExecutionError("semantic execution requires a validator-issued authorization")
        expected = self._issued_authorizations.get(authorization.authorization_id)
        if expected is None or expected is not authorization:
            raise ResponseValidationExecutionError("authorization is unknown, forged, or substituted")
        return expected

    def _refusal_result(
        self,
        *,
        allocation: ValidationEventAllocation,
        state: ValidationState,
        reason_code: str,
        available_authority: tuple[tuple[str, str], ...],
    ) -> ResponseValidationAuthorizationResult:
        refusal = ResponseValidationRefusal(
            validation_event_id=allocation.validation_event_id,
            validation_cutoff=allocation.validation_cutoff,
            state=state,
            reason_code=reason_code,
            available_authority=available_authority,
        )
        attestation = ResponseValidationAttestation(
            _ATTESTATION_MINT,
            kind=ValidationAttestationKind.REFUSAL,
            decision_id=refusal.refusal_id,
            validation_event_id=allocation.validation_event_id,
            state=state,
            authorization_id=None,
        )
        return ResponseValidationAuthorizationResult(
            refusal=ResponseValidationRefusalResult(refusal=refusal, attestation=attestation)
        )

    def _discard_transient_response(
        self,
        coordinates: tuple[tuple[str, str], ...] | None,
        *,
        refusing_validation_event_id: str,
    ) -> None:
        boundary = self._transient_response_vault
        if boundary is None or coordinates is None:
            return
        resolved = dict(coordinates)
        capture_identity = resolved.get("response_capture_identity")
        if capture_identity:
            owner = self._foundation._repository.transient_capture_owner(capture_identity)  # noqa: SLF001
            if owner is not None and owner != refusing_validation_event_id:
                # A later/refusing event must never destroy the first owner's
                # single-use body. Reservation ownership is durable and wins.
                return
        boundary.discard_authorized(**resolved)

    def _canonical_authorization_inputs(
        self,
        allocation: ValidationEventAllocation,
    ) -> _CanonicalAuthorizationInputs:
        repository = self._foundation._repository  # noqa: SLF001 - same owning ResponseValidator boundary
        canonical_allocation = repository.validation_event(allocation.validation_event_id)
        if canonical_allocation is None or canonical_allocation != allocation:
            raise ResponseValidationAuthorizationError(
                "validation event identity or cutoff does not match the canonical Phase A allocation"
            )

        try:
            profile_resolution = self._foundation.resolve_profile_for_event(canonical_allocation)
        except ResponseValidationProfileResolutionError as error:
            raise _AuthorizationRefusalSignal(
                ValidationState.RULE_UNAVAILABLE,
                "PROFILE_AUTHORITY_UNAVAILABLE_AT_VALIDATION_CUTOFF",
                (("validation_event_id", canonical_allocation.validation_event_id),),
            ) from error
        profile = profile_resolution.profile
        available = (
            ("profile_publication_id", profile.publication_id),
            ("profile_resolution_id", profile_resolution.resolution_id),
            ("validation_event_id", canonical_allocation.validation_event_id),
        )
        if not self._runtime.capability_matches(profile):
            raise _AuthorizationRefusalSignal(
                ValidationState.VALIDATOR_CAPABILITY_UNKNOWN,
                "PROFILE_VALIDATOR_CAPABILITY_UNAVAILABLE",
                available,
            )
        if not self._runtime.rules_match(profile):
            raise _AuthorizationRefusalSignal(
                ValidationState.RULE_UNAVAILABLE,
                "PROFILE_EXECUTABLE_RULE_UNAVAILABLE",
                available,
            )

        capture = self._model_adapter_repository.strict_known_response_capture(
            canonical_allocation.base_validation_key.response_capture_identity,
            canonical_allocation.validation_cutoff,
        )
        if capture is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "RESPONSE_CAPTURE_NOT_KNOWN_AT_VALIDATION_CUTOFF",
                available,
            )
        response_artifact, outcome = capture
        if outcome.outcome != "SUCCEEDED_TRANSPORT" or outcome.attempt_id is None or outcome.handoff_id is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "MODEL_ATTEMPT_HAS_NO_SUCCESSFUL_RESPONSE",
                available,
            )

        attempt = self._model_adapter_repository.strict_known_attempt(
            outcome.attempt_id,
            canonical_allocation.validation_cutoff,
        )
        handoff = self._model_adapter_repository.strict_known_handoff(
            outcome.handoff_id,
            canonical_allocation.validation_cutoff,
        )
        if attempt is None or handoff is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "MODEL_ATTEMPT_LINEAGE_NOT_KNOWN_AT_VALIDATION_CUTOFF",
                available,
            )
        self._require_model_lineage(
            allocation=canonical_allocation,
            response_artifact=response_artifact,
            outcome=outcome,
            attempt=attempt,
            handoff=handoff,
        )

        bundle = self._pre_model_repository.strict_known_bundle(
            attempt.build_record_id,
            canonical_allocation.validation_cutoff,
        )
        if bundle is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "PRE_MODEL_AUTHORITY_NOT_KNOWN_AT_VALIDATION_CUTOFF",
                available,
            )
        build = bundle.build_result.build_record
        prompt = bundle.build_result.prompt_artifact
        package = bundle.build_result.package
        if prompt is None or package is None or build.prompt_artifact_id is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "PRE_MODEL_REQUIRED_INPUT_UNAVAILABLE",
                available,
            )
        if (
            build.build_record_id != attempt.build_record_id
            or build.prompt_artifact_id != attempt.prompt_artifact_id
            or prompt.artifact_id != attempt.prompt_artifact_id
            or build.intent_id != bundle.intent.intent_id
            or build.ledger_id != bundle.build_result.ledger.ledger_id
            or build.allocation_id != bundle.build_result.allocation.allocation_id
            or build.package_id != package.package_id
        ):
            raise ResponseValidationAuthorizationError("canonical pre-model evidence/input lineage is contradictory")

        key = canonical_allocation.base_validation_key
        if (
            key.requested_output_contract_identity != bundle.intent.output_contract_id
            or key.requested_output_contract_version != bundle.intent.output_contract_version
        ):
            raise ResponseValidationAuthorizationError(
                "validation event requested-output coordinates do not match ADR 0031 authority"
            )
        if (
            profile.spec.requested_output_contract_identity != bundle.intent.output_contract_id
            or profile.spec.requested_output_contract_version != bundle.intent.output_contract_version
        ):
            raise ResponseValidationAuthorizationError(
                "validation profile requested-output coordinates do not match ADR 0031 authority"
            )
        transient_cleanup_coordinates = None
        if response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY":
            transient_cleanup_coordinates = (
                ("response_capture_identity", response_artifact.response_artifact_identity),
                ("attempt_id", attempt.attempt_id),
                ("handoff_id", handoff.handoff_id),
                ("outcome_id", outcome.outcome_id),
                ("execution_profile_identity", attempt.execution_profile_identity),
                ("response_protocol_identity", response_artifact.response_protocol_identity),
                ("response_protocol_version", response_artifact.response_protocol_version),
                ("transport_identity", response_artifact.transport_identity),
                ("transport_version", response_artifact.transport_version),
            )
        output_contract = _parse_output_contract(
            bundle.specification.output_contract,
            available_authority=available,
            maximum_depth=self._runtime.maximum_json_depth,
            transient_cleanup_coordinates=transient_cleanup_coordinates,
        )

        document_ids = {span.document_id for span in bundle.canonical_inventory}
        if len(document_ids) != 1:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "CANONICAL_EVIDENCE_INPUT_SCOPE_UNAVAILABLE",
                available,
                transient_cleanup_coordinates,
            )
        fact_at_attempt = self._source_handling_store.canonical_record_by_id("FACT", handoff.fact_record_id)
        policy_at_attempt = self._source_handling_store.canonical_record_by_id("POLICY", handoff.policy_record_id)
        if fact_at_attempt is None or policy_at_attempt is None:
            raise _AuthorizationRefusalSignal(
                ValidationState.SOURCE_HANDLING_BLOCKED,
                "SOURCE_HANDLING_LINEAGE_UNAVAILABLE",
                available,
                transient_cleanup_coordinates,
            )
        fact_scope = str(fact_at_attempt.get("scope") or "")
        policy_scope = str(policy_at_attempt.get("scope") or "")
        if fact_scope not in document_ids or policy_scope != f"policy:{fact_scope}:v1":
            raise ResponseValidationAuthorizationError(
                "Model Adapter Source Handling lineage does not match canonical evidence inputs"
            )
        validation_authority = EvidencePreModelSourceHandlingAuthority(
            store=self._source_handling_store,
            fact_scope=fact_scope,
            policy_scope=policy_scope,
            cutoff=canonical_allocation.validation_cutoff,
        )
        try:
            resolved_source = resolve_pre_model_source_handling(validation_authority)
        except SourceHandlingBlockedError as error:
            raise _AuthorizationRefusalSignal(
                ValidationState.SOURCE_HANDLING_BLOCKED,
                "SOURCE_HANDLING_AUTHORITY_BLOCKED_AT_VALIDATION_CUTOFF",
                available,
                transient_cleanup_coordinates,
            ) from error
        decision = resolved_source.decision
        if decision.get("processing_decision") != "ALLOW" or decision.get("access_decision") != "ALLOW":
            raise _AuthorizationRefusalSignal(
                ValidationState.SOURCE_HANDLING_BLOCKED,
                "SOURCE_HANDLING_DOES_NOT_AUTHORIZE_VALIDATION_ACCESS",
                available,
                transient_cleanup_coordinates,
            )

        source_ids = {
            "fact_record_id": str(decision.get("fact_record_id") or ""),
            "policy_record_id": str(decision.get("policy_record_id") or ""),
            "field_category_registry_id": str(decision.get("field_category_registry_id") or ""),
            "authorization_rule_id": str(decision.get("authorization_rule_id") or ""),
        }
        if any(not value for value in source_ids.values()):
            raise _AuthorizationRefusalSignal(
                ValidationState.SOURCE_HANDLING_BLOCKED,
                "SOURCE_HANDLING_RESOLUTION_IDENTITY_INCOMPLETE",
                available,
                transient_cleanup_coordinates,
            )
        source_resolution_id = _identity(
            "response-validation-source-handling-resolution",
            {
                "validation_cutoff": canonical_allocation.validation_cutoff,
                "decision": _jsonable(decision),
            },
        )

        if response_artifact.state == "RESPONSE_EVIDENCE_DURABLE":
            if response_artifact.content is None:
                raise _AuthorizationRefusalSignal(
                    ValidationState.INPUT_UNAVAILABLE,
                    "DURABLE_RESPONSE_CONTENT_UNAVAILABLE",
                    available,
                    transient_cleanup_coordinates,
                )
            input_mode = ValidationInputMode.DURABLE
        elif response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY":
            input_mode = ValidationInputMode.TRANSIENT_NOT_RETAINED
            boundary = self._transient_response_vault
            if boundary is None or not boundary.available_for_validation(
                response_capture_identity=response_artifact.response_artifact_identity,
                attempt_id=attempt.attempt_id,
                handoff_id=handoff.handoff_id,
                outcome_id=outcome.outcome_id,
                execution_profile_identity=attempt.execution_profile_identity,
                response_protocol_identity=response_artifact.response_protocol_identity,
                response_protocol_version=response_artifact.response_protocol_version,
                transport_identity=response_artifact.transport_identity,
                transport_version=response_artifact.transport_version,
            ):
                raise _AuthorizationRefusalSignal(
                    ValidationState.INPUT_UNAVAILABLE,
                    "TRANSIENT_RESPONSE_ACCESS_UNAVAILABLE",
                    available,
                    transient_cleanup_coordinates,
                )
        elif response_artifact.state == "RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK":
            raise _AuthorizationRefusalSignal(
                ValidationState.SECURITY_BLOCKED,
                "RESPONSE_CONTENT_NOT_ESTABLISHED_CREDENTIAL_FREE",
                available,
                transient_cleanup_coordinates,
            )
        else:
            raise _AuthorizationRefusalSignal(
                ValidationState.INPUT_UNAVAILABLE,
                "RESPONSE_EVIDENCE_STATE_UNAVAILABLE",
                available,
                transient_cleanup_coordinates,
            )

        evidence_inputs = tuple(sorted((span.span_id, span.text_hash) for span in bundle.canonical_inventory))
        evidence_input_identity = _identity(
            "response-validation-evidence-input-lineage",
            {
                "intent_id": bundle.intent.intent_id,
                "ledger_id": bundle.build_result.ledger.ledger_id,
                "allocation_id": bundle.build_result.allocation.allocation_id,
                "package_id": package.package_id,
                "evidence_inputs": evidence_inputs,
            },
        )
        coordinates = ResponseValidationAuthorityCoordinates(
            validation_event_id=canonical_allocation.validation_event_id,
            validation_cutoff=canonical_allocation.validation_cutoff,
            profile_resolution_id=profile_resolution.resolution_id,
            profile_publication_id=profile.publication_id,
            profile_version=profile.profile_version,
            validator_contract_identity=profile.spec.validator_contract_identity,
            validator_contract_version=profile.spec.validator_contract_version,
            requested_output_contract_identity=bundle.intent.output_contract_id,
            requested_output_contract_version=bundle.intent.output_contract_version,
            output_contract_hash=hashlib.sha256(bundle.specification.output_contract.encode("utf-8")).hexdigest(),
            source_handling_resolution_id=source_resolution_id,
            source_handling_fact_record_id=source_ids["fact_record_id"],
            source_handling_policy_record_id=source_ids["policy_record_id"],
            source_handling_registry_id=source_ids["field_category_registry_id"],
            source_handling_authorization_rule_id=source_ids["authorization_rule_id"],
            response_capture_identity=response_artifact.response_artifact_identity,
            response_evidence_state=response_artifact.state,
            attempt_id=attempt.attempt_id,
            handoff_id=handoff.handoff_id,
            outcome_id=outcome.outcome_id,
            execution_profile_identity=attempt.execution_profile_identity,
            request_evidence_identity=attempt.request_evidence_identity,
            build_record_id=build.build_record_id,
            prompt_artifact_id=prompt.artifact_id,
            intent_id=bundle.intent.intent_id,
            ledger_id=bundle.build_result.ledger.ledger_id,
            allocation_id=bundle.build_result.allocation.allocation_id,
            package_id=package.package_id,
            evidence_input_identity=evidence_input_identity,
            input_mode=input_mode,
            revalidation_generation=canonical_allocation.revalidation_generation,
            predecessor_validation_event_id=canonical_allocation.predecessor_validation_event_id,
        )
        return _CanonicalAuthorizationInputs(
            allocation=canonical_allocation,
            profile_resolution=profile_resolution,
            response_artifact=response_artifact,
            outcome=outcome,
            attempt=attempt,
            handoff=handoff,
            output_contract=output_contract,
            evidence_inputs=evidence_inputs,
            provider_status_metadata=response_artifact.provider_status_metadata,
            required_dimensions=profile.spec.required_dimensions,
            coordinates=coordinates,
        )

    @staticmethod
    def _require_model_lineage(
        *,
        allocation: ValidationEventAllocation,
        response_artifact: ProviderResponseArtifact,
        outcome: ModelAttemptOutcomeRecord,
        attempt: ModelAttemptRecord,
        handoff: ModelHandoffRecord,
    ) -> None:
        if response_artifact.response_artifact_identity != allocation.base_validation_key.response_capture_identity:
            raise ResponseValidationAuthorizationError("response capture identity was substituted")
        exact_equalities = (
            (outcome.attempt_id, attempt.attempt_id, "outcome attempt"),
            (outcome.handoff_id, handoff.handoff_id, "outcome handoff"),
            (response_artifact.attempt_id, attempt.attempt_id, "response attempt"),
            (response_artifact.handoff_id, handoff.handoff_id, "response handoff"),
            (handoff.attempt_id, attempt.attempt_id, "handoff attempt"),
            (outcome.build_record_id, attempt.build_record_id, "outcome build"),
            (handoff.build_record_id, attempt.build_record_id, "handoff build"),
            (outcome.prompt_artifact_id, attempt.prompt_artifact_id, "outcome prompt"),
            (handoff.prompt_artifact_id, attempt.prompt_artifact_id, "handoff prompt"),
            (
                response_artifact.execution_profile_identity,
                attempt.execution_profile_identity,
                "response execution profile",
            ),
            (outcome.execution_profile_identity, attempt.execution_profile_identity, "outcome execution profile"),
            (handoff.execution_profile_identity, attempt.execution_profile_identity, "handoff execution profile"),
            (
                response_artifact.request_evidence_identity,
                attempt.request_evidence_identity,
                "response request evidence",
            ),
        )
        for actual, expected, name in exact_equalities:
            if actual != expected:
                raise ResponseValidationAuthorizationError(f"canonical Model Adapter {name} lineage is contradictory")
        if (
            outcome.response_artifact_identity is not None
            and outcome.response_artifact_identity != response_artifact.response_artifact_identity
        ):
            raise ResponseValidationAuthorizationError(
                "canonical Model Adapter outcome capture lineage is contradictory"
            )


_SUPPORTED_OUTPUT_CONTRACT_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "default",
        "examples",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)


def _parse_output_contract(
    value: str,
    *,
    available_authority: tuple[tuple[str, str], ...],
    maximum_depth: int,
    transient_cleanup_coordinates: tuple[tuple[str, str], ...] | None,
) -> Mapping[str, Any]:
    if _json_nesting_exceeds(value, maximum_depth):
        raise _AuthorizationRefusalSignal(
            ValidationState.RULE_UNAVAILABLE,
            "REQUESTED_OUTPUT_CONTRACT_EXCEEDS_DEPTH_POLICY",
            available_authority,
            transient_cleanup_coordinates,
        )
    try:
        parsed = json.loads(
            value,
            parse_float=Decimal,
            parse_int=_parse_json_integer,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (TypeError, InvalidOperation, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise _AuthorizationRefusalSignal(
            ValidationState.RULE_UNAVAILABLE,
            "REQUESTED_OUTPUT_CONTRACT_UNREADABLE",
            available_authority,
            transient_cleanup_coordinates,
        ) from error
    if not isinstance(parsed, Mapping):
        raise _AuthorizationRefusalSignal(
            ValidationState.RULE_UNAVAILABLE,
            "REQUESTED_OUTPUT_CONTRACT_IS_NOT_A_SCHEMA_OBJECT",
            available_authority,
            transient_cleanup_coordinates,
        )
    try:
        _validate_output_contract_schema(parsed)
    except (RecursionError, ResponseValidationRuleUnavailable) as error:
        raise _AuthorizationRefusalSignal(
            ValidationState.RULE_UNAVAILABLE,
            "REQUESTED_OUTPUT_CONTRACT_RULE_UNAVAILABLE",
            available_authority,
            transient_cleanup_coordinates,
        ) from error
    return dict(parsed)


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value}")


def _parse_json_integer(value: str) -> int | Decimal:
    """Parse JSON integers without depending on Python's bounded int conversion."""
    digits = value[1:] if value.startswith("-") else value
    if len(digits) <= 256:
        return int(value)
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ResponseValidationRuleUnavailable(
            "JSON integer exceeds the exact installed numeric capability"
        ) from error


def _json_nesting_exceeds(value: str, maximum_depth: int) -> bool:
    """Measure JSON structure iteratively, ignoring delimiters inside strings."""
    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum_depth:
                return True
        elif character in "]}":
            depth = max(0, depth - 1)
    return False


def _validate_output_contract_schema(schema: Mapping[str, Any]) -> None:
    unknown = set(schema).difference(_SUPPORTED_OUTPUT_CONTRACT_KEYWORDS)
    if unknown:
        raise ResponseValidationRuleUnavailable("output contract contains unsupported rule keywords")
    declared_type = schema.get("type")
    if declared_type is not None and declared_type not in {
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    }:
        raise ResponseValidationRuleUnavailable("output contract declares an unsupported type")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ResponseValidationRuleUnavailable("output-contract properties must be a mapping")
        for child in properties.values():
            if not isinstance(child, Mapping):
                raise ResponseValidationRuleUnavailable("each output-contract property must be a schema")
            _validate_output_contract_schema(child)
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(item, str) or not item for item in required)
        or len(set(required)) != len(required)
    ):
        raise ResponseValidationRuleUnavailable("output-contract required fields are invalid")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, Mapping)):
        raise ResponseValidationRuleUnavailable("additionalProperties must be boolean or a schema")
    if isinstance(additional, Mapping):
        _validate_output_contract_schema(additional)
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            raise ResponseValidationRuleUnavailable("output-contract items must be a schema")
        _validate_output_contract_schema(items)
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ResponseValidationRuleUnavailable("output-contract enum must be a non-empty list")
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        item = schema.get(name)
        if item is not None and (not isinstance(item, (int, float, Decimal)) or isinstance(item, bool)):
            raise ResponseValidationRuleUnavailable(f"output-contract {name} must be numeric")
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        item = schema.get(name)
        if item is not None and not _is_non_negative_json_integer(item):
            raise ResponseValidationRuleUnavailable(f"output-contract {name} must be a non-negative integer")


def _is_non_negative_json_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, Decimal):
        return value.is_finite() and value >= 0 and value == value.to_integral_value()
    return False


def _length_less_than_bound(length: int, bound: Any) -> bool:
    if isinstance(bound, Decimal):
        return Decimal(length) < bound
    return length < bound


def _length_greater_than_bound(length: int, bound: Any) -> bool:
    if isinstance(bound, Decimal):
        return Decimal(length) > bound
    return length > bound


def _schema_type_invalid(value: Any, schema: Mapping[str, Any]) -> bool:
    declared = schema.get("type")
    if declared is not None and not _matches_json_type(value, str(declared)):
        return True
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                if key in value and isinstance(child, Mapping) and _schema_type_invalid(value[key], child):
                    return True
        additional = schema.get("additionalProperties")
        if isinstance(additional, Mapping):
            known = set(properties) if isinstance(properties, Mapping) else set()
            if any(_schema_type_invalid(item, additional) for key, item in value.items() if key not in known):
                return True
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        return any(_schema_type_invalid(item, schema["items"]) for item in value)
    return False


def _output_contract_invalid(value: Any, schema: Mapping[str, Any]) -> bool:
    declared = schema.get("type")
    if declared is not None and not _matches_json_type(value, str(declared)):
        # Type/shape failure belongs solely to INVALID_SCHEMA.
        return False
    if "enum" in schema and not any(_json_values_equal(value, candidate) for candidate in schema["enum"]):
        return True
    if "const" in schema and not _json_values_equal(value, schema["const"]):
        return True
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        known = set(properties) if isinstance(properties, Mapping) else set()
        required = schema.get("required")
        if isinstance(required, list) and any(item not in value for item in required):
            return True
        additional = schema.get("additionalProperties")
        extra = set(value).difference(known)
        if additional is False and extra:
            return True
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                if key in value and isinstance(child, Mapping) and _output_contract_invalid(value[key], child):
                    return True
        if isinstance(additional, Mapping) and any(_output_contract_invalid(value[key], additional) for key in extra):
            return True
    if isinstance(value, list):
        if "minItems" in schema and _length_less_than_bound(len(value), schema["minItems"]):
            return True
        if "maxItems" in schema and _length_greater_than_bound(len(value), schema["maxItems"]):
            return True
        items = schema.get("items")
        if isinstance(items, Mapping) and any(_output_contract_invalid(item, items) for item in value):
            return True
    if isinstance(value, str):
        if "minLength" in schema and _length_less_than_bound(len(value), schema["minLength"]):
            return True
        if "maxLength" in schema and _length_greater_than_bound(len(value), schema["maxLength"]):
            return True
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return True
        if "maximum" in schema and value > schema["maximum"]:
            return True
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return True
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return True
    return False


def _json_values_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool-as-int equality leak."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float, Decimal)) or isinstance(right, (int, float, Decimal)):
        return (
            isinstance(left, (int, float, Decimal))
            and isinstance(right, (int, float, Decimal))
            and not isinstance(left, bool)
            and not isinstance(right, bool)
            and left == right
        )
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_json_values_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return (
            isinstance(left, Mapping)
            and isinstance(right, Mapping)
            and set(left) == set(right)
            and all(_json_values_equal(left[key], right[key]) for key in left)
        )
    return False


def _matches_json_type(value: Any, declared: str) -> bool:
    if declared == "object":
        return isinstance(value, Mapping)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            or isinstance(value, Decimal)
            and value.is_finite()
            and value == value.to_integral_value()
        )
    if declared == "number":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "null":
        return value is None
    return False


def _contains_forbidden_response_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _FORBIDDEN_RESPONSE_KEYS or _contains_forbidden_response_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_response_key(item) for item in value)
    return False


def _response_lineage_matches(value: Any, coordinates: ResponseValidationAuthorityCoordinates) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("lineage"), Mapping):
        return False
    lineage = value["lineage"]
    expected = {
        "attempt_id": coordinates.attempt_id,
        "build_record_id": coordinates.build_record_id,
        "prompt_artifact_id": coordinates.prompt_artifact_id,
    }
    return all(lineage.get(name) == expected_value for name, expected_value in expected.items())


def _evidence_reference_state(
    value: Any,
    evidence_inputs: tuple[tuple[str, str], ...],
) -> ResponseValidationFinding | None:
    if not isinstance(value, Mapping):
        return ResponseValidationFinding(
            "EVIDENCE_REFERENCE_STRUCTURE",
            ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
            "EVIDENCE_REFERENCE_CONTAINER_INVALID",
        )
    references = value.get("evidence_references")
    if not isinstance(references, list):
        return ResponseValidationFinding(
            "EVIDENCE_REFERENCE_STRUCTURE",
            ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
            "EVIDENCE_REFERENCES_MISSING_OR_NOT_A_LIST",
        )

    canonical: dict[str, set[str]] = {}
    for span_id, content_hash in evidence_inputs:
        canonical.setdefault(span_id, set()).add(content_hash)
    if any(len(hashes) != 1 for hashes in canonical.values()):
        return ResponseValidationFinding(
            "EVIDENCE_REFERENCE_STRUCTURE",
            ValidationState.EVIDENCE_AMBIGUOUS,
            "CANONICAL_EVIDENCE_LINEAGE_AMBIGUOUS",
        )

    seen: dict[str, str] = {}
    for reference in references:
        if not isinstance(reference, Mapping) or set(reference) != {"span_id", "content_hash"}:
            return ResponseValidationFinding(
                "EVIDENCE_REFERENCE_STRUCTURE",
                ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
                "EVIDENCE_REFERENCE_SHAPE_INVALID",
            )
        span_id = reference.get("span_id")
        content_hash = reference.get("content_hash")
        if not isinstance(span_id, str) or not span_id or not isinstance(content_hash, str) or not content_hash:
            return ResponseValidationFinding(
                "EVIDENCE_REFERENCE_STRUCTURE",
                ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
                "EVIDENCE_REFERENCE_COORDINATE_INVALID",
            )
        if span_id in seen and seen[span_id] != content_hash:
            return ResponseValidationFinding(
                "EVIDENCE_REFERENCE_STRUCTURE",
                ValidationState.EVIDENCE_AMBIGUOUS,
                "RESPONSE_EVIDENCE_REFERENCE_AMBIGUOUS",
            )
        seen[span_id] = content_hash
        if canonical.get(span_id) != {content_hash}:
            return ResponseValidationFinding(
                "EVIDENCE_REFERENCE_STRUCTURE",
                ValidationState.INVALID_EVIDENCE_REFERENCE_STRUCTURE,
                "EVIDENCE_REFERENCE_DOES_NOT_MATCH_CANONICAL_INPUT",
            )
    return None


def _response_is_partial(value: Any, provider_status_metadata: tuple[tuple[str, str], ...]) -> bool:
    if isinstance(value, Mapping) and value.get("partial") is True:
        return True
    statuses = {str(key).lower(): str(item).lower() for key, item in provider_status_metadata}
    return statuses.get("finish_reason") in {"length", "max_tokens", "truncated", "incomplete"}


def _canonical_findings(findings: Sequence[ResponseValidationFinding]) -> tuple[ResponseValidationFinding, ...]:
    unique = {(item.dimension, item.state, item.reason_code): item for item in findings}
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: (_VALIDATION_STATE_RANK[item[1]], item[0], item[2]),
        )
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
