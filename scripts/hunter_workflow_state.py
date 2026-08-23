"""Agent workflow-state enforcement.

An agent reports where it believes a contribution stands. This module decides
where it *actually* stands, from current GitHub state, and demotes any claim the
evidence does not support.

    GITHUB STATE > AGENT CLAIMS

The workflow states are:

```text
IMPLEMENTED -> TESTED -> PREFLIGHT_PASSED -> PR_OPEN ->
    REVIEWED -> ZERO_OPEN_FINDINGS -> ALL_CHECKS_GREEN -> MERGE_READY
```

Scope boundary
--------------

This module defines **no** merge-readiness policy. `MERGE_READY` is exactly
`scripts/hunter_merge_readiness_v2.evaluate()` returning `success`, and the
check/governance signals are read through that same module's constants and
helpers. `docs/DEVELOPMENT_GOVERNANCE.md` remains the owner of merge-readiness
semantics; this module only reports which lifecycle stage current evidence
supports.

It publishes no commit status and adds no required check. It is an evaluator an
agent runs against itself, not a new merge gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import hunter_merge_readiness_v2 as readiness

# The exact-head check that runs the canonical preflight in normal mode
# (`.github/workflows/ci.yml` runs `scripts/hunter_pr_preflight.py --mode normal`).
#
# `Hunter Pre-PR Preflight` deliberately is NOT an authority here even though it
# runs the same script: on a branch head carrying `.hunter-preflight-mode`, that
# workflow passes in tests-first-red mode with a genuinely failing test suite, so
# accepting it would let a red suite establish TESTED.
CANONICAL_PREFLIGHT_CHECK = "Quality Gates"

GITHUB = "github"
LOCAL = "local"
DECLARED = "declared"
NONE = "none"


class WorkflowState(IntEnum):
    """Lifecycle stages, ordered. A stage is reached only if all earlier ones are."""

    UNVERIFIED = 0
    IMPLEMENTED = 1
    TESTED = 2
    PREFLIGHT_PASSED = 3
    PR_OPEN = 4
    REVIEWED = 5
    ZERO_OPEN_FINDINGS = 6
    ALL_CHECKS_GREEN = 7
    MERGE_READY = 8


ENFORCED_STATES: tuple[WorkflowState, ...] = tuple(
    state for state in WorkflowState if state is not WorkflowState.UNVERIFIED
)


class Verdict:
    CONFIRMED = "CONFIRMED"
    ADVANCED = "ADVANCED"
    DEMOTED = "DEMOTED"
    UNCLAIMED = "UNCLAIMED"


@dataclass(frozen=True)
class LocalEvidence:
    """Off-GitHub evidence an agent reports about its own working tree.

    This is itself an agent claim, so it is admissible only while GitHub has
    nothing to say -- that is, before an open PR exists. Once a PR is open, every
    state is decided from current GitHub state and these values are ignored
    entirely; otherwise a claim could be laundered into evidence.
    """

    changed_files: int = 0
    pytest_passed: bool = False
    preflight_passed: bool = False


@dataclass(frozen=True)
class Review:
    author: str
    state: str


@dataclass(frozen=True)
class PullRequestObservation:
    """Current GitHub state for the contribution under evaluation."""

    number: int
    is_open: bool
    head_sha: str
    author: str
    base_ref: str = "main"
    changed_files: int = 0
    draft: bool = False
    mergeable: bool | None = None
    reviews: tuple[Review, ...] = ()
    unresolved_review_threads: tuple[str, ...] = ()
    changes_requested: tuple[str, ...] = ()
    check_runs: tuple[dict[str, Any], ...] = ()
    governance_status: dict[str, Any] | None = None
    shared_open_prs: tuple[int, ...] = ()

    def readiness_observation(self) -> readiness.StaticReadinessObservation:
        return readiness.StaticReadinessObservation(
            draft=self.draft,
            mergeable=self.mergeable,
            unresolved_review_threads=self.unresolved_review_threads,
            changes_requested=self.changes_requested,
            check_runs=self.check_runs,
            governance_status=self.governance_status,
            shared_open_prs=self.shared_open_prs,
        )

    def checks_only_observation(self) -> readiness.StaticReadinessObservation:
        """The same observation with every non-check blocker neutralised.

        Evaluating this through the canonical decision is what makes
        ALL_CHECKS_GREEN mean exactly what merge readiness means by it,
        including the stale-governance-pending allowance, without restating it.
        """

        return readiness.StaticReadinessObservation(
            draft=False,
            mergeable=True,
            unresolved_review_threads=(),
            changes_requested=(),
            check_runs=self.check_runs,
            governance_status=self.governance_status,
            shared_open_prs=(),
        )


@dataclass(frozen=True)
class StateFinding:
    state: WorkflowState
    established: bool
    authority: str
    detail: str

    @property
    def name(self) -> str:
        return self.state.name


@dataclass(frozen=True)
class WorkflowStateReport:
    derived: WorkflowState
    claimed: WorkflowState | None
    verdict: str
    findings: tuple[StateFinding, ...] = field(default_factory=tuple)

    @property
    def blocker(self) -> StateFinding | None:
        """The first stage the evidence does not support."""

        for finding in self.findings:
            if not finding.established:
                return finding
        return None

    @property
    def claim_upheld(self) -> bool:
        return self.verdict != Verdict.DEMOTED

    def as_dict(self) -> dict[str, Any]:
        blocker = self.blocker
        return {
            "derived_state": self.derived.name,
            "claimed_state": None if self.claimed is None else self.claimed.name,
            "verdict": self.verdict,
            "blocked_at": None if blocker is None else blocker.name,
            "blocked_because": None if blocker is None else blocker.detail,
            "states": [
                {
                    "state": finding.name,
                    "established": finding.established,
                    "authority": finding.authority,
                    "detail": finding.detail,
                }
                for finding in self.findings
            ],
        }

    def render(self) -> str:
        lines = [f"Derived workflow state: {self.derived.name}"]
        for finding in self.findings:
            mark = "PASS" if finding.established else "STOP"
            lines.append(f"  [{mark}] {finding.name} ({finding.authority}) — {finding.detail}")
        if self.claimed is not None:
            lines.append(f"Agent claim: {self.claimed.name} — {self.verdict}")
            if self.verdict == Verdict.DEMOTED:
                blocker = self.blocker
                because = "no supporting evidence" if blocker is None else f"{blocker.name}: {blocker.detail}"
                lines.append(f"  Claim rejected — current state is {self.derived.name}. Blocked at {because}")
        return "\n".join(lines)


def _check_conclusion(observation: PullRequestObservation, name: str) -> tuple[bool, str]:
    """Report whether one exact-head check succeeded, and why not when it did not.

    Anything other than a completed success -- missing, still running, failed,
    cancelled -- leaves the state unestablished.
    """

    head = observation.head_sha[:10]
    run = readiness.latest_check(list(observation.check_runs), name)
    if run is None:
        return False, f"{name} has not reported on head {head}"
    if run.get("status") != "completed":
        return False, f"{name} is still running on head {head}"
    conclusion = str(run.get("conclusion") or "")
    if conclusion == "success":
        return True, f"{name} succeeded on head {head}"
    return False, f"{name}={conclusion or 'no conclusion'} on head {head}"


def _implemented(observation: PullRequestObservation | None, local: LocalEvidence) -> StateFinding:
    state = WorkflowState.IMPLEMENTED
    if observation is not None and observation.is_open:
        if observation.changed_files > 0:
            return StateFinding(
                state, True, GITHUB, f"PR #{observation.number} changes {observation.changed_files} file(s)"
            )
        return StateFinding(state, False, GITHUB, f"PR #{observation.number} contains no changed files")
    if local.changed_files > 0:
        return StateFinding(state, True, LOCAL, f"{local.changed_files} locally changed file(s), no PR open yet")
    return StateFinding(state, False, NONE, "no changed files reported and no open PR to read")


def _from_canonical_preflight(observation: PullRequestObservation, state: WorkflowState, subject: str) -> StateFinding:
    succeeded, detail = _check_conclusion(observation, CANONICAL_PREFLIGHT_CHECK)
    return StateFinding(state, succeeded, GITHUB, f"{subject}: {detail}" if succeeded else detail)


def _tested(observation: PullRequestObservation | None, local: LocalEvidence) -> StateFinding:
    state = WorkflowState.TESTED
    if observation is not None and observation.is_open:
        # The canonical preflight contains the Pytest gate, so a green exact-head
        # run is itself the test evidence.
        return _from_canonical_preflight(observation, state, "test gate ran inside the canonical preflight")
    if local.pytest_passed:
        return StateFinding(state, True, LOCAL, "local test run reported passing, no PR open yet")
    return StateFinding(state, False, NONE, "no passing test run reported and no open PR to read")


def _preflight_passed(observation: PullRequestObservation | None, local: LocalEvidence) -> StateFinding:
    state = WorkflowState.PREFLIGHT_PASSED
    if observation is not None and observation.is_open:
        return _from_canonical_preflight(observation, state, "canonical preflight")
    if local.preflight_passed:
        return StateFinding(state, True, LOCAL, "local canonical preflight reported passing, no PR open yet")
    return StateFinding(state, False, NONE, "no passing canonical preflight reported and no open PR to read")


def _pr_open(observation: PullRequestObservation | None) -> StateFinding:
    state = WorkflowState.PR_OPEN
    if observation is None:
        return StateFinding(state, False, NONE, "no pull request exists")
    if not observation.is_open:
        return StateFinding(state, False, GITHUB, f"PR #{observation.number} is not open")
    if observation.base_ref != "main":
        return StateFinding(
            state, False, GITHUB, f"PR #{observation.number} targets {observation.base_ref!r}, not 'main'"
        )
    draft_note = " (Draft)" if observation.draft else ""
    return StateFinding(state, True, GITHUB, f"PR #{observation.number} is open against main{draft_note}")


def _reviewed(observation: PullRequestObservation | None, review_required: bool) -> StateFinding:
    state = WorkflowState.REVIEWED
    if observation is None or not observation.is_open:
        return StateFinding(state, False, NONE, "no open pull request to carry a review")

    independent = tuple(
        review
        for review in observation.reviews
        if review.author and review.author != observation.author and review.state.upper() != "PENDING"
    )
    if independent:
        # Real review evidence always decides, whatever was declared about the
        # requirement: a waiver may excuse a missing review, never hide one.
        reviewers = ", ".join(sorted({review.author for review in independent}))
        return StateFinding(state, True, GITHUB, f"submitted review(s) by {reviewers}")

    if not review_required:
        return StateFinding(
            state,
            True,
            DECLARED,
            "no independent review; caller declared this change does not require one under the "
            "proportional-review rule in docs/DEVELOPMENT_GOVERNANCE.md",
        )

    return StateFinding(state, False, GITHUB, "no submitted review by anyone other than the PR author")


def _zero_open_findings(observation: PullRequestObservation | None) -> StateFinding:
    state = WorkflowState.ZERO_OPEN_FINDINGS
    if observation is None or not observation.is_open:
        return StateFinding(state, False, NONE, "no open pull request to carry review findings")
    if observation.unresolved_review_threads:
        count = len(observation.unresolved_review_threads)
        return StateFinding(state, False, GITHUB, f"{count} unresolved review thread(s) remain")
    if observation.changes_requested:
        who = ", ".join(observation.changes_requested)
        return StateFinding(state, False, GITHUB, f"current CHANGES_REQUESTED from: {who}")
    return StateFinding(state, True, GITHUB, "no unresolved review threads and no current CHANGES_REQUESTED")


def _all_checks_green(observation: PullRequestObservation | None) -> StateFinding:
    state = WorkflowState.ALL_CHECKS_GREEN
    if observation is None or not observation.is_open:
        return StateFinding(state, False, NONE, "no open pull request whose head can carry checks")
    decision = readiness.evaluate(observation.checks_only_observation())
    if decision.state == "success":
        required = ", ".join((*readiness.REQUIRED_CHECKS, readiness.GOVERNANCE_CONTEXT))
        return StateFinding(state, True, GITHUB, f"green on head {observation.head_sha[:10]}: {required}")
    return StateFinding(state, False, GITHUB, decision.description)


def _merge_ready(observation: PullRequestObservation | None) -> StateFinding:
    state = WorkflowState.MERGE_READY
    if observation is None or not observation.is_open:
        return StateFinding(state, False, NONE, "no open pull request to evaluate for merge readiness")
    decision = readiness.evaluate(observation.readiness_observation())
    return StateFinding(state, decision.state == "success", GITHUB, decision.description)


def evaluate_workflow_state(
    *,
    observation: PullRequestObservation | None = None,
    local_evidence: LocalEvidence | None = None,
    claimed: WorkflowState | None = None,
    review_required: bool = True,
) -> WorkflowStateReport:
    """Derive the workflow state current evidence supports, and judge the claim.

    The claim is never an input to the derivation. It is compared with the
    derived state afterwards, so an agent cannot advance itself by asserting a
    state it has not reached.

    `review_required` is the one judgement this evaluator does not derive.
    `docs/DEVELOPMENT_GOVERNANCE.md` requires independent review for substantive
    changes and forbids forcing a small cleanup through the same review volume,
    and that is a judgement about changed behaviour rather than anything GitHub
    reports. It defaults to required, so the fail-closed direction is the
    default, and it can only ever excuse a *missing* review: it cannot suppress
    a real one, and it reaches no other state -- unresolved threads and current
    `CHANGES_REQUESTED` still block at ZERO_OPEN_FINDINGS.
    """

    local = local_evidence or LocalEvidence()
    findings = (
        _implemented(observation, local),
        _tested(observation, local),
        _preflight_passed(observation, local),
        _pr_open(observation),
        _reviewed(observation, review_required),
        _zero_open_findings(observation),
        _all_checks_green(observation),
        _merge_ready(observation),
    )

    derived = WorkflowState.UNVERIFIED
    for finding in findings:
        if not finding.established:
            break
        derived = finding.state

    if claimed is None:
        verdict = Verdict.UNCLAIMED
    elif claimed > derived:
        verdict = Verdict.DEMOTED
    elif claimed < derived:
        verdict = Verdict.ADVANCED
    else:
        verdict = Verdict.CONFIRMED

    return WorkflowStateReport(derived=derived, claimed=claimed, verdict=verdict, findings=findings)


def parse_state(value: str) -> WorkflowState:
    try:
        return WorkflowState[value.strip().upper()]
    except KeyError:
        names = ", ".join(state.name for state in ENFORCED_STATES)
        raise argparse.ArgumentTypeError(f"unknown workflow state {value!r}; expected one of: {names}") from None


def submitted_reviews(pr_number: int) -> tuple[Review, ...]:
    reviews: list[Review] = []
    for review in readiness.paged(f"pulls/{pr_number}/reviews"):
        if not isinstance(review, dict):
            continue
        author = str((review.get("user") or {}).get("login") or "").strip()
        state = str(review.get("state") or "").upper()
        if not author or not state:
            continue
        reviews.append(Review(author=author, state=state))
    return tuple(reviews)


def observe_pull_request(pr_number: int) -> PullRequestObservation | None:
    """Read the current GitHub state for one PR."""

    pr = readiness.request_json("GET", f"pulls/{pr_number}")
    if not isinstance(pr, dict) or not pr.get("number"):
        return None

    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
    is_open = str(pr.get("state") or "") == "open"
    if not is_open or not head_sha:
        return PullRequestObservation(
            number=int(pr["number"]),
            is_open=False,
            head_sha=head_sha,
            author=str((pr.get("user") or {}).get("login") or ""),
            base_ref=str((pr.get("base") or {}).get("ref") or ""),
        )

    return PullRequestObservation(
        number=int(pr["number"]),
        is_open=True,
        head_sha=head_sha,
        author=str((pr.get("user") or {}).get("login") or ""),
        base_ref=str((pr.get("base") or {}).get("ref") or ""),
        changed_files=int(pr.get("changed_files") or 0),
        draft=bool(pr.get("draft")),
        mergeable=pr.get("mergeable"),
        reviews=submitted_reviews(pr_number),
        unresolved_review_threads=readiness.unresolved_review_threads(pr_number),
        changes_requested=readiness.changes_requested_reviewers(pr_number),
        check_runs=tuple(readiness.all_check_runs(head_sha)),
        governance_status=readiness.latest_status(head_sha, readiness.GOVERNANCE_CONTEXT),
        shared_open_prs=tuple(number for number in readiness.open_prs_for_head(head_sha) if number != int(pr_number)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hunter_workflow_state",
        description="Derive an agent's workflow state from current GitHub state and judge its claim.",
    )
    parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="pull request number to evaluate; omit before the PR exists to evaluate local evidence only",
    )
    parser.add_argument(
        "--claim",
        type=parse_state,
        default=None,
        help="workflow state the agent claims to have reached",
    )
    parser.add_argument(
        "--changed-files",
        type=int,
        default=0,
        help="locally changed file count (used only when no open PR exists)",
    )
    parser.add_argument(
        "--local-tests-passed",
        action="store_true",
        help="agent reports a passing local test run (used only when no open PR exists)",
    )
    parser.add_argument(
        "--local-preflight-passed",
        action="store_true",
        help="agent reports a passing local canonical preflight (used only when no open PR exists)",
    )
    parser.add_argument(
        "--review-not-required",
        action="store_true",
        help=(
            "declare that this change does not require independent review under the proportional-review "
            "rule; excuses a missing review only, and never suppresses one that exists"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    observation = None
    if args.pr is not None:
        # Without --pr there is nothing to read: that is the pre-PR case, where
        # no PR number exists to supply and GitHub has nothing to say yet.
        try:
            observation = observe_pull_request(args.pr)
        except readiness.transport.GitHubUnavailable as exc:
            print(f"Workflow-state infrastructure unavailable: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Workflow-state evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    report = evaluate_workflow_state(
        observation=observation,
        local_evidence=LocalEvidence(
            changed_files=args.changed_files,
            pytest_passed=args.local_tests_passed,
            preflight_passed=args.local_preflight_passed,
        ),
        claimed=args.claim,
        review_required=not args.review_not_required,
    )

    print(json.dumps(report.as_dict(), indent=2) if args.json else report.render())
    return 0 if report.claim_upheld else 1


if __name__ == "__main__":
    raise SystemExit(main())
