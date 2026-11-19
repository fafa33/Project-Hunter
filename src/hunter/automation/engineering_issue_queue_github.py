"""Read-only GitHub evidence adapter for the #437 governed Issue queue.

The owner-authored machine-readable comment is authority for eligibility and
priority; ordinary Issue body, labels, and agent prose are never authority.
This adapter never authorizes, dispatches, opens PRs, or compiles prompts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hunter.automation.engineering_issue_queue import (
    IssueCandidate,
    IssueSelection,
    QueueAuthorityError,
    select_next_issue,
)

POLICY_PREFIX = "hunter-engineering-queue-v1:"
_POLICY_FIELDS = frozenset({"engineering", "owner_approved", "ready", "priority", "order", "blocked_by"})
_SHA = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")


def _object(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise QueueAuthorityError(f"invalid {description}")
    return value


def _issue_number(issue: Mapping[str, Any]) -> int:
    number = issue.get("number")
    if type(number) is not int or number < 1:
        raise QueueAuthorityError("invalid GitHub Issue number")
    return number


def _owner_policy(comments: Sequence[Mapping[str, Any]], owner_login: str) -> Mapping[str, Any] | None:
    # A newer policy by an untrusted actor cannot supersede the owner's policy.
    # Multiple owner policy records are rejected rather than guessed.
    policies: list[Mapping[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or not body.startswith(POLICY_PREFIX):
            continue
        author = _object(comment.get("user"), "comment author")
        if author.get("login") != owner_login or author.get("type") != "User":
            continue
        try:
            policy = json.loads(body[len(POLICY_PREFIX) :])
        except (ValueError, TypeError):
            raise QueueAuthorityError("malformed owner queue policy") from None
        policy = _object(policy, "owner queue policy")
        if set(policy) != _POLICY_FIELDS:
            raise QueueAuthorityError("incomplete or unknown owner queue policy fields")
        policies.append(policy)
    if len(policies) > 1:
        raise QueueAuthorityError("conflicting owner queue policies")
    return policies[0] if policies else None


def _parse_candidate(issue: Mapping[str, Any], policy: Mapping[str, Any] | None) -> IssueCandidate:
    number = _issue_number(issue)
    state = issue.get("state")
    if state not in ("open", "closed"):
        raise QueueAuthorityError("indefinite Issue state")
    if policy is None:
        return IssueCandidate(number, state == "open", False, False, False, "P3", number)
    for flag in ("engineering", "owner_approved", "ready"):
        if type(policy[flag]) is not bool:
            raise QueueAuthorityError("ambiguous owner queue eligibility")
    priority = policy["priority"]
    order = policy["order"]
    blockers = policy["blocked_by"]
    if not isinstance(priority, str) or type(order) is not int or order < 0:
        raise QueueAuthorityError("invalid owner queue priority/order")
    if not isinstance(blockers, list) or any(type(n) is not int or n < 1 for n in blockers):
        raise QueueAuthorityError("invalid owner queue blockers")
    return IssueCandidate(
        number,
        state == "open",
        policy["engineering"],
        policy["owner_approved"],
        policy["ready"],
        priority,
        order,
        tuple(blockers),
    )


def select_from_github(
    *,
    repository: str,
    owner_login: str,
    list_issues: Callable[[], Sequence[Mapping[str, Any]]],
    list_comments: Callable[[int], Sequence[Mapping[str, Any]]],
    get_issue: Callable[[int], Mapping[str, Any]],
    active_lifecycles: Callable[[], int],
    open_issue_agent_prs: Callable[[], Sequence[int]],
    owner_override: int | None = None,
) -> IssueSelection:
    """Resolve a complete trusted snapshot, then delegate ordering to #437 core.

    Caller must use authenticated, paginated, non-truncated GitHub reads and a
    definitive existing execution ledger count. Failures propagate closed.
    """
    if not _SHA.fullmatch(repository) or not owner_login:
        raise QueueAuthorityError("invalid repository/owner authority")
    raw = list_issues()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise QueueAuthorityError("indefinite Issue listing")
    candidates: dict[int, IssueCandidate] = {}
    for issue in raw:
        obj = _object(issue, "Issue")
        number = _issue_number(obj)
        if number in candidates:
            raise QueueAuthorityError("duplicate GitHub Issue")
        comments = list_comments(number)
        policy = _owner_policy(comments, owner_login)
        candidates[number] = _parse_candidate(obj, policy)
    # Fetch referenced blockers explicitly; closed Issues are absent from
    # the open queue listing and must still be checked, never presumed closed.
    pending = {n for candidate in candidates.values() for n in candidate.blockers}
    while pending:
        number = min(pending)
        pending.remove(number)
        if number in candidates:
            continue
        obj = _object(get_issue(number), "blocker Issue")
        if _issue_number(obj) != number:
            raise QueueAuthorityError("blocker identity mismatch")
        policy = _owner_policy(list_comments(number), owner_login)
        candidate = _parse_candidate(obj, policy)
        candidates[number] = candidate
        pending.update(n for n in candidate.blockers if n not in candidates)
    active = active_lifecycles()
    prs = open_issue_agent_prs()
    if not isinstance(prs, Sequence) or any(type(n) is not int or n < 1 for n in prs):
        raise QueueAuthorityError("indefinite active PR ownership")
    if len(set(prs)) != len(prs):
        raise QueueAuthorityError("duplicate active PR ownership")
    # Open PR for any candidate means this queue cannot issue another task.
    # Existing authorization remains the final atomic admission authority.
    if prs:
        active = max(active, 1)
    return select_next_issue(tuple(candidates.values()), active_lifecycles=active, owner_override=owner_override)
