"""Trusted admission kernel for Hunter Merge Readiness controller upgrades.

This module exists to make the controller-upgrade lifecycle a first-class,
explicit, testable part of the architecture rather than an exception hidden
inside ordinary freshness/staleness handling. It answers exactly one
question: "does the exact current head of a PR that touches controller-owned
files already carry enough independently-verifiable, exact-head-bound
evidence to be trusted, without ever executing that PR's own code?"

Trust boundary (do not weaken this file without re-reading this comment):

- This module NEVER imports, executes, or reads the *content* of any file
  from the pull request under evaluation. It only reads GitHub API evidence
  about the PR: which paths changed (names only), check-run/status state for
  the exact head SHA, and review/thread state. The only things that ever
  execute the PR's own code are Quality Gates, dependency-review, CodeQL, and
  Hunter Governance Review -- and all four run via the plain `pull_request`
  event (never `pull_request_target`), in an isolated, secretless, read-only
  token context. This module only consumes the *evidence those already-safe
  runs produced*.
- Detection of a "controller-upgrade candidate" is purely evidence-based
  (changed file paths from the GitHub API), never based on PR number, PR
  body text, labels, or any other author-controlled signal that could be
  used to manufacture a bypass.
- There is deliberately no persisted controller "version" or "generation"
  registry. The generation is simply whatever commit is on the default
  branch HEAD at evaluation time -- git history is the ledger. This avoids
  inventing a new authority/registry that nothing in this repository's
  accepted governance already owns.
- Admission is a strict superset of the ordinary readiness gates (required
  checks, Governance freshness, unresolved feedback), applied with fresh,
  uncached, independently re-verified evidence. It never lowers a
  requirement; it exists to apply *more* scrutiny to PRs that modify the
  trust boundary itself, not less.

Bootstrap limitation (irreducible, not a bug): the very first PR that
introduces this file cannot be admitted by it, because this module does not
exist on the default branch yet to do the admitting. That one-time
installation is a human-authorized root-of-trust event, structurally
identical to how `docs/HUNTER_GOVERNANCE_REVIEW.md` documents its own
bootstrap exception for Governance Review's initial installation. Every
subsequent PR that touches CONTROLLER_OWNED_PATHS, once this module is
resident on the default branch, is governed by this same mechanism
automatically -- no further exception, no PR-specific bypass.
"""

from datetime import datetime
from typing import Any, NamedTuple

# `hunter_merge_readiness` (core) imports this module to call into it, so
# importing core back at module scope here would be circular. Each function
# below imports it locally instead -- by call time core is always fully
# loaded, since core is the only caller of this module.

# The explicit, fixed set of paths that define the trust boundary. A PR is a
# "controller-upgrade candidate" if and only if it changes one of these exact
# paths -- deliberately including this file itself, so a future PR that
# modifies the admission kernel is held to the same standard as one that
# modifies the controller it protects.
CONTROLLER_OWNED_PATHS = frozenset(
    {
        "scripts/hunter_merge_readiness.py",
        "scripts/hunter_merge_readiness_entrypoint.py",
        "scripts/hunter_controller_admission.py",
        ".github/workflows/hunter-merge-readiness.yml",
    }
)


class AdmissionResult(NamedTuple):
    admitted: bool
    reason: str


def is_controller_upgrade_candidate(pr_number: int) -> bool:
    """Returns True iff the PR's changed files (paths only) touch the trust boundary.

    Evidence-only: reads file paths from the GitHub API. Never inspects file
    content, PR body, labels, or PR number. Checks both the current filename
    and, for renamed files, the previous filename -- a PR that renames a
    controller-owned path away from itself must still be treated as a
    candidate (in practice such a rename would also break the workflow's own
    static import of this module, but detection should not depend on that).
    """
    import hunter_merge_readiness as core

    for entry in core.paged(f"pulls/{pr_number}/files"):
        if not isinstance(entry, dict):
            continue
        if entry.get("filename") in CONTROLLER_OWNED_PATHS:
            return True
        if entry.get("previous_filename") in CONTROLLER_OWNED_PATHS:
            return True
    return False


def evaluate_admission(
    pr_number: int,
    pr: dict[str, Any],
    sha: str,
    runs: list[dict[str, Any]],
    governance_started_at: datetime,
) -> AdmissionResult:
    """One-shot, always-fresh re-verification for a controller-upgrade candidate.

    Independently re-derives every required gate from evidence, using the
    same resident primitives the ordinary steady-state policy uses (so this
    automatically inherits whatever freshness/exemption semantics are
    currently deployed on the default branch -- never anything the candidate
    itself introduces). Returns (admitted, reason); callers must treat a
    non-admitted result exactly like any other "stay pending" outcome.
    """
    import hunter_merge_readiness as core

    current = core.request_json("GET", f"pulls/{pr_number}")
    current_sha = ((current.get("head") or {}).get("sha") or "").strip()
    if current_sha != sha:
        return AdmissionResult(False, f"head changed during admission (now {current_sha[:10] or 'unknown'}).")

    missing_or_failed = []
    for name in core.required_checks:
        run = core.latest_check(runs, name)
        if run is None:
            missing_or_failed.append(f"{name}=missing")
        elif run.get("status") != "completed":
            missing_or_failed.append(f"{name}={run.get('status')}")
        elif run.get("conclusion") != "success":
            missing_or_failed.append(f"{name}={run.get('conclusion')}")
    if missing_or_failed:
        return AdmissionResult(False, "required checks not green: " + ", ".join(missing_or_failed))

    governance_run = core.latest_check(runs, core.governance_context)
    if governance_run is None:
        return AdmissionResult(False, f"{core.governance_context} has not run for this exact head.")
    if governance_run.get("status") != "completed" or governance_run.get("conclusion") != "success":
        conclusion = governance_run.get("conclusion") or governance_run.get("status") or "pending"
        return AdmissionResult(False, f"{core.governance_context}={conclusion}.")

    invalidation_time = core.get_latest_invalidation_time(pr_number, pr)
    if governance_started_at < invalidation_time:
        return AdmissionResult(
            False,
            f"{core.governance_context} started {governance_started_at.isoformat()}, "
            f"older than latest invalidation at {invalidation_time.isoformat()}.",
        )

    unresolved_threads = core.unresolved_review_thread_count(pr_number)
    if unresolved_threads:
        return AdmissionResult(False, f"unresolved review threads remain: {unresolved_threads}.")

    changes_requested = core.current_changes_requested_reviewers(pr_number)
    if changes_requested:
        return AdmissionResult(False, "changes requested by: " + ", ".join(changes_requested))

    return AdmissionResult(
        True,
        f"controller-upgrade candidate admitted: exact head {sha[:10]}, "
        f"{core.governance_context} fresh since {governance_started_at.isoformat()}, "
        "all required checks green, no unresolved feedback.",
    )
