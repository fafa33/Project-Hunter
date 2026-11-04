"""Adversarial checks for the model's restricted CONNECT gateway."""

import socket
from unittest.mock import patch

import pytest

from scripts.issue_agent.model_egress_gateway import permitted_target, public_addresses


@pytest.mark.parametrize(
    "payload",
    [
        b"CONNECT ai-gateway.kilo.ai:443 HTTP/1.1\r\nHost: ai-gateway.kilo.ai\r\n\r\n",
        b"CONNECT models.dev:443 HTTP/1.1\r\n\r\n",
    ],
)
def test_only_explicit_provider_targets_allowed(payload: bytes) -> None:
    assert permitted_target(payload) is not None


@pytest.mark.parametrize(
    "payload",
    [
        b"GET http://ai-gateway.kilo.ai/ HTTP/1.1\r\n\r\n",
        b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
        b"CONNECT ai-gateway.kilo.ai.evil.org:443 HTTP/1.1\r\n\r\n",
        b"CONNECT ai-gateway.kilo.ai:80 HTTP/1.1\r\n\r\n",
        b"CONNECT API.KILO.AI:443 HTTP/1.1\r\n\r\n",
        b"CONNECT ai-gateway.kilo.ai:443 HTTP/1.0\r\n\r\n",
        b"CONNECT ai-gateway.kilo.ai:443 HTTP/1.1\r\n" + b"X" * 4096 + b"\r\n\r\n",
    ],
)
def test_unapproved_destinations_fail_closed(payload: bytes) -> None:
    assert permitted_target(payload) is None


def test_dns_rebinding_to_private_address_denied() -> None:
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(PermissionError, match="non-public"):
            public_addresses("ai-gateway.kilo.ai")
