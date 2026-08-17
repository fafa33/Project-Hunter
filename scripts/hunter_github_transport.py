"""Shared bounded-retry GitHub API transport for the Hunter governance surface.

Every GitHub request made by the Hunter governance controllers
(``hunter_merge_readiness``, ``hunter_draft_promotion_signal``,
``hunter_governance_review``, ``hunter_governance_preflight``) crosses one
boundary defined here so that transient or transport-layer GitHub failures are
classified, retried with bounded deterministic backoff, and -- after
exhaustion -- raised as a typed :class:`GitHubUnavailable` outcome instead of
being misread as a semantic governance result.

Classification invariants:

- ``429/500/502/503/504`` are transient and retryable.
- A ``404`` whose body is the GitHub node-resolution inconsistency
  ("Could not resolve to a node with the global id ...") is an
  infrastructure/data-resolution failure: retryable with the same bounded
  policy, and never treated as "no data", "no reviews", or "no blockers".
- Any other ``404`` (and every other non-transient HTTP status) is permanent
  and is never retried.
- Transport failures (timeouts, connection refusal/reset, DNS) are transient
  and retryable.
- ``Retry-After`` is honored when present, capped at the policy's maximum
  delay so CI runs stay bounded.
- After bounded exhaustion a :class:`GitHubUnavailable` is raised carrying
  the typed unavailable category; callers must fail closed on it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# GitHub's REST API occasionally answers a live repository object with a 404
# whose body explains that an internal node reference cannot be resolved
# ("Could not resolve to a node with the global id of 'PR_...'"). That is an
# infrastructure/data-resolution inconsistency, not a genuine "not found".
# The same marker can appear inside GraphQL error payloads.
NODE_RESOLUTION_404_PATTERNS = (re.compile(r"could not resolve to a node with the global id", re.IGNORECASE),)

DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 8.0

_REQUEST_TIMEOUT_SECONDS = 30

_REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
}


class GitHubRequestError(RuntimeError):
    """A classified GitHub request failure.

    ``category`` is one of ``"transient"``, ``"node-resolution"``,
    ``"transport"``, or ``"permanent"``. Only the first three are retryable.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.category in {"transient", "node-resolution", "transport"}


class GitHubUnavailable(GitHubRequestError):
    """Bounded retries were exhausted; the GitHub infrastructure is unavailable.

    This is the typed fail-closed outcome. It never implies a semantic
    approval, readiness, review verdict, or blocker: callers must distinguish
    it from every semantic result.
    """

    def __init__(self, what: str, *, attempts: int, last: GitHubRequestError) -> None:
        super().__init__(
            f"{what} unavailable after {attempts} attempts: {last}",
            category="unavailable",
            status_code=last.status_code,
        )
        self.attempts = attempts
        self.last = last


@dataclass(frozen=True)
class RetryPolicy:
    """Deterministic bounded backoff suitable for CI runs."""

    attempts: int = DEFAULT_RETRY_ATTEMPTS
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS

    def delay_seconds(self, attempt: int) -> float:
        return min(self.base_delay_seconds * (2 ** max(attempt - 1, 0)), self.max_delay_seconds)


# Module-level singleton so argument defaults never perform a function call.
DEFAULT_RETRY_POLICY = RetryPolicy()


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """Parse ``Retry-After`` seconds from response headers, if present."""
    value = (headers or {}).get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form has no seconds semantics for our deterministic
        # backoff; callers fall back to the policy delay.
        return None


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return (exc.read().decode("utf-8", errors="replace") or "").strip()
    except Exception:
        return ""


def classify_http_error(exc: urllib.error.HTTPError) -> GitHubRequestError:
    """Classify a raw HTTPError into a typed, retryable or permanent error."""
    body = _read_http_error_body(exc)
    code = exc.code
    if code in TRANSIENT_STATUS_CODES:
        return GitHubRequestError(
            f"GitHub HTTP {code}: {body[:200] or 'no response body'}",
            category="transient",
            status_code=code,
            retry_after=parse_retry_after(exc.headers),
        )
    if code == 404 and any(pattern.search(body) for pattern in NODE_RESOLUTION_404_PATTERNS):
        return GitHubRequestError(
            f"GitHub node-resolution 404: {body[:200]}",
            category="node-resolution",
            status_code=404,
            retry_after=parse_retry_after(exc.headers),
        )
    return GitHubRequestError(
        f"GitHub HTTP {code}: {body[:200] or 'no response body'}",
        category="permanent",
        status_code=code,
    )


def classify_transport_error(exc: urllib.error.URLError) -> GitHubRequestError:
    """Classify a transport-layer failure (timeout, connection, DNS) as transient."""
    reason = getattr(exc, "reason", None)
    return GitHubRequestError(
        f"GitHub transport error: {reason or exc}",
        category="transport",
    )


def classify_cli_failure(detail: str) -> GitHubRequestError:
    """Classify a failing ``gh``/external CLI invocation from its diagnostics.

    ``gh api`` failures print ``HTTP <code>`` (or ``HTTP/1.1 <code>``) in
    stderr; the node-resolution 404 marker can appear there too. Anything
    else is permanent.
    """
    status_match = re.search(r"\bHTTP(?:/[0-9.]+)?\s+(?P<code>[0-9]{3})\b", detail, re.IGNORECASE)
    code = int(status_match.group("code")) if status_match else None
    if code in TRANSIENT_STATUS_CODES:
        return GitHubRequestError(f"GitHub HTTP {code}: {detail[:200]}", category="transient", status_code=code)
    if code == 404 and any(pattern.search(detail) for pattern in NODE_RESOLUTION_404_PATTERNS):
        return GitHubRequestError(
            f"GitHub node-resolution 404: {detail[:200]}", category="node-resolution", status_code=404
        )
    return GitHubRequestError(f"external command failed: {detail[:200]}", category="permanent", status_code=code)


def execute_with_retry(
    fn: Callable[[], Any],
    *,
    what: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    """Run ``fn`` retrying transient failures with bounded deterministic backoff.

    Raises :class:`GitHubUnavailable` after ``policy.attempts`` attempts when
    every failure was retryable. Permanent failures are raised immediately.
    """
    last_error: GitHubRequestError | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except GitHubRequestError as exc:
            last_error = exc
        except urllib.error.HTTPError as exc:
            last_error = classify_http_error(exc)
        except urllib.error.URLError as exc:
            last_error = classify_transport_error(exc)
        except json.JSONDecodeError as exc:
            last_error = GitHubRequestError(f"GitHub returned malformed JSON: {exc}", category="transport")

        if not last_error.retryable:
            raise last_error
        if attempt < policy.attempts:
            delay = last_error.retry_after
            if delay is None:
                delay = policy.delay_seconds(attempt)
            delay = min(delay, policy.max_delay_seconds)
            print(
                f"... {what} failed ({last_error.category}): {last_error}; "
                f"retrying in {delay:.1f}s (attempt {attempt}/{policy.attempts})"
            )
            sleeper(delay)

    assert last_error is not None
    raise GitHubUnavailable(what, attempts=policy.attempts, last=last_error)


def rest_json(
    *,
    url: str,
    method: str,
    headers: Mapping[str, str],
    data: bytes | None,
    token: str,
) -> Any:
    """Perform one REST request and return the decoded JSON payload."""
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            **_REST_HEADERS,
            "Authorization": f"Bearer {token}",
            **headers,
        },
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        raw_body = response.read().decode("utf-8")
        if not raw_body:
            return None
        return json.loads(raw_body)


def request_rest_json(
    *,
    url: str,
    method: str,
    headers: Mapping[str, str],
    data: bytes | None,
    token: str,
    what: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    """REST request with the shared bounded-retry boundary."""
    return execute_with_retry(
        lambda: rest_json(url=url, method=method, headers=headers, data=data, token=token),
        what=what,
        policy=policy,
        sleeper=sleeper,
    )


def graphql_json(
    *,
    url: str,
    headers: Mapping[str, str],
    query: str,
    variables: dict[str, Any],
    token: str,
) -> Any:
    """Perform one GraphQL request, failing closed on query errors."""
    request = urllib.request.Request(
        url,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        method="POST",
        headers={
            **_REST_HEADERS,
            "Authorization": f"Bearer {token}",
            **headers,
        },
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    errors = payload.get("errors")
    if errors:
        text = str(errors)
        if any(pattern.search(text) for pattern in NODE_RESOLUTION_404_PATTERNS):
            raise GitHubRequestError(f"GraphQL node-resolution error: {text[:200]}", category="node-resolution")
        raise RuntimeError(f"GraphQL query failed: {errors}")
    return payload["data"]


def request_graphql_json(
    *,
    url: str,
    headers: Mapping[str, str],
    query: str,
    variables: dict[str, Any],
    token: str,
    what: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleeper: Callable[[float], None] = time.sleep,
) -> Any:
    """GraphQL request with the shared bounded-retry boundary."""
    return execute_with_retry(
        lambda: graphql_json(url=url, headers=headers, query=query, variables=variables, token=token),
        what=what,
        policy=policy,
        sleeper=sleeper,
    )
