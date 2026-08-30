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

SCHEMA_VERSION = "hunter-issue-agent-authorization-v1"
DEFAULT_LABEL = "hunter-agent-execute"
MAX_EVENT_BYTES = 256 * 1024


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

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    canonical = json.dumps(canonical_claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    authorization_id = f"hunter-issue-agent-authorization:{hashlib.sha256(canonical).hexdigest()}"
    return IssueAgentAuthorization(**canonical_claims, authorization_id=authorization_id)


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


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        authorization = authorize_event(
            _load_event(Path(arguments.event)),
            expected_repository=arguments.repository,
            owner_login=arguments.owner_login,
            authorization_label=arguments.label,
        )
        document = authorization.to_json()
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
