"""ADR 0034 Phases A and B — the Model Adapter authority boundary.

This module owns the canonical semantics ADR 0034 assigns to the Model Adapter:
execution-profile identity, provider-request lineage, model-attempt identity,
attempt-time Source Handling enforcement, single-use handoff semantics, and —
added in Phase B — attempt-outcome lineage and governed provider-response
capture.

Phase A stops before any network transmission and still does: `prepare_attempt`
and the no-network dispatch seam are unchanged. Phase B adds one dispatch path
that consumes a valid handoff exactly once, invokes one explicitly configured
provider transport, classifies what happened truthfully, appends an immutable
`ModelAttemptOutcomeRecord`, and captures a governed `ProviderResponseArtifact`.

The boundary ends there. There is no `ResponseValidator`, no semantic response
validation, no extraction or knowledge promotion, no second provider, and no
routing, ranking, fallback, or dynamic selection. A provider response that
arrives here is transport evidence and nothing more.

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

from hunter.evidence_intelligence.model_adapter_transport import (
    _DISPATCH_MINT,
    DeliveryCertainty,
    DispatchAuthorization,
    ProviderExecutionEvidence,
    ProviderTransport,
    TransportCredential,
    TransportRequest,
    TransportResult,
)
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

ResponseEvidenceState = Literal[
    "RESPONSE_EVIDENCE_DURABLE",
    "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY",
    "RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK",
]

RESPONSE_EVIDENCE_UNAVAILABLE_REASON = "RESPONSE_CONTENT_RETENTION_PROHIBITED"
RESPONSE_EVIDENCE_CREDENTIAL_RISK_REASON = "RESPONSE_CONTENT_NOT_ESTABLISHED_CREDENTIAL_FREE"

# The canonical Phase B outcome vocabulary. Every family ADR 0034 requires to be
# separately distinguishable is a separate member; nothing is collapsed into a
# generic failure. `LOCAL_PRE_SEND_FAILED` is the implementation-chosen code for
# the pre-network local failure the ADR requires to remain distinguishable from
# an external attempt.
AttemptOutcome = Literal[
    "SUCCEEDED_TRANSPORT",
    "PROVIDER_REFUSED",
    "PROVIDER_UNAVAILABLE",
    "TIMEOUT_CONFIRMED_NO_DELIVERY",
    "DELIVERY_UNKNOWN",
    "OUTCOME_UNKNOWN",
    "RATE_LIMITED",
    "QUOTA_UNAVAILABLE",
    "BILLING_UNAVAILABLE",
    "CAPABILITY_UNSUPPORTED",
    "MALFORMED_TRANSPORT_RESPONSE",
    "SECURITY_BLOCKED",
    "SOURCE_HANDLING_BLOCKED",
    "RESPONSE_CAPTURED_PERSISTENCE_FAILED",
    "INTERNAL_ADAPTER_ERROR",
    "LOCAL_PRE_SEND_FAILED",
]

# What a recorded outcome permits about a *future* attempt. This is never
# permission to re-dispatch the attempt it describes: ADR 0034 makes every retry a
# new attempt with its own cutoff, authority, and handoff.
RetryAuthorization = Literal[
    "RETRY_NOT_APPLICABLE",
    "RETRY_REQUIRES_NEW_ATTEMPT",
    "RETRY_BLOCKED_DELIVERY_UNCERTAIN",
    "RETRY_BLOCKED_RECONCILIATION_REQUIRED",
]

# Outcomes that establish nothing was ever transmitted. Recorded separately from
# delivery certainty so a refusal cannot be mistaken for an external attempt.
NON_TRANSMITTING_OUTCOMES: tuple[AttemptOutcome, ...] = (
    "CAPABILITY_UNSUPPORTED",
    "SOURCE_HANDLING_BLOCKED",
    "LOCAL_PRE_SEND_FAILED",
)

# Outcomes that may only ever be recorded with the delivery certainty that proves
# them. The table is enforced at construction, so a caller cannot label an
# ambiguous network failure as known non-delivery.
_REQUIRED_DELIVERY_CERTAINTY: dict[str, tuple[str, ...]] = {
    "SUCCEEDED_TRANSPORT": ("ANSWERED",),
    "MALFORMED_TRANSPORT_RESPONSE": ("ANSWERED",),
    "TIMEOUT_CONFIRMED_NO_DELIVERY": ("CONFIRMED_NOT_DELIVERED",),
    "DELIVERY_UNKNOWN": ("UNKNOWN",),
    "OUTCOME_UNKNOWN": ("UNKNOWN",),
    "CAPABILITY_UNSUPPORTED": ("CONFIRMED_NOT_DELIVERED", "ANSWERED"),
    "SOURCE_HANDLING_BLOCKED": ("CONFIRMED_NOT_DELIVERED",),
    "LOCAL_PRE_SEND_FAILED": ("CONFIRMED_NOT_DELIVERED",),
    "RESPONSE_CAPTURED_PERSISTENCE_FAILED": ("ANSWERED",),
}

# How a transport observation becomes a canonical outcome. One mapping, no
# heuristics at the call site, so a new transport cannot invent its own meaning
# for an existing outcome code.
_TRANSPORT_OUTCOME: dict[str, AttemptOutcome] = {
    "RESPONSE_RECEIVED": "SUCCEEDED_TRANSPORT",
    "MALFORMED_RESPONSE": "MALFORMED_TRANSPORT_RESPONSE",
    "PROVIDER_REFUSED": "PROVIDER_REFUSED",
    "PROVIDER_UNAVAILABLE": "PROVIDER_UNAVAILABLE",
    "RATE_LIMITED": "RATE_LIMITED",
    "QUOTA_UNAVAILABLE": "QUOTA_UNAVAILABLE",
    "BILLING_UNAVAILABLE": "BILLING_UNAVAILABLE",
    "CAPABILITY_REJECTED": "CAPABILITY_UNSUPPORTED",
    "SECURITY_BLOCKED": "SECURITY_BLOCKED",
}

# `TIMEOUT` and `CONNECTION_FAILED` are deliberately absent from the table above.
# Neither transport class determines an outcome on its own: a timeout is
# `TIMEOUT_CONFIRMED_NO_DELIVERY` only when the transport *proved* non-delivery,
# and is `DELIVERY_UNKNOWN` otherwise. Resolving them by table would be exactly
# the mislabelling ADR 0034 forbids, so `classify_transport_result` resolves them
# from the delivery-certainty evidence instead.
_CERTAINTY_DEPENDENT_TRANSPORT_CLASSES = ("TIMEOUT", "CONNECTION_FAILED")

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

# The capture gate's pattern. `_SECRET_VALUE_PATTERN` is anchored because it
# screens whole field *values*; a provider response is a document, so a credential
# echoed anywhere inside it must be found wherever it sits. This one is therefore
# unanchored, and it is deliberately shape-based rather than keyword-based: it
# looks for material that is actually credential-shaped, so an ordinary model
# answer that merely discusses authentication is not refused.
_RESPONSE_SECRET_PATTERN = re.compile(
    r"(?i)(\bbearer\s+[A-Za-z0-9._\-]{12,}|\bbasic\s+[A-Za-z0-9+/=]{12,}|"
    r"\bsk-[A-Za-z0-9_-]{8,}|\bghp_[A-Za-z0-9]{8,}|\bxox[baprs]-[A-Za-z0-9-]{8,}|"
    r"\bAKIA[0-9A-Z]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"authorization|set-cookie)\s*[\"\']?\s*[:=]\s*[\"\']?[A-Za-z0-9._\-+/=]{12,})"
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


class RetryNotAuthorized(ModelAdapterError):
    """Raised when a predecessor outcome does not permit a further attempt.

    ADR 0034: an uncertain attempt is not automatically retryable, and a retry
    never inherits a predecessor's authorization. This is the enforcement of that,
    not a warning about it.
    """


class ResponseCaptureBlocked(ModelAdapterError):
    """Raised when captured response content may not become durable evidence."""


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
class ProviderResponseArtifact:
    """Governed durable evidence of what one provider returned.

    Evidence, never truth. ADR 0034 is explicit that persisting response bytes
    does not validate them, does not make them an `ExtractionProposal`, and
    creates no canonical or analytical authority. Accordingly this record carries
    no validity, correctness, schema-conformance, acceptance, or promotion field,
    and deliberately offers no method that could be read as asserting one.

    Durability is decided per category by Source Handling and, independently, by
    the credential capture gate. When either withholds permission the record
    carries the governed unavailable state and *nothing* content-derived: no
    bytes, no hash, no measured size, no derived identity, and no fabricated
    substitute.
    """

    attempt_id: str
    handoff_id: str
    execution_profile_identity: str
    request_evidence_identity: str
    request_evidence_state: RequestEvidenceState
    response_protocol_identity: str
    response_protocol_version: str
    transport_identity: str
    transport_version: str
    state: ResponseEvidenceState
    recorded_at: datetime
    reason_code: str | None = None
    content: str | None = None
    content_hash: str | None = None
    measured_size_bytes: int | None = None
    content_derived_identity: str | None = None
    provider_status_metadata: tuple[tuple[str, str], ...] = ()
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.recorded_at, datetime) or self.recorded_at.tzinfo is None:
            raise ModelAdapterError("recorded_at must be timezone-aware")
        content_derived = (
            self.content,
            self.content_hash,
            self.measured_size_bytes,
            self.content_derived_identity,
        )
        if self.state == "RESPONSE_EVIDENCE_DURABLE":
            if any(value is None for value in content_derived):
                raise ModelAdapterError(
                    "durable response evidence requires content, hash, measured size, and derived identity"
                )
        else:
            if any(value is not None for value in content_derived):
                raise ModelAdapterError(
                    "unavailable response evidence may not carry bytes, hash, size, or content-derived identity"
                )
            if not self.reason_code:
                raise ModelAdapterError("unavailable response evidence requires a governed reason code")
        # Provider status metadata is untrusted external content. It is screened
        # on the same structural boundary as every other durable surface, so a
        # provider that echoes a credential in a status field cannot persist it.
        object.__setattr__(
            self,
            "provider_status_metadata",
            _screen_parameters("provider response status metadata", self.provider_status_metadata),
        )

    @property
    def response_artifact_identity(self) -> str:
        return _identity("provider-response-artifact", _jsonable(asdict(self)))


@dataclass(frozen=True)
class ModelAttemptOutcomeRecord:
    """One immutable, append-only terminal or uncertainty state for an attempt.

    ADR 0034: the pre-send `ModelAttemptRecord` is never updated in place, and no
    outcome is ever mutated. A correction is a new record carrying
    `supersedes_outcome_id`, never a rewrite of the record it corrects.

    `attempt_id` and `handoff_id` are optional precisely because a pre-dispatch
    refusal is decided before either exists. They carry exactly the lineage that
    actually existed, and a refused or placeholder handoff is never fabricated to
    fill a linkage field.
    """

    build_record_id: str
    prompt_artifact_id: str
    execution_profile_identity: str
    transport_identity: str
    transport_version: str
    outcome: AttemptOutcome
    delivery_certainty: DeliveryCertainty
    execution_evidence: ProviderExecutionEvidence
    retry_authorization: RetryAuthorization
    attempt_cutoff: datetime
    recorded_at: datetime
    reason_code: str
    attempt_id: str | None = None
    handoff_id: str | None = None
    predecessor_attempt_id: str | None = None
    supersedes_outcome_id: str | None = None
    dispatched_at: datetime | None = None
    response_artifact_identity: str | None = None
    response_evidence_state: ResponseEvidenceState | None = None
    correlation_identity: str | None = None
    idempotency_identity: str | None = None
    schema_version: str = "1"

    def __post_init__(self) -> None:
        for name in ("attempt_cutoff", "recorded_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ModelAdapterError(f"{name} must be timezone-aware")
        if self.dispatched_at is not None and self.dispatched_at.tzinfo is None:
            raise ModelAdapterError("dispatched_at must be timezone-aware when present")
        if not self.reason_code:
            raise ModelAdapterError("every outcome requires a stable reason code")

        required = _REQUIRED_DELIVERY_CERTAINTY.get(self.outcome)
        if required is not None and self.delivery_certainty not in required:
            # The single most dangerous mislabelling ADR 0034 names: an ambiguous
            # network failure recorded as known non-delivery. It is refused at
            # construction, so no code path can produce such a record at all.
            raise ModelAdapterError(
                f"outcome {self.outcome} may not be recorded with delivery certainty {self.delivery_certainty}"
            )
        if self.outcome in NON_TRANSMITTING_OUTCOMES and self.dispatched_at is not None:
            raise ModelAdapterError(
                f"outcome {self.outcome} asserts nothing was transmitted, so it has no dispatch time"
            )
        if self.handoff_id is not None and self.attempt_id is None:
            raise ModelAdapterError("an outcome carrying a handoff must carry the attempt that handoff authorized")
        if self.response_artifact_identity is not None and self.response_evidence_state != "RESPONSE_EVIDENCE_DURABLE":
            raise ModelAdapterError("a response-artifact identity requires durable response evidence")
        if self.delivery_certainty == "CONFIRMED_NOT_DELIVERED" and self.response_artifact_identity is not None:
            raise ModelAdapterError("an undelivered request cannot carry captured response evidence")
        if self.retry_authorization == "RETRY_REQUIRES_NEW_ATTEMPT" and self.delivery_certainty == "UNKNOWN":
            # Uncertainty never converts into retry permission.
            raise ModelAdapterError("uncertain delivery cannot authorize a retry")

    @property
    def outcome_id(self) -> str:
        return _identity("model-attempt-outcome", _jsonable(asdict(self)))

    @property
    def is_terminal(self) -> bool:
        """Whether this outcome closes the attempt.

        Uncertain outcomes still close the *attempt* — the attempt is over and is
        never re-dispatched — while leaving retry blocked. That is the ADR 0034
        distinction between an attempt ending and a result being known.
        """
        return True


@dataclass(frozen=True)
class ModelDispatchOutcome:
    """The result of one authorized Phase B dispatch.

    Deliberately carries no raw transport result. Response content leaves the
    adapter only through the governed `ProviderResponseArtifact`, so a caller
    cannot receive bytes whose durability Source Handling denied and persist them
    itself. Governed provider status metadata reaches the caller on the artifact.
    """

    outcome: ModelAttemptOutcomeRecord
    response_artifact: ProviderResponseArtifact | None = None


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


def disposition_identity(decision: Mapping[str, Any]) -> str:
    """Identity of a decision's durable dispositions.

    Public because the persistence boundary must derive this identity the
    *same* way the adapter does in order to reject a handoff bound to
    dispositions the rederived authority did not produce. Single-sourcing it
    here is what makes that check meaningful; a second implementation could
    drift and silently agree with a forgery.
    """
    return _identity("durable-disposition", _jsonable(decision.get("durable_dispositions") or {}))


def category_persist_allowed(decision: Mapping[str, Any], category: str) -> bool:
    """Whether one durable category's PERSIST disposition is ALLOW.

    Public for the same reason as `disposition_identity`: the persistence boundary
    must ask this question the *same* way the adapter does, and a second
    implementation could drift and silently agree with a record the adapter would
    have refused.
    """
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
        category_persist_allowed(decision, category) for category in CONTENT_DERIVED_CATEGORIES
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


def permitted_response_evidence_state(decision: Mapping[str, Any]) -> ResponseEvidenceState:
    """The most permissive response-evidence state this decision authorizes.

    Response durability is governed independently of request durability and
    independently of processing: an attempt may be authorized to send and still be
    prohibited from retaining a single byte of what comes back. Shared by the
    adapter and by persistence so both derive the same answer from the same
    authority rather than trusting a supplied object.
    """
    if decision.get("retention_decision") != "ALLOW" or not all(
        category_persist_allowed(decision, category) for category in CONTENT_DERIVED_CATEGORIES
    ):
        return "RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY"
    return "RESPONSE_EVIDENCE_DURABLE"


def response_content_credential_risk(content: str) -> str | None:
    """The capture gate: a reason code when content may not be persisted, else `None`.

    ADR 0034 requires this to run *before* `ProviderResponseArtifact` is
    constructed, because a provider response is untrusted external content that may
    echo a credential irrespective of which durable categories are authorized.
    Scanning a record after the fact would be detection; this is the boundary.

    It is shape-based rather than keyword-based, so a model answer that merely
    discusses authentication is still retainable, while material that actually
    looks like a key, token, cookie, or private key is refused.
    """
    if _RESPONSE_SECRET_PATTERN.search(content):
        return RESPONSE_EVIDENCE_CREDENTIAL_RISK_REASON
    return None


def classify_transport_result(result: TransportResult) -> tuple[AttemptOutcome, DeliveryCertainty]:
    """Map one transport observation onto a canonical outcome, truthfully.

    The certainty axis governs. A `TIMEOUT` becomes `TIMEOUT_CONFIRMED_NO_DELIVERY`
    only where the transport actually proved non-delivery; otherwise it is
    `DELIVERY_UNKNOWN` and stays uncertain. The same holds for a connection
    failure. ADR 0034 forbids inferring safe non-delivery from a failure class.
    """
    certainty = result.delivery_certainty
    if result.result_class in _CERTAINTY_DEPENDENT_TRANSPORT_CLASSES:
        if certainty == "CONFIRMED_NOT_DELIVERED":
            return "TIMEOUT_CONFIRMED_NO_DELIVERY", certainty
        return "DELIVERY_UNKNOWN", "UNKNOWN"
    outcome = _TRANSPORT_OUTCOME.get(result.result_class)
    if outcome is None:
        # An unrecognized transport class is an adapter-internal condition, not a
        # licence to guess a delivery state.
        return "INTERNAL_ADAPTER_ERROR", "UNKNOWN"
    return outcome, certainty


def derive_retry_authorization(
    *,
    outcome: AttemptOutcome,
    delivery_certainty: DeliveryCertainty,
    execution_evidence: ProviderExecutionEvidence,
) -> RetryAuthorization:
    """What a recorded outcome permits about a *future*, separate attempt.

    Fail-closed by construction: only two things authorize a later attempt — the
    transport proving the request never reached the provider, or the provider
    establishing it rejected the request instead of executing it. Every other
    state, including a rate limit and a server error, leaves retry blocked because
    neither proves the model did not run.
    """
    if outcome == "SUCCEEDED_TRANSPORT":
        return "RETRY_NOT_APPLICABLE"
    if delivery_certainty == "UNKNOWN":
        return "RETRY_BLOCKED_DELIVERY_UNCERTAIN"
    if delivery_certainty == "CONFIRMED_NOT_DELIVERED":
        return "RETRY_REQUIRES_NEW_ATTEMPT"
    if execution_evidence == "NO_EXECUTION_ESTABLISHED":
        return "RETRY_REQUIRES_NEW_ATTEMPT"
    return "RETRY_BLOCKED_RECONCILIATION_REQUIRED"


def _verify_prompt_content_matches_declared_identity(prompt_artifact: EvidencePromptArtifact) -> None:
    """Refuse a prompt artifact whose bytes do not match its own declared identity.

    ADR 0031 derives `artifact_id` from the declared `content_hash` and
    `measured_size_bytes` with `content` excluded, and the artifact performs no
    self-check. Identity equality therefore proves only that the *claims* match;
    this proves the bytes behind those claims do too. Without it, durable lineage
    could name the prepared prompt while entirely different content went to the
    provider.

    Read-only: the upstream artifact is never mutated or re-canonicalized.
    """
    try:
        encoded = prompt_artifact.content.encode(prompt_artifact.encoding)
    except (LookupError, UnicodeEncodeError) as error:
        raise ModelAdapterAuthorityError(
            f"prompt artifact content cannot be encoded as {prompt_artifact.encoding!r}"
        ) from error
    if hashlib.sha256(encoded).hexdigest() != prompt_artifact.content_hash:
        raise ModelAdapterAuthorityError(
            "prompt artifact content does not match the content hash its identity is derived from"
        )
    if len(encoded) != prompt_artifact.measured_size_bytes:
        raise ModelAdapterAuthorityError(
            "prompt artifact content does not match the measured size its identity is derived from"
        )


def attempt_idempotency_identity(attempt: ModelAttemptRecord) -> str | None:
    """One stable, opaque, attempt-scoped idempotency key, where the profile supports it.

    Derived from the attempt identity, so it is stable for reconciliation of *this*
    attempt and structurally cannot be inherited by a later attempt: a retry is a
    different attempt record and therefore a different key.
    """
    if attempt.idempotency_capability != "SUPPORTED":
        return None
    return _identity("model-attempt-idempotency", attempt.attempt_id)


class ModelAdapterService:
    """Sole owner of Phase A Model Adapter authority.

    Every authorization decision is made here. The persistence repository is
    mechanical and cannot be used to fabricate an authoritative record.
    """

    def __init__(
        self,
        repository: Any,
        *,
        dispatch_seam: NoNetworkDispatchSeam | None = None,
        transport_endpoints: Mapping[str, str] | None = None,
    ) -> None:
        self._repository = repository
        self._dispatch_seam = dispatch_seam or NoNetworkDispatchSeam()
        # Endpoint URLs are deployment configuration keyed by the profile's own
        # endpoint-class identity, not a selection surface: the profile names
        # exactly one class and the adapter looks up exactly that one. An absent
        # entry fails closed rather than falling back to a default endpoint.
        self._transport_endpoints: Mapping[str, str] = dict(transport_endpoints or {})

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
        predecessor_outcome: ModelAttemptOutcomeRecord | None = None,
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

        # 0. A retry must first prove the predecessor permits one. ADR 0034 blocks
        #    automatic retry from any outcome whose semantics do not establish that
        #    no provider execution occurred, so the predecessor's own recorded
        #    outcome — not the caller's intent — decides whether a further attempt
        #    may even be prepared. This runs before anything else because a blocked
        #    retry must not resolve authority, write an attempt, or mint a handoff.
        if attempt_ordinal > 1:
            self._require_retry_authorization(
                predecessor_attempt_id=predecessor_attempt_id,
                predecessor_outcome=predecessor_outcome,
                cutoff=attempt_authority.cutoff,
            )
        elif predecessor_outcome is not None:
            raise ModelAdapterError("a first attempt has no predecessor outcome")
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
        if category_persist_allowed(decision, DISPATCH_CAPABILITY_CATEGORY):
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
            durable_disposition_identity=disposition_identity(decision),
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

    # -- Phase B: one real provider dispatch --------------------------------

    def dispatch(
        self,
        *,
        prepared: PreparedModelAttempt,
        profile: ModelExecutionProfile,
        transport: ProviderTransport,
        credential: TransportCredential,
        prompt_artifact: EvidencePromptArtifact,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
        dispatched_at: datetime,
        concluded_at: datetime,
    ) -> ModelDispatchOutcome:
        """Dispatch exactly one prepared attempt to exactly one configured transport.

        The order is fixed and fail-closed:

        1. verify the bound profile and its transport — there is no selection here;
        2. verify the supplied authority is the one this attempt was prepared under;
        3. verify the supplied prompt is the prepared one, bytes included;
        4. verify no terminal outcome already closed this attempt;
        5. build the transient wire representation locally;
        6. atomically consume the single-use handoff, minting the only dispatch
           authorization the transport will accept;
        7. send exactly once;
        8. classify truthfully, capture governed response evidence, append an
           immutable outcome.

        A failure at step 5 is a local pre-send failure: the handoff is not
        consumed, nothing is transmitted, and the recorded outcome says so. A
        failure at or after step 7 can never claim non-delivery it cannot prove.
        """
        for name, value in (("dispatched_at", dispatched_at), ("concluded_at", concluded_at)):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ModelAdapterError(f"{name} must be timezone-aware")

        attempt = prepared.attempt
        handoff = prepared.handoff

        # 1. Exactly one explicitly configured execution profile. The profile must
        #    be the one this attempt was prepared against, and the transport must
        #    be the one that profile names. That pair of checks is what makes
        #    substituting a provider structurally impossible rather than merely
        #    undocumented: there is no selection input to influence.
        if profile.profile_identity != attempt.execution_profile_identity:
            raise ModelAdapterAuthorityError(
                "the supplied execution profile is not the profile this attempt was prepared against"
            )
        transport_identity = getattr(transport, "transport_identity", None)
        transport_version = getattr(transport, "transport_version", None)
        if transport_identity != profile.transport_identity or transport_version != profile.transport_version:
            raise ModelAdapterAuthorityError(
                "the supplied transport is not the transport identity and version bound by the execution profile"
            )

        # 2. The authority handed to dispatch must be the authority this attempt was
        #    prepared under. Persistence re-resolves it independently, but that
        #    check alone would not notice a *different* authority resolving to the
        #    same cutoff, and the capture decision below reads the prepared
        #    snapshot. Comparing the two closes the substitution.
        try:
            supplied = resolve_pre_model_source_handling(attempt_authority)
        except SourceHandlingBlockedError as error:
            raise ModelAdapterAuthorityError(
                f"the authority supplied to dispatch cannot be resolved: {error}"
            ) from error
        if _canonical_json(_jsonable(dict(supplied.decision))) != _canonical_json(
            _jsonable(dict(prepared.resolved_authority.decision))
        ):
            raise ModelAdapterAuthorityError(
                "the authority supplied to dispatch is not the authority this attempt was prepared under"
            )

        # 3. The prompt whose bytes go on the wire must be the prompt this attempt
        #    was prepared against. Matching `artifact_id` alone is not enough:
        #    ADR 0031 derives that identity from the *declared* hash and size with
        #    `content` excluded, and the artifact does not self-verify, so an
        #    artifact retaining the prepared identity could still carry arbitrary
        #    bytes. Both the identity and the bytes behind it are checked, before
        #    the handoff is consumed, so durable lineage cannot name one prompt
        #    while another was transmitted.
        if prompt_artifact.artifact_id != attempt.prompt_artifact_id:
            raise ModelAdapterAuthorityError(
                "the supplied prompt artifact is not the artifact this attempt was prepared against"
            )
        _verify_prompt_content_matches_declared_identity(prompt_artifact)

        # 4. An attempt that already has an outcome is over. Without this, a local
        #    pre-send failure -- which correctly leaves the handoff unconsumed --
        #    would leave the attempt re-dispatchable.
        if self._repository.terminal_outcome_exists(attempt.attempt_id):
            raise HandoffConsumptionError(
                "this attempt already has a recorded outcome; a further attempt requires a new attempt record"
            )

        # 5. Local pre-send transformation. Nothing has been transmitted yet, and
        #    the handoff is deliberately still unconsumed.
        idempotency_identity = attempt_idempotency_identity(attempt)
        try:
            request = TransportRequest(
                endpoint_url=self._endpoint_url(profile),
                request_protocol_identity=profile.request_protocol_identity,
                request_protocol_version=profile.request_protocol_version,
                model_identity=profile.model_identity,
                prompt_content=prompt_artifact.content,
                parameters=profile.parameters,
                idempotency_key=idempotency_identity,
            )
        except Exception as error:  # noqa: BLE001 - any local failure is still pre-send
            return self._record_outcome(
                prepared=prepared,
                profile=profile,
                attempt_authority=attempt_authority,
                outcome="LOCAL_PRE_SEND_FAILED",
                delivery_certainty="CONFIRMED_NOT_DELIVERED",
                execution_evidence="NO_EXECUTION_ESTABLISHED",
                reason_code=f"LOCAL_REQUEST_CONSTRUCTION_FAILED_{type(error).__name__}",
                recorded_at=concluded_at,
                dispatched_at=None,
                transport_identity=profile.transport_identity,
                transport_version=profile.transport_version,
                handoff_id=handoff.handoff_id,
            )

        # 6. Atomic single-use consumption. This is the only place a dispatch
        #    authorization is minted, and it is minted only after the compare-and-set
        #    has already claimed the handoff, so at most one caller can proceed.
        if handoff.is_expired_at(dispatched_at):
            raise HandoffConsumptionError("handoff is expired and cannot authorize dispatch")
        self._repository.consume_handoff_once(handoff_id=handoff.handoff_id, consumed_at=dispatched_at)
        authorization = DispatchAuthorization(
            _DISPATCH_MINT,
            handoff_id=handoff.handoff_id,
            attempt_id=attempt.attempt_id,
            execution_profile_identity=profile.profile_identity,
            consumed_at=dispatched_at.astimezone(UTC).isoformat(),
        )

        # 7. Exactly one provider invocation.
        try:
            result = transport.send(request, authorization=authorization, credential=credential)
        except Exception as error:  # noqa: BLE001 - a transport that raises must not lose lineage
            # The handoff is already consumed, so bytes may well have gone out.
            # Claiming non-delivery here would be exactly the fabrication ADR 0034
            # prohibits; the attempt stays attributable and uncertain instead.
            return self._record_outcome(
                prepared=prepared,
                profile=profile,
                attempt_authority=attempt_authority,
                outcome="INTERNAL_ADAPTER_ERROR",
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                reason_code=f"TRANSPORT_RAISED_{type(error).__name__}",
                recorded_at=concluded_at,
                dispatched_at=dispatched_at,
                transport_identity=profile.transport_identity,
                transport_version=profile.transport_version,
                handoff_id=handoff.handoff_id,
            )

        if not isinstance(result, TransportResult):
            return self._record_outcome(
                prepared=prepared,
                profile=profile,
                attempt_authority=attempt_authority,
                outcome="INTERNAL_ADAPTER_ERROR",
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                reason_code="TRANSPORT_RETURNED_NON_CANONICAL_RESULT",
                recorded_at=concluded_at,
                dispatched_at=dispatched_at,
                transport_identity=profile.transport_identity,
                transport_version=profile.transport_version,
                handoff_id=handoff.handoff_id,
            )
        if result.transport_identity != profile.transport_identity or (
            result.transport_version != profile.transport_version
        ):
            # Version mismatch is treated exactly like identity mismatch: the
            # attempt and profile are bound to one exact transport version, so
            # persisting a different reported version would make the recorded
            # response lineage contradict the durable execution profile.
            #
            # The send already happened, so raising here would lose the lineage
            # that invocation created. It is recorded as an adapter-internal
            # condition with honest uncertainty instead.
            return self._record_outcome(
                prepared=prepared,
                profile=profile,
                attempt_authority=attempt_authority,
                outcome="INTERNAL_ADAPTER_ERROR",
                delivery_certainty="UNKNOWN",
                execution_evidence="UNKNOWN",
                reason_code="TRANSPORT_RESULT_ATTRIBUTED_TO_A_DIFFERENT_TRANSPORT_IDENTITY_OR_VERSION",
                recorded_at=concluded_at,
                dispatched_at=dispatched_at,
                transport_identity=profile.transport_identity,
                transport_version=profile.transport_version,
                handoff_id=handoff.handoff_id,
            )

        # 8. Classification, governed capture, immutable outcome.
        outcome, certainty = classify_transport_result(result)
        return self._record_outcome(
            prepared=prepared,
            profile=profile,
            attempt_authority=attempt_authority,
            outcome=outcome,
            delivery_certainty=certainty,
            execution_evidence=result.execution_evidence,
            reason_code=result.reason_code or f"TRANSPORT_{result.result_class}",
            recorded_at=concluded_at,
            dispatched_at=dispatched_at,
            transport_identity=result.transport_identity,
            transport_version=result.transport_version,
            handoff_id=handoff.handoff_id,
            result=result,
        )

    def recover_nonterminal_attempts(self, *, cutoff: datetime) -> tuple[str, ...]:
        """Attempt identities durably recorded at `cutoff` with no outcome.

        ADR 0034 crash recovery: such an attempt is evidence of uncertainty, never
        permission to retry, and is never rewritten into success or failure. This
        reports them; it fabricates nothing and writes nothing.
        """
        if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
            raise ModelAdapterError("cutoff must be timezone-aware")
        return self._repository.attempts_without_outcome(cutoff=cutoff)

    # -- internals ----------------------------------------------------------

    def _require_retry_authorization(
        self,
        *,
        predecessor_attempt_id: str | None,
        predecessor_outcome: ModelAttemptOutcomeRecord | None,
        cutoff: datetime,
    ) -> None:
        """Authorize a retry only from the predecessor's own *durable* outcome.

        A caller-supplied `ModelAttemptOutcomeRecord` is evidence, never authority.
        Deriving the decision from it would let a caller construct a record naming
        the predecessor and claiming `RETRY_REQUIRES_NEW_ATTEMPT` while the
        repository holds an uncertain outcome or none at all — exactly the blind
        duplicate invocation this gate exists to prevent. So the governing outcome
        is resolved from persistence, strict-known at this attempt's cutoff, and a
        supplied record is only ever compared against it.

        This mirrors the treatment `prepare_attempt` already gives a
        caller-supplied Source Handling decision, and the ADR 0033 persistence
        invariant generally: rederive, then reject disagreement.
        """
        if not predecessor_attempt_id:
            raise RetryNotAuthorized("a retry requires the predecessor attempt identity")

        durable = self._repository.authoritative_outcome(predecessor_attempt_id, cutoff)
        if durable is None:
            raise RetryNotAuthorized(
                "the predecessor attempt has no outcome durably recorded at this cutoff; "
                "an attempt with no recorded outcome is uncertain and blocks retry"
            )
        if durable.attempt_id != predecessor_attempt_id:
            raise RetryNotAuthorized("the durable predecessor outcome does not belong to the predecessor attempt")

        # A supplied record may not disagree with the durable one. Rejecting the
        # mismatch rather than ignoring the argument keeps a caller that believes
        # something false from proceeding on a silently different basis.
        if predecessor_outcome is not None and predecessor_outcome.outcome_id != durable.outcome_id:
            raise RetryNotAuthorized(
                "the supplied predecessor outcome does not match the outcome durably recorded for that attempt"
            )

        if durable.retry_authorization != "RETRY_REQUIRES_NEW_ATTEMPT":
            raise RetryNotAuthorized(
                f"predecessor outcome {durable.outcome} carries "
                f"{durable.retry_authorization} and does not authorize a further attempt"
            )

    def _endpoint_url(self, profile: ModelExecutionProfile) -> str:
        endpoint = self._transport_endpoints.get(profile.endpoint_class_identity)
        if not endpoint:
            raise ModelAdapterError(f"no endpoint is configured for endpoint class {profile.endpoint_class_identity!r}")
        return endpoint

    def _capture_response_evidence(
        self,
        *,
        prepared: PreparedModelAttempt,
        profile: ModelExecutionProfile,
        decision: Mapping[str, Any],
        result: TransportResult,
        recorded_at: datetime,
    ) -> ProviderResponseArtifact:
        """Decide, per category and then per credential risk, what response may persist.

        Two independent gates, in this order. Source Handling decides whether the
        category may ever be durable; the capture gate then decides whether *this*
        content is establishable as credential-free. Either one withholding
        permission produces the governed unavailable state with no content-derived
        material of any kind.
        """
        attempt = prepared.attempt
        handoff = prepared.handoff
        common: dict[str, Any] = {
            "attempt_id": attempt.attempt_id,
            "handoff_id": handoff.handoff_id,
            "execution_profile_identity": profile.profile_identity,
            "request_evidence_identity": attempt.request_evidence_identity,
            "request_evidence_state": attempt.request_evidence_state,
            "response_protocol_identity": result.response_protocol_identity,
            "response_protocol_version": result.response_protocol_version,
            "transport_identity": result.transport_identity,
            "transport_version": result.transport_version,
            "recorded_at": recorded_at.astimezone(UTC),
            "provider_status_metadata": result.provider_status_metadata,
        }

        permitted = permitted_response_evidence_state(decision)
        content = result.response_text
        if permitted != "RESPONSE_EVIDENCE_DURABLE" or content is None:
            reason = (
                RESPONSE_EVIDENCE_UNAVAILABLE_REASON
                if permitted != "RESPONSE_EVIDENCE_DURABLE"
                else "RESPONSE_CONTENT_ABSENT"
            )
            return ProviderResponseArtifact(
                state="RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY",
                reason_code=reason,
                **common,
            )

        risk = response_content_credential_risk(content)
        if risk is not None:
            # Category authorization alone never licenses exact-content
            # persistence. Nothing derived from the content is written either --
            # not a hash, not a size -- because a digest of credential-bearing
            # material is itself a credential-derived representation.
            return ProviderResponseArtifact(
                state="RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK",
                reason_code=risk,
                **common,
            )

        encoded = content.encode("utf-8")
        content_hash = hashlib.sha256(encoded).hexdigest()
        return ProviderResponseArtifact(
            state="RESPONSE_EVIDENCE_DURABLE",
            content=content,
            content_hash=content_hash,
            measured_size_bytes=len(encoded),
            content_derived_identity=_identity(
                "provider-response-content",
                {
                    "attempt_id": attempt.attempt_id,
                    "content_hash": content_hash,
                    "measured_size_bytes": len(encoded),
                },
            ),
            **common,
        )

    def _record_outcome(
        self,
        *,
        prepared: PreparedModelAttempt,
        profile: ModelExecutionProfile,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
        outcome: AttemptOutcome,
        delivery_certainty: DeliveryCertainty,
        execution_evidence: ProviderExecutionEvidence,
        reason_code: str,
        recorded_at: datetime,
        dispatched_at: datetime | None,
        transport_identity: str,
        transport_version: str,
        handoff_id: str | None,
        result: TransportResult | None = None,
    ) -> ModelDispatchOutcome:
        attempt = prepared.attempt
        decision = prepared.resolved_authority.decision

        response_artifact: ProviderResponseArtifact | None = None
        if result is not None and result.response_text is not None:
            response_artifact = self._capture_response_evidence(
                prepared=prepared,
                profile=profile,
                decision=decision,
                result=result,
                recorded_at=recorded_at,
            )

        # Provider correlation and idempotency identifiers are operational
        # metadata, not content. They persist only where that category is
        # authorized, and no substitute is fabricated where it is not.
        correlation_permitted = category_persist_allowed(decision, DISPATCH_CAPABILITY_CATEGORY)
        correlation_identity = result.correlation_identity if result is not None else None
        record = ModelAttemptOutcomeRecord(
            build_record_id=attempt.build_record_id,
            prompt_artifact_id=attempt.prompt_artifact_id,
            execution_profile_identity=profile.profile_identity,
            transport_identity=transport_identity,
            transport_version=transport_version,
            outcome=outcome,
            delivery_certainty=delivery_certainty,
            execution_evidence=execution_evidence,
            retry_authorization=derive_retry_authorization(
                outcome=outcome,
                delivery_certainty=delivery_certainty,
                execution_evidence=execution_evidence,
            ),
            attempt_cutoff=attempt.attempt_cutoff,
            recorded_at=recorded_at.astimezone(UTC),
            reason_code=reason_code,
            attempt_id=attempt.attempt_id,
            handoff_id=handoff_id,
            predecessor_attempt_id=attempt.predecessor_attempt_id,
            dispatched_at=dispatched_at.astimezone(UTC) if dispatched_at is not None else None,
            response_artifact_identity=(
                response_artifact.response_artifact_identity
                if response_artifact is not None and response_artifact.state == "RESPONSE_EVIDENCE_DURABLE"
                else None
            ),
            response_evidence_state=response_artifact.state if response_artifact is not None else None,
            correlation_identity=correlation_identity if correlation_permitted else None,
            idempotency_identity=attempt_idempotency_identity(attempt) if correlation_permitted else None,
        )

        # Every durable payload element is re-verified against the exact historical
        # registry before anything is written, exactly as the pre-send path does.
        # A field whose category the registry does not govern is refused rather
        # than written under an assumed category.
        resolved = prepared.resolved_authority
        fact = resolved.fact_record.get("fact")
        secret_presence = set(_string_sequence(fact.get("secret_presence") if isinstance(fact, Mapping) else None))
        payload: dict[str, Any] = {"model_attempt_outcome": _jsonable(asdict(record))}
        if response_artifact is not None and response_artifact.state == "RESPONSE_EVIDENCE_DURABLE":
            payload["provider_response_content"] = _jsonable(asdict(response_artifact))
        if record.correlation_identity is not None or record.idempotency_identity is not None:
            payload["provider_correlation"] = {
                "correlation_identity": record.correlation_identity,
                "idempotency_identity": record.idempotency_identity,
            }
        try:
            validate_durable_payload(
                decision=decision,
                registry=resolved.registry_record,
                payload=payload,
                secret_presence=secret_presence,
            )
        except SourceHandlingBlockedError as error:
            raise ResponseCaptureBlocked(str(error)) from error

        try:
            self._repository.append_outcome(
                outcome=record,
                response_artifact=response_artifact,
                attempt_authority=attempt_authority,
            )
        except Exception as error:  # noqa: BLE001 - persistence failure is itself an outcome
            if response_artifact is None:
                # Nothing was captured, so there is no captured-response state to
                # record. The attempt intentionally stays nonterminal and recovery
                # reconstructs it as uncertain rather than fabricating a result.
                raise
            return self._record_capture_persistence_failure(
                prepared=prepared,
                profile=profile,
                attempt_authority=attempt_authority,
                recorded_at=recorded_at,
                dispatched_at=dispatched_at,
                transport_identity=transport_identity,
                transport_version=transport_version,
                handoff_id=handoff_id,
                cause=error,
            )

        return ModelDispatchOutcome(outcome=record, response_artifact=response_artifact)

    def _record_capture_persistence_failure(
        self,
        *,
        prepared: PreparedModelAttempt,
        profile: ModelExecutionProfile,
        attempt_authority: EvidencePreModelSourceHandlingAuthority,
        recorded_at: datetime,
        dispatched_at: datetime | None,
        transport_identity: str,
        transport_version: str,
        handoff_id: str | None,
        cause: Exception,
    ) -> ModelDispatchOutcome:
        """Record that a response was captured but its governed persistence failed.

        ADR 0034 is explicit that Hunter must not call the provider again here, and
        that this state stays distinguishable from both execution failure and
        uncertain delivery. It is recorded without the response artifact, because
        the artifact is precisely what could not be written.
        """
        attempt = prepared.attempt
        record = ModelAttemptOutcomeRecord(
            build_record_id=attempt.build_record_id,
            prompt_artifact_id=attempt.prompt_artifact_id,
            execution_profile_identity=profile.profile_identity,
            transport_identity=transport_identity,
            transport_version=transport_version,
            outcome="RESPONSE_CAPTURED_PERSISTENCE_FAILED",
            delivery_certainty="ANSWERED",
            execution_evidence="PROVIDER_RETURNED_COMPLETION",
            retry_authorization="RETRY_BLOCKED_RECONCILIATION_REQUIRED",
            attempt_cutoff=attempt.attempt_cutoff,
            recorded_at=recorded_at.astimezone(UTC),
            reason_code=f"RESPONSE_PERSISTENCE_FAILED_{type(cause).__name__}",
            attempt_id=attempt.attempt_id,
            handoff_id=handoff_id,
            predecessor_attempt_id=attempt.predecessor_attempt_id,
            dispatched_at=dispatched_at.astimezone(UTC) if dispatched_at is not None else None,
        )
        # If even this cannot be written the canonical store is unavailable, so the
        # pre-send attempt is left nonterminal on purpose and recovery classifies
        # it OUTCOME_UNKNOWN. Nothing is invented to paper over that.
        self._repository.append_outcome(outcome=record, response_artifact=None, attempt_authority=attempt_authority)
        return ModelDispatchOutcome(outcome=record, response_artifact=None)
