"""Trusted admission kernel for Hunter Merge Readiness controller upgrades.

This module answers exactly one question: "does the exact current state of a
pull request that changes controller-owned files carry enough independently
verifiable evidence to be declared ready, without ever executing that pull
request's own code?"

Trust boundary (do not weaken this file without re-reading this comment):

- This module NEVER imports, executes, or reads the *content* of any file from
  the pull request under evaluation. It reads nothing at all: it is a pure
  function of a ``CurrentState`` snapshot that the controller already gathered
  from the GitHub API. The only things that ever execute the pull request's own
  code are Quality Gates, dependency-review, CodeQL, and Hunter Governance
  Review -- all four run via the plain ``pull_request`` event (never
  ``pull_request_target``), in an isolated, secretless, read-only token context.
  This module only consumes the evidence those already-safe runs produced.
- Detection of a "controller-upgrade candidate" is purely evidence-based
  (changed file paths from the GitHub API), never based on pull-request number,
  body text, labels, or any other author-controlled signal that could be used to
  manufacture a bypass. No pull request is ever special-cased.
- There is deliberately no persisted controller "version" or "generation"
  registry. The generation is simply whatever commit is on the default branch at
  evaluation time -- git history is the ledger.
- Admission is a strict superset of the ordinary readiness gates, never a
  relaxation. Every gate the ordinary path applies is re-derived here, on a
  second live reading of current state, by code that shares no logic with
  ``hunter_merge_readiness.decide``.

Why the re-derivation below deliberately duplicates ``decide`` rather than
calling it: reusing ``decide`` would make admission tautological, and a
controller-upgrade pull request is precisely the pull request that can rewrite
``decide``. Two independent implementations of the same requirements must agree
before a pull request is allowed to change the trust boundary. If a future edit
weakens one of them, they disagree and the candidate stays pending instead of
silently inheriting the weakened rule -- the ordinary path would green-light it.
That divergence-detection property, not extra strictness on a well-formed
snapshot, is what this kernel buys.

Bootstrap limitation (irreducible, not a bug): the very first pull request that
introduces this file cannot be admitted by it, because this module does not
exist on the default branch yet to do the admitting. That one-time installation
is a human-authorized root-of-trust event, structurally identical to how
``docs/HUNTER_GOVERNANCE_REVIEW.md`` documents its own bootstrap exception.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from hunter_merge_readiness import CurrentState

# The explicit, fixed set of paths that define the trust boundary. A pull
# request is a "controller-upgrade candidate" if and only if it changes one of
# these exact paths -- deliberately including this file itself, so a future pull
# request that modifies the admission kernel is held to the same standard as one
# that modifies the controller it protects.
#
# `hunter_governance_revision.py` is included because it defines the identity by
# which Governance evidence is bound to current state; changing it changes what
# "current evidence" means for every readiness decision, which is exactly the
# trust boundary this set exists to guard.
CONTROLLER_OWNED_PATHS = frozenset(
    {
        "scripts/hunter_merge_readiness.py",
        "scripts/hunter_controller_admission.py",
        "scripts/hunter_governance_revision.py",
        ".github/workflows/hunter-merge-readiness.yml",
    }
)


class AdmissionResult(NamedTuple):
    admitted: bool
    reason: str


def touches_trust_boundary(changed_paths: Iterable[str]) -> bool:
    """True iff these changed paths (names only) touch the trust boundary.

    Evidence-only: file paths from the GitHub API. Never inspects file content,
    pull-request body, labels, or number.
    """
    return any(path in CONTROLLER_OWNED_PATHS for path in changed_paths)


def evaluate_admission(state: CurrentState) -> AdmissionResult:
    """Independently re-derive every gate from a second live observation.

    ``state`` must be a snapshot read *after* the one that produced the success
    decision, so that admission is agreement between two independent readings of
    live GitHub state rather than a second look at one reading.
    """
    if not state.open:
        return AdmissionResult(False, "pull request is no longer open.")
    if state.draft:
        return AdmissionResult(False, "pull request is a draft.")
    if state.shared_head_pull_requests:
        # The readiness status is keyed by (SHA, context), so a green published
        # on a shared head asserts mergeability for every open pull request on
        # that head. Refused here as well as in `decide`, because this kernel
        # must never rely on the caller having already refused.
        others = ", ".join(f"#{number}" for number in state.shared_head_pull_requests)
        return AdmissionResult(False, f"head is shared with open PR {others}.")

    not_green = []
    for check in state.required:
        if not check.present:
            not_green.append(f"{check.name}=missing")
        elif not check.completed:
            not_green.append(f"{check.name}=incomplete")
        elif check.conclusion != "success":
            not_green.append(f"{check.name}={check.conclusion}")
    if not_green:
        return AdmissionResult(False, "required checks not green: " + ", ".join(not_green))

    evidence = state.governance
    if evidence is None:
        return AdmissionResult(
            False,
            f"no Hunter Governance Review evidence names revision {state.governance_revision}.",
        )
    if evidence.pull_request_number != state.pull_request_number:
        return AdmissionResult(
            False,
            f"Governance evidence names PR #{evidence.pull_request_number}, not #{state.pull_request_number}.",
        )
    if evidence.revision != state.governance_revision:
        return AdmissionResult(
            False,
            f"Governance evidence names revision {evidence.revision}, not {state.governance_revision}.",
        )
    if evidence.state != "success":
        return AdmissionResult(False, f"Hunter Governance Review={evidence.state}.")

    if state.unresolved_thread_ids:
        return AdmissionResult(False, f"unresolved review threads remain: {len(state.unresolved_thread_ids)}.")
    if state.changes_requested:
        return AdmissionResult(False, "changes requested by: " + ", ".join(state.changes_requested))
    if state.unacknowledged_comments:
        return AdmissionResult(
            False,
            f"unacknowledged PR comments remain: {len(state.unacknowledged_comments)}.",
        )

    return AdmissionResult(
        True,
        f"controller-upgrade candidate admitted: exact head {state.head_sha[:10]}, "
        f"Hunter Governance Review success for revision {state.governance_revision}, "
        "all required checks green, no unresolved feedback.",
    )
