from __future__ import annotations

import copy
from typing import Any

import pytest
from hunter_issue_agent_trigger import (
    DEFAULT_LABEL,
    IssueAgentTriggerError,
    authorize_event,
)


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


def _authorize(event: dict[str, Any]):
    return authorize_event(
        event,
        expected_repository="fafa33/Project-Hunter",
        owner_login="fafa33",
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
