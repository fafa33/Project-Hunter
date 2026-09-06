from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

#: The inner authorization payload named by accepted ADR 0036 s7. Its shape and
#: its identity derivation are unchanged: this contribution wraps it, and never
#: redefines it.
SCHEMA_VERSION = "hunter-issue-agent-authorization-v1"

#: The transport that carries that exact payload plus issuer authentication.
#: Authentication is added as a separate outer schema precisely so the canonical
#: inner payload keeps the meaning the accepted ADR gave it.
ENVELOPE_SCHEMA_VERSION = "hunter-issue-agent-signed-authorization-v1"

DEFAULT_LABEL = "hunter-agent-execute"
MAX_EVENT_BYTES = 256 * 1024
SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY"
SIGNING_KEY_BYTES = 32
SIGNATURE_BYTES = 64

#: Domain separator mixed into the signed message. Without it a signature over
#: these canonical bytes could be replayed as a signature over any other
#: structure that happens to canonicalize identically.
SIGNATURE_DOMAIN = b"hunter-issue-agent-signed-authorization-v1:"


class IssueAgentTriggerError(RuntimeError):
    pass


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
    issuer_signature: str
    schema_version: str = ENVELOPE_SCHEMA_VERSION

    def to_json(self) -> str:
        return _canonical_json(asdict(self))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def authorization_signing_message(payload: dict[str, Any]) -> bytes:
    """Return the exact bytes the issuer signature covers."""
    return SIGNATURE_DOMAIN + _canonical_json(payload).encode("utf-8")


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
    return SignedIssueAgentAuthorization(
        authorization=payload,
        issuer_signature=signing_key.sign(authorization_signing_message(payload)).hex(),
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


def _post_authorization(url: str, document: str, *, timeout: float = 30.0) -> None:
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
                raise IssueAgentTriggerError("issue-agent webhook rejected the authorization")
    except (urllib.error.URLError, TimeoutError):
        raise IssueAgentTriggerError("issue-agent webhook dispatch failed") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_trigger")
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--owner-login", required=True)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--webhook-url", default=os.environ.get("HUNTER_ISSUE_AGENT_WEBHOOK_URL"))
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
            _post_authorization(webhook_url, document)
        print(document)
        return 0
    except (IssueAgentTriggerError, OSError) as error:
        print(f"issue-agent trigger rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
