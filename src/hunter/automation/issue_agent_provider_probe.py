"""Trusted, model-free provider probe for the Issue #390 fallback decision (ADR 0041 F2).

The primary executor's stderr classification is only a hint: it is output of a model-influenced process. The probe
is the independent observation. It runs in the executor's trusted step after every isolated process has stopped,
sends one bounded authenticated ``GET`` with no prompt, repository content or model output, follows no redirect,
reads no body, and reports only a closed category. A healthy provider is ``UNCLASSIFIED``: there is then nothing
to fall back from, so a forged or stale "rate limit" hint never earns a fallback.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Final

#: Kilo's documented model listing (https://kilo.ai/docs/gateway/models-and-providers). Whether a listing call
#: reflects per-key limits is unverified; any such gap fails safe (a healthy answer never admits a fallback).
KILO_PROBE_URL: Final = "https://api.kilo.ai/api/gateway/models"
PROBE_TIMEOUT_SECONDS: Final = 10.0
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
