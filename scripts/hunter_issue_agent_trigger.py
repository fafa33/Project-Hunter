from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.task_scope import TaskScopeContract

#: The inner authorization payload named by accepted ADR 0036 s7. Its shape and
#: its identity derivation are unchanged: this contribution wraps it, and never
#: redefines it.
SCHEMA_VERSION = "hunter-issue-agent-authorization-v1"

#: The transport that carries that exact payload plus issuer authentication.
#: Authentication is added as a separate outer schema precisely so the canonical
#: inner payload keeps the meaning the accepted ADR gave it.
ENVELOPE_SCHEMA_VERSION = "hunter-issue-agent-signed-authorization-v2"

#: The repository-owned trusted provisioning boundary endpoint (Issue #497).
#: Every dispatch provisions the canonical per-Issue Source Handling authority
#: here first and only then POSTs the identical signed authorization to the
#: read-only execution issuer edge.
PROVISIONING_URL_ENV = "HUNTER_ISSUE_AGENT_PROVISIONING_URL"

DEFAULT_LABEL = "hunter-agent-execute"
MAX_EVENT_BYTES = 256 * 1024

WEBHOOK_TIMEOUT_ENV = "HUNTER_ISSUE_AGENT_WEBHOOK_TIMEOUT_SECONDS"
DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 900.0
MAX_WEBHOOK_TIMEOUT_SECONDS = 1200.0
SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY"
SIGNING_KEY_BYTES = 32
SIGNATURE_BYTES = 64

#: Issue #497 transport hardening. Only genuinely transient failures are ever
#: retried: gateway 502/503/504 and network-level timeouts/refusals/resets.
#: Semantic rejections (400/401/403/409/422 and every other status) and
#: deterministic server failures (500) are never retried; an exhausted budget
#: fails the trigger closed instead of guessing at acceptance.
TRANSIENT_HTTP_STATUS_CODES = frozenset({502, 503, 504})

DEFAULT_TRANSPORT_ATTEMPTS = 4
DEFAULT_TRANSPORT_BASE_DELAY_SECONDS = 2.0
DEFAULT_TRANSPORT_MAX_DELAY_SECONDS = 10.0

#: Domain separator mixed into the signed message. Without it a signature over
#: these canonical bytes could be replayed as a signature over any other
#: structure that happens to canonicalize identically.
SIGNATURE_DOMAIN = b"hunter-issue-agent-signed-authorization-v2:"


class IssueAgentTriggerError(RuntimeError):
    pass


class _TransientDispatchError(IssueAgentTriggerError):
    """A genuinely transient webhook transport failure (Issue #497).

    Retrying this is safe only because the exact signed authorization document
    is re-POSTed byte for byte: the same ``authorization_id``, payer, Issue
    identity, owner, ``issue_updated_at`` binding and signature. Nothing is
    re-signed and nothing that marks acceptance is fabricated.
    """


class _RejectedDispatchError(IssueAgentTriggerError):
    """A semantic webhook rejection: never retried, always fail closed."""


class _RetryBudgetExhaustedError(IssueAgentTriggerError):
    """Every transient attempt was spent; the authorization was not accepted."""


@dataclass(frozen=True, slots=True)
class IssueAgentAuthorization:
    repository: str
    issue_number: int
    issue_url: str
    issue_title: str
    issue_body: str
    authorized_by: str
    authorization_label: str
    issue_updated_at: str
    authorization_id: str
    schema_version: str = SCHEMA_VERSION

    def payload(self) -> dict[str, Any]:
        """The exact canonical v1 payload, unchanged by this contribution."""
        return asdict(self)

    def to_json(self) -> str:
        return _canonical_json(self.payload())


@dataclass(frozen=True, slots=True)
class SignedIssueAgentAuthorization:
    """One canonical v1 payload plus the issuer proof that it was minted here.

    The payload is carried verbatim, so what accepted ADR 0036 s7 names is
    exactly what travels and exactly what the runtime resolves. The signature
    covers that whole payload -- including its `authorization_id` and its
    `schema_version` -- so no field of it can be altered in transit.
    """

    authorization: dict[str, Any]
    implementation_scope: dict[str, Any]
    issuer_signature: str
    schema_version: str = ENVELOPE_SCHEMA_VERSION

    def to_json(self) -> str:
        return _canonical_json(asdict(self))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def authorization_signing_message(payload: dict[str, Any], scope: dict[str, Any]) -> bytes:
    """Return exact authenticated bytes for authorization plus governed scope."""
    return SIGNATURE_DOMAIN + _canonical_json({"authorization": payload, "implementation_scope": scope}).encode("utf-8")


def _reject_scope_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise IssueAgentTriggerError("Issue task scope block contains duplicate JSON keys")
        values[key] = value
    return values


def implementation_scope_from_issue_body(body: str, *, task_id: str) -> TaskScopeContract:
    prefix, suffix = "<!-- hunter-task-scope-v1\n", "\n-->"
    starts = [i for i in range(len(body)) if body.startswith(prefix, i)]
    if len(starts) != 1:
        raise IssueAgentTriggerError("Issue must contain exactly one hunter-task-scope-v1 block")
    start = starts[0] + len(prefix)
    end = body.find(suffix, start)
    if end < 0:
        raise IssueAgentTriggerError("Issue task scope block is malformed")
    try:
        raw = json.loads(body[start:end], object_pairs_hook=_reject_scope_duplicate_keys)
    except ValueError:
        raise IssueAgentTriggerError("Issue task scope block must be valid JSON") from None
    if not isinstance(raw, dict) or "task_id" in raw:
        raise IssueAgentTriggerError("Issue task scope must be an object and cannot choose task_id")
    try:
        scope = TaskScopeContract.from_dict({**raw, "task_id": task_id})
    except ValueError as error:
        raise IssueAgentTriggerError(str(error)) from None
    incomplete = scope.incompleteness()
    if incomplete:
        raise IssueAgentTriggerError(incomplete)
    if len(scope.base_sha) != 40 or any(c not in "0123456789abcdef" for c in scope.base_sha):
        raise IssueAgentTriggerError("scope base_sha must be an exact lowercase commit SHA")
    return scope


def load_signing_key(value: object) -> Ed25519PrivateKey:
    """Load the issuer-only Ed25519 private key without echoing secret material."""
    if not isinstance(value, str) or not value.strip():
        raise IssueAgentTriggerError(f"{SIGNING_KEY_ENV} must provide the issuer signing key")
    try:
        key = bytes.fromhex(value.strip())
    except ValueError:
        raise IssueAgentTriggerError(f"{SIGNING_KEY_ENV} must be a hex-encoded byte string") from None
    if len(key) != SIGNING_KEY_BYTES:
        raise IssueAgentTriggerError(f"{SIGNING_KEY_ENV} must decode to exactly {SIGNING_KEY_BYTES} bytes")
    return Ed25519PrivateKey.from_private_bytes(key)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IssueAgentTriggerError(f"{name} must be a non-empty string")
    return value.strip()


def _load_event(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_EVENT_BYTES:
        raise IssueAgentTriggerError("GitHub event payload is too large")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise IssueAgentTriggerError("GitHub event payload must be valid UTF-8 JSON") from None
    if not isinstance(decoded, dict):
        raise IssueAgentTriggerError("GitHub event payload must be a JSON object")
    return decoded


def authorize_event(
    event: dict[str, Any],
    *,
    expected_repository: str,
    owner_login: str,
    authorization_label: str = DEFAULT_LABEL,
) -> IssueAgentAuthorization:
    """Authorize one exact event into the canonical v1 payload.

    Unchanged from PR #391: this decides *whether* the owner authorized the
    Issue and produces the payload accepted ADR 0036 s7 names. It deliberately
    carries no proof of origin -- the digest is over public Issue fields, so
    anyone can recompute it -- which is why the payload alone is no longer
    executable and `sign_authorization` exists.
    """
    if event.get("action") != "labeled":
        raise IssueAgentTriggerError("only the issues:labeled event is authorized")

    repository = event.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != expected_repository:
        raise IssueAgentTriggerError("event repository does not match the configured repository")

    sender = event.get("sender")
    if not isinstance(sender, dict) or sender.get("login") != owner_login:
        raise IssueAgentTriggerError("only the configured repository owner may authorize execution")

    label = event.get("label")
    if not isinstance(label, dict) or label.get("name") != authorization_label:
        raise IssueAgentTriggerError("event label is not the governed execution label")

    issue = event.get("issue")
    if not isinstance(issue, dict) or "pull_request" in issue:
        raise IssueAgentTriggerError("authorization requires a GitHub Issue, not a pull request")
    if issue.get("state") != "open":
        raise IssueAgentTriggerError("only open Issues may be authorized")

    number = issue.get("number")
    if type(number) is not int or number <= 0:
        raise IssueAgentTriggerError("issue number must be a positive integer")

    issue_url = _required_text("issue html_url", issue.get("html_url"))
    issue_title = _required_text("issue title", issue.get("title"))
    body = issue.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise IssueAgentTriggerError("issue body must be text")
    updated_at = _required_text("issue updated_at", issue.get("updated_at"))

    canonical_claims = {
        "repository": expected_repository,
        "issue_number": number,
        "issue_url": issue_url,
        "issue_title": issue_title,
        "issue_body": body,
        "authorized_by": owner_login,
        "authorization_label": authorization_label,
        "issue_updated_at": updated_at,
        "schema_version": SCHEMA_VERSION,
    }
    canonical = _canonical_json(canonical_claims).encode("utf-8")
    authorization_id = f"hunter-issue-agent-authorization:{hashlib.sha256(canonical).hexdigest()}"
    return IssueAgentAuthorization(**canonical_claims, authorization_id=authorization_id)


def sign_authorization(
    authorization: IssueAgentAuthorization,
    *,
    signing_key: Ed25519PrivateKey,
) -> SignedIssueAgentAuthorization:
    """Wrap one canonical v1 payload in the issuer-authenticated transport."""
    if not isinstance(authorization, IssueAgentAuthorization):
        raise IssueAgentTriggerError("only a canonical authorization payload may be signed")
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise IssueAgentTriggerError("issuer signing authority is required to authorize execution")
    payload = authorization.payload()
    scope = implementation_scope_from_issue_body(authorization.issue_body, task_id=authorization.authorization_id)
    scope_payload = asdict(scope)
    return SignedIssueAgentAuthorization(
        authorization=payload,
        implementation_scope=scope_payload,
        issuer_signature=signing_key.sign(authorization_signing_message(payload, scope_payload)).hex(),
    )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Fail closed on any redirect rather than following it.

    urllib's default handler follows 301/302 and rewrites the POST to a GET, so a
    redirected dispatch could report success from a final 2xx while the
    authorization document was never delivered to the authorized endpoint.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise IssueAgentTriggerError(f"issue-agent webhook redirect ({code}) is not an authorized dispatch path")


_OPENER = urllib.request.build_opener(_RejectRedirects)

#: Module-level sleep seam so tests can drive the bounded backoff deterministically.
_sleep = time.sleep


def _webhook_timeout(value: object) -> float:
    if value is None:
        return DEFAULT_WEBHOOK_TIMEOUT_SECONDS
    if not isinstance(value, str) or not value.strip():
        raise IssueAgentTriggerError(f"{WEBHOOK_TIMEOUT_ENV} must be a non-empty positive finite number")
    try:
        timeout = float(value.strip())
    except ValueError:
        raise IssueAgentTriggerError(f"{WEBHOOK_TIMEOUT_ENV} must be a positive finite number") from None
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_WEBHOOK_TIMEOUT_SECONDS:
        raise IssueAgentTriggerError(
            f"{WEBHOOK_TIMEOUT_ENV} must be > 0 and <= {MAX_WEBHOOK_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _post_authorization(
    url: str,
    document: str,
    *,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
) -> None:
    """POST one signed authorization document and enforce the response.

    Non-2xx answers are classified, not collapsed: 502/503/504 raise
    ``_TransientDispatchError`` (safe to retry byte-identically), every other
    rejection raises ``_RejectedDispatchError`` (never retried, fail closed).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise IssueAgentTriggerError("issue-agent webhook URL must be a credential-free HTTPS URL")
    request = urllib.request.Request(
        url,
        data=document.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                if response.status in TRANSIENT_HTTP_STATUS_CODES:
                    raise _TransientDispatchError(f"issue-agent webhook answered transient HTTP {response.status}")
                raise _RejectedDispatchError(
                    f"issue-agent webhook rejected the authorization with HTTP {response.status}"
                )
    except urllib.error.HTTPError as error:
        if error.code in TRANSIENT_HTTP_STATUS_CODES:
            raise _TransientDispatchError(f"issue-agent webhook answered transient HTTP {error.code}") from None
        detail = ""
        try:
            raw = error.read(MAX_EVENT_BYTES)
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                detail = payload["error"].strip()
        except (OSError, UnicodeDecodeError, ValueError):
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise _RejectedDispatchError(
            f"issue-agent webhook rejected the authorization with HTTP {error.code}{suffix}"
        ) from None
    except urllib.error.URLError:
        raise _TransientDispatchError("issue-agent webhook dispatch failed (network error)") from None
    except (TimeoutError, OSError):
        raise _TransientDispatchError("issue-agent webhook dispatch failed (timeout or connection failure)") from None


def _retry_delay_seconds(
    attempt: int,
    *,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    """Deterministic exponential backoff for the bounded retry budget."""
    return min(base_delay_seconds * (2 ** max(attempt - 1, 0)), max_delay_seconds)


def _post_with_transport_retry(
    url: str,
    document: str,
    *,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_TRANSPORT_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_TRANSPORT_BASE_DELAY_SECONDS,
    max_delay_seconds: float = DEFAULT_TRANSPORT_MAX_DELAY_SECONDS,
) -> None:
    """POST the one byte-identical document, retrying only transient failures.

    Issue #497. ``document`` is produced once and re-POSTed unchanged on every
    attempt, so a transport recovery can never invent a second authorization or
    a duplicate execution. Semantic rejections abort immediately; only 502/503/
    504 and network/timeout failures consume the bounded deterministic budget,
    and exhaustion raises ``_RetryBudgetExhaustedError``.
    """
    if attempts < 1:
        raise IssueAgentTriggerError("transport retry attempts must be a positive integer")
    for attempt in range(1, attempts + 1):
        try:
            _post_authorization(url, document, timeout=timeout)
            return
        except _TransientDispatchError as error:
            if attempt >= attempts:
                raise _RetryBudgetExhaustedError(
                    f"issue-agent webhook remained unavailable after {attempts} attempts: {error}"
                ) from None
            delay = _retry_delay_seconds(
                attempt,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
            print(
                f"... issue-agent webhook unavailable; retrying in {delay:g}s " f"(attempt {attempt}/{attempts})",
                file=sys.stderr,
            )
            _sleep(delay)
        except IssueAgentTriggerError:
            raise


def _provision_and_dispatch(
    provisioning_url: str,
    webhook_url: str,
    document: str,
    *,
    timeout: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
) -> None:
    """Provision the per-Issue Source Handling authority, then dispatch.

    Issue #497. Issuer dispatch must never occur unless trusted provisioning
    succeeded, so ``_post_with_transport_retry`` on the provisioning edge runs
    first and raises before the webhook is ever contacted if it fails closed.
    """
    _post_with_transport_retry(provisioning_url, document, timeout=timeout)
    _post_with_transport_retry(webhook_url, document, timeout=timeout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_trigger")
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner-login", required=True)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--webhook-url", default=os.environ.get("HUNTER_ISSUE_AGENT_WEBHOOK_URL"))
    parser.add_argument("--provisioning-url", default=os.environ.get(PROVISIONING_URL_ENV))
    parser.add_argument("--webhook-timeout", default=os.environ.get(WEBHOOK_TIMEOUT_ENV))
    parser.add_argument("--authorization-out")
    parser.add_argument("--no-dispatch", action="store_true")
    return parser


def _issuer_signing_key() -> Ed25519PrivateKey:
    """Read the issuer key from machine-only configuration; never from argv.

    Keeping it off the command line means it cannot land in a process listing,
    a shell history, or a workflow log line, and there is no flag an operator
    can accidentally use to pass it in the clear.
    """
    return load_signing_key(os.environ.get(SIGNING_KEY_ENV))


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        authorization = authorize_event(
            _load_event(Path(arguments.event)),
            expected_repository=arguments.repository,
            owner_login=arguments.owner_login,
            authorization_label=arguments.label,
        )
        document = sign_authorization(authorization, signing_key=_issuer_signing_key()).to_json()
        if arguments.authorization_out:
            Path(arguments.authorization_out).write_text(document + "\n", encoding="utf-8")
        if not arguments.no_dispatch:
            webhook_url = _required_text("HUNTER_ISSUE_AGENT_WEBHOOK_URL", arguments.webhook_url)
            timeout = _webhook_timeout(arguments.webhook_timeout)
            provisioning_url = _required_text(PROVISIONING_URL_ENV, arguments.provisioning_url)
            _provision_and_dispatch(provisioning_url, webhook_url, document, timeout=timeout)
        print(document)
        return 0
    except (IssueAgentTriggerError, OSError) as error:
        print(f"issue-agent trigger rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
