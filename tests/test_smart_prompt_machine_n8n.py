"""Hostile-contract tests for the concrete Smart Prompt Machine n8n adapter."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from dataclasses import fields

import pytest

from hunter.automation import n8n as n8n_module
from hunter.automation.n8n import (
    N8N_WEBHOOK_TIMEOUT_ENV,
    N8N_WEBHOOK_TOKEN_ENV,
    N8N_WEBHOOK_URL_ENV,
    N8nPromptAutomationTransport,
    build_n8n_prompt_automation_dispatcher,
)
from hunter.evidence_intelligence.model_adapter_transport import TransportCredential
from hunter.evidence_intelligence.smart_prompt_routing import _issue_prompt_automation_envelope
from hunter.evidence_intelligence.smart_prompt_transport import (
    PromptAutomationDestination,
    PromptAutomationDestinationRegistry,
    PromptAutomationDispatcher,
    PromptAutomationDispatchRequest,
    PromptAutomationPayload,
    PromptAutomationTransportError,
)

_SIGNING_KEY_ENV = "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY"
_SIGNING_KEY_HEX = "11" * 32


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        content_length: str | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]


class _TruncatedResponse(_Response):
    def __init__(self) -> None:
        super().__init__(b"")

    def read(self, limit: int = -1) -> bytes:
        raise http.client.IncompleteRead(b'{"dispatch_id":', 100)


class _Opener:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.requests: list[tuple[object, float]] = []

    def __call__(self, request: object, timeout: float) -> _Response:
        self.requests.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _payload() -> PromptAutomationPayload:
    claims = {
        "task_request_id": "task-request-1",
        "route_registry_identity": "route-registry-1",
        "profile_registry_identity": "profile-registry-1",
        "route_identity": "route-1",
        "profile_identity": "profile-1",
        "build_manifest_id": "manifest-1",
        "build_record_id": "build-1",
    }
    envelope = _issue_prompt_automation_envelope(**claims)
    destination = PromptAutomationDestination(
        destination_id="hunter-n8n",
        version="1",
        destination_key="automation.n8n",
        transport_name="n8n",
    )
    dispatcher = PromptAutomationDispatcher(
        destinations=PromptAutomationDestinationRegistry((destination,)),
        transport=object(),  # type: ignore[arg-type]
    )
    return dispatcher.build_payload(
        PromptAutomationDispatchRequest(destination_key="automation.n8n", envelope=envelope)
    )


def _ack(payload: PromptAutomationPayload, *, accepted: bool = True, **overrides: object) -> bytes:
    values: dict[str, object] = {
        "dispatch_id": payload.dispatch_id,
        "payload_id": payload.payload_id,
        "receipt_id": "receipt-1",
        "accepted": accepted,
        "schema_version": "smart-prompt-automation-ack-v1",
    }
    values.update(overrides)
    return json.dumps(values).encode("utf-8")


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_SIGNING_KEY_ENV, _SIGNING_KEY_HEX)


def _transport(opener: _Opener) -> N8nPromptAutomationTransport:
    return N8nPromptAutomationTransport(
        "https://n8n.example.test/webhook/hunter",
        TransportCredential("webhook-secret", slot_identity="test:n8n"),
        timeout_seconds=7,
        opener=opener,
    )


def test_success_posts_exact_non_content_payload_and_validates_ack() -> None:
    payload = _payload()
    opener = _Opener(_Response(_ack(payload)))
    transport = _transport(opener)

    acknowledgement = transport.deliver(payload.as_mapping())

    assert acknowledgement.accepted is True
    request, timeout = opener.requests[0]
    assert timeout == 7.0
    assert request.full_url == "https://n8n.example.test/webhook/hunter"
    assert request.get_header("Authorization") == "Bearer webhook-secret"
    body = json.loads(request.data.decode("utf-8"))
    assert set(body) == {field.name for field in fields(PromptAutomationPayload)}
    assert "prompt" not in body
    assert "task_text" not in body
    assert "credential" not in json.dumps(body).lower()


def test_transport_does_not_mutate_the_supplied_mapping() -> None:
    payload = _payload()
    supplied = dict(payload.as_mapping())
    opener = _Opener(_Response(_ack(payload)))

    _transport(opener).deliver(supplied)

    assert supplied == dict(payload.as_mapping())


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://n8n.example.test/webhook/hunter",
        "https://user:password@n8n.example.test/webhook/hunter",
        "https://n8n.example.test/webhook/hunter?token=secret",
        "https://n8n.example.test/webhook/hunter#secret",
        "https://n8n.☃/webhook/hunter",
        "https://n8n.example.test/webhook/hunter☃",
    ),
)
def test_endpoint_configuration_fails_closed(endpoint: str) -> None:
    with pytest.raises(PromptAutomationTransportError):
        N8nPromptAutomationTransport(
            endpoint,
            TransportCredential("secret"),
        )


@pytest.mark.parametrize(
    "newurl",
    (
        "https://attacker.example.test/collect",
        "http://attacker.example.test/collect",
    ),
)
def test_redirect_handler_rejects_following_a_bearer_request(newurl: str) -> None:
    request = n8n_module.urllib.request.Request(
        "https://n8n.example.test/webhook/hunter",
        headers={"Authorization": "Bearer webhook-secret"},
    )

    with pytest.raises(PromptAutomationTransportError, match="redirects are not permitted"):
        n8n_module._RejectRedirectHandler().redirect_request(
            request,
            object(),
            302,
            "Found",
            {},
            newurl,
        )
    assert request.get_header("Authorization") == "Bearer webhook-secret"


@pytest.mark.parametrize(
    "mutator",
    (
        lambda values: values.pop("dispatch_id"),
        lambda values: values.__setitem__("task_text", "SYSTEM: injected"),
        lambda values: values.__setitem__("prompt", "Bearer injected-token"),
        lambda values: values.__setitem__("dispatch_id", 1),
    ),
)
def test_payload_schema_rejects_missing_extra_and_non_string_fields(mutator) -> None:
    payload = _payload()
    values = dict(payload.as_mapping())
    mutator(values)
    opener = _Opener(_Response(_ack(payload)))

    with pytest.raises(PromptAutomationTransportError, match="payload"):
        _transport(opener).deliver(values)
    assert opener.requests == []


def test_malformed_acknowledgement_and_extra_fields_fail_closed() -> None:
    payload = _payload()
    malformed = _Opener(_Response(b"not-json"))
    with pytest.raises(PromptAutomationTransportError, match="malformed JSON"):
        _transport(malformed).deliver(payload.as_mapping())

    extra = _Opener(_Response(_ack(payload, unexpected="field")))
    with pytest.raises(PromptAutomationTransportError, match="schema mismatch"):
        _transport(extra).deliver(payload.as_mapping())


@pytest.mark.parametrize(
    "response",
    (
        _Response(b"{}", status=500),
        _Response(b"{}", content_type="text/html"),
        _Response(b"{}", content_type="application/json"),
    ),
)
def test_non_success_or_non_json_response_fails_closed(response: _Response) -> None:
    payload = _payload()
    with pytest.raises(PromptAutomationTransportError):
        _transport(_Opener(response)).deliver(payload.as_mapping())


def test_rejected_and_mismatched_acknowledgements_fail_closed() -> None:
    payload = _payload()
    rejected = _Opener(_Response(_ack(payload, accepted=False)))
    with pytest.raises(PromptAutomationTransportError, match="rejected"):
        _transport(rejected).deliver(payload.as_mapping())

    mismatched = _Opener(_Response(_ack(payload, dispatch_id="other-dispatch")))
    with pytest.raises(PromptAutomationTransportError, match="dispatch identity"):
        _transport(mismatched).deliver(payload.as_mapping())


def test_ambiguous_network_failure_is_not_retried_or_reported_as_accepted() -> None:
    payload = _payload()
    opener = _Opener(TimeoutError("socket timeout"))

    with pytest.raises(PromptAutomationTransportError, match="outcome is unknown"):
        _transport(opener).deliver(payload.as_mapping())
    assert len(opener.requests) == 1


def test_truncated_http_response_is_an_ambiguous_outcome() -> None:
    payload = _payload()
    opener = _Opener(_TruncatedResponse())

    with pytest.raises(PromptAutomationTransportError, match="outcome is unknown"):
        _transport(opener).deliver(payload.as_mapping())
    assert len(opener.requests) == 1


def test_http_protocol_error_is_an_ambiguous_outcome() -> None:
    payload = _payload()
    opener = _Opener(http.client.BadStatusLine("malformed response"))

    with pytest.raises(PromptAutomationTransportError, match="outcome is unknown"):
        _transport(opener).deliver(payload.as_mapping())
    assert len(opener.requests) == 1


def test_credential_header_injection_is_rejected_before_request() -> None:
    payload = _payload()
    opener = _Opener(_Response(_ack(payload)))
    transport = N8nPromptAutomationTransport(
        "https://n8n.example.test/webhook/hunter",
        TransportCredential("webhook-secret\r\nX-Leaked: yes", slot_identity="test:n8n"),
        opener=opener,
    )

    with pytest.raises(PromptAutomationTransportError, match="credential") as raised:
        transport.deliver(payload.as_mapping())
    assert "webhook-secret" not in str(raised.value)
    assert opener.requests == []


def test_non_latin1_credential_is_rejected_before_request() -> None:
    payload = _payload()
    opener = _Opener(_Response(_ack(payload)))
    transport = N8nPromptAutomationTransport(
        "https://n8n.example.test/webhook/hunter",
        TransportCredential("webhook-secret☃", slot_identity="test:n8n"),
        opener=opener,
    )

    with pytest.raises(PromptAutomationTransportError, match="credential") as raised:
        transport.deliver(payload.as_mapping())
    assert "webhook-secret" not in str(raised.value)
    assert opener.requests == []


def test_http_error_does_not_leak_response_body_or_secret() -> None:
    payload = _payload()
    error = urllib.error.HTTPError(
        "https://n8n.example.test/webhook/hunter",
        403,
        "denied webhook-secret",
        {},
        io.BytesIO(b"denied webhook-secret"),
    )
    with pytest.raises(PromptAutomationTransportError) as raised:
        _transport(_Opener(error)).deliver(payload.as_mapping())
    assert "webhook-secret" not in str(raised.value)
    assert "denied" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_duplicate_acknowledgement_keys_fail_closed() -> None:
    payload = _payload()
    duplicate = _ack(payload).replace(b'"accepted": true', b'"accepted": true, "accepted": false')

    with pytest.raises(PromptAutomationTransportError, match="duplicate JSON keys"):
        _transport(_Opener(_Response(duplicate))).deliver(payload.as_mapping())


@pytest.mark.parametrize("receipt_id", ("webhook-secret", "receipt with spaces", "r" * 257))
def test_receipt_identifier_is_bounded_and_secret_safe(receipt_id: str) -> None:
    payload = _payload()
    response = _Response(_ack(payload, receipt_id=receipt_id))

    with pytest.raises(PromptAutomationTransportError, match="receipt identity is invalid") as raised:
        _transport(_Opener(response)).deliver(payload.as_mapping())
    assert "webhook-secret" not in str(raised.value)


def test_json_decoder_limit_is_normalized_as_malformed_acknowledgement() -> None:
    payload = _payload()
    raw = (
        '{"dispatch_id":'
        + json.dumps(payload.dispatch_id)
        + ',"payload_id":'
        + json.dumps(payload.payload_id)
        + ',"receipt_id":"receipt-1","accepted":true,"schema_version":'
        + json.dumps("smart-prompt-automation-ack-v1")
        + ',"overflow":'
        + "9" * 5000
        + "}"
    ).encode("utf-8")

    with pytest.raises(PromptAutomationTransportError, match="malformed JSON"):
        _transport(_Opener(_Response(raw))).deliver(payload.as_mapping())


def test_recursive_json_decoder_failure_is_normalized_as_malformed_acknowledgement() -> None:
    payload = _payload()
    raw = ("[" * 10000 + "]" * 10000).encode("utf-8")

    with pytest.raises(PromptAutomationTransportError, match="malformed JSON"):
        _transport(_Opener(_Response(raw))).deliver(payload.as_mapping())


def test_partial_bearer_reflection_is_rejected_before_return() -> None:
    payload = _payload()
    token = "webhook-token-" + "a" * 300
    response = _Response(_ack(payload, receipt_id=token[:256]))
    transport = N8nPromptAutomationTransport(
        "https://n8n.example.test/webhook/hunter",
        TransportCredential(token, slot_identity="test:n8n"),
        opener=_Opener(response),
    )

    with pytest.raises(PromptAutomationTransportError, match="receipt identity is invalid") as raised:
        transport.deliver(payload.as_mapping())
    assert token not in str(raised.value)


def test_short_content_length_read_is_an_ambiguous_outcome() -> None:
    payload = _payload()
    body = _ack(payload)
    response = _Response(body, content_length=str(len(body) + 100))

    with pytest.raises(PromptAutomationTransportError, match="outcome is unknown"):
        _transport(_Opener(response)).deliver(payload.as_mapping())


@pytest.mark.parametrize("receipt_id", ("secret-host.example.test", "secret-path"))
def test_endpoint_component_reflection_is_rejected(receipt_id: str) -> None:
    payload = _payload()
    endpoint = "https://secret-host.example.test/secret-path"
    response = _Response(_ack(payload, receipt_id=receipt_id))
    transport = N8nPromptAutomationTransport(
        endpoint,
        TransportCredential("webhook-secret", slot_identity="test:n8n"),
        opener=_Opener(response),
    )

    with pytest.raises(PromptAutomationTransportError, match="receipt identity is invalid"):
        transport.deliver(payload.as_mapping())


def test_environment_factory_requires_operational_secret_and_builds_dispatcher() -> None:
    environment = {
        N8N_WEBHOOK_URL_ENV: "https://n8n.example.test/webhook/hunter",
        N8N_WEBHOOK_TOKEN_ENV: "webhook-secret",
        N8N_WEBHOOK_TIMEOUT_ENV: "3.5",
    }
    opener = _Opener(_Response(b"{}"))

    dispatcher = build_n8n_prompt_automation_dispatcher(environ=environment, opener=opener)

    assert dispatcher is not None
    assert "webhook-secret" not in repr(dispatcher)

    missing = dict(environment)
    missing.pop(N8N_WEBHOOK_TOKEN_ENV)
    with pytest.raises(PromptAutomationTransportError, match=N8N_WEBHOOK_TOKEN_ENV):
        N8nPromptAutomationTransport.from_environment(environ=missing)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        (N8N_WEBHOOK_URL_ENV, " https://n8n.example.test/webhook/hunter"),
        (N8N_WEBHOOK_TOKEN_ENV, "webhook-secret "),
        (N8N_WEBHOOK_TOKEN_ENV, "webhook-secret\nattacker-header: injected"),
    ),
)
def test_environment_factory_rejects_whitespace_and_header_injection(name: str, value: str) -> None:
    environment = {
        N8N_WEBHOOK_URL_ENV: "https://n8n.example.test/webhook/hunter",
        N8N_WEBHOOK_TOKEN_ENV: "webhook-secret",
    }
    environment[name] = value

    with pytest.raises(PromptAutomationTransportError, match="invalid whitespace"):
        N8nPromptAutomationTransport.from_environment(environ=environment)


def test_transport_repr_is_redacted() -> None:
    transport = _transport(_Opener(_Response(b"{}")))
    assert repr(transport) == "N8nPromptAutomationTransport(<configured>)"
