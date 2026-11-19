"""Deterministic, fail-closed Issue selection; no dispatch or authorization side effects.

Issue #437. Inputs MUST be verified by a trusted GitHub/controller caller;
candidate Issue text is never a policy or ownership authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

POLICY_VERSION = "hunter-engineering-issue-queue-v1"
PRIORITIES = ("P0", "P1", "P2", "P3")


class QueueAuthorityError(ValueError):
    """Missing or contradictory trusted queue evidence."""


@dataclass(frozen=True)
class IssueCandidate:
    number: int
    open: bool
    engineering: bool
    owner_approved: bool
    ready: bool
    priority: str
    order: int
    blockers: tuple[int, ...] = ()
    active_owner: bool = False


@dataclass(frozen=True)
class IssueSelection:
    issue_number: int | None
    policy_version: str
    identity: str
    reason: str
    evaluated: tuple[tuple[int, str], ...]


def select_next_issue(
    candidates: Sequence[IssueCandidate],
    *,
    active_lifecycles: int,
    owner_override: int | None = None,
) -> IssueSelection:
    """Select only from trusted, complete, explicit candidate facts.

    All referenced blockers must be represented in the trusted snapshot.
    A malformed candidate or dependency cycle refuses the whole snapshot.
    This pure function never constructs prompts, chooses providers or dispatches.
    """
    if type(active_lifecycles) is not int or active_lifecycles < 0:
        raise QueueAuthorityError("invalid active lifecycle count")
    if owner_override is not None and (type(owner_override) is not int or owner_override < 1):
        raise QueueAuthorityError("invalid owner override")
    by_number: dict[int, IssueCandidate] = {}
    for item in candidates:
        if not isinstance(item, IssueCandidate) or type(item.number) is not int or item.number < 1:
            raise QueueAuthorityError("invalid issue identity")
        if item.number in by_number:
            raise QueueAuthorityError("duplicate issue identity")
        if any(
            type(getattr(item, key)) is not bool
            for key in ("open", "engineering", "owner_approved", "ready", "active_owner")
        ):
            raise QueueAuthorityError("ambiguous eligibility flags")
        if item.priority not in PRIORITIES or type(item.order) is not int or item.order < 0:
            raise QueueAuthorityError("invalid priority or order")
        if not isinstance(item.blockers, tuple) or any(type(n) is not int or n < 1 for n in item.blockers):
            raise QueueAuthorityError("invalid blocker identity")
        if len(set(item.blockers)) != len(item.blockers):
            raise QueueAuthorityError("duplicate blockers")
        by_number[item.number] = item
    for item in candidates:
        if any(n not in by_number for n in item.blockers):
            raise QueueAuthorityError("unresolved blocker reference")
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(n: int) -> None:
        if n in visiting:
            raise QueueAuthorityError("dependency cycle")
        if n in visited:
            return
        visiting.add(n)
        for blocker in by_number[n].blockers:
            visit(blocker)
        visiting.remove(n)
        visited.add(n)

    for n in sorted(by_number):
        visit(n)
    if owner_override is not None and owner_override not in by_number:
        raise QueueAuthorityError("owner override is absent from trusted snapshot")
    evaluated: list[tuple[int, str]] = []
    eligible: list[IssueCandidate] = []
    for item in sorted(candidates, key=lambda x: x.number):
        reason = (
            "closed"
            if not item.open
            else (
                "not-engineering"
                if not item.engineering
                else (
                    "not-owner-approved"
                    if not item.owner_approved
                    else (
                        "not-ready"
                        if not item.ready
                        else (
                            "blocked"
                            if any(by_number[n].open for n in item.blockers)
                            else "active-ownership" if active_lifecycles or item.active_owner else "eligible"
                        )
                    )
                )
            )
        )
        evaluated.append((item.number, reason))
        if reason == "eligible":
            eligible.append(item)
    eligible.sort(key=lambda x: (PRIORITIES.index(x.priority), x.order, x.number))
    selected = (
        next((x for x in eligible if x.number == owner_override), None)
        if owner_override is not None
        else (eligible[0] if eligible else None)
    )
    if owner_override is not None and selected is None:
        raise QueueAuthorityError("owner override is not eligible")
    number = selected.number if selected else None
    payload = {
        "policy": POLICY_VERSION,
        "candidates": [
            {
                "number": x.number,
                "open": x.open,
                "engineering": x.engineering,
                "owner_approved": x.owner_approved,
                "ready": x.ready,
                "priority": x.priority,
                "order": x.order,
                "blockers": sorted(x.blockers),
                "active_owner": x.active_owner,
            }
            for x in sorted(candidates, key=lambda x: x.number)
        ],
        "active_lifecycles": active_lifecycles,
        "owner_override": owner_override,
        "selected": number,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return IssueSelection(
        number,
        POLICY_VERSION,
        digest,
        "owner-override" if owner_override is not None else ("selected" if selected else "no-eligible-issue"),
        tuple(evaluated),
    )


def handoff_selected_issue(
    selection: IssueSelection,
    *,
    verified_issue_request: Callable[[int], object],
    governed_ingress: Callable[[object], object],
    trusted_candidates: Sequence[IssueCandidate],
    active_lifecycles: int,
    owner_override: int | None = None,
) -> object:
    """Trusted composition adapter: only a selected identity reaches the existing ingress."""
    if not isinstance(selection, IssueSelection) or selection.issue_number is None:
        raise QueueAuthorityError("no selected issue for ingress")
    current = select_next_issue(trusted_candidates, active_lifecycles=active_lifecycles, owner_override=owner_override)
    if selection != current:
        raise QueueAuthorityError("stale or forged queue selection")
    return governed_ingress(verified_issue_request(selection.issue_number))
