from __future__ import annotations

import copy
import email
import io
import urllib.request
import urllib.response
from typing import Any

import hunter_issue_agent_trigger as trigger
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from hunter_issue_agent_trigger import (
    DEFAULT_LABEL,
    IssueAgentTriggerError,
    authorize_event,
    canonical_claims_message,
    load_signing_key,
)

ISSUER_KEY_HEX = "11" * 32
ISSUER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(ISSUER_KEY_HEX))


def _event() -> dict[str, Any]:
    return {
        "action": "labeled",
        "repository": {"full_name": "fafa33/Project-Hunter"},
        "sender": {"login": "fafa33"},
        "label": {"name": DEFAULT_LABEL},
        "issue": {
            "number": 389,
            "html_url": "https://github.com/fafa33/Project-Hunter/issues/389",
            "title": "Build point-in-time candidate authority",
            "body": "Execute only the governed Issue scope. provider=jules merge=true",
            "state": "open",
            "updated_at": "2026-08-30T13:42:04Z",
        },
    }


def _authorize(event: dict[str, Any], *, signing_key: Any = ISSUER_KEY):
    return authorize_event(
        event,
        expected_repository="fafa33/Project-Hunter",
        owner_login="fafa33",
        signing_key=signing_key,
    )


def test_owner_label_authorization_is_deterministic_and_content_cannot_choose_provider() -> None:
    first = _authorize(_event())
    second = _authorize(copy.deepcopy(_event()))

    assert first.authorization_id == second.authorization_id
    assert first.to_json() == second.to_json()
    assert "provider=jules" in first.issue_body
    assert not hasattr(first, "provider")
    assert not hasattr(first, "merge")


def test_untrusted_actor_cannot_authorize_execution() -> None:
    event = _event()
    event["sender"] = {"login": "attacker"}

    with pytest.raises(IssueAgentTriggerError, match="only the configured repository owner"):
        _authorize(event)


def test_wrong_label_and_non_labeled_events_fail_closed() -> None:
    event = _event()
    event["label"] = {"name": "bug"}
    with pytest.raises(IssueAgentTriggerError, match="governed execution label"):
        _authorize(event)

    event = _event()
    event["action"] = "edited"
    with pytest.raises(IssueAgentTriggerError, match="issues:labeled"):
        _authorize(event)


def test_closed_issue_and_pull_request_payload_fail_closed() -> None:
    event = _event()
    issue = dict(event["issue"])
    issue["state"] = "closed"
    event["issue"] = issue
    with pytest.raises(IssueAgentTriggerError, match="only open Issues"):
        _authorize(event)

    event = _event()
    issue = dict(event["issue"])
    issue["pull_request"] = {"url": "https://api.github.com/repos/fafa33/Project-Hunter/pulls/389"}
    event["issue"] = issue
    with pytest.raises(IssueAgentTriggerError, match="not a pull request"):
        _authorize(event)


def test_mutating_issue_content_changes_authorization_identity() -> None:
    first = _authorize(_event())
    event = _event()
    issue = dict(event["issue"])
    issue["body"] = "changed after authorization"
    issue["updated_at"] = "2026-08-30T13:45:00Z"
    event["issue"] = issue

    second = _authorize(event)

    assert first.authorization_id != second.authorization_id


class _StubResponse(urllib.response.addinfourl):
    def __init__(self, code: int, headers: str, url: str, body: bytes = b"") -> None:
        super().__init__(io.BytesIO(body), email.message_from_string(headers), url, code)
        self.msg = "stub"


class _ScriptedHTTPSHandler(urllib.request.HTTPSHandler):
    """Drives the real opener chain without touching the network."""

    def __init__(self, responses: list) -> None:
        super().__init__()
        self._responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def https_open(self, req: urllib.request.Request):
        self.requests.append(req)
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)()


def _install_opener(monkeypatch, responses: list) -> _ScriptedHTTPSHandler:
    handler = _ScriptedHTTPSHandler(responses)
    monkeypatch.setattr(trigger, "_OPENER", urllib.request.build_opener(trigger._RejectRedirects, handler))
    return handler


def _redirect(code: int):
    return lambda: _StubResponse(code, "Location: https://relay.example/collected\n", "https://hook.example/dispatch")


def _ok():
    return lambda: _StubResponse(200, "\n", "https://hook.example/dispatch")


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_is_rejected_fail_closed(monkeypatch, code: int) -> None:
    _install_opener(monkeypatch, [_redirect(code)])

    with pytest.raises(IssueAgentTriggerError) as error:
        trigger._post_authorization("https://hook.example/dispatch", "{}")

    assert "redirect" in str(error.value)


def test_redirect_followed_by_final_2xx_is_not_success(monkeypatch) -> None:
    handler = _install_opener(monkeypatch, [_redirect(302), _ok()])

    with pytest.raises(IssueAgentTriggerError):
        trigger._post_authorization("https://hook.example/dispatch", "{}")

    # The 2xx behind the redirect was never reached, so it cannot report success.
    assert len(handler.requests) == 1


def test_direct_2xx_post_still_succeeds(monkeypatch) -> None:
    handler = _install_opener(monkeypatch, [_ok()])

    trigger._post_authorization("https://hook.example/dispatch", '{"schema_version":"v1"}')

    assert len(handler.requests) == 1
    assert handler.requests[0].get_method() == "POST"
    assert handler.requests[0].data == b'{"schema_version":"v1"}'


def test_non_2xx_response_is_rejected(monkeypatch) -> None:
    _install_opener(monkeypatch, [lambda: _StubResponse(500, "\n", "https://hook.example/dispatch")])

    with pytest.raises(IssueAgentTriggerError):
        trigger._post_authorization("https://hook.example/dispatch", "{}")


@pytest.mark.parametrize(
    "url",
    [
        "http://hook.example/dispatch",
        "https://user:secret@hook.example/dispatch",
        "https://hook.example/dispatch#fragment",
        "https:///dispatch",
        "not-a-url",
    ],
)
def test_malformed_or_non_https_webhook_remains_fail_closed(monkeypatch, url: str) -> None:
    handler = _install_opener(monkeypatch, [])

    with pytest.raises(IssueAgentTriggerError) as error:
        trigger._post_authorization(url, "{}")

    assert "credential-free HTTPS URL" in str(error.value)
    assert handler.requests == []


# --- Trusted-origin issuer proof --------------------------------------------


def _public_key() -> Ed25519PublicKey:
    return ISSUER_KEY.public_key()


def test_authorization_carries_a_verifiable_issuer_signature() -> None:
    authorization = _authorize(_event())

    assert len(authorization.issuer_signature) == 128
    message = canonical_claims_message(
        {
            "repository": authorization.repository,
            "issue_number": authorization.issue_number,
            "issue_url": authorization.issue_url,
            "issue_title": authorization.issue_title,
            "issue_body": authorization.issue_body,
            "authorized_by": authorization.authorized_by,
            "authorization_label": authorization.authorization_label,
            "issue_updated_at": authorization.issue_updated_at,
            "schema_version": authorization.schema_version,
        }
    )
    # Raises InvalidSignature if the proof does not cover the exact claims.
    _public_key().verify(bytes.fromhex(authorization.issuer_signature), message)


def test_a_different_issuer_key_produces_a_document_the_owner_key_rejects() -> None:
    foreign = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
    forged = _authorize(_event(), signing_key=foreign)

    message = canonical_claims_message(
        {
            "repository": forged.repository,
            "issue_number": forged.issue_number,
            "issue_url": forged.issue_url,
            "issue_title": forged.issue_title,
            "issue_body": forged.issue_body,
            "authorized_by": forged.authorized_by,
            "authorization_label": forged.authorization_label,
            "issue_updated_at": forged.issue_updated_at,
            "schema_version": forged.schema_version,
        }
    )
    # The claims are identical; only the minting key differs, and that is enough.
    assert forged.authorization_id == _authorize(_event()).authorization_id
    with pytest.raises(InvalidSignature):
        _public_key().verify(bytes.fromhex(forged.issuer_signature), message)


def test_authorization_without_an_issuer_signing_authority_fails_closed() -> None:
    with pytest.raises(IssueAgentTriggerError, match="issuer signing authority"):
        _authorize(_event(), signing_key=None)


@pytest.mark.parametrize("value", [None, "", "   ", "not-hex", "aa", "11" * 31, "11" * 33])
def test_missing_or_malformed_issuer_signing_key_fails_closed(value: object) -> None:
    with pytest.raises(IssueAgentTriggerError, match=trigger.SIGNING_KEY_ENV):
        load_signing_key(value)


def test_issuer_key_is_never_accepted_from_the_command_line() -> None:
    """The key is machine-only configuration, so no flag can carry it in the clear."""
    parser = trigger._parser()
    flags = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert "--signing-key" not in flags
    assert not any("key" in flag for flag in flags)


def test_signed_message_is_domain_separated() -> None:
    assert canonical_claims_message({}).startswith(trigger.SIGNATURE_DOMAIN)
    assert trigger.SIGNATURE_DOMAIN == b"hunter-issue-agent-authorization-v2:"
