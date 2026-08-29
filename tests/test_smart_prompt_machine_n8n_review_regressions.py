"""Regression tests for n8n review findings that could expose untrusted text."""

from __future__ import annotations

import pytest

from hunter.automation import n8n as n8n_module
from hunter.automation.n8n import N8nPromptAutomationTransport
from hunter.evidence_intelligence.model_adapter_transport import TransportCredential
from hunter.evidence_intelligence.smart_prompt_transport import PromptAutomationTransportError


def test_malformed_endpoint_suppresses_sensitive_parser_context() -> None:
    """Malformed endpoint material must not survive in chained parser errors."""
    endpoint = "https://n8n.example.test:webhook-secret/webhook/hunter"

    with pytest.raises(PromptAutomationTransportError, match="endpoint is malformed") as raised:
        N8nPromptAutomationTransport(endpoint, TransportCredential("runtime-secret"))

    assert "webhook-secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_acknowledgement_schema_error_does_not_echo_remote_keys() -> None:
    """Attacker-controlled acknowledgement keys must never be copied into errors."""
    acknowledgement = {
        "dispatch_id": "dispatch-1",
        "payload_id": "payload-1",
        "receipt_id": "receipt-1",
        "accepted": True,
        "schema_version": "smart-prompt-automation-ack-v1",
        "webhook-secret": "reflected",
    }

    with pytest.raises(PromptAutomationTransportError, match="schema mismatch") as raised:
        n8n_module._canonical_acknowledgement(acknowledgement)

    assert "webhook-secret" not in str(raised.value)


def test_payload_schema_error_does_not_echo_untrusted_keys() -> None:
    """Direct transport misuse cannot reflect arbitrary payload keys into errors."""
    payload = {"webhook-secret": "reflected"}

    with pytest.raises(PromptAutomationTransportError, match="payload schema mismatch") as raised:
        n8n_module._canonical_payload(payload)

    assert "webhook-secret" not in str(raised.value)
