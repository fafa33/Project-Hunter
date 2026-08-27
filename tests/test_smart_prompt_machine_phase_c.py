from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace

import pytest
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationEnvelope
from hunter.evidence_intelligence.smart_prompt_transport import (
    PROMPT_AUTOMATION_ACK_SCHEMA_VERSION,
    PromptAutomationAcknowledgement,
    PromptAutomationDestination,
    PromptAutomationDestinationRegistry,
    PromptAutomationDispatcher,
    PromptAutomationDispatchRequest,
    PromptAutomationPayload,
    PromptAutomationTransportError,
)


def _envelope() -> PromptAutomationEnvelope:
    return PromptAutomationEnvelope(
        task_request_id="task-request-1",
        route_registry_identity="route-registry-1",
        profile_registry_identity="profile-registry-1",
        route_identity="route-1",
        profile_identity="profile-1",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )


def _destination(
    *,
    destination_id: str = "hunter-n8n",
    destination_key: str = "automation.n8n",
    transport_name: str = "n8n",
) -> PromptAutomationDestination:
    return PromptAutomationDestination(
        destination_id=destination_id,
        version="1",
        destination_key=destination_key,
        transport_name=transport_name,
    )


class _AcceptingTransport:
    def __init__(self) -> None:
        self.payloads: list[Mapping[str, str]] = []

    def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
        self.payloads.append(payload)
        return PromptAutomationAcknowledgement(
            dispatch_id=payload["dispatch_id"],
            payload_id=PromptAutomationPayload(**dict(payload)).payload_id,
            receipt_id="receipt-1",
            accepted=True,
        )


def _dispatcher(transport: object) -> PromptAutomationDispatcher:
    return PromptAutomationDispatcher(
        destinations=PromptAutomationDestinationRegistry((_destination(),)),
        transport=transport,  # type: ignore[arg-type]
    )


def test_dispatch_request_exposes_no_prompt_or_delivery_authority_surface() -> None:
    assert tuple(field.name for field in fields(PromptAutomationDispatchRequest)) == (
        "destination_key",
        "envelope",
        "schema_version",
    )
    forbidden = {
        "task_text",
        "prompt",
        "system_prompt",
        "profile_id",
        "profile_version",
        "context_policy_id",
        "source_handling_authority",
        "provider",
        "retention",
        "webhook_url",
        "token",
        "secret",
    }
    assert forbidden.isdisjoint(field.name for field in fields(PromptAutomationDispatchRequest))

    with pytest.raises(TypeError):
        PromptAutomationDispatchRequest(  # type: ignore[call-arg]
            destination_key="automation.n8n",
            envelope=_envelope(),
            webhook_url="https://example.invalid/hook",
        )


def test_destination_registry_is_exact_order_stable_and_identity_unique() -> None:
    first = _destination(destination_id="n8n-a", destination_key="automation.a")
    second = _destination(destination_id="n8n-b", destination_key="automation.b")
    left = PromptAutomationDestinationRegistry((second, first))
    right = PromptAutomationDestinationRegistry((first, second))
    assert left.registry_identity == right.registry_identity

    with pytest.raises(PromptAutomationTransportError, match="duplicate automation destination identity/version"):
        PromptAutomationDestinationRegistry((first, first))

    conflicting_coordinate = replace(first, destination_key="automation.other")
    with pytest.raises(PromptAutomationTransportError, match="conflicting automation destination identity/version"):
        PromptAutomationDestinationRegistry((first, conflicting_coordinate))

    conflicting_key = _destination(destination_id="different", destination_key="automation.a")
    with pytest.raises(PromptAutomationTransportError, match="conflicting automation destination key"):
        PromptAutomationDestinationRegistry((first, conflicting_key))

    with pytest.raises(PromptAutomationTransportError, match="wildcards are forbidden"):
        _destination(destination_key="automation.*")
    with pytest.raises(PromptAutomationTransportError, match="unknown governed automation destination"):
        left.resolve("automation.missing")


def test_unknown_schema_versions_fail_closed() -> None:
    with pytest.raises(PromptAutomationTransportError, match="destination schema"):
        replace(_destination(), schema_version="unknown")
    with pytest.raises(PromptAutomationTransportError, match="dispatch schema"):
        PromptAutomationDispatchRequest(
            destination_key="automation.n8n",
            envelope=_envelope(),
            schema_version="unknown",
        )
    with pytest.raises(PromptAutomationTransportError, match="acknowledgement schema"):
        PromptAutomationAcknowledgement(
            dispatch_id="dispatch-1",
            payload_id="payload-1",
            receipt_id="receipt-1",
            accepted=True,
            schema_version="unknown",
        )


def test_payload_contains_only_non_content_lineage_and_is_deterministic() -> None:
    transport = _AcceptingTransport()
    dispatcher = _dispatcher(transport)
    request = PromptAutomationDispatchRequest(
        destination_key="automation.n8n",
        envelope=_envelope(),
    )

    first = dispatcher.build_payload(request)
    second = dispatcher.build_payload(request)
    assert first == second
    assert first.dispatch_id == second.dispatch_id
    assert first.payload_id == second.payload_id

    payload_keys = set(first.as_mapping())
    forbidden_fragments = (
        "task_text",
        "prompt",
        "evidence",
        "source_bytes",
        "trusted_system",
        "webhook",
        "secret",
        "credential",
        "token",
        "retention",
        "provider",
    )
    assert all(fragment not in key for key in payload_keys for fragment in forbidden_fragments)
    assert set(payload_keys) == {field.name for field in fields(PromptAutomationPayload)}


def test_retry_delivers_identical_immutable_payload_and_replay_identity() -> None:
    transport = _AcceptingTransport()
    dispatcher = _dispatcher(transport)
    request = PromptAutomationDispatchRequest(
        destination_key="automation.n8n",
        envelope=_envelope(),
    )

    first = dispatcher.dispatch(request)
    second = dispatcher.dispatch(request)
    assert first.payload == second.payload
    assert first.payload.dispatch_id == second.payload.dispatch_id
    assert first.payload.payload_id == second.payload.payload_id
    assert dict(transport.payloads[0]) == dict(transport.payloads[1])

    with pytest.raises(TypeError):
        transport.payloads[0]["build_record_id"] = "mutated"  # type: ignore[index]


def test_dispatcher_rejects_forged_or_negative_acknowledgements() -> None:
    class _ForgedDispatchTransport:
        def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
            canonical = PromptAutomationPayload(**dict(payload))
            return PromptAutomationAcknowledgement(
                dispatch_id="forged-dispatch",
                payload_id=canonical.payload_id,
                receipt_id="receipt-1",
                accepted=True,
            )

    request = PromptAutomationDispatchRequest(
        destination_key="automation.n8n",
        envelope=_envelope(),
    )
    with pytest.raises(PromptAutomationTransportError, match="dispatch identity mismatch"):
        _dispatcher(_ForgedDispatchTransport()).dispatch(request)

    class _ForgedPayloadTransport:
        def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
            return PromptAutomationAcknowledgement(
                dispatch_id=payload["dispatch_id"],
                payload_id="forged-payload",
                receipt_id="receipt-1",
                accepted=True,
            )

    with pytest.raises(PromptAutomationTransportError, match="payload identity mismatch"):
        _dispatcher(_ForgedPayloadTransport()).dispatch(request)

    class _RejectedTransport:
        def deliver(self, payload: Mapping[str, str]) -> PromptAutomationAcknowledgement:
            canonical = PromptAutomationPayload(**dict(payload))
            return PromptAutomationAcknowledgement(
                dispatch_id=payload["dispatch_id"],
                payload_id=canonical.payload_id,
                receipt_id="receipt-1",
                accepted=False,
                schema_version=PROMPT_AUTOMATION_ACK_SCHEMA_VERSION,
            )

    with pytest.raises(PromptAutomationTransportError, match="rejected"):
        _dispatcher(_RejectedTransport()).dispatch(request)

    class _MalformedTransport:
        def deliver(self, payload: Mapping[str, str]) -> object:
            return {"dispatch_id": payload["dispatch_id"]}

    with pytest.raises(PromptAutomationTransportError, match="non-canonical acknowledgement"):
        _dispatcher(_MalformedTransport()).dispatch(request)


def test_changed_destination_or_envelope_changes_dispatch_identity() -> None:
    envelope = _envelope()
    first_dispatcher = _dispatcher(_AcceptingTransport())
    first = first_dispatcher.build_payload(
        PromptAutomationDispatchRequest(destination_key="automation.n8n", envelope=envelope)
    )

    changed_envelope = replace(envelope, build_record_id="build-2")
    changed = first_dispatcher.build_payload(
        PromptAutomationDispatchRequest(destination_key="automation.n8n", envelope=changed_envelope)
    )
    assert changed.dispatch_id != first.dispatch_id

    destinations = PromptAutomationDestinationRegistry(
        (_destination(destination_id="other-n8n", destination_key="automation.other"),)
    )
    other_dispatcher = PromptAutomationDispatcher(
        destinations=destinations,
        transport=_AcceptingTransport(),
    )
    other = other_dispatcher.build_payload(
        PromptAutomationDispatchRequest(destination_key="automation.other", envelope=envelope)
    )
    assert other.dispatch_id != first.dispatch_id
