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

- writes only ``docs/DEFECT_REGISTRY.json``, inside a disposable worktree --
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
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hunter.evidence_intelligence import controlled_learning_integration as learning
from hunter.evidence_intelligence.incremental_knowledge_learning import (
    LearningLedgerError,
    build_learning_ledger,
)

_PREFIX = "[Hunter Canonicalization Candidate PR]"
DEDICATED_BRANCH = "canonicalization/defect-registry-auto"
BASE_BRANCH = "main"
REGISTRY_RELATIVE_PATH = "docs/DEFECT_REGISTRY.json"
PR_TITLE = "chore(dpm): automatic defect-registry canonicalization"

#: Injectable command runner so tests never touch a real git remote or `gh`.
RunCommand = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class CanonicalizationCandidatePrError(RuntimeError):
    """Raised when a canonicalization candidate cannot be safely proposed."""


@dataclass(frozen=True)
class CanonicalizationPlan:
    """The pure, git-free decision of what this run would do."""

    changed: bool
    integrated_proposal_ids: tuple[str, ...]
    skipped_items: int
    registry_bytes: bytes


def _default_run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - command list is always literal/argv, never shell text
        list(command), check=True, capture_output=True, text=True
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


def _run(run: RunCommand, command: Sequence[str], *, what: str) -> str:
    try:
        completed = run(command)
    except subprocess.CalledProcessError as exc:
        raise CanonicalizationCandidatePrError(f"{what} failed: {exc.stderr or exc}") from exc
    return completed.stdout


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


def _open_candidate_pr(run: RunCommand, *, pr: int, extra_body: str) -> str:
    body = (
        "Automatic canonicalization candidate, opened by "
        "`scripts/hunter_canonicalization_candidate_pr.py` per "
        "`docs/superpowers/specs/2026-09-23-live-dpm-knowledge-feedback-loop-design.md`.\n\n"
        f"{extra_body}\n"
        f"- source PR: #{pr}\n\n"
        "This PR only ever touches `docs/DEFECT_REGISTRY.json` and carries the unmodified output "
        "of `materialize_learning_ledger`. It never self-merges; normal Hunter governance/review "
        "and human merge approval apply."
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
            DEDICATED_BRANCH,
            "--draft",
            "--title",
            PR_TITLE,
            "--body",
            body,
        ],
        what="open candidate PR",
    )


def _existing_open_pr_number(run: RunCommand, branch: str) -> int | None:
    output = _run(
        run,
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number"],
        what="check existing candidate PR",
    )
    stripped = output.strip()
    return int(stripped) if stripped else None


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

    with tempfile.TemporaryDirectory(prefix="hunter-canonicalization-worktree-") as scratch:
        worktree = Path(scratch) / "worktree"
        _run(run, ["git", "-C", root, "fetch", repo, BASE_BRANCH], what="fetch base branch")
        main_sha = _run(run, ["git", "-C", root, "rev-parse", "FETCH_HEAD"], what="resolve base branch tip").strip()

        # Best-effort: the dedicated branch may not exist yet (first run ever).
        # Fetching it (rather than only `ls-remote`) also pulls its tree/blob
        # objects into this repository's shared object store, which the
        # disposable worktree created below needs in order to resolve
        # `<previous_sha>^{tree}` locally for the idempotency check.
        previous_sha: str | None = None
        try:
            _run(run, ["git", "-C", root, "fetch", repo, DEDICATED_BRANCH], what="fetch dedicated branch")
            previous_sha = _run(
                run, ["git", "-C", root, "rev-parse", "FETCH_HEAD"], what="resolve dedicated branch tip"
            ).strip()
        except CanonicalizationCandidatePrError:
            previous_sha = None

        # Accumulate onto the dedicated branch's own tip when it still carries
        # an unmerged candidate (not yet an ancestor of main); rebuild fresh
        # from main only for the first run ever, or once the branch's prior
        # content has already landed on main. Basing this unconditionally on
        # `main_sha` would let a second finding silently overwrite a first,
        # still-unmerged finding's proposal on the same branch/PR.
        base_sha = main_sha
        if previous_sha is not None and not _is_ancestor(run, root, previous_sha, main_sha):
            base_sha = previous_sha

        _run(
            run,
            ["git", "-C", root, "worktree", "add", "--detach", str(worktree), base_sha],
            what="create disposable worktree",
        )
        try:
            registry_path = worktree / REGISTRY_RELATIVE_PATH
            plan = compute_plan(
                pr=pr,
                head=head,
                base=base,
                observations=observations,
                origin_registry_bytes=registry_path.read_bytes(),
            )
            if not plan.changed:
                # Codex P1 (PR #530): if a *prior* run's push succeeded but it
                # then crashed, or its own `gh` call failed, before opening a
                # PR, the dedicated branch already carries real unmerged
                # content with nothing representing it. Every later run would
                # otherwise keep computing "no new content" against that same
                # content and return NO-OP forever, silently orphaning the
                # branch. Reconcile it here: if the branch is real, unmerged,
                # and has no open PR, open one for it -- no new commit needed.
                if previous_sha is not None and not _is_ancestor(run, root, previous_sha, main_sha):
                    if _existing_open_pr_number(run, DEDICATED_BRANCH) is None:
                        create_output = _open_candidate_pr(
                            run,
                            pr=pr,
                            extra_body=(
                                "Reconciled: this branch already carried this exact content from a prior "
                                "run that pushed but never opened a PR for it (for example a transient "
                                "`gh` failure). No new commit was made; this call only supplies the "
                                "missing PR.\n"
                            ),
                        )
                        return f"{_PREFIX} RECONCILED: opened the missing PR for already-pushed content: {create_output.strip()}"
                return f"{_PREFIX} NO-OP: observations produced no registry change; nothing proposed."

            registry_path.write_bytes(plan.registry_bytes)
            _run(run, ["git", "-C", str(worktree), "checkout", "-B", DEDICATED_BRANCH], what="create dedicated branch")
            _run(
                run,
                ["git", "-C", str(worktree), "add", "--", REGISTRY_RELATIVE_PATH],
                what="stage registry candidate",
            )
            message = (
                f"chore(dpm): canonicalize {len(plan.integrated_proposal_ids)} proposal(s) from PR #{pr}\n\n"
                f"integrated_proposal_ids={list(plan.integrated_proposal_ids)}\n"
                f"skipped_items={plan.skipped_items}\n"
                f"source_head={head}\n"
                "Generated by scripts/hunter_canonicalization_candidate_pr.py; carries "
                "materialize_learning_ledger's own atomic, replay-bound output unchanged."
            )
            _run(run, ["git", "-C", str(worktree), "commit", "-m", message], what="commit registry candidate")

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
            lease = f"{DEDICATED_BRANCH}:{previous_sha or ''}"
            _run(
                run,
                [
                    "git",
                    "-C",
                    str(worktree),
                    "push",
                    "--force-with-lease=" + lease,
                    repo,
                    f"HEAD:refs/heads/{DEDICATED_BRANCH}",
                ],
                what="push dedicated branch",
            )

            existing = _existing_open_pr_number(run, DEDICATED_BRANCH)
            if existing is not None:
                return f"{_PREFIX} UPDATED: pushed new canonicalization content to existing PR #{existing}."

            create_output = _open_candidate_pr(
                run,
                pr=pr,
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
