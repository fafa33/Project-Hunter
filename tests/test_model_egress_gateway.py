"""Adversarial checks for the model's restricted CONNECT gateway."""

import importlib.util
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

# Load this standalone container entrypoint without giving it two mypy module names.
_gateway_path = Path(__file__).resolve().parents[1] / "scripts/issue_agent/model_egress_gateway.py"
_spec = importlib.util.spec_from_file_location("hunter_gateway_test_entrypoint", _gateway_path)
assert _spec is not None and _spec.loader is not None
_gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gateway)
permitted_target = _gateway.permitted_target
public_addresses = _gateway.public_addresses


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


# --- Issue #577: Kilo's offline guard must be able to see the network -----------------------------------------

#: Kilo 7.8.18 ``SessionNetwork`` probe URLs (packages/opencode/src/session/network.ts at v7.8.18). After a model
#: stream is silent for 10 s, the guard dials the provider over raw TCP (never possible from the internal network)
#: and then sends ``HEAD`` to these through the proxy; if every one fails it aborts the healthy stream.
KILO_OFFLINE_PROBE_URLS = ("https://kilo.ai", "https://example.com", "https://cloudflare.com/cdn-cgi/trace")


def _connect(host: str, port: int = 443) -> bytes:
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()


def _probe_hosts() -> list[str]:
    return [url.removeprefix("https://").split("/", 1)[0] for url in KILO_OFFLINE_PROBE_URLS]


def test_the_kilo_profile_admits_a_kilo_offline_probe_so_a_silent_stream_is_not_aborted() -> None:
    # Hosted runs 42486218343/42487085826: every probe host was refused, so Kilo ended with
    # APIError "Network connection failed" although the provider stream was healthy.
    kilo_hosts = _gateway.PROFILES["kilo"][0]
    admitted = [host for host in _probe_hosts() if permitted_target(_connect(host), kilo_hosts) is not None]
    assert admitted == ["kilo.ai"]
    assert _gateway.KILO_LIVENESS_HOST == "kilo.ai"


@pytest.mark.parametrize(
    "host,port",
    [
        ("example.com", 443),
        ("cloudflare.com", 443),
        ("www.kilo.ai", 443),
        ("api.kilo.ai", 443),
        ("kilo.ai.evil.org", 443),
        ("evilkilo.ai", 443),
        ("KILO.AI", 443),
        ("kilo.ai", 80),
    ],
)
def test_only_the_vendor_apex_is_admitted_for_the_probe(host: str, port: int) -> None:
    assert permitted_target(_connect(host, port), _gateway.PROFILES["kilo"][0]) is None


def test_no_other_profile_gains_the_liveness_host() -> None:
    assert _gateway.KILO_LIVENESS_HOST not in _gateway.ALLOWED_HOSTS
    assert _gateway.KILO_LIVENESS_HOST not in _gateway.PROFILES["kilo-canary"][0]
    assert _gateway.KILO_LIVENESS_HOST not in _gateway.PROFILES["codex"][0]
    assert _gateway.PROFILES["kilo"][0] == _gateway.ALLOWED_HOSTS | {"kilo.ai"}
