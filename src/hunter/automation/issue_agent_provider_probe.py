"""Trusted, model-free provider probe for the Issue #390 fallback decision (ADR 0041 F2).

The primary executor's stderr classification is only a hint: it is output of a model-influenced process. The probe
is the independent observation. It runs in the executor's trusted step after every isolated process has stopped,
sends one bounded authenticated ``GET`` with no prompt, repository content or model output, follows no redirect,
reads no body, and reports only a closed category. A healthy provider is ``UNCLASSIFIED``: there is then nothing
to fall back from, so a forged or stale "rate limit" hint never earns a fallback.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Final

#: Kilo's documented model listing (https://kilo.ai/docs/gateway/models-and-providers). Whether a listing call
#: reflects per-key limits is unverified; any such gap fails safe (a healthy answer never admits a fallback).
KILO_PROBE_URL: Final = "https://api.kilo.ai/api/gateway/models"
PROBE_TIMEOUT_SECONDS: Final = 10.0
#: ADR 0041 F8: the pinned provider host the ``kilo-canary`` gateway profile removes (``model_egress_gateway.py``).
KILO_PROVIDER_HOST: Final = "ai-gateway.kilo.ai"
#: The canary probe targets the removed host itself: a refusal of any other host would prove nothing about the canary.
KILO_CANARY_PROBE_URL: Final = f"https://{KILO_PROVIDER_HOST}/"
_MAX_STATUS_LINE: Final = 4096
_STATUS_CATEGORIES: Final = {401: "AUTHENTICATION", 402: "QUOTA_EXCEEDED", 403: "PERMISSION", 429: "RATE_LIMIT"}

Opener = Callable[[urllib.request.Request, float], Any]


def classify_probe(status: int | None) -> str:
    """Map an HTTP status, or ``None`` for a transport failure, into the closed category vocabulary."""

    if status is None:
        return "NETWORK"
    if status in _STATUS_CATEGORIES:
        return _STATUS_CATEGORIES[status]
    if 500 <= status <= 599:
        return "PROVIDER_UNAVAILABLE"
    return "UNCLASSIFIED"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is answered as its own status; the key never follows it anywhere


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)  # noqa: S310 - pinned https


def probe_provider(url: str, *, api_key: str, opener: Opener | None = None) -> str:
    """One authenticated, content-free request. Never raises, never logs, never returns provider text."""

    if not url.startswith("https://") or not api_key or any(ch in api_key for ch in "\r\n"):
        return "UNCLASSIFIED"
    request = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with (opener or _default_opener)(request, PROBE_TIMEOUT_SECONDS) as response:
            return classify_probe(int(response.status))
    except urllib.error.HTTPError as error:
        return classify_probe(int(error.code))
    except (urllib.error.URLError, TimeoutError, OSError):
        return classify_probe(None)
    except Exception:  # noqa: BLE001 - a probe defect is an honest "unknown", never a fallback cause
        return "UNCLASSIFIED"


def _status_line(stream: socket.socket) -> int | None:
    received = b""
    while b"\r\n" not in received and len(received) < _MAX_STATUS_LINE:
        part = stream.recv(_MAX_STATUS_LINE - len(received))
        if not part:
            break
        received += part
    words = received.split(b"\r\n", 1)[0].split(b" ")
    if len(words) < 2 or words[0] not in (b"HTTP/1.0", b"HTTP/1.1") or not words[1].isdigit():
        return None
    return int(words[1])


def probe_through_gateway(
    url: str, *, gateway: tuple[str, int], connect: Callable[..., socket.socket] = socket.create_connection
) -> str:
    """ADR 0041 F8: the same content-free ``GET``, sent through the primary run's own egress gateway.

    The request carries no credential: the canary refuses the tunnel before any TLS session, so a key could only
    ever be exposed, never needed. A refused ``CONNECT`` is the transport failure the primary saw: ``NETWORK``.
    Never raises, never returns provider text.
    """

    if not url.startswith("https://"):
        return "UNCLASSIFIED"
    rest = url[len("https://") :]
    host, _, path = rest.partition("/")
    try:
        with connect(gateway, timeout=PROBE_TIMEOUT_SECONDS) as tunnel:
            tunnel.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"))
            if _status_line(tunnel) != 200:
                return classify_probe(None)
            with ssl.create_default_context().wrap_socket(tunnel, server_hostname=host) as session:
                session.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
                status = _status_line(session)
                return "UNCLASSIFIED" if status is None else classify_probe(status)
    except (OSError, TimeoutError, UnicodeError):
        return classify_probe(None)
    except Exception:  # noqa: BLE001 - a probe defect is an honest "unknown", never a fallback cause
        return "UNCLASSIFIED"
