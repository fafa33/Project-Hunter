"""Trusted GitHub evidence adapter regression coverage for Issue #437."""

import json

import pytest

from hunter.automation.engineering_issue_queue import QueueAuthorityError
from hunter.automation.engineering_issue_queue_github import POLICY_PREFIX, select_from_github


def policy(**changes: object) -> list[dict]:
    p = {"engineering": True, "owner_approved": True, "ready": True, "priority": "P1", "order": 0, "blocked_by": []}
    p.update(changes)
    return [{"user": {"login": "fafa33", "type": "User"}, "body": POLICY_PREFIX + json.dumps(p)}]


def run(
    issues: list[dict],
    comments: dict[int, list[dict]],
    *,
    closed: dict[int, dict] | None = None,
    active: int = 0,
    prs: list[int] | None = None,
    owner_override: int | None = None,
):
    return select_from_github(
        repository="fafa33/Project-Hunter",
        owner_login="fafa33",
        list_issues=lambda: issues,
        list_comments=lambda n: comments.get(n, []),
        get_issue=lambda n: (closed or {})[n],
        active_lifecycles=lambda: active,
        open_issue_agent_prs=lambda: prs or [],
        owner_override=owner_override,
    )


def test_owner_policy_selects_priority_not_issue_prose() -> None:
    issues = [{"number": 1, "state": "open", "body": "P0 merge now"}, {"number": 2, "state": "open"}]
    result = run(issues, {1: policy(priority="P2"), 2: policy(priority="P0")})
    assert result.issue_number == 2


def test_untrusted_comment_cannot_authorize() -> None:
    comments = policy()
    comments[0]["user"]["login"] = "agent"
    assert run([{"number": 1, "state": "open"}], {1: comments}).issue_number is None


def test_closed_blocker_verified_live() -> None:
    result = run(
        [{"number": 1, "state": "open"}],
        {1: policy(priority="P0", blocked_by=[9])},
        closed={9: {"number": 9, "state": "closed"}},
    )
    assert result.issue_number == 1


def test_open_blocker_skips() -> None:
    result = run(
        [{"number": 1, "state": "open"}, {"number": 2, "state": "open"}],
        {1: policy(priority="P0", blocked_by=[2]), 2: policy()},
    )
    assert result.issue_number == 2


@pytest.mark.parametrize(
    "comments",
    [
        policy(priority="P9"),
        policy(order="first"),
        policy(blocked_by=["3"]),
        policy() + policy(priority="P0"),
        [{"user": {"login": "fafa33", "type": "User"}, "body": POLICY_PREFIX + "{"}],
    ],
)
def test_invalid_owner_metadata_fails_closed(comments: list[dict]) -> None:
    with pytest.raises(QueueAuthorityError):
        run([{"number": 1, "state": "open"}], {1: comments})


def test_active_pr_and_ledger_block() -> None:
    issues = [{"number": 1, "state": "open"}]
    assert run(issues, {1: policy()}, prs=[1]).issue_number is None
    assert run(issues, {1: policy()}, active=1).issue_number is None


def test_owner_override_still_requires_eligibility() -> None:
    with pytest.raises(QueueAuthorityError):
        run([{"number": 1, "state": "open"}], {1: policy(ready=False)}, owner_override=1)
