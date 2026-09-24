"""Canonical owner-authored task scope contract.

Repository-owned scope semantics shared by workflow-state enforcement and the
engineering prevention path.  Caller prose never widens this contract.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from fnmatch import fnmatch
from typing import Any


@dataclass(frozen=True)
class TaskScopeContract:
    """The starting scope an agent was assigned, as machine-readable fields.

    This is the *assignment*, not the agent's account of it. It is owner-authored
    and supplied to the evaluator; nothing the agent writes -- prose, PR body,
    commit message, comment, or claim -- can widen, waive, or override it.

    Every field is compared against repository and pull-request evidence. A
    disagreement is a `SCOPE_MISMATCH`, which prevents IMPLEMENTED and therefore
    every later state.
    """

    task_id: str
    branch_pattern: str
    base_ref: str = "main"
    # Optional on purpose. `base_ref` is the required base *relationship*;
    # pinning an exact commit as well is available for an assignment that wants
    # it, but is not demanded of every contract: the base branch moves, so a
    # mandatory commit pin would reject a PR branched from a newer main -- work
    # that is exactly the assignment -- and a guard that rejects valid state is
    # itself a defect (docs/DEFECT_REGISTRY.json, PRH-009). When it is omitted
    # the base commit must still be present in the evidence.
    base_sha: str = ""
    allowed_paths: tuple[str, ...] = ()
    prohibited_paths: tuple[str, ...] = ()

    def incompleteness(self) -> str:
        """Why this contract cannot be enforced, or "" when it can.

        A contract missing the fields the gate compares cannot detect anything,
        so an incomplete one fails closed rather than passing vacuously. An empty
        `allowed_paths` is incomplete for the same reason: it would either admit
        every path or none, and neither is a scope statement.
        """

        for name in ("task_id", "branch_pattern", "base_ref"):
            if not str(getattr(self, name) or "").strip():
                return f"scope contract is missing {name}"
        if not self.allowed_paths:
            return "scope contract declares no allowed_paths"
        return ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskScopeContract:
        if not isinstance(payload, dict):
            raise ValueError("scope contract must be a JSON object")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            # An unreadable field is a scope statement the gate would silently
            # ignore, so refuse the contract rather than enforce part of it.
            raise ValueError("scope contract has unknown field(s): " + ", ".join(unknown))

        def text(name: str, default: str = "") -> str:
            value = payload.get(name)
            if value is None:
                return default
            if not isinstance(value, str):
                raise ValueError(f"scope contract field {name!r} must be a string")
            return value or default

        def path_tuple(name: str) -> tuple[str, ...]:
            value = payload.get(name)
            if value is None:
                return ()
            # A JSON string is iterable, so "src/" would silently become
            # ('s', 'r', 'c', '/'): four entries that match no real path, which
            # disables a prohibition list entirely and rejects every path in an
            # allow list. Refuse it, for the same reason an unknown field is
            # refused -- an owner-authored assignment is enforced whole or not
            # at all.
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise ValueError(f"scope contract field {name!r} must be an array of paths")
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"scope contract field {name!r} must contain only path strings")
            return tuple(value)

        return cls(
            task_id=text("task_id"),
            branch_pattern=text("branch_pattern"),
            base_ref=text("base_ref", "main"),
            base_sha=text("base_sha"),
            allowed_paths=path_tuple("allowed_paths"),
            prohibited_paths=path_tuple("prohibited_paths"),
        )


def path_matches_scope_entry(path: str, entry: str) -> bool:
    """A changed path is covered by a scope entry by directory prefix or glob.

    Public because the governed connector write ingress
    (`hunter_connector_write_ingress`) compares changed paths against the same
    kind of owner-authored scope entry, and two implementations of "is this
    path in scope" would be two different answers.
    """

    entry = entry.strip()
    if not entry:
        return False
    if path == entry or fnmatch(path, entry):
        return True
    return path.startswith(entry.rstrip("/") + "/")


__all__ = ["TaskScopeContract", "path_matches_scope_entry", "strip_task_scope_block"]

TASK_SCOPE_BLOCK_PREFIX = "<!-- hunter-task-scope-v1\n"
TASK_SCOPE_BLOCK_SUFFIX = "\n-->"


def strip_task_scope_block(body: str) -> str:
    """Remove the one explicit scope metadata block from untrusted task prose."""
    start = body.find(TASK_SCOPE_BLOCK_PREFIX)
    if start < 0:
        return body
    end = body.find(TASK_SCOPE_BLOCK_SUFFIX, start + len(TASK_SCOPE_BLOCK_PREFIX))
    if end < 0:
        return body
    return (body[:start] + body[end + len(TASK_SCOPE_BLOCK_SUFFIX) :]).strip()
