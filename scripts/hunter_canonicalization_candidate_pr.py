#!/usr/bin/env python3
"""Turn a computed canonicalization delta into one Draft PR, over local_git_push.

``docs/superpowers/specs/2026-09-23-live-dpm-knowledge-feedback-loop-design.md``
("Follow-on: closing the loop from candidate to canonical registry") states the
gap this closes precisely: ``materialize_learning_ledger`` -- the one function
with write authority over ``docs/DEFECT_REGISTRY.json`` -- "had no caller...
nothing ever turned a rendered candidate back into a change a human could
review and merge through the normal PR path." That design deliberately keeps
``materialize_learning_ledger`` free of commit/push/PR authority; this script
supplies exactly that one missing step and nothing else.

It composes the existing, unmodified ``build_learning_ledger`` and
``materialize_learning_ledger`` authorities (no new registry, persistence, or
replay semantics -- see ``scripts/hunter_canonicalize_learning.py``, which this
script calls the same way a human would) and then carries a real change
through ``local_git_push``: the same channel -- gated only by
``.githooks/pre-push`` and a verified signature from
``docs/CODE_WRITE_POLICY.json``'s ``authorized_signers`` -- every human
contribution to this repository already uses. It never touches ``main``
directly, never marks a PR ready, and never merges. Human review and Merge
Readiness apply to the resulting PR exactly like any other.

This is deliberately NOT a scheduled/unattended trigger. The design doc's own
"Requirement-by-requirement" table records that an unattended Claude Code
Remote Routine was built, fired, and found blocked by a platform limitation
(fresh/persistent sessions are provisioned without repository or GitHub
access), and that granting an always-on CI workflow ``contents: write`` over
``docs/DEFECT_REGISTRY.json`` would require an owner-authorized
``connector_write_ingress.governance_maintenance_authorizations`` grant for the
``defect-registry`` scope -- something a contribution must never self-issue
per that policy's own self-escalation boundary. This script instead assumes an
interactive or already-repo-attached caller with real git push access (a
person, or an agent session already working in this repository), which is the
channel the design doc identifies as requiring no new authorization at all.

Safety properties, all verified by the accompanying regression tests:

- writes only the existing governed knowledge surfaces ``docs/DEFECT_REGISTRY.json`` and ``docs/REVIEWER_FINDING_DISPOSITIONS.json``, inside a disposable worktree --
  the caller's own checkout and working tree are never touched;
- commits and pushes only to one fixed, dedicated branch
  (``canonicalization/defect-registry-auto``); ``main`` is never checked out
  for a commit and never pushed to;
- accumulates rather than clobbers: the worktree is built on top of the
  dedicated branch's own tip when it still carries an unmerged candidate, and
  only rebuilt fresh from ``main`` on the first run or once that candidate has
  already landed on ``main`` (detected with ``git merge-base
  --is-ancestor``). A second finding processed while the first finding's PR
  is still open therefore adds to it instead of silently overwriting it;
- opens or updates at most one Draft PR for that branch; never marks it
  ready and never merges (no command in this module can);
- idempotent: unchanged observations reproduce byte-identical registry
  content, which this script detects by comparing tree state before pushing,
  so a repeat run is a clean no-op rather than a duplicate PR or empty churn;
- race-safe between two invocations of this script: the push uses
  ``--force-with-lease`` bound to the tip this run actually fetched, so a
  second, concurrently-running invocation that moved the branch first makes
  this run's push fail closed instead of silently clobbering it. This branch
  is machine-owned and always rebuilt fresh from ``main`` plus the supplied
  observations -- it is not a merge of whatever the branch previously held,
  so it is not a safe place for a human to commit unrelated manual edits;
  the lease is a race guard between automated runs, not a promise to
  preserve content this design does not treat as an input.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import hunter_pre_ready_review as pre_ready

from hunter.evidence_intelligence import controlled_learning_integration as learning
from hunter.evidence_intelligence.incremental_knowledge_learning import (
    LearningLedgerError,
    build_learning_ledger,
)

_PREFIX = "[Hunter Canonicalization Candidate PR]"
DEDICATED_BRANCH = "canonicalization/defect-registry-auto"
ACTIVE_BRANCH_PREFIX = "fix/hunter-canonicalization-"
BASE_BRANCH = "main"
REGISTRY_RELATIVE_PATH = "docs/DEFECT_REGISTRY.json"
DISPOSITIONS_RELATIVE_PATH = "docs/REVIEWER_FINDING_DISPOSITIONS.json"
PR_TITLE = "chore(dpm): automatic defect-registry canonicalization"
# Owner-authored mission governing the canonicalization candidate lifecycle.
GOVERNING_ISSUE = "532"

#: Injectable command runner so tests never touch a real git remote or `gh`.
RunCommand = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class CanonicalizationCandidatePrError(RuntimeError):
    """Raised when a canonicalization candidate cannot be safely proposed."""


def _bound_writer_identity() -> tuple[str, str]:
    """The authorization-bound writer identity canonicalization commits must carry.

    Canonicalization content is recorded through the owner's ``local_git_push`` channel, whose
    ambient git identity must not be trusted: an unbound name/email pair on the runner is correctly
    refused by the canonical pre-push hook, which only admits ``docs/CODE_WRITE_POLICY.json``
    ``writer_identity_binding`` identities. Resolve the bound owner identity from the policy instead
    and fail closed if it is not declared, so an unbound commit can never be produced.
    """

    import hunter_writer_provenance as provenance

    binding, error = provenance.load_binding()
    if binding is None:
        raise CanonicalizationCandidatePrError(f"cannot resolve the authorization-bound writer identity: {error}")
    writer = binding.identity_for("fafa33")
    if writer is None:
        raise CanonicalizationCandidatePrError(
            "CODE_WRITE_POLICY writer_identity_binding does not bind the owner login 'fafa33'; "
            "refusing to record a canonicalization commit under an unbound identity"
        )
    return writer.canonical_name, writer.canonical_email


@dataclass(frozen=True)
class CanonicalizationPlan:
    """The pure, git-free decision of what this run would do."""

    changed: bool
    integrated_proposal_ids: tuple[str, ...]
    skipped_items: int
    registry_bytes: bytes


def _default_run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    # The workflow's GH_TOKEN is intentionally read-only for GraphQL lookups.
    # A Draft PR creation is a separate, owner-authorized write operation:
    # never let the read-only workflow token shadow the runner's configured
    # gh credential. If no authorized credential exists, gh fails closed.
    env = os.environ.copy()
    if tuple(command[:3]) == ("gh", "pr", "create"):
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
    return subprocess.run(  # noqa: S603 - command list is always literal/argv, never shell text
        list(command), check=True, capture_output=True, text=True, env=env
    )


def compute_plan(
    *,
    pr: int,
    head: str,
    base: str,
    observations: list[dict[str, object]],
    origin_registry_bytes: bytes,
) -> CanonicalizationPlan:
    """Compute the canonicalization delta against a supplied registry snapshot.

    Pure and git-free: no filesystem outside a private temp registry copy, no
    subprocess, no network. Reuses the same ``build_learning_ledger`` /
    ``materialize_learning_ledger`` authorities the manual CLI uses, so the
    decision here is byte-identical to what a human running
    ``scripts/hunter_canonicalize_learning.py`` locally would get for the same
    observations and the same base registry snapshot.
    """

    with tempfile.TemporaryDirectory(prefix="hunter-canonicalization-plan-") as scratch:
        scratch_path = Path(scratch)
        registry_path = scratch_path / "DEFECT_REGISTRY.json"
        ledger_path = scratch_path / "hunter-learning-ledger.json"
        registry_path.write_bytes(origin_registry_bytes)

        # The ledger must be built against the exact same registry snapshot it
        # is later replayed against (`registry_path`, holding
        # `origin_registry_bytes`) -- never against whatever
        # `learning.CANONICAL_DEFECT_REGISTRY` happens to point at on disk
        # right now. Building it against a different snapshot than the one
        # `materialize_learning_ledger` replays against would bind the
        # proposal's `registry_digest` to the wrong content and fail closed
        # with "stale registry snapshot" the moment the two diverge (as they
        # do once `origin_registry_bytes` is an accumulated candidate rather
        # than the pristine on-disk registry).
        try:
            ledger = build_learning_ledger(pr, head, base, observations, registry_path)
        except LearningLedgerError as exc:
            raise CanonicalizationCandidatePrError(f"observations rejected: {exc}") from exc
        ledger_path.write_text(json.dumps(ledger, sort_keys=True, indent=2), encoding="utf-8")

        original_registry = learning.CANONICAL_DEFECT_REGISTRY
        original_ledger = learning.CANONICAL_LEARNING_LEDGER
        learning.CANONICAL_DEFECT_REGISTRY = registry_path
        learning.CANONICAL_LEARNING_LEDGER = ledger_path
        try:
            result = learning.materialize_learning_ledger(dry_run=False)
        except learning.ControlledLearningIntegrationError as exc:
            raise CanonicalizationCandidatePrError(f"canonicalization rejected: {exc}") from exc
        finally:
            learning.CANONICAL_DEFECT_REGISTRY = original_registry
            learning.CANONICAL_LEARNING_LEDGER = original_ledger

        final_bytes = registry_path.read_bytes() if result.changed else origin_registry_bytes
        return CanonicalizationPlan(
            changed=result.changed,
            integrated_proposal_ids=result.integrated_proposal_ids,
            skipped_items=result.skipped_items,
            registry_bytes=final_bytes,
        )


def capture_reviewer_findings(
    *, observations: list[dict[str, object]], origin_bytes: bytes
) -> tuple[bytes, tuple[str, ...]]:
    """Append authenticated review observations as unvalidated durable findings.

    Capture is deliberately weaker than validation: seeing an authority-review
    finding is enough to make it impossible to disappear, but never enough to
    call it confirmed/resolved.  Validation/canonicalization remains governed by
    the existing replayable evidence path. Stable provider event identity makes
    duplicate review events a byte-identical no-op.

    The captured record also carries the finding's own substantive content
    (message, and path/line when the observation has them) under
    ``finding_evidence``. Provenance alone (reviewer/PR/event id/source SHA) is
    not enough: the transient lifecycle artifact that also holds this content
    expires after 90 days, and the source GitHub comment can itself be deleted
    (Codex P1, PR #530) -- without this, the durable RFD record would outlive
    every copy of what the finding actually said.
    """
    try:
        document = json.loads(origin_bytes)
    except json.JSONDecodeError as exc:
        raise CanonicalizationCandidatePrError("reviewer disposition registry is unreadable") from exc
    findings = document.get("findings") if isinstance(document, dict) else None
    if document.get("version") != 1 or not isinstance(findings, list):
        raise CanonicalizationCandidatePrError("reviewer disposition registry is malformed")
    existing = {str(item.get("id")) for item in findings if isinstance(item, dict)}
    added: list[str] = []
    for observation in observations:
        if observation.get("source") != "github-review" or observation.get("provider") != "github-review":
            continue
        event_id = observation.get("event_id")
        # Only inline comments are findings; overview reviews are not.
        if not isinstance(event_id, str) or not event_id.startswith("review-comment-"):
            continue
        reviewer = observation.get("reviewer")
        source_pr = observation.get("source_pr")
        message = observation.get("message")
        if (
            not isinstance(event_id, str)
            or not event_id.strip()
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or type(source_pr) is not int
            or not isinstance(message, str)
            or not message.strip()
        ):
            raise CanonicalizationCandidatePrError("review finding capture evidence is malformed")
        finding_id = f"RFD-AUTO-{source_pr}-{event_id}"
        if finding_id in existing:
            continue
        source_head = observation.get("source_event_head_sha") or observation.get("reviewed_head_sha")
        finding_evidence: dict[str, object] = {"message": message}
        path = observation.get("path")
        if path is not None:
            finding_evidence["path"] = path
        line = observation.get("line")
        if line is not None:
            finding_evidence["line"] = line
        findings.append(
            {
                "id": finding_id,
                "source_provenance": {
                    "reviewer": reviewer,
                    "pr_number": source_pr,
                    "reference": f"GitHub authority review event {event_id}; source_head={source_head}",
                },
                "finding_evidence": finding_evidence,
                "validation_state": "unvalidated",
            }
        )
        existing.add(finding_id)
        added.append(finding_id)
    rendered = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
    return rendered, tuple(added)


def _run(run: RunCommand, command: Sequence[str], *, what: str) -> str:
    try:
        completed = run(command)
    except subprocess.CalledProcessError as exc:
        raise CanonicalizationCandidatePrError(f"{what} failed: {exc.stderr or exc}") from exc
    return completed.stdout


def _write_governed_review_request(run: RunCommand, worktree: Path, main_sha: str) -> None:
    """Bind the request to the GitHub Issue, never to inherited branch JSON."""
    raw = _run(
        run,
        ["gh", "issue", "view", GOVERNING_ISSUE, "--json", "body", "--jq", ".body"],
        what="read governing Issue acceptance criteria",
    )
    criteria = pre_ready.parse_issue_acceptance_criteria(raw)
    if not criteria:
        raise CanonicalizationCandidatePrError(
            f"governing Issue #{GOVERNING_ISSUE} has no verifiable acceptance criteria"
        )
    head = _run(run, ["git", "-C", str(worktree), "rev-parse", "HEAD"], what="resolve candidate head").strip()
    inherited = worktree / pre_ready.REVIEW_RELATIVE_PATH
    # Existing review requests are not evidence of this candidate's mission.
    previous = json.loads(inherited.read_text()) if inherited.exists() else {}
    dimensions = (previous.get("claims") or {}).get("adversarial_dimensions", [])
    dimensions = sorted(set(dimensions) | set(pre_ready.REQUIRED_ADVERSARIAL_DIMENSIONS))
    changes = pre_ready.local_changes(main_sha, head, cwd=worktree)
    families, error = pre_ready.load_families(worktree / pre_ready.REGISTRY_RELATIVE_PATH)
    if error:
        raise CanonicalizationCandidatePrError(error)
    applicable = pre_ready.applicable_family_ids(families, tuple(change.path for change in changes))
    records = [
        {
            "id": f"AC-{index:02d}",
            "criterion": criterion,
            "verdict": (
                "satisfied"
                if "automatic finding-to-canonical-knowledge lifecycle" in criterion.lower()
                else "not-applicable"
            ),
            "evidence": (
                "The candidate branch contains the governed canonical registry delta; "
                "external review and merge remain separate gates."
                if "automatic finding-to-canonical-knowledge lifecycle" in criterion.lower()
                else "This candidate does not establish the broader mission criterion; it remains open under Issue #532."
            ),
        }
        for index, criterion in enumerate(criteria, 1)
    ]
    if not any(item["verdict"] == "satisfied" for item in records):
        raise CanonicalizationCandidatePrError("no governing Issue criterion applies to canonicalization")
    judgement = {
        "acceptance_criteria": records,
        "defect_families": [
            {
                "family": family,
                "outcome": "clear",
                "evidence": "Requested independent review; no reviewer approval asserted.",
            }
            for family in applicable
        ],
        "findings": [],
        "adversarial_dimensions": dimensions,
    }
    try:
        pre_ready.finalize_writer_review_request(
            issue=GOVERNING_ISSUE,
            base=main_sha,
            head=head,
            base_ref=BASE_BRANCH,
            judgement=judgement,
            cwd=worktree,
            issue_criteria=criteria,
        )
    except ValueError as exc:
        raise CanonicalizationCandidatePrError(str(exc)) from exc


def _recoverable_candidate_delta(run: RunCommand, root: str, main_sha: str, previous_sha: str) -> bool:
    """Reject stale squash-merged branches and non-knowledge orphan recovery.

    An ancestry test alone cannot identify a squash merge: the old tip is
    unreachable even when all its knowledge changes landed on main.
    """
    merge_base = _run(
        run,
        ["git", "-C", root, "merge-base", main_sha, previous_sha],
        what="resolve candidate fork point",
    ).strip()
    introduced = set(
        _run(
            run,
            ["git", "-C", root, "diff", "--name-only", merge_base, previous_sha],
            what="inspect candidate-introduced paths",
        ).splitlines()
    )
    knowledge_paths = {REGISTRY_RELATIVE_PATH, DISPOSITIONS_RELATIVE_PATH}
    allowed = knowledge_paths | {pre_ready.REVIEW_RELATIVE_PATH}
    unexpected = introduced - allowed
    if unexpected:
        raise CanonicalizationCandidatePrError(
            "orphan canonicalization branch contains non-knowledge changes: " + ", ".join(sorted(unexpected))
        )
    # Compare *contents* with current main independently: the old candidate
    # may be unreachable after a squash merge but carry no new knowledge.
    remaining = set(
        _run(
            run,
            ["git", "-C", root, "diff", "--name-only", main_sha, previous_sha, "--", *sorted(knowledge_paths)],
            what="verify remaining candidate knowledge content",
        ).splitlines()
    )
    return bool(introduced & knowledge_paths and remaining)


def _remote_branch_tree(run: RunCommand, worktree: Path, sha: str) -> str:
    output = _run(run, ["git", "-C", str(worktree), "rev-parse", f"{sha}^{{tree}}"], what="resolve remote tree")
    return output.strip()


def _is_ancestor(run: RunCommand, root: str, ancestor_sha: str, descendant_sha: str) -> bool:
    """True when ``ancestor_sha`` is reachable from ``descendant_sha``.

    Decides whether the dedicated branch's previous tip has already been
    fully incorporated into ``main`` (a stale branch nothing depends on
    anymore, safe to rebuild fresh) or still carries an unmerged candidate
    proposal (must be built ON TOP of, never replaced -- otherwise a second
    finding processed before the first one's PR merges would silently
    overwrite the first finding's proposal instead of accumulating both,
    which is exactly the "concurrent processing must not lose evidence"
    property this script has to hold). Exit code 1 from
    ``git merge-base --is-ancestor`` means "not an ancestor" and is an
    expected outcome, not a failure; any other nonzero exit is a real error.
    """

    try:
        run(["git", "-C", root, "merge-base", "--is-ancestor", ancestor_sha, descendant_sha])
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return False
        raise CanonicalizationCandidatePrError(f"ancestor check failed: {exc.stderr or exc}") from exc
    return True


def _open_candidate_pr(run: RunCommand, *, pr: int, extra_body: str, branch: str = DEDICATED_BRANCH) -> str:
    body = (
        "Automatic canonicalization candidate, opened by "
        "`scripts/hunter_canonicalization_candidate_pr.py` per "
        "`docs/superpowers/specs/2026-09-23-live-dpm-knowledge-feedback-loop-design.md`.\n\n"
        f"{extra_body}\n"
        f"- source PR: #{pr}\n\n"
        "This PR only ever touches the governed knowledge surfaces `docs/DEFECT_REGISTRY.json` and "
        "`docs/REVIEWER_FINDING_DISPOSITIONS.json`; canonical family changes carry the unmodified output "
        "of `materialize_learning_ledger`, while raw authority-review findings are captured as unvalidated evidence. "
        "It never self-merges; normal Hunter governance/review and human merge approval apply."
    )
    return _run(
        run,
        [
            "gh",
            "pr",
            "create",
            "--base",
            BASE_BRANCH,
            "--head",
            branch,
            "--draft",
            "--title",
            PR_TITLE,
            "--body",
            body,
        ],
        what="open candidate PR",
    )


def _source_pr_is_merged(run: RunCommand, pr: int) -> bool:
    """Return true only after the source PR has actually merged.

    Review events may collect evidence while work is still changing, but they
    must never create follow-on work behind an open source PR.
    """
    output = _run(
        run,
        ["gh", "pr", "view", str(pr), "--json", "state,mergedAt", "--jq", '.state + " " + (.mergedAt // "")'],
        what="check source PR lifecycle",
    )
    state, _, merged_at = output.strip().partition(" ")
    return state == "MERGED" and bool(merged_at)


def _merged_candidate_tip(run: RunCommand, branch: str, previous_sha: str) -> bool:
    """A squash-merged historical branch is not an orphan to resurrect.

    Require GitHub's merged PR evidence bound to the exact remote branch tip;
    a similarly named or older merged PR cannot excuse a newer unmerged tip.
    """
    output = _run(
        run,
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "merged",
            "--limit",
            "100",
            "--json",
            "headRefOid",
            "--jq",
            ".[].headRefOid",
        ],
        what="check merged candidate branch identity",
    )
    return previous_sha in output.splitlines()


def _existing_open_pr_number(run: RunCommand, branch: str) -> int | None:
    # Do not trust unqualified --head: a fork can use the same branch name.
    output = _run(
        run,
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,headRefName,isCrossRepository,headRepositoryOwner,baseRefName",
            "--jq",
            '.[] | select(.headRefName == "'
            + branch
            + '" and .isCrossRepository == false and .headRepositoryOwner.login == "fafa33" and .baseRefName == "main") | .number',
        ],
        what="check trusted same-repository candidate PR",
    )
    numbers = output.splitlines()
    if len(numbers) > 1:
        raise CanonicalizationCandidatePrError("ambiguous owner candidate PR")
    return int(numbers[0]) if numbers else None


def _remote_generation_sha(run: RunCommand, root: str, repo: str, branch: str) -> str:
    output = _run(
        run, ["git", "-C", root, "ls-remote", "--heads", repo, branch], what="resolve remote candidate generation"
    )
    rows = output.splitlines()
    if len(rows) != 1 or not rows[0].endswith("refs/heads/" + branch):
        raise CanonicalizationCandidatePrError("ambiguous candidate remote ref")
    return rows[0].split()[0]


def _candidate_remote_generations(run: RunCommand, root: str, repo: str) -> list[str]:
    output = _run(
        run,
        ["git", "-C", root, "ls-remote", "--heads", repo, ACTIVE_BRANCH_PREFIX + "*"],
        what="discover existing owner-repository candidate branches",
    )
    return sorted(
        {
            line.split("refs/heads/", 1)[1]
            for line in output.splitlines()
            if "refs/heads/" in line and line.split("refs/heads/", 1)[1].startswith(ACTIVE_BRANCH_PREFIX)
        }
    )


def propose(
    *,
    pr: int,
    head: str,
    base: str,
    observations: list[dict[str, object]],
    repo: str,
    repo_root: Path | None = None,
    run: RunCommand = _default_run,
) -> str:
    """Compute the canonicalization delta and, if any, propose it as one Draft PR.

    Every git/gh operation happens inside a disposable worktree built fresh
    from the current ``origin/main`` tip; the caller's own checkout is never
    touched, and neither ``main`` nor any path other than
    ``docs/DEFECT_REGISTRY.json`` is ever written by this function.

    ``repo_root`` is the repository whose object store/worktree list is used
    (defaults to the current working directory, matching this repository's
    other repo-root-assuming scripts). Every git invocation is explicitly
    bound to it with ``-C`` rather than relying on process cwd, so a caller
    (or a test) can point this at an isolated checkout without disturbing
    whatever repository the calling process happens to be sitting in.
    """

    root = str(repo_root) if repo_root is not None else "."

    # Anti-PR-sprawl invariant: review activity on work that is still open may
    # collect/upload evidence, but it must not create branches, commits, pushes,
    # or follow-on PRs. Canonicalization is continuation work and starts only
    # after the source PR has actually merged.
    if not _source_pr_is_merged(run, pr):
        return f"{_PREFIX} DEFERRED: source PR #{pr} is not merged; evidence only, no follow-on work created."

    with tempfile.TemporaryDirectory(prefix="hunter-canonicalization-worktree-") as scratch:
        worktree = Path(scratch) / "worktree"
        _run(run, ["git", "-C", root, "fetch", repo, BASE_BRANCH], what="fetch base branch")
        main_sha = _run(run, ["git", "-C", root, "rev-parse", "FETCH_HEAD"], what="resolve base branch tip").strip()

        # A single active generation is shared by all source PRs. After merge,
        # the next generation is named from the new main tip. Unlike the old
        # fixed branch, no generation ever requires a non-fast-forward push.
        remote_generations = _candidate_remote_generations(run, root, repo)
        active = [name for name in remote_generations if _existing_open_pr_number(run, name) is not None]
        orphans = [
            name
            for name in remote_generations
            if name not in active
            and not _merged_candidate_tip(run, name, _remote_generation_sha(run, root, repo, name))
        ]
        if len(active) > 1 or len(orphans) > 1 or (active and orphans):
            raise CanonicalizationCandidatePrError("ambiguous canonicalization generations; refusing new proposal")
        branch = (active or orphans or [ACTIVE_BRANCH_PREFIX + main_sha[:16]])[0]

        # Best-effort: the dedicated branch may not exist yet (first run ever).
        # Fetching it (rather than only `ls-remote`) also pulls its tree/blob
        # objects into this repository's shared object store, which the
        # disposable worktree created below needs in order to resolve
        # `<previous_sha>^{tree}` locally for the idempotency check.
        previous_sha: str | None = None
        try:
            _run(run, ["git", "-C", root, "fetch", repo, branch], what="fetch dedicated branch")
            previous_sha = _run(
                run, ["git", "-C", root, "rev-parse", "FETCH_HEAD"], what="resolve dedicated branch tip"
            ).strip()
        except CanonicalizationCandidatePrError:
            previous_sha = None

        # A merged PR can leave a squash-unreachable branch with historical
        # implementation changes. Never treat that exact merged tip as a live
        # orphan or use it as the next proposal base. Preserve its SHA for the
        # later compare-and-swap push lease.
        historical_merged_tip = (
            previous_sha is not None
            and _existing_open_pr_number(run, branch) is None
            and _merged_candidate_tip(run, branch, previous_sha)
        )

        # Accumulate onto the dedicated branch's own tip when it still carries
        # an unmerged candidate (not yet an ancestor of main); rebuild fresh
        # from main only for the first run ever, or once the branch's prior
        # content has already landed on main. Basing this unconditionally on
        # `main_sha` would let a second finding silently overwrite a first,
        # still-unmerged finding's proposal on the same branch/PR.
        base_sha = main_sha
        if (
            previous_sha is not None
            and not historical_merged_tip
            and not _is_ancestor(run, root, previous_sha, main_sha)
        ):
            # A squash-merged candidate is not a Git ancestor of main. Only
            # accumulate a branch that still carries actual knowledge changes.
            if _recoverable_candidate_delta(run, root, main_sha, previous_sha):
                base_sha = previous_sha

        _run(
            run,
            ["git", "-C", root, "worktree", "add", "--detach", str(worktree), base_sha],
            what="create disposable worktree",
        )
        try:
            registry_path = worktree / REGISTRY_RELATIVE_PATH
            dispositions_path = worktree / DISPOSITIONS_RELATIVE_PATH
            captured_bytes, captured_ids = capture_reviewer_findings(
                observations=observations, origin_bytes=dispositions_path.read_bytes()
            )
            dispositions_changed = captured_bytes != dispositions_path.read_bytes()
            plan = compute_plan(
                pr=pr,
                head=head,
                base=base,
                observations=observations,
                origin_registry_bytes=registry_path.read_bytes(),
            )
            # Raw review lifecycle evidence is preserved by the workflow artifact.
            # It is not, by itself, justification for repository churn. A canonical
            # PR requires at least one replayable proposal that changes the registry.
            if not plan.changed and not dispositions_changed:
                # Codex P1 (PR #530): if a *prior* run's push succeeded but it
                # then crashed, or its own `gh` call failed, before opening a
                # PR, the dedicated branch already carries real unmerged
                # content with nothing representing it. Every later run would
                # otherwise keep computing "no new content" against that same
                # content and return NO-OP forever, silently orphaning the
                # branch. Reconcile it here: if the branch is real, unmerged,
                # and has no open PR, open one for it -- no new commit needed.
                if (
                    previous_sha is not None
                    and not historical_merged_tip
                    and not _is_ancestor(run, root, previous_sha, main_sha)
                    and _recoverable_candidate_delta(run, root, main_sha, previous_sha)
                ):
                    if _existing_open_pr_number(run, branch) is None:
                        create_output = _open_candidate_pr(
                            run,
                            pr=pr,
                            branch=branch,
                            extra_body=(
                                "Reconciled: this branch already carried this exact content from a prior "
                                "run that pushed but never opened a PR for it (for example a transient "
                                "`gh` failure). No new commit was made; this call only supplies the "
                                "missing PR.\n"
                            ),
                        )
                        return f"{_PREFIX} RECONCILED: opened the missing PR for already-pushed content: {create_output.strip()}"
                return f"{_PREFIX} NO-OP: observations produced no registry change; nothing proposed."

            if not plan.changed:
                # Preserve the same crash-recovery guarantee even when this run
                # captured only raw lifecycle evidence. A disposition delta is
                # intentionally not committed, but it must not suppress recovery
                # of a previously pushed canonical branch that still lacks a PR.
                if (
                    previous_sha is not None
                    and not historical_merged_tip
                    and not _is_ancestor(run, root, previous_sha, main_sha)
                    and _recoverable_candidate_delta(run, root, main_sha, previous_sha)
                ):
                    if _existing_open_pr_number(run, branch) is None:
                        create_output = _open_candidate_pr(
                            run,
                            pr=pr,
                            branch=branch,
                            extra_body=(
                                "Reconciled: this branch already carried canonical content from a prior "
                                "run that pushed but never opened a PR. Raw lifecycle evidence from this "
                                "run remains workflow-only; no new commit was made.\n"
                            ),
                        )
                        return f"{_PREFIX} RECONCILED: opened the missing PR for already-pushed content: {create_output.strip()}"
                return f"{_PREFIX} NO-OP: no canonical proposal; lifecycle evidence remains in the workflow artifact."

            if plan.changed:
                registry_path.write_bytes(plan.registry_bytes)
            if dispositions_changed:
                dispositions_path.write_bytes(captured_bytes)
            _run(run, ["git", "-C", str(worktree), "checkout", "-B", branch], what="create dedicated branch")
            _run(
                run,
                ["git", "-C", str(worktree), "add", "--", REGISTRY_RELATIVE_PATH, DISPOSITIONS_RELATIVE_PATH],
                what="stage knowledge candidate",
            )
            message = (
                f"chore(dpm): canonicalize {len(plan.integrated_proposal_ids)} proposal(s) from PR #{pr}\n\n"
                f"integrated_proposal_ids={list(plan.integrated_proposal_ids)}\n"
                f"skipped_items={plan.skipped_items}\n"
                f"captured_finding_ids={list(captured_ids)}\n"
                f"source_head={head}\n"
                "Generated by scripts/hunter_canonicalization_candidate_pr.py; carries "
                "materialize_learning_ledger's own atomic, replay-bound output unchanged."
            )
            canonical_name, canonical_email = _bound_writer_identity()
            _run(
                run,
                [
                    "env",
                    "-u",
                    "GIT_AUTHOR_NAME",
                    "-u",
                    "GIT_AUTHOR_EMAIL",
                    "-u",
                    "GIT_COMMITTER_NAME",
                    "-u",
                    "GIT_COMMITTER_EMAIL",
                    "-u",
                    "EMAIL",
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    f"user.name={canonical_name}",
                    "-c",
                    f"user.email={canonical_email}",
                    "commit",
                    "-m",
                    message,
                ],
                what="commit registry candidate",
            )

            # The first commit fixes the reviewed content; the second binds its
            # request to that content without claiming external review authority.
            _write_governed_review_request(run, worktree, main_sha)
            _run(
                run,
                ["git", "-C", str(worktree), "add", "--", pre_ready.REVIEW_RELATIVE_PATH],
                what="stage governed review request",
            )
            _run(
                run,
                [
                    "env",
                    "-u",
                    "GIT_AUTHOR_NAME",
                    "-u",
                    "GIT_AUTHOR_EMAIL",
                    "-u",
                    "GIT_COMMITTER_NAME",
                    "-u",
                    "GIT_COMMITTER_EMAIL",
                    "-u",
                    "EMAIL",
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    f"user.name={canonical_name}",
                    "-c",
                    f"user.email={canonical_email}",
                    "commit",
                    "-m",
                    "chore(review): bind canonicalization request to governing Issue",
                ],
                what="commit governed review request",
            )

            # A review request is committed *after* the reviewed content. Verify
            # the final commit's actual base-to-HEAD diff, not just its parent.
            # Otherwise a valid-looking request can be pushed with stale claims
            # and the trusted Reconcile fails only after PR creation.
            final_head = _run(
                run,
                ["git", "-C", str(worktree), "rev-parse", "HEAD"],
                what="resolve final candidate head",
            ).strip()
            final_document = json.loads((worktree / pre_ready.REVIEW_RELATIVE_PATH).read_text())
            final_changes = pre_ready.local_changes(main_sha, final_head, cwd=worktree)
            final_families, final_error = pre_ready.load_families(worktree / pre_ready.REGISTRY_RELATIVE_PATH)
            if final_error:
                raise CanonicalizationCandidatePrError(final_error)
            final_verdict = pre_ready.verify_review_request(
                final_document,
                base_sha=main_sha,
                changes=final_changes,
                families=final_families,
                issue_criteria=pre_ready.parse_issue_acceptance_criteria(
                    _run(
                        run,
                        ["gh", "issue", "view", GOVERNING_ISSUE, "--json", "body", "--jq", ".body"],
                        what="verify final governing Issue criteria",
                    )
                ),
                head_sha=final_head,
            )
            if not final_verdict.ok:
                raise CanonicalizationCandidatePrError(
                    "final committed review request does not match candidate HEAD: " + final_verdict.reason
                )

            new_tree = _run(
                run, ["git", "-C", str(worktree), "rev-parse", "HEAD^{tree}"], what="resolve candidate tree"
            ).strip()
            if previous_sha is not None and _remote_branch_tree(run, worktree, previous_sha) == new_tree:
                return (
                    f"{_PREFIX} NO-OP: dedicated branch already carries this exact canonicalization "
                    f"({previous_sha[:10]})."
                )

            # An empty expected value after the colon means "this ref must not
            # already exist on the remote" -- the correct lease for the very
            # first push of the dedicated branch, distinct from omitting a
            # value entirely (which git resolves from a local remote-tracking
            # ref that this disposable worktree never fetched and does not
            # have).
            _run(
                run,
                [
                    "git",
                    "-C",
                    str(worktree),
                    "push",
                    repo,
                    f"HEAD:refs/heads/{branch}",
                ],
                what="push dedicated branch",
            )

            existing = _existing_open_pr_number(run, branch)
            if existing is not None:
                return f"{_PREFIX} UPDATED: pushed new canonicalization content to existing PR #{existing}."

            create_output = _open_candidate_pr(
                run,
                pr=pr,
                branch=branch,
                extra_body=(
                    f"- integrated proposal ids: {list(plan.integrated_proposal_ids)}\n"
                    f"- skipped items: {plan.skipped_items}\n"
                ),
            )
            return f"{_PREFIX} OPENED: {create_output.strip()}"
        finally:
            shutil.rmtree(worktree, ignore_errors=True)
            _run(run, ["git", "-C", root, "worktree", "prune"], what="prune disposable worktree")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--repo", default="origin", help="git remote to fetch/push (default: origin)")
    args = parser.parse_args(argv)

    try:
        raw = json.loads(args.observations.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{_PREFIX} FAIL: observations input is unreadable: {exc}")
        return 2
    if not isinstance(raw, list):
        print(f"{_PREFIX} FAIL: observations must be a JSON list")
        return 2

    try:
        message = propose(pr=args.pr, head=args.head, base=args.base, observations=raw, repo=args.repo)
    except CanonicalizationCandidatePrError as exc:
        print(f"{_PREFIX} FAIL: {exc}")
        return 1

    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
