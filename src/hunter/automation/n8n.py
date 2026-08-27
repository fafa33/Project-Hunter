"""Concrete n8n transport for the Smart Prompt Machine automation boundary.

This module is an operational adapter, not a Smart Prompt Machine authority.
It receives the canonical non-content mapping produced by
``PromptAutomationDispatcher`` and delivers that mapping to one configured n8n
webhook. Endpoint and authentication material remain at the transport edge:
they are loaded from environment configuration, used only to build the outbound
HTTP request, and never enter the canonical payload or acknowledgement.

The adapter deliberately does not retry. A timeout or connection failure leaves
delivery uncertain because n8n may have accepted the request. The caller may
replay the same canonical dispatch after reconciliation; the stable dispatch and
payload identities are the idempotency coordinates for that replay.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import Any
from urllib.parse import unquote, urlsplit

from hunter.evidence_intelligence.model_adapter_transport import TransportCredential
from hunter.evidence_intelligence.smart_prompt_machine import SmartPromptMachineError
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationEnvelope
from hunter.evidence_intelligence.smart_prompt_transport import (
    PromptAutomationAcknowledgement,
    PromptAutomationDestination,
    PromptAutomationDestinationRegistry,
    PromptAutomationDispatcher,
    PromptAutomationPayload,
    PromptAutomationTransportError,
    _identity,
)

N8N_WEBHOOK_URL_ENV = "HUNTER_N8N_WEBHOOK_URL"
N8N_WEBHOOK_TOKEN_ENV = "HUNTER_N8N_WEBHOOK_TOKEN"
N8N_WEBHOOK_TIMEOUT_ENV = "HUNTER_N8N_WEBHOOK_TIMEOUT_SECONDS"

N8N_TRANSPORT_IDENTITY = "transport:n8n-webhook"
N8N_TRANSPORT_VERSION = "1"
N8N_DESTINATION = PromptAutomationDestination(
    destination_id="hunter-n8n",
    version="1",
    destination_key="automation.n8n",
    transport_name="n8n",
)
_N8N_DESTINATION_REGISTRY = PromptAutomationDestinationRegistry((N8N_DESTINATION,))

_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_ACKNOWLEDGEMENT_BYTES = 64 * 1024
_MAX_RECEIPT_ID_LENGTH = 256
_MAX_ENDPOINT_PERCENT_DECODE_ROUNDS = 8
_RECEIPT_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
_PAYLOAD_FIELDS = frozenset(field.name for field in fields(PromptAutomationPayload))
_ACKNOWLEDGEMENT_FIELDS = frozenset(field.name for field in fields(PromptAutomationAcknowledgement))


class _DuplicateJSONKeyError(ValueError):
    """Raised when an untrusted JSON object repeats a key."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Materialize one JSON object while rejecting ambiguous duplicate keys."""
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise _DuplicateJSONKeyError(key)
        values[key] = value
    return values


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the bearer token stays at the configured URL."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Reject every redirect before urllib can construct a follow-up request."""
        raise PromptAutomationTransportError("n8n webhook redirects are not permitted")


_N8N_OPENER = urllib.request.build_opener(_RejectRedirectHandler())


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    """Perform the real request; tests replace this callable with a fake opener."""
    return _N8N_OPENER.open(request, timeout=timeout)


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    """Load one required operational string without accepting control whitespace."""
    value = environ.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise PromptAutomationTransportError(f"{name} must be configured")
    if value != value.strip() or any(ord(character) < 0x20 for character in value):
        raise PromptAutomationTransportError(f"{name} contains invalid whitespace")
    return value


def _validate_endpoint(value: object) -> str:
    """Validate one HTTPS endpoint without echoing malformed endpoint material."""
    if not isinstance(value, str) or not value.strip():
        raise PromptAutomationTransportError("n8n webhook endpoint must be a non-empty string")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value):
        raise PromptAutomationTransportError("n8n webhook endpoint contains invalid whitespace")
    if not value.isascii():
        raise PromptAutomationTransportError("n8n webhook endpoint contains invalid characters")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise PromptAutomationTransportError("n8n webhook endpoint is malformed") from None
    if parsed.scheme != "https" or not hostname:
        raise PromptAutomationTransportError("n8n webhook endpoint must use https")
    if parsed.username is not None or parsed.password is not None:
        raise PromptAutomationTransportError("n8n webhook endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise PromptAutomationTransportError("n8n webhook endpoint must not contain query strings or fragments")
    if port is not None and not 1 <= port <= 65535:
        raise PromptAutomationTransportError("n8n webhook endpoint port is invalid")
    _bounded_percent_decode(parsed.path)
    _bounded_percent_decode(hostname)
    return value


def _validate_timeout(value: object) -> float:
    """Return one positive finite timeout value or fail closed."""
    if isinstance(value, bool):
        raise PromptAutomationTransportError("n8n webhook timeout must be a positive finite number")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise PromptAutomationTransportError("n8n webhook timeout must be a positive finite number") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise PromptAutomationTransportError("n8n webhook timeout must be a positive finite number")
    return timeout


def _canonical_payload(payload: Mapping[str, str]) -> PromptAutomationPayload:
    """Validate the exact Phase C payload shape without acquiring authority."""
    if not isinstance(payload, Mapping):
        raise PromptAutomationTransportError("n8n transport requires a canonical payload mapping")
    try:
        values = dict(payload)
    except (TypeError, ValueError):
        raise PromptAutomationTransportError("n8n transport payload cannot be materialized") from None
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise PromptAutomationTransportError("n8n transport payload must contain string fields only")
    if frozenset(values) != _PAYLOAD_FIELDS:
        raise PromptAutomationTransportError("n8n transport payload schema mismatch")
    try:
        return PromptAutomationPayload(**values)
    except (TypeError, ValueError):
        raise PromptAutomationTransportError("n8n transport payload is not canonical") from None


def _validate_dispatcher_lineage(
    payload: PromptAutomationPayload,
    envelope: object,
) -> None:
    """Require a verified signed envelope bound to this exact n8n dispatch."""
    if type(envelope) is not PromptAutomationEnvelope:
        raise PromptAutomationTransportError("n8n transport requires the canonical automation envelope")
    try:
        envelope.verify_issuer_signature()
    except SmartPromptMachineError:
        raise PromptAutomationTransportError(
            "n8n transport automation envelope issuer signature could not be verified"
        ) from None
    if payload.destination_registry_identity != _N8N_DESTINATION_REGISTRY.registry_identity:
        raise PromptAutomationTransportError("n8n transport destination registry identity mismatch")
    if payload.destination_identity != N8N_DESTINATION.destination_identity:
        raise PromptAutomationTransportError("n8n transport destination identity mismatch")
    if payload.destination_key != N8N_DESTINATION.destination_key:
        raise PromptAutomationTransportError("n8n transport destination key mismatch")
    if payload.transport_name != N8N_DESTINATION.transport_name:
        raise PromptAutomationTransportError("n8n transport name mismatch")
    lineage = (
        ("envelope_id", envelope.envelope_id),
        ("task_request_id", envelope.task_request_id),
        ("route_registry_identity", envelope.route_registry_identity),
        ("profile_registry_identity", envelope.profile_registry_identity),
        ("route_identity", envelope.route_identity),
        ("profile_identity", envelope.profile_identity),
        ("build_manifest_id", envelope.build_manifest_id),
        ("build_record_id", envelope.build_record_id),
    )
    if any(getattr(payload, name) != value for name, value in lineage):
        raise PromptAutomationTransportError("n8n transport payload and envelope lineage mismatch")
    expected_dispatch_id = _identity(
        "smart-prompt-automation-dispatch",
        {
            "destination_registry_identity": payload.destination_registry_identity,
            "destination_identity": payload.destination_identity,
            "envelope_id": envelope.envelope_id,
        },
    )
    if payload.dispatch_id != expected_dispatch_id:
        raise PromptAutomationTransportError("n8n transport dispatch identity is not canonical")


def _canonical_acknowledgement(value: object) -> PromptAutomationAcknowledgement:
    """Parse one exact acknowledgement without echoing untrusted remote keys."""
    if not isinstance(value, dict):
        raise PromptAutomationTransportError("n8n response must be a JSON object acknowledgement")
    if frozenset(value) != _ACKNOWLEDGEMENT_FIELDS:
        raise PromptAutomationTransportError("n8n acknowledgement schema mismatch")
    try:
        return PromptAutomationAcknowledgement(**value)
    except (TypeError, ValueError):
        raise PromptAutomationTransportError("n8n response acknowledgement is not canonical") from None


def _response_content_type(response: Any) -> str:
    """Return the response Content-Type when a header mapping is available."""
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get("Content-Type", "")
    return value if isinstance(value, str) else ""


def _response_content_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("Content-Length")
    if value is None:
        return None
    if not isinstance(value, str):
        raise PromptAutomationTransportError("n8n webhook returned an invalid Content-Length")
    try:
        content_length = int(value, 10)
    except ValueError:
        raise PromptAutomationTransportError("n8n webhook returned an invalid Content-Length") from None
    if content_length < 0:
        raise PromptAutomationTransportError("n8n webhook returned an invalid Content-Length")
    return content_length


def _bounded_percent_decode(value: str) -> str:
    """Decode endpoint material to a fixed point within a bounded number of rounds."""
    current = value
    for _ in range(_MAX_ENDPOINT_PERCENT_DECODE_ROUNDS + 1):
        normalized = unquote(current)
        if normalized == current:
            return current
        current = normalized
    raise PromptAutomationTransportError("n8n webhook endpoint normalization exceeds safe bound")


def _normalized_endpoint_components(endpoint_url: str) -> tuple[str, ...]:
    """Return case- and percent-normalized endpoint components for redaction checks."""
    parsed = urlsplit(endpoint_url)
    decoded_path = _bounded_percent_decode(parsed.path)
    decoded_hostname = _bounded_percent_decode(parsed.hostname or "")
    components = {
        endpoint_url.casefold(),
        parsed.netloc.casefold(),
        (parsed.hostname or "").casefold(),
        decoded_hostname.casefold(),
        parsed.path.casefold(),
        decoded_path.casefold(),
    }
    for hostname in (parsed.hostname or "", decoded_hostname):
        components.update(part.casefold() for part in hostname.split(".") if part)
    for path in (parsed.path, decoded_path):
        components.update(part.casefold() for part in path.split("/") if part)
    return tuple(sorted(component for component in components if component))


def _validate_receipt_id(receipt_id: object, *, bearer_token: str, endpoint_url: str) -> None:
    if not isinstance(receipt_id, str) or not 1 <= len(receipt_id) <= _MAX_RECEIPT_ID_LENGTH:
        raise PromptAutomationTransportError("n8n acknowledgement receipt identity is invalid")
    if any(character not in _RECEIPT_ID_CHARACTERS for character in receipt_id):
        raise PromptAutomationTransportError("n8n acknowledgement receipt identity is invalid")
    normalized_receipt_id = receipt_id.casefold()
    if (
        bearer_token in receipt_id
        or receipt_id in bearer_token
        or endpoint_url in receipt_id
        or receipt_id in endpoint_url
        or any(
            component in normalized_receipt_id or normalized_receipt_id in component
            for component in _normalized_endpoint_components(endpoint_url)
        )
    ):
        raise PromptAutomationTransportError("n8n acknowledgement receipt identity is invalid")


def _validated_bearer_token(credential: TransportCredential) -> str:
    """Reveal and validate one runtime bearer token before HTTP header construction."""
    token = credential.reveal()
    if (
        not isinstance(token, str)
        or not token
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in token)
    ):
        raise PromptAutomationTransportError("n8n webhook credential contains invalid header characters")
    try:
        token.encode("latin-1")
    except UnicodeEncodeError:
        raise PromptAutomationTransportError("n8n webhook credential contains invalid header characters") from None
    return token


class N8nPromptAutomationTransport:
    """Deliver Phase C non-content payloads to one configured n8n webhook.

    The transport has no route/profile/source/prompt authority. The dispatcher
    verifies the signed envelope before the private delivery hook performs HTTP
    mechanics, and the adapter verifies that same signed lineage again. Direct
    calls to ``deliver`` are rejected so payload shape alone cannot authorize a
    network send.
    """

    __slots__ = ("_endpoint_url", "_credential", "_timeout_seconds", "_opener")

    transport_identity = N8N_TRANSPORT_IDENTITY
    transport_version = N8N_TRANSPORT_VERSION

    def __init__(
        self,
        endpoint_url: str,
        credential: TransportCredential,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> None:
        """Bind validated operational endpoint, credential, timeout, and opener state."""
        if not isinstance(credential, TransportCredential):
            raise TypeError("n8n transport requires a non-durable TransportCredential")
        self._endpoint_url = _validate_endpoint(endpoint_url)
        self._credential = credential
        self._timeout_seconds = _validate_timeout(timeout_seconds)
        self._opener = opener or _default_opener

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> N8nPromptAutomationTransport:
        """Build the adapter from operational endpoint/secret configuration."""
        source = os.environ if environ is None else environ
        endpoint_url = _required_environment(source, N8N_WEBHOOK_URL_ENV)
        token = _required_environment(source, N8N_WEBHOOK_TOKEN_ENV)
        timeout_text = source.get(N8N_WEBHOOK_TIMEOUT_ENV, str(_DEFAULT_TIMEOUT_SECONDS))
        credential = TransportCredential(token, slot_identity=f"env:{N8N_WEBHOOK_TOKEN_ENV}")
        return cls(
            endpoint_url,
            credential,
            timeout_seconds=timeout_text,
            opener=opener,
        )

    def __repr__(self) -> str:
        """Return a representation that never renders endpoint or credential material."""
        return "N8nPromptAutomationTransport(<configured>)"

    def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
        """Reject direct delivery without dispatcher-minted authority."""
        raise PromptAutomationTransportError("n8n transport delivery requires dispatcher authorization")

    def _deliver_from_dispatcher(
        self,
        payload: Mapping[str, str],
        envelope: object,
    ) -> PromptAutomationAcknowledgement:
        """POST one canonical payload after signed dispatcher lineage is proven."""
        canonical = _canonical_payload(payload)
        _validate_dispatcher_lineage(canonical, envelope)
        body = json.dumps(
            dict(canonical.as_mapping()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        bearer_token = _validated_bearer_token(self._credential)
        request = urllib.request.Request(
            self._endpoint_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "User-Agent": "Project-Hunter-Smart-Prompt-Machine/1",
            },
            method="POST",
        )
        try:
            with self._opener(request, self._timeout_seconds) as response:
                status = getattr(response, "status", None)
                raw = response.read(_MAX_ACKNOWLEDGEMENT_BYTES + 1)
                content_type = _response_content_type(response)
                content_length = _response_content_length(response)
        except urllib.error.HTTPError as error:
            raise PromptAutomationTransportError(f"n8n webhook returned HTTP {error.code}") from None
        except http.client.HTTPException:
            raise PromptAutomationTransportError(
                "n8n webhook delivery outcome is unknown; reconcile before replaying the same dispatch"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise PromptAutomationTransportError(
                "n8n webhook delivery outcome is unknown; reconcile before replaying the same dispatch"
            ) from None

        if not isinstance(status, int) or isinstance(status, bool):
            raise PromptAutomationTransportError("n8n webhook returned an invalid HTTP status")
        if not 200 <= status < 300:
            raise PromptAutomationTransportError(f"n8n webhook returned HTTP {status}")
        if not isinstance(raw, bytes):
            raise PromptAutomationTransportError("n8n webhook acknowledgement body must be UTF-8 JSON bytes")
        if len(raw) > _MAX_ACKNOWLEDGEMENT_BYTES:
            raise PromptAutomationTransportError("n8n webhook acknowledgement is too large")
        if content_length is not None and len(raw) != content_length:
            raise PromptAutomationTransportError(
                "n8n webhook delivery outcome is unknown; reconcile before replaying the same dispatch"
            )
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise PromptAutomationTransportError("n8n webhook acknowledgement must use application/json")
        try:
            decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
        except _DuplicateJSONKeyError:
            raise PromptAutomationTransportError("n8n webhook acknowledgement contains duplicate JSON keys") from None
        except (RecursionError, UnicodeDecodeError, ValueError):
            raise PromptAutomationTransportError("n8n webhook acknowledgement is malformed JSON") from None

        acknowledgement = _canonical_acknowledgement(decoded)
        _validate_receipt_id(
            acknowledgement.receipt_id,
            bearer_token=bearer_token,
            endpoint_url=self._endpoint_url,
        )
        if acknowledgement.dispatch_id != canonical.dispatch_id:
            raise PromptAutomationTransportError("n8n acknowledgement dispatch identity mismatch")
        if acknowledgement.payload_id != canonical.payload_id:
            raise PromptAutomationTransportError("n8n acknowledgement payload identity mismatch")
        if not acknowledgement.accepted:
            raise PromptAutomationTransportError("n8n webhook rejected the canonical payload")
        return acknowledgement


def build_n8n_prompt_automation_dispatcher(
    *,
    environ: Mapping[str, str] | None = None,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
) -> PromptAutomationDispatcher:
    """Construct the Phase C dispatcher with the operational n8n transport."""
    return PromptAutomationDispatcher(
        destinations=_N8N_DESTINATION_REGISTRY,
        transport=N8nPromptAutomationTransport.from_environment(environ=environ, opener=opener),
    )


__all__ = [
    "N8N_DESTINATION",
    "N8N_TRANSPORT_IDENTITY",
    "N8N_TRANSPORT_VERSION",
    "N8N_WEBHOOK_TIMEOUT_ENV",
    "N8N_WEBHOOK_TOKEN_ENV",
    "N8N_WEBHOOK_URL_ENV",
    "N8nPromptAutomationTransport",
    "build_n8n_prompt_automation_dispatcher",
]
