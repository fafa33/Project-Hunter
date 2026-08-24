"""ADR 0034 Phase B — provider transport boundary and the first concrete transport.

A transport is a *subordinate implementation detail* of the Model Adapter. ADR
0034 confines it to provider-specific mechanics:

* deterministic translation of already-authorized adapter state into a transient,
  non-secret wire representation;
* the network invocation itself;
* provider correlation and idempotency mechanics the provider defines;
* transport-level normalization of the result.

Everything else is refused here by construction. A transport in this module owns
no canonical record family, no Source Handling decision, no retention outcome, no
response truth, no promotion, no routing, and no repository access. It returns
evidence; `ModelAdapterService` decides what that evidence means.

Two structural boundaries live here rather than in prose:

* `TransportCredential` holds provider authentication material in memory only. It
  is not a string, does not render its value, and refuses serialization, so a
  credential cannot be smuggled into a durable record by assignment or by an
  accidental `repr()` in a log line.
* `DispatchAuthorization` cannot be minted through any public API. Only the Model
  Adapter can create one, and only after it has atomically consumed a valid
  single-use handoff. A transport therefore has no supported way to send anything
  on its own authority.

Delivery certainty is reported as evidence, never as convenience. A transport
here claims `CONFIRMED_NOT_DELIVERED` only where the failure provably occurred
before the provider accepted any request bytes; every other ambiguity is
`UNKNOWN` and stays uncertain.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

# Whether the request provably reached the provider.
#
# `ANSWERED` means a complete provider response was received — success or a
# provider-declared error alike. `CONFIRMED_NOT_DELIVERED` is reserved for
# failures that happen before the provider can accept request bytes at all.
# Everything else is `UNKNOWN`, which ADR 0034 requires to stay uncertain rather
# than collapse into safe non-delivery.
DeliveryCertainty = Literal["ANSWERED", "CONFIRMED_NOT_DELIVERED", "UNKNOWN"]

# Whether it is established that no model execution occurred.
# Separate from delivery certainty on purpose: a request can be delivered and
# answered while the provider rejects it instead of executing the model, and a
# request can be delivered while execution remains genuinely unknown. Only two
# things establish non-execution: the provider returning a structured rejection
# in place of a completion, or the request provably never reaching the provider.
ProviderExecutionEvidence = Literal[
    "PROVIDER_RETURNED_COMPLETION",
    "NO_EXECUTION_ESTABLISHED",
    "UNKNOWN",
]

# Transport-level result classes. These are *transport* observations; the
# canonical outcome vocabulary belongs to the Model Adapter, which maps these
# onto `ModelAttemptOutcomeRecord` outcomes.
TransportResultClass = Literal[
    "RESPONSE_RECEIVED",
    "MALFORMED_RESPONSE",
    "PROVIDER_REFUSED",
    "PROVIDER_UNAVAILABLE",
    "RATE_LIMITED",
    "QUOTA_UNAVAILABLE",
    "BILLING_UNAVAILABLE",
    "CAPABILITY_REJECTED",
    "SECURITY_BLOCKED",
    "TIMEOUT",
    "CONNECTION_FAILED",
]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    """The real network call, isolated so tests can substitute it without a socket."""
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https enforced by TransportRequest


class ProviderTransportError(RuntimeError):
    """Base class for transport-boundary violations."""


class TransportAuthorityError(ProviderTransportError):
    """Raised when a transport is asked to act without Model Adapter authority."""


# Only `model_adapter` holds this object. `DispatchAuthorization` refuses to be
# constructed without it, so no public call sequence can manufacture the proof
# that exactly one valid handoff was consumed for this send.
_DISPATCH_MINT = object()


class TransportCredential:
    """Provider authentication material, held in memory at the transport edge only.

    Deliberately not a `str` and not a dataclass. It renders as a redaction, has
    no public attribute carrying the value, refuses `__eq__`-by-value comparison
    against raw strings, and raises on any attempt to serialize it. The durable
    Model Adapter surfaces reject non-scalar values outright, so an instance of
    this type cannot become a persisted field even by mistake.
    """

    __slots__ = ("_secret", "_slot_identity")

    def __init__(self, secret: str, *, slot_identity: str = "") -> None:
        if not isinstance(secret, str) or not secret:
            raise ProviderTransportError("a transport credential requires a non-empty secret value")
        if not isinstance(slot_identity, str):
            raise ProviderTransportError("credential slot identity must be a string when present")
        object.__setattr__(self, "_secret", secret)
        object.__setattr__(self, "_slot_identity", slot_identity)

    @property
    def slot_identity(self) -> str:
        """The non-secret configuration slot name, safe to record where authorized."""
        return str(object.__getattribute__(self, "_slot_identity"))

    def reveal(self) -> str:
        """Return the secret. Called only while building an outbound wire request."""
        return str(object.__getattribute__(self, "_secret"))

    def __setattr__(self, name: str, value: object) -> None:
        raise ProviderTransportError("a transport credential is immutable")

    def __repr__(self) -> str:
        return "TransportCredential(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise ProviderTransportError("a transport credential may not be serialized")

    def __getstate__(self) -> Any:
        raise ProviderTransportError("a transport credential may not be serialized")


@dataclass(frozen=True)
class DispatchAuthorization:
    """Proof that the adapter consumed exactly one valid handoff for exactly one send.

    A transport cannot mint this. `ModelAdapterService` creates it only after
    `consume_handoff_once` has claimed the handoff, so the existence of an
    authorization is itself evidence that a durable attempt and an unconsumed
    handoff were present a moment earlier.
    """

    handoff_id: str
    attempt_id: str
    execution_profile_identity: str
    consumed_at: str

    def __init__(
        self,
        mint: object,
        *,
        handoff_id: str,
        attempt_id: str,
        execution_profile_identity: str,
        consumed_at: str,
    ) -> None:
        if mint is not _DISPATCH_MINT:
            raise TransportAuthorityError(
                "a dispatch authorization is minted only by the Model Adapter after consuming a handoff"
            )
        object.__setattr__(self, "handoff_id", handoff_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "execution_profile_identity", execution_profile_identity)
        object.__setattr__(self, "consumed_at", consumed_at)


@dataclass(frozen=True)
class TransportRequest:
    """The transient, non-secret transformation result.

    ADR 0034: this is not a Hunter durable artifact, carries no Source Handling or
    persistence authority, and never mutates `EvidencePromptArtifact`. It holds no
    credential — authentication is applied by the transport at send time and never
    stored on this object.
    """

    endpoint_url: str
    request_protocol_identity: str
    request_protocol_version: str
    model_identity: str
    prompt_content: str
    parameters: tuple[tuple[str, str], ...] = ()
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint_url.startswith("https://"):
            # A cleartext endpoint would put the credential on the wire in the
            # clear, so the transformation result refuses to describe one.
            raise ProviderTransportError("a provider endpoint must be https")


@dataclass(frozen=True)
class TransportResult:
    """One normalized transport observation, held in memory.

    Carries raw response text as *evidence*. Whether any of it may become durable
    is decided by the Model Adapter against Source Handling, never here.
    """

    result_class: TransportResultClass
    delivery_certainty: DeliveryCertainty
    execution_evidence: ProviderExecutionEvidence
    transport_identity: str
    transport_version: str
    response_protocol_identity: str
    response_protocol_version: str
    response_text: str | None = None
    provider_status_metadata: tuple[tuple[str, str], ...] = ()
    correlation_identity: str | None = None
    idempotency_key: str | None = None
    reason_code: str = ""

    def __post_init__(self) -> None:
        if self.result_class == "RESPONSE_RECEIVED" and self.response_text is None:
            raise ProviderTransportError("a received response must carry the captured response text")
        if self.delivery_certainty == "CONFIRMED_NOT_DELIVERED" and self.response_text is not None:
            raise ProviderTransportError("a request proven undelivered cannot carry a provider response")
        if self.execution_evidence == "PROVIDER_RETURNED_COMPLETION" and self.delivery_certainty != "ANSWERED":
            raise ProviderTransportError("a returned completion requires an answered delivery certainty")


@runtime_checkable
class ProviderTransport(Protocol):
    """The provider-neutral transport contract the Model Adapter depends on.

    Note what is absent: no repository, no Source Handling parameter, no artifact
    construction, no profile selection, and no retry. A transport sends one
    already-authorized request exactly once and reports what happened.
    """

    transport_identity: str
    transport_version: str

    def send(
        self,
        request: TransportRequest,
        *,
        authorization: DispatchAuthorization,
        credential: TransportCredential,
    ) -> TransportResult:
        """Perform exactly one provider invocation for one consumed handoff."""
        raise NotImplementedError


# --- OpenAI Chat Completions -------------------------------------------------

OPENAI_TRANSPORT_IDENTITY = "transport:openai-chat-completions"
OPENAI_TRANSPORT_VERSION = "1"
OPENAI_REQUEST_PROTOCOL_IDENTITY = "protocol:openai-chat-completions"
OPENAI_REQUEST_PROTOCOL_VERSION = "1"
OPENAI_RESPONSE_PROTOCOL_IDENTITY = "response-protocol:openai-chat-completions"
OPENAI_RESPONSE_PROTOCOL_VERSION = "1"
OPENAI_DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

# Provider error codes that establish the request was rejected *instead of* being
# executed. Narrow on purpose: anything not proven here stays `UNKNOWN`, because
# ADR 0034 forbids assuming non-execution from a status code alone.
_OPENAI_NO_EXECUTION_STATUSES = frozenset({400, 401, 402, 403, 404, 422})
_OPENAI_CAPABILITY_ERROR_CODES = frozenset(
    {"model_not_found", "unsupported_parameter", "unsupported_value", "unsupported_country_region_territory"}
)
_OPENAI_QUOTA_ERROR_CODES = frozenset({"insufficient_quota"})
_OPENAI_BILLING_ERROR_CODES = frozenset({"billing_hard_limit_reached", "account_deactivated"})

# Numeric-valued OpenAI parameters. Profile parameters are canonically strings so
# that profile identity stays byte-stable; the wire body needs their real JSON
# types, and a provider rejects `"temperature": "0"`.
_OPENAI_FLOAT_PARAMETERS = frozenset({"temperature", "top_p", "presence_penalty", "frequency_penalty"})
_OPENAI_INT_PARAMETERS = frozenset({"max_tokens", "max_completion_tokens", "n", "seed"})
_OPENAI_BOOL_PARAMETERS = frozenset({"stream", "store", "logprobs"})


# Body keys a profile parameter may never set. `model` is provider selection and
# `messages` is prompt content: allowing either as a parameter would let
# configuration silently override the profile's model identity or the canonical
# prompt artifact, which is precisely the authority the transport does not hold.
_OPENAI_RESERVED_BODY_KEYS = frozenset({"model", "messages"})


def openai_request_body(request: TransportRequest) -> dict[str, Any]:
    """Build the exact OpenAI chat-completions body for one request.

    Pure and deterministic, so the wire shape is testable without a network.
    """
    body: dict[str, Any] = {
        "model": request.model_identity,
        "messages": [{"role": "user", "content": request.prompt_content}],
    }
    for name, value in request.parameters:
        if name in _OPENAI_RESERVED_BODY_KEYS:
            raise ProviderTransportError(f"provider parameter {name!r} may not override the request body")
        body[name] = _openai_parameter_value(name, value)
    return body


def _openai_parameter_value(name: str, value: str) -> Any:
    try:
        if name in _OPENAI_FLOAT_PARAMETERS:
            return float(value)
        if name in _OPENAI_INT_PARAMETERS:
            return int(value)
    except ValueError as error:
        raise ProviderTransportError(f"numeric provider parameter {name!r} is not a valid number") from error
    if name in _OPENAI_BOOL_PARAMETERS:
        if value not in ("true", "false"):
            raise ProviderTransportError(f"boolean provider parameter {name!r} must be 'true' or 'false'")
        return value == "true"
    return value


def _error_code(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return ""
    code = error.get("code") or error.get("type")
    return str(code) if isinstance(code, str) else ""


def classify_openai_http_status(status: int, body_text: str) -> tuple[TransportResultClass, ProviderExecutionEvidence]:
    """Map one OpenAI HTTP status and body onto a transport class and execution evidence.

    Pure, so every branch is deterministically testable. The execution-evidence
    axis is deliberately conservative: `429` and `5xx` stay `UNKNOWN` because
    neither status proves the model did not run, and ADR 0034 refuses to assume
    non-delivery from a rate-limit or a server error.
    """
    try:
        payload: object = json.loads(body_text) if body_text else None
    except (TypeError, ValueError):
        payload = None
    code = _error_code(payload)

    if code in _OPENAI_CAPABILITY_ERROR_CODES:
        return "CAPABILITY_REJECTED", "NO_EXECUTION_ESTABLISHED"
    if code in _OPENAI_QUOTA_ERROR_CODES:
        # Quota exhaustion is declared before execution, but the request was still
        # answered; `insufficient_quota` arrives as 429, so it is separated from a
        # plain rate limit by its error code rather than by its status.
        return "QUOTA_UNAVAILABLE", "NO_EXECUTION_ESTABLISHED"
    if code in _OPENAI_BILLING_ERROR_CODES:
        return "BILLING_UNAVAILABLE", "NO_EXECUTION_ESTABLISHED"

    execution: ProviderExecutionEvidence = (
        "NO_EXECUTION_ESTABLISHED" if status in _OPENAI_NO_EXECUTION_STATUSES else "UNKNOWN"
    )
    if status in (401, 403):
        return "SECURITY_BLOCKED", execution
    if status == 402:
        return "BILLING_UNAVAILABLE", execution
    if status == 429:
        return "RATE_LIMITED", "UNKNOWN"
    if status in (400, 404, 422):
        return "PROVIDER_REFUSED", execution
    if status >= 500:
        return "PROVIDER_UNAVAILABLE", "UNKNOWN"
    return "PROVIDER_REFUSED", execution


def _openai_status_metadata(status: int, payload: object) -> tuple[tuple[str, str], ...]:
    """Governed, non-secret provider status metadata.

    A closed allow-list, not a copy of the response: an echo of arbitrary provider
    fields is exactly how untrusted content reaches a durable surface.
    """
    metadata: list[tuple[str, str]] = [("http_status", str(status))]
    if isinstance(payload, Mapping):
        choices = payload.get("choices")
        if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)) and choices:
            first = choices[0]
            if isinstance(first, Mapping) and isinstance(first.get("finish_reason"), str):
                metadata.append(("finish_reason", str(first["finish_reason"])))
        if isinstance(payload.get("model"), str):
            metadata.append(("provider_reported_model", str(payload["model"])))
        code = _error_code(payload)
        if code:
            metadata.append(("provider_error_code", code))
    return tuple(metadata)


def _openai_correlation_identity(headers: Mapping[str, str] | None) -> str | None:
    """The provider's correlation identifier, found regardless of header casing.

    `send` converts the response headers with `dict(...)`, which drops the
    case-insensitive lookup `http.client.HTTPMessage` provides. Enumerating
    spellings would silently lose the identity for any casing not listed, so the
    names are normalized instead.
    """
    if not headers:
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == "x-request-id" and isinstance(value, str) and value:
            return value
    return None


@dataclass(frozen=True)
class OpenAIChatCompletionsTransport:
    """The first concrete Phase B transport: OpenAI Chat Completions over HTTPS.

    Uses the repository's existing stdlib HTTP convention rather than adding a
    provider SDK dependency, so no new third-party package enters the governed
    dependency set for one endpoint.

    `opener` exists so the classification and normalization path is exercised
    deterministically in CI without a socket. It is an injection seam, not a
    routing or fallback surface: it changes who performs the HTTP call, never
    which provider, model, endpoint, or profile is used.
    """

    endpoint_url: str = OPENAI_DEFAULT_ENDPOINT
    request_timeout_seconds: float = 60.0
    opener: Callable[..., Any] = _default_opener
    transport_identity: str = OPENAI_TRANSPORT_IDENTITY
    transport_version: str = OPENAI_TRANSPORT_VERSION

    def send(
        self,
        request: TransportRequest,
        *,
        authorization: DispatchAuthorization,
        credential: TransportCredential,
    ) -> TransportResult:
        if not isinstance(authorization, DispatchAuthorization):
            raise TransportAuthorityError("a provider send requires a Model Adapter dispatch authorization")
        if not isinstance(credential, TransportCredential):
            raise TransportAuthorityError("a provider send requires a non-durable transport credential")
        if request.endpoint_url != self.endpoint_url:
            # `send` builds the wire request from `request.endpoint_url`, which the
            # adapter derives from the profile's endpoint class. Leaving this field
            # unchecked would make it a silent second source of truth for where a
            # credential is transmitted: a deployment configuring the transport
            # endpoint would send somewhere else and never know.
            raise ProviderTransportError("the request endpoint is not the endpoint this transport is configured for")

        try:
            body = json.dumps(openai_request_body(request)).encode("utf-8")
        except ProviderTransportError as error:
            # The body could not be built, so no request byte was ever offered to
            # the network. That is provable non-delivery; reporting it as
            # uncertain would overstate what happened and needlessly block retry.
            return self._result(
                "CONNECTION_FAILED",
                "CONFIRMED_NOT_DELIVERED",
                "NO_EXECUTION_ESTABLISHED",
                reason_code=f"REQUEST_BODY_NOT_CONSTRUCTED_{type(error).__name__}",
            )
        headers = {
            "Content-Type": "application/json",
            # The only place the secret exists on this path, and it is written
            # straight into an outbound header that is never returned or recorded.
            "Authorization": f"Bearer {credential.reveal()}",
        }
        if request.idempotency_key:
            headers["Idempotency-Key"] = request.idempotency_key

        wire = urllib.request.Request(request.endpoint_url, data=body, headers=headers, method="POST")
        try:
            with self.opener(wire, timeout=self.request_timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or 0)
                raw = response.read()
                response_headers = dict(getattr(response, "headers", {}) or {})
        except urllib.error.HTTPError as error:  # a provider-declared error, delivered and answered
            return self._answered_error(error)
        except TimeoutError as error:
            # The request was already on the wire, so acceptance is genuinely
            # unknown. ADR 0034 forbids calling this confirmed non-delivery.
            return self._uncertain("TIMEOUT", f"read timeout: {type(error).__name__}")
        except urllib.error.URLError as error:
            return self._url_error(error)
        except OSError as error:
            return self._uncertain("CONNECTION_FAILED", f"connection failure: {type(error).__name__}")

        return self._answered_success(status, raw, response_headers)

    # -- normalization ------------------------------------------------------

    def _answered_success(self, status: int, raw: object, headers: Mapping[str, str]) -> TransportResult:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            payload: object = json.loads(text)
        except ValueError:
            return self._result(
                "MALFORMED_RESPONSE",
                "ANSWERED",
                "UNKNOWN",
                response_text=text,
                provider_status_metadata=(("http_status", str(status)),),
                correlation_identity=_openai_correlation_identity(headers),
                reason_code="RESPONSE_BODY_NOT_VALID_JSON",
            )
        if not isinstance(payload, Mapping) or not payload.get("choices"):
            return self._result(
                "MALFORMED_RESPONSE",
                "ANSWERED",
                "UNKNOWN",
                response_text=text,
                provider_status_metadata=_openai_status_metadata(status, payload),
                correlation_identity=_openai_correlation_identity(headers),
                reason_code="RESPONSE_CARRIES_NO_COMPLETION",
            )
        return self._result(
            "RESPONSE_RECEIVED",
            "ANSWERED",
            "PROVIDER_RETURNED_COMPLETION",
            response_text=text,
            provider_status_metadata=_openai_status_metadata(status, payload),
            correlation_identity=_openai_correlation_identity(headers),
        )

    def _answered_error(self, error: urllib.error.HTTPError) -> TransportResult:
        status = int(error.code)
        try:
            raw = error.read()
        except Exception:  # noqa: BLE001 - a body that cannot be read is still an answered error
            raw = b""
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        result_class, execution = classify_openai_http_status(status, text)
        try:
            payload: object = json.loads(text) if text else None
        except ValueError:
            payload = None
        return self._result(
            result_class,
            "ANSWERED",
            execution,
            # The provider error body is not captured as response evidence: it is
            # not a model response, and the governed status metadata below already
            # records the parts that are safe and useful.
            response_text=None,
            provider_status_metadata=_openai_status_metadata(status, payload),
            correlation_identity=_openai_correlation_identity(dict(getattr(error, "headers", {}) or {})),
            reason_code=f"PROVIDER_HTTP_{status}",
        )

    def _url_error(self, error: urllib.error.URLError) -> TransportResult:
        reason = error.reason
        if isinstance(reason, TimeoutError):
            return self._uncertain("TIMEOUT", "read timeout: URLError(TimeoutError)")
        if isinstance(reason, OSError) and _is_pre_send_failure(reason):
            # Name resolution and connection refusal both fail before any request
            # byte is accepted, so non-delivery here is provable rather than assumed.
            return self._result(
                "CONNECTION_FAILED",
                "CONFIRMED_NOT_DELIVERED",
                "NO_EXECUTION_ESTABLISHED",
                reason_code=f"CONNECTION_NOT_ESTABLISHED_{type(reason).__name__}",
            )
        return self._uncertain("CONNECTION_FAILED", f"connection failure: {type(reason).__name__}")

    def _uncertain(self, result_class: TransportResultClass, reason_code: str) -> TransportResult:
        return self._result(result_class, "UNKNOWN", "UNKNOWN", reason_code=reason_code)

    def _result(
        self,
        result_class: TransportResultClass,
        delivery_certainty: DeliveryCertainty,
        execution_evidence: ProviderExecutionEvidence,
        *,
        response_text: str | None = None,
        provider_status_metadata: tuple[tuple[str, str], ...] = (),
        correlation_identity: str | None = None,
        reason_code: str = "",
    ) -> TransportResult:
        return TransportResult(
            result_class=result_class,
            delivery_certainty=delivery_certainty,
            execution_evidence=execution_evidence,
            transport_identity=self.transport_identity,
            transport_version=self.transport_version,
            response_protocol_identity=OPENAI_RESPONSE_PROTOCOL_IDENTITY,
            response_protocol_version=OPENAI_RESPONSE_PROTOCOL_VERSION,
            response_text=response_text,
            provider_status_metadata=provider_status_metadata,
            correlation_identity=correlation_identity,
            reason_code=reason_code,
        )


def _is_pre_send_failure(reason: OSError) -> bool:
    """Whether this OS-level failure provably happened before any request byte was accepted.

    Only DNS resolution failure and connection refusal qualify. A reset or a
    broken pipe can occur after the provider has already accepted the request, so
    those deliberately do not qualify and remain uncertain.
    """
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))
