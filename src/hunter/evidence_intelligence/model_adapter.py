"""ADR 0034 Phase A — provider-neutral Model Adapter pre-dispatch foundation.

This module owns the canonical semantics ADR 0034 assigns to the Model Adapter:
execution-profile identity, provider-request lineage, model-attempt identity,
attempt-time Source Handling enforcement, and single-use handoff semantics.

Phase A deliberately stops before any network transmission. There is no provider
transport, no provider SDK, no endpoint, no credential, no response artifact, no
`ResponseValidator`, and no routing here. The pipeline terminates at a no-network
dispatch seam whose only purpose is to prove that one attempt cannot dispatch
twice.

Authority boundaries preserved here:

* ADR 0033 — this module is a *consumer* of Source Handling Authority. It
  resolves and enforces; it never creates, overrides, or substitutes authority,
  and it never trusts a caller-supplied handling decision.
* ADR 0031 — `EvidencePromptArtifact` and `EvidencePreModelBuildRecord` are
  immutable and upstream-owned. This module consumes their exact identities and
  never mutates or re-canonicalizes them.
* ADR 0020 — historical reads are strict-known at a recorded cutoff; current
  state is never substituted for historical absence.
* ADR 0009 — persistence is mechanical and lives in
  `model_adapter_persistence`; every authority decision is made here.
* ADR 0016 — nothing in this module promotes anything to canonical authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidenceContextAllocationResult,
    EvidencePreModelBuildRecord,
    EvidencePreModelSourceHandlingAuthority,
    EvidencePromptArtifact,
    ResolvedPreModelSourceHandling,
    resolve_pre_model_source_handling,
)
from hunter.evidence_intelligence.source_handling import (
    SourceHandlingBlockedError,
    _string_sequence,
    validate_durable_payload,
)

IdempotencyCapability = Literal["SUPPORTED", "UNAVAILABLE", "UNKNOWN"]

RequestEvidenceState = Literal[
    "REQUEST_EVIDENCE_DURABLE",
    "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY",
]

PreDispatchRefusal = Literal[
    "CAPABILITY_UNSUPPORTED",
    "SOURCE_HANDLING_BLOCKED",
    "ATTEMPT_PERSISTENCE_FAILED",
    "HANDOFF_CREATION_FAILED",
]

REQUEST_EVIDENCE_UNAVAILABLE_REASON = "REQUEST_CONTENT_RETENTION_PROHIBITED"

# Durable categories that carry request content or anything derived from it.
# ADR 0034: processing permission alone never authorizes any of these. Each is
# checked independently, because a hash is not automatically safe merely because
# it is a hash.
CONTENT_DERIVED_CATEGORIES = ("SOURCE_BYTES", "SOURCE_DERIVED_TEXT", "CONTENT_DERIVED_ID")

# The single-use dispatch capability is opaque and not content-derived, so it is
# governed as operational metadata rather than as a content category.
DISPATCH_CAPABILITY_CATEGORY = "OPERATIONAL_METADATA"

# The one field whose *name* legitimately contains "credential": it names a slot,
# and is value-checked instead. Without this exemption the modelled field would be
# unusable and its value-shape check unreachable.
CREDENTIAL_SLOT_FIELD = "credential_slot_identity"

# Field names that carry, or plausibly carry, credential material. These are
# rejected structurally at construction rather than redacted at write time:
# ADR 0034 requires a boundary that makes secrets unrepresentable, not a
# convention that discourages them.
_SECRET_FIELD_PATTERN = re.compile(
    r"(?i)(api[_-]?key|apikey|authorization|auth[_-]?header|bearer|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|secret|credential|passwd|password|private[_-]?key|"
    r"client[_-]?secret|signing[_-]?key|session[_-]?token|cookie|x[_-]?api[_-]?key)"
)

# Values that look like transmitted credential material regardless of their key.
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(^bearer\s+\S+|^basic\s+\S+|^sk-[A-Za-z0-9_-]{8,}|^ghp_[A-Za-z0-9]{8,}|"
    r"^xox[baprs]-[A-Za-z0-9-]{8,}|^AKIA[0-9A-Z]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


class ModelAdapterError(RuntimeError):
    """Base class for Model Adapter authority violations."""


class SecretMaterialRejected(ModelAdapterError):
    """Raised when credential material is offered to a durable Model Adapter surface."""


class CapabilityMismatch(ModelAdapterError):
    """Raised when a profile cannot satisfy the governing pre-model build's capability."""


class ModelAdapterAuthorityError(ModelAdapterError):
    """Raised when caller-supplied state attempts to act as authority."""


class HandoffConsumptionError(ModelAdapterError):
    """Raised when a handoff cannot be consumed exactly once as authorized."""


class PreDispatchRefused(ModelAdapterError):
    """Raised when the adapter refuses before any attempt becomes dispatchable.

    ADR 0034 records these as pre-dispatch refusals: they are decided before an
    attempt or handoff exists, so they carry only the lineage that actually
    exists and never a fabricated attempt or handoff identity.
    """

    def __init__(self, refusal: PreDispatchRefusal, reason: str) -> None:
        super().__init__(f"{refusal}: {reason}")
        self.refusal: PreDispatchRefusal = refusal
        self.reason = reason


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _identity(kind: str, value: object) -> str:
    """Deterministic Hunter-controlled identity, matching the ADR 0031 convention."""
    digest = hashlib.sha256(_canonical_json(value)).hexdigest()
    return f"{kind}:{digest}"


def _reject_secret_material(where: str, key: str, value: object, *, check_name: bool = True) -> None:
    """Structurally refuse credential material on a durable Model Adapter surface."""
    if check_name and _SECRET_FIELD_PATTERN.search(key):
        raise SecretMaterialRejected(f"{where} may not carry credential-bearing field {key!r}")
    if isinstance(value, str) and _SECRET_VALUE_PATTERN.search(value.strip()):
        raise SecretMaterialRejected(f"{where} field {key!r} carries credential-shaped material")
    if isinstance(value, Mapping):
        # A nested mapping is the classic smuggling route for a raw credential
        # dictionary, so the durable surface refuses the container outright.
        raise SecretMaterialRejected(f"{where} field {key!r} may not carry a nested mapping")
    if isinstance(value, (bytes, bytearray)):
        raise SecretMaterialRejected(f"{where} field {key!r} may not carry raw bytes")


def _screen_parameters(where: str, parameters: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    screened: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in parameters:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ModelAdapterError(f"{where} parameters must be (name, value) pairs")
        name, value = entry
        if not isinstance(name, str) or not isinstance(value, str):
            raise ModelAdapterError(f"{where} parameters must be string pairs")
        if name in seen:
            raise ModelAdapterError(f"{where} parameter {name!r} is declared twice")
        seen.add(name)
        _reject_secret_material(where, name, value)
        screened.append((name, value))
    return tuple(sorted(screened))


@dataclass(frozen=True)
class ModelExecutionProfile:
    """One exact, immutable, versioned provider/model execution configuration.

    Carries only non-secret execution identity. Credentials are structurally
    excluded: no field accepts them, and construction rejects credential-bearing
    parameter names, credential-shaped values, nested mappings, and raw bytes.

    A changed parameter is a new profile version, never an edit: the dataclass is
    frozen and `profile_identity` is derived from the full body.
    """

    profile_name: str
    profile_version: str
    provider_identity: str
    model_identity: str
    model_version: str
    endpoint_class_identity: str
    transport_identity: str
    transport_version: str
    request_protocol_identity: str
    request_protocol_version: str
    required_capability_identity: str
    response_format_identity: str
    idempotency_capability: IdempotencyCapability
    parameters: tuple[tuple[str, str], ...] = ()
    prohibited_capabilities: tuple[str, ...] = ()
    credential_slot_identity: str | None = None
    schema_version: str = "1"

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name in {"parameters", "prohibited_capabilities"}:
                continue
            if value is None:
                continue
            _reject_secret_material(
                "execution profile",
                name,
                value,
                check_name=name != CREDENTIAL_SLOT_FIELD,
            )
        if self.idempotency_capability not in ("SUPPORTED", "UNAVAILABLE", "UNKNOWN"):
            raise ModelAdapterError("idempotency capability classification is invalid")
        object.__setattr__(self, "parameters", _screen_parameters("execution profile", self.parameters))
        # These entries are hashed into `profile_identity`, which is persisted on
        # the attempt and handoff, so an unscreened entry would become a durable
        # credential-derived representation.
        for capability_name in self.prohibited_capabilities:
            if not isinstance(capability_name, str):
                raise ModelAdapterError("prohibited capabilities must be strings")
            _reject_secret_material("execution profile", "prohibited_capability", capability_name)
        object.__setattr__(self, "prohibited_capabilities", tuple(sorted(set(self.prohibited_capabilities))))
        if self.credential_slot_identity is not None:
            slot = self.credential_slot_identity
            if not slot or _SECRET_VALUE_PATTERN.search(slot.strip()):
                raise SecretMaterialRejected("credential slot identity may not carry a credential value")
        for required in (
            self.profile_name,
            self.profile_version,
            self.provider_identity,
            self.model_identity,
            self.model_version,
            self.endpoint_class_identity,
            self.transport_identity,
            self.transport_version,
            self.request_protocol_identity,
            self.request_protocol_version,
            self.required_capability_identity,
            self.response_format_identity,
        ):
            if not isinstance(required, str) or not required.strip():
                raise ModelAdapterError("execution profile requires every non-secret identity field")

    @property
    def profile_identity(self) -> str:
        return _identity("model-execution-profile", asdict(self))

    def satisfies(self, capability: EvidenceCapabilityConstraint) -> bool:
        """Whether this profile exactly satisfies the build's capability constraint."""
        return self.required_capability_identity == capability.constraint_identity


@dataclass(frozen=True)
class ProviderRequestEvidence:
    """The Model Adapter's per-category request-evidence decision.

    Semantically distinct from `EvidencePromptArtifact` even when a durable
    mapping would be byte-equivalent. When durable content categories are denied,
    this records explicit governed unavailability and carries no bytes, hash,
    size, or content-derived identity — and nothing regenerates them later.
    """

    state: RequestEvidenceState
    reason_code: str | None = None
    content_hash: str | None = None
    measured_size_bytes: int | None = None
    content_derived_identity: str | None = None

    def __post_init__(self) -> None:
        if self.state == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY":
            if any(
                value is not None
                for value in (self.content_hash, self.measured_size_bytes, self.content_derived_identity)
            ):
                raise ModelAdapterError(
                    "unavailable request evidence may not carry bytes, hash, size, or content-derived identity"
                )
            if not self.reason_code:
                raise ModelAdapterError("unavailable request evidence requires a governed reason code")

    @property
    def request_evidence_identity(self) -> str:
        return _identity("provider-request-evidence", asdict(self))


@dataclass(frozen=True)
class ModelAttemptRecord:
    """Immutable pre-send attempt identity and lineage.

    Never carries terminal state and is never mutated into an outcome. Identity is
    deterministic from canonical inputs under Hunter's control; remote model
    output is not an input and is never part of this identity.
    """

    execution_owner_id: str
    build_record_id: str
    prompt_artifact_id: str
    execution_profile_identity: str
    request_evidence_identity: str
    request_evidence_state: RequestEvidenceState
    attempt_ordinal: int
    build_cutoff: datetime
    attempt_cutoff: datetime
    recorded_at: datetime
    idempotency_capability: IdempotencyCapability
    predecessor_attempt_id: str | None = None
    schema_version: str = "1"

    def __post_init__(self) -> None:
        for name in ("build_cutoff", "attempt_cutoff", "recorded_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ModelAdapterError(f"{name} must be timezone-aware")
        if self.attempt_ordinal < 1:
            raise ModelAdapterError("attempt ordinal starts at 1")
        if self.attempt_ordinal > 1 and self.predecessor_attempt_id is None:
            raise ModelAdapterError("a retry attempt requires explicit predecessor lineage")
        if self.attempt_ordinal == 1 and self.predecessor_attempt_id is not None:
            raise ModelAdapterError("the first attempt has no predecessor")

    @property
    def attempt_id(self) -> str:
        return _identity("model-attempt", _jsonable(asdict(self)))


@dataclass(frozen=True)
class ModelHandoffRecord:
    """Immutable, single-use authorization to dispatch exactly one attempt.

    Execution evidence, never Source Handling authority: its decisions are valid
    only because they are bound to the exact canonical Source Handling snapshot
    resolved at this attempt's own cutoff.
    """

    attempt_id: str
    build_record_id: str
    prompt_artifact_id: str
    execution_profile_identity: str
    request_evidence_identity: str
    fact_record_id: str
    policy_record_id: str
    field_category_registry_id: str
    authorization_rule_id: str
    attempt_cutoff: datetime
    processing_decision: str
    durable_disposition_identity: str
    dispatch_capability_identity: str | None = None
    expires_at: datetime | None = None
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if self.processing_decision != "ALLOW":
            raise ModelAdapterError("a handoff may only exist for processing_decision == ALLOW")
        if not isinstance(self.attempt_cutoff, datetime) or self.attempt_cutoff.tzinfo is None:
            raise ModelAdapterError("attempt_cutoff must be timezone-aware")
        if self.expires_at is not None and self.expires_at <= self.attempt_cutoff:
            raise ModelAdapterError("handoff expiry must be later than the attempt cutoff")
        for name in (
            "fact_record_id",
            "policy_record_id",
            "field_category_registry_id",
            "authorization_rule_id",
        ):
            if not getattr(self, name):
                raise ModelAdapterError(f"handoff requires the exact {name} resolved at the attempt cutoff")

    @property
    def handoff_id(self) -> str:
        return _identity("model-handoff", _jsonable(asdict(self)))

    def is_expired_at(self, moment: datetime) -> bool:
        return self.expires_at is not None and moment >= self.expires_at


@dataclass(frozen=True)
class PreparedModelAttempt:
    """The durable result of one authorized pre-dispatch preparation."""

    attempt: ModelAttemptRecord
    handoff: ModelHandoffRecord
    request_evidence: ProviderRequestEvidence
    resolved_authority: ResolvedPreModelSourceHandling = field(repr=False)


@dataclass(frozen=True)
class NoNetworkDispatchResult:
    """Proof that exactly one dispatch opportunity was consumed, with no network use."""

    handoff_id: str
    attempt_id: str
    dispatched: bool = True
    transmitted_bytes: int = 0


class NoNetworkDispatchSeam:
    """The Phase A terminus.

    Deliberately not a provider abstraction: it takes no endpoint, no client, and
    no credential, and it cannot be pointed at a network. It exists only so the
    single-use consumption guarantee can be proven without contacting anything.
    """

    def __init__(self) -> None:
        self.dispatched_handoff_ids: list[str] = []

    def dispatch(self, handoff: ModelHandoffRecord) -> NoNetworkDispatchResult:
        self.dispatched_handoff_ids.append(handoff.handoff_id)
        return NoNetworkDispatchResult(handoff_id=handoff.handoff_id, attempt_id=handoff.attempt_id)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _disposition_identity(decision: Mapping[str, Any]) -> str:
    return _identity("durable-disposition", _jsonable(decision.get("durable_dispositions") or {}))


def _category_persist_allowed(decision: Mapping[str, Any], category: str) -> bool:
    dispositions = decision.get("durable_dispositions")
    if not isinstance(dispositions, Mapping):
        return False
    category_dispositions = dispositions.get(category)
    if not isinstance(category_dispositions, Mapping):
        return False
    return category_dispositions.get("PERSIST") == "ALLOW"


def permitted_request_evidence_state(decision: Mapping[str, Any]) -> RequestEvidenceState:
    """The only request-evidence state this decision authorizes.

    Shared by the adapter and by persistence so both derive the same answer from
    the same authority rather than trusting a supplied object.
    """
    if decision.get("retention_decision") != "ALLOW" or not all(
        _category_persist_allowed(decision, category) for category in CONTENT_DERIVED_CATEGORIES
    ):
        return "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY"
    return "REQUEST_EVIDENCE_DURABLE"


def derive_request_evidence(
    *,
    resolved: ResolvedPreModelSourceHandling,
    prompt_artifact: EvidencePromptArtifact,
) -> ProviderRequestEvidence:
    """Decide, per durable category, what request evidence may exist.

    Processing permission grants nothing here. Every content-derived category is
    checked independently against the exact historical field-category registry, so
    an authorized send can still produce explicitly unavailable request evidence.
    """
    decision = resolved.decision
    if permitted_request_evidence_state(decision) == "REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY":
        return ProviderRequestEvidence(
            state="REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY",
            reason_code=REQUEST_EVIDENCE_UNAVAILABLE_REASON,
        )

    # The prompt artifact is never mutated; a distinct provider-facing identity is
    # derived from its already-published identity coordinates.
    return ProviderRequestEvidence(
        state="REQUEST_EVIDENCE_DURABLE",
        content_hash=prompt_artifact.content_hash,
        measured_size_bytes=prompt_artifact.measured_size_bytes,
        content_derived_identity=_identity(
            "provider-request-content",
            {
                "prompt_artifact_id": prompt_artifact.artifact_id,
                "content_hash": prompt_artifact.content_hash,
                "measured_size_bytes": prompt_artifact.measured_size_bytes,
            },
        ),
    )


class ModelAdapterService:
    """Sole owner of Phase A Model Adapter authority.

    Every authorization decision is made here. The persistence repository is
    mechanical and cannot be used to fabricate an authoritative record.
    """

    def __init__(self, repository: Any, *, dispatch_seam: NoNetworkDispatchSeam | None = None) -> None:
        self._repository = repository
        self._dispatch_seam = dispatch_seam or NoNetworkDispatchSeam()

    @property
    def dispatch_seam(self) -> NoNetworkDispatchSeam:
        return self._dispatch_seam

    def prepare_attempt(
        self,
        *,
        execution_owner_id: str,
        build_record: EvidencePreModelBuildRecord,
        prompt_artifact: EvidencePromptArtifact,
        capability: EvidenceCapabilityConstraint,
        allocation: EvidenceContextAllocationResult,
        profile: ModelExecutionProfile,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
        build_cutoff: datetime,
        recorded_at: datetime,
        attempt_ordinal: int = 1,
        predecessor_attempt_id: str | None = None,
        handoff_expires_at: datetime | None = None,
        supplied_handling_decision: Mapping[str, Any] | None = None,
    ) -> PreparedModelAttempt:
        """Prepare exactly one dispatchable attempt, or refuse before dispatch.

        The order is fixed and fail-closed: capability compatibility, then one
        strict-known Source Handling resolution at this attempt's own cutoff, then
        the per-category request-evidence decision, then a durable attempt, then a
        handoff bound to that same snapshot. Attempt and handoff are committed in
        one transaction, so a dispatch-capable handoff can never exist without its
        durable attempt.
        """
        if build_record.prompt_artifact_id != prompt_artifact.artifact_id:
            raise ModelAdapterError("prompt artifact does not belong to the supplied build record")
        if attempt_authority.cutoff.tzinfo is None:
            raise ModelAdapterError("attempt cutoff must be timezone-aware")
        if build_cutoff.tzinfo is None or recorded_at.tzinfo is None:
            raise ModelAdapterError("build cutoff and recorded_at must be timezone-aware")

        # ADR 0034: a caller may supply a decision as evidence, never as authority.
        # It is compared against independently rederived authority and never used
        # in its place.
        if supplied_handling_decision is not None and not isinstance(supplied_handling_decision, Mapping):
            raise ModelAdapterAuthorityError("supplied handling decision must be a mapping when present")

        # 1. Capability compatibility fails closed before anything durable exists.
        #    The constraint is bound to the build's own allocation, not merely to a
        #    caller-supplied pair: a profile and capability that agree with each
        #    other but not with the governing build must not authorize a dispatch.
        if allocation.allocation_id != build_record.allocation_id:
            raise ModelAdapterError("supplied allocation does not belong to the governing pre-model build")
        if allocation.capability_identity != capability.constraint_identity:
            raise ModelAdapterError("supplied capability is not the constraint recorded by the build's allocation")
        if not profile.satisfies(capability):
            raise PreDispatchRefused(
                "CAPABILITY_UNSUPPORTED",
                "execution profile capability contract does not exactly satisfy the pre-model build constraint",
            )

        # 2. Attempt-time strict-known Source Handling. One snapshot resolves the
        #    fact, policy, registry, and rule together at this attempt's cutoff.
        #    Build-time authority is lineage only and is never consulted here.
        try:
            resolved = resolve_pre_model_source_handling(attempt_authority)
        except SourceHandlingBlockedError as error:
            raise PreDispatchRefused("SOURCE_HANDLING_BLOCKED", str(error)) from error

        decision = resolved.decision
        if decision.get("processing_decision") != "ALLOW":
            raise PreDispatchRefused(
                "SOURCE_HANDLING_BLOCKED",
                "attempt-time processing decision does not permit model-facing processing",
            )

        if supplied_handling_decision is not None and _canonical_json(
            _jsonable(dict(supplied_handling_decision))
        ) != _canonical_json(_jsonable(dict(decision))):
            raise ModelAdapterAuthorityError(
                "caller-supplied source-handling decision does not match independently rederived authority"
            )

        # 3. Per-category request-evidence decision.
        request_evidence = derive_request_evidence(resolved=resolved, prompt_artifact=prompt_artifact)

        attempt = ModelAttemptRecord(
            execution_owner_id=execution_owner_id,
            build_record_id=build_record.build_record_id,
            prompt_artifact_id=prompt_artifact.artifact_id,
            execution_profile_identity=profile.profile_identity,
            request_evidence_identity=request_evidence.request_evidence_identity,
            request_evidence_state=request_evidence.state,
            attempt_ordinal=attempt_ordinal,
            build_cutoff=build_cutoff.astimezone(UTC),
            attempt_cutoff=attempt_authority.cutoff.astimezone(UTC),
            recorded_at=recorded_at.astimezone(UTC),
            idempotency_capability=profile.idempotency_capability,
            predecessor_attempt_id=predecessor_attempt_id,
        )

        # ADR 0034: the dispatch capability identity is opaque and NOT
        # content-derived, so it is governed as operational metadata. It is
        # persisted only when that category is authorized; when it is not, no
        # substitute identifier is fabricated.
        dispatch_capability_identity = None
        if _category_persist_allowed(decision, DISPATCH_CAPABILITY_CATEGORY):
            dispatch_capability_identity = _identity(
                "model-dispatch-capability",
                {
                    "attempt": _jsonable(asdict(attempt)),
                    "profile_identity": profile.profile_identity,
                },
            )

        handoff = ModelHandoffRecord(
            attempt_id=attempt.attempt_id,
            build_record_id=attempt.build_record_id,
            prompt_artifact_id=attempt.prompt_artifact_id,
            execution_profile_identity=profile.profile_identity,
            request_evidence_identity=request_evidence.request_evidence_identity,
            fact_record_id=str(decision.get("fact_record_id") or ""),
            policy_record_id=str(decision.get("policy_record_id") or ""),
            field_category_registry_id=str(decision.get("field_category_registry_id") or ""),
            authorization_rule_id=str(decision.get("authorization_rule_id") or ""),
            attempt_cutoff=attempt.attempt_cutoff,
            processing_decision="ALLOW",
            durable_disposition_identity=_disposition_identity(decision),
            dispatch_capability_identity=dispatch_capability_identity,
            expires_at=handoff_expires_at,
        )

        # 4. Every durable payload element is re-verified against the exact
        #    historical registry before anything is written.
        fact = resolved.fact_record.get("fact")
        secret_presence = set(_string_sequence(fact.get("secret_presence") if isinstance(fact, Mapping) else None))
        try:
            validate_durable_payload(
                decision=decision,
                registry=resolved.registry_record,
                payload={
                    "model_attempt": _jsonable(asdict(attempt)),
                    "model_handoff": _jsonable(asdict(handoff)),
                },
                secret_presence=secret_presence,
            )
        except SourceHandlingBlockedError as error:
            raise PreDispatchRefused("SOURCE_HANDLING_BLOCKED", str(error)) from error

        # Persistence independently rederives this same authority and rejects any
        # disagreement (ADR 0033 persistence invariant), so the repository cannot
        # be used as a bypass around this service.
        self._repository.persist_attempt_and_handoff(
            attempt=attempt,
            handoff=handoff,
            request_evidence=request_evidence,
            attempt_authority=attempt_authority,
        )

        return PreparedModelAttempt(
            attempt=attempt,
            handoff=handoff,
            request_evidence=request_evidence,
            resolved_authority=resolved,
        )

    def consume_handoff(
        self,
        *,
        handoff: ModelHandoffRecord,
        consumed_at: datetime,
    ) -> NoNetworkDispatchResult:
        """Consume one handoff exactly once and stop at the no-network seam."""
        if consumed_at.tzinfo is None:
            raise ModelAdapterError("consumed_at must be timezone-aware")
        if handoff.is_expired_at(consumed_at):
            raise HandoffConsumptionError("handoff is expired and cannot authorize dispatch")

        # The repository performs the compare-and-set; the adapter owns what a
        # successful consumption is allowed to mean.
        self._repository.consume_handoff_once(handoff_id=handoff.handoff_id, consumed_at=consumed_at)
        return self._dispatch_seam.dispatch(handoff)
