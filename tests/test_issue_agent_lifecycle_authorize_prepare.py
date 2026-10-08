"""Regression for the live ``authorize`` failure (runs 37660344935 / 37660702621, Issue #574).

``cmd_authorize_prepare`` handed ``authorize.prepare`` ``document.encode()`` although ``document`` is already bytes on
both inputs, so every real authorization died with ``AttributeError: 'bytes' object has no attribute 'encode'`` before
any Source Handling, SPM or DPM work. The entry-point tests only exercised argv, so the body never ran. These tests run
the real body for both authorization sources and stop only at the heavy ``authorize.prepare`` composition, replacing it
with a recorder that applies the real first step of that function: parsing the document it was given.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import hunter_issue_agent_trigger as trigger
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from issue_agent_wire import issue_body_with_scope

from hunter.automation import issue_agent_remediation as remediation
from hunter.automation.issue_agent_execution import SignedIssueAgentAuthorization

REPOSITORY, OWNER = "fafa33/Project-Hunter", "fafa33"
ISSUER_KEY_HEX = "11" * 32
ISSUER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(ISSUER_KEY_HEX))
UPDATED_AT = "2026-10-08T07:00:00Z"
ISSUE = 389


class _Prepared:
    authorization_id = "hunter-issue-agent-authorization:" + "a" * 64
    issue_number = ISSUE

    def to_json(self) -> str:
        return '{"recorded":"prepared"}'


def _issue() -> dict[str, Any]:
    return {
        "number": ISSUE,
        "html_url": f"https://github.com/{REPOSITORY}/issues/{ISSUE}",
        "title": "Create the canary",
        "body": issue_body_with_scope("Create docs/ISSUE_AGENT_CANARY.md."),
        "state": "open",
        "updated_at": UPDATED_AT,
        "labels": [{"name": trigger.DEFAULT_LABEL}],
    }


def _label_event() -> dict[str, Any]:
    return {
        "action": "labeled",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "label": {"name": trigger.DEFAULT_LABEL},
        "issue": _issue(),
    }


def _remediation_document() -> bytes:
    group = remediation.remediation_group(
        parent_authorization_id="hunter-issue-agent-authorization:" + "b" * 64,
        issue_number=ISSUE,
        pull_request_number=600,
        bound_head_sha="c" * 40,
        attempt=1,
        findings=[
            {"finding_id": "f" * 64, "path": "docs/ISSUE_AGENT_CANARY.md", "claim": "the canary must say canary."}
        ],
    )
    authorization = remediation.remediation_authorization(
        _issue(), repository=REPOSITORY, owner_login=OWNER, remediation=group
    )
    parent_scope = {
        "task_id": "hunter-issue-agent-authorization:" + "b" * 64,
        "branch_pattern": "issue-*",
        "base_ref": "main",
        "base_sha": "a" * 40,
        "allowed_paths": ["src/", "scripts/", "tests/", "docs/"],
        "prohibited_paths": [],
    }
    scope = remediation.remediation_scope(authorization, parent_scope)
    return remediation.sign_remediation(authorization, scope, signing_key=ISSUER_KEY).to_json().encode()


class _Harness:
    """Real ``cmd_authorize_prepare`` with only the network, ledger and heavy composition replaced."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.received: list[object] = []
        self.parsed: list[SignedIssueAgentAuthorization] = []
        self.anchored: list[int] = []
        self.tmp = tmp_path
        monkeypatch.setenv(trigger.SIGNING_KEY_ENV, ISSUER_KEY_HEX)
        monkeypatch.setattr(lifecycle, "_run_context", lambda: (100, 1, "c" * 40))
        monkeypatch.setattr(lifecycle, "_export_public_trust", lambda _configuration: None)
        monkeypatch.setattr(lifecycle, "_github", lambda _configuration, **_kw: object())
        monkeypatch.setattr(lifecycle, "_require_anchors", lambda _c, _g, issue: self.anchored.append(issue))
        monkeypatch.setattr(lifecycle, "_authorize_dependencies", lambda _c, _g: object())
        monkeypatch.setattr(lifecycle, "_store", lambda _c, **_kw: object())
        monkeypatch.setattr(lifecycle.authorize, "prepare", self._prepare)
        self.configuration = SimpleNamespace(repository=REPOSITORY, owner_login=OWNER)

    def _prepare(self, document: object, **_kw: object) -> tuple[_Prepared, bytes]:
        self.received.append(document)
        # The real first step of authorize.prepare; it accepts bytes and would raise on any other shape.
        self.parsed.append(SignedIssueAgentAuthorization.from_json(document))  # type: ignore[arg-type]
        return _Prepared(), b"sealed-handoff"

    def run(self, **source: str | None) -> Path:
        out = self.tmp / "out"
        arguments = argparse.Namespace(**{"event": None, "document": None, "out_dir": str(out), **source})
        assert lifecycle.cmd_authorize_prepare(self.configuration, arguments) == 0  # type: ignore[arg-type]
        return out


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    return _Harness(monkeypatch, tmp_path)


def _assert_outputs(out: Path) -> None:
    assert (out / "handoff.sealed").read_bytes() == b"sealed-handoff"
    assert json.loads((out / "prepared.json").read_text()) == {"recorded": "prepared"}
    assert (out / "recorded_at").read_text()


def test_label_event_reaches_prepare_as_bytes_and_writes_its_outputs(harness: _Harness, tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps(_label_event()))

    out = harness.run(event=str(event))

    (received,) = harness.received
    assert isinstance(received, bytes)  # the defect: this was str.encode() on bytes, an AttributeError
    (signed,) = harness.parsed
    assert signed.authorization.issue_number == ISSUE
    assert signed.authorization.repository == REPOSITORY
    assert harness.anchored == [ISSUE]
    _assert_outputs(out)


def test_remediation_document_reaches_prepare_byte_for_byte_and_is_never_re_minted(
    harness: _Harness, tmp_path: Path
) -> None:
    document = _remediation_document()
    path = tmp_path / "remediation.json"
    path.write_bytes(document)

    out = harness.run(document=str(path))

    (received,) = harness.received
    assert received == document  # the K_AUTH-signed bytes the reconcile job minted, unchanged
    (signed,) = harness.parsed
    assert signed.authorization.issue_number == ISSUE
    assert harness.anchored == [ISSUE]
    _assert_outputs(out)


def test_exactly_one_authorization_source_is_required(harness: _Harness, tmp_path: Path) -> None:
    both = tmp_path / "x.json"
    both.write_text("{}")
    for source in ({}, {"event": str(both), "document": str(both)}):
        arguments = argparse.Namespace(**{"event": None, "document": None, "out_dir": str(tmp_path / "o"), **source})
        with pytest.raises(lifecycle.LifecycleRefused):
            lifecycle.cmd_authorize_prepare(harness.configuration, arguments)  # type: ignore[arg-type]
    assert harness.received == []
