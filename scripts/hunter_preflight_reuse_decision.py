"""Whether the trusted candidate lane may reuse the exact-head host proof.

Issue #580: ``Hunter / Pre-PR Preflight`` (the authoritative hosted full-proof
lane) and ``Trusted Candidate Preflight Validation`` ran the identical full
Pytest suite on the identical candidate head, one after the other. The trusted
candidate lane exists to verify changes a candidate makes to the validation
definition itself -- the files whose edits the hosted push proof cannot
self-attest. When a candidate changes none of those files, the trusted lane's
full-suite run establishes nothing the hosted exact-head proof did not.

This decision is made by trusted default-branch code, never by candidate code,
and every failure mode refuses reuse:

1. **Changed-file evidence.** The candidate's changed paths are read from the
   pull-request API by the trusted controller. Unavailable or malformed evidence
   runs the full trusted gates.
2. **Trusted definition paths.** If any changed path is a trusted validation
   definition path, the candidate is carrying a definition the hosted proof
   cannot attest, so the full trusted gates run.
3. **The hosted exact-head proof.** Otherwise the reuse adjudicator verifies that
   a completed, successful exact-head ``Hunter / Pre-PR Preflight`` push run
   exists for the same immutable head SHA, with matching content, definition and
   toolchain identity, and that this runner matches the tree's pinned toolchain.
   Reuse is refused -- and the full trusted gates run -- on any mismatch, on an
   absent or still-running proof, on a tests-first-red head, or on any evidence
   unavailability.

Refusing reuse always runs the full trusted gates. It costs time, never proof.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import hunter_ci_impact as impact
import hunter_governance_review_v2 as governance
import hunter_validation_reuse as reuse

#: Paths a candidate may not touch and still reuse the hosted exact-head proof.
#: The hosted push proof cannot attest changes to these files: the candidate's
#: own branch would execute them, so the trusted controller must re-run the
#: full gates against them. Base is the admission routing authority
#: (``PREFLIGHT_OWNED_PATHS``) plus the pytest/validation definition surfaces
#: the trusted structure guard already owns.
TRUSTED_DEFINITION_PATHS = frozenset(governance.PREFLIGHT_OWNED_PATHS) | frozenset(
    {
        ".github/workflows/ci.yml",
        ".github/workflows/hunter-trusted-preflight-upgrade.yml",
        "docs/CODE_WRITE_POLICY.json",
        "docs/VALIDATION_STAGE_CONTRACT.json",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "requirements/ci-constraints.txt",
        "scripts/hunter_github_contract.py",
        "scripts/hunter_validation_receipt.py",
        "scripts/hunter_validation_reuse.py",
        "scripts/hunter_ci_impact.py",
        "scripts/hunter_preflight_reuse_decision.py",
        "tests/conftest.py",
    }
)


TRUSTED_DEFINITION_PREFIXES = (
    "build_backend/",
    "requirements/",
    ".github/actions/",
    ".github/workflows/",
    "tests/",
)


def is_trusted_definition_path(path: str) -> bool:
    """Fail closed for candidate-controlled build/bootstrap and validation definitions."""
    return (
        path in TRUSTED_DEFINITION_PATHS
        or any(path.startswith(prefix) for prefix in TRUSTED_DEFINITION_PREFIXES)
        or path == "conftest.py"
        or path.endswith("/conftest.py")
    )


def changed_paths_between(root: Path, source_head: str, current_head: str) -> tuple[list[str] | None, str]:
    """The exact commit delta between the source proof HEAD and the current HEAD.

    The pull-request files API reports the cumulative base-to-head diff, which
    can hide an ordinary code commit behind an earlier metadata-only commit.
    Equivalence must instead classify only what changed since the source proof.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", source_head) or not re.fullmatch(r"[0-9a-f]{40}", current_head):
        return None, "source/current proof heads must be exact 40-hex commit IDs"
    try:
        completed = subprocess.run(
            ("git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", source_head, current_head, "--"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return None, f"source-to-current diff unavailable ({type(exc).__name__}: {exc})"
    if completed.returncode != 0:
        return None, f"source-to-current diff unavailable ({completed.stderr.strip() or 'git diff failed'})"
    return completed.stdout.splitlines(), ""


@dataclass(frozen=True)
class PushLaneDecision:
    """A current-HEAD-bound push-lane outcome, never a fresh full proof claim."""

    reusable: bool
    reason: str
    execution_mode: str
    proof_digest: str = ""
    binding: reuse.EquivalenceBinding | None = None


def emit(
    reusable: bool,
    reason: str,
    *,
    execution_mode: str = reuse.PROOF_MODE_FULL,
    proof_digest: str = "",
) -> None:
    print(f"[Preflight Reuse Decision] {'REUSE' if reusable else 'RUN-FRESH'}: {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"reusable={'true' if reusable else 'false'}\n")
        handle.write(f"reason={reason}\n")
        handle.write(f"execution_mode={execution_mode}\n")
        handle.write(f"proof_digest={proof_digest}\n")


def _pr_identity_still_matches(
    repository: str, token: str, pr_number: int, head_sha: str, base_sha: str
) -> tuple[bool, str]:
    """Recheck the live PR after a reuse decision, before the gate can be skipped."""
    try:
        latest = governance.request_json(repository, token, "GET", f"pulls/{pr_number}")
        if (
            not isinstance(latest, dict)
            or not isinstance(latest.get("head"), dict)
            or latest["head"].get("sha") != head_sha
        ):
            return False, "PR head changed during proof adjudication"
        latest_base = latest.get("base")
        if not isinstance(latest_base, dict) or latest_base.get("sha") != base_sha:
            return False, "PR base changed during proof adjudication"
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, f"PR head recheck unavailable ({type(exc).__name__}: {exc})"
    return True, ""


def decide_push(git_root: Path, *, head_sha: str, repository: str, token: str) -> PushLaneDecision:
    """Adjudicate push-lane cross-HEAD reuse from trusted default-branch code.

    The push workflow checks out trusted code and calls this function with a
    read-only token. Candidate code neither decides reuse nor receives that
    token. A successful result is a NEW current-HEAD-bound equivalence decision
    that references the immutable source proof; it never claims fresh execution.
    Any absent, conflicting, stale or unverifiable identity runs the full suite.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        return PushLaneDecision(False, "push head is not an exact 40-hex commit ID", reuse.PROOF_MODE_FULL)
    if not isinstance(repository, str) or repository.count("/") != 1 or not all(repository.split("/")):
        return PushLaneDecision(False, "repository identity is malformed", reuse.PROOF_MODE_FULL)

    try:
        containing = governance.request_json(repository, token, "GET", f"commits/{head_sha}/pulls")
    except Exception as exc:  # noqa: BLE001 - unavailable PR identity must run full
        return PushLaneDecision(False, f"push PR identity unavailable ({type(exc).__name__})", reuse.PROOF_MODE_FULL)
    if not isinstance(containing, list) or len(containing) != 1 or not isinstance(containing[0], dict):
        return PushLaneDecision(False, "push HEAD has no unique PR identity", reuse.PROOF_MODE_FULL)
    pr = containing[0]
    if str(pr.get("state") or "") != "open":
        return PushLaneDecision(False, "push HEAD PR is not open", reuse.PROOF_MODE_FULL)
    pr_number = pr.get("number")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        return PushLaneDecision(False, "push PR number is malformed", reuse.PROOF_MODE_FULL)
    head = pr.get("head")
    base = pr.get("base")
    if not isinstance(head, dict) or str(head.get("sha") or "") != head_sha:
        return PushLaneDecision(False, "live PR head differs from the pushed exact HEAD", reuse.PROOF_MODE_FULL)
    head_repo = head.get("repo")
    if head_repo is not None:
        if not isinstance(head_repo, dict) or str(head_repo.get("full_name") or "").lower() != repository.lower():
            return PushLaneDecision(False, "fork/non-repository PR never authorizes reuse", reuse.PROOF_MODE_FULL)
    if (
        not isinstance(base, dict)
        or not isinstance(base.get("sha"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", base["sha"])
    ):
        return PushLaneDecision(False, "push PR base identity is unavailable or malformed", reuse.PROOF_MODE_FULL)
    base_sha = base["sha"]

    source_head, problem = reuse.find_previous_proven_head(repository, token, head_sha, root=git_root)
    if not source_head:
        return PushLaneDecision(
            False, f"metadata-only candidate has no usable source proof ({problem})", reuse.PROOF_MODE_FULL
        )
    delta_paths, problem = changed_paths_between(git_root, source_head, head_sha)
    if problem:
        return PushLaneDecision(False, problem, reuse.PROOF_MODE_FULL)
    if not impact.classify_review_metadata_only(delta_paths or []):
        return PushLaneDecision(
            False, "candidate HEAD delta since the source proof is not metadata-only", reuse.PROOF_MODE_FULL
        )

    equivalent = reuse.verify_for_candidate(
        git_root,
        repository=repository,
        pr_number=pr_number,
        current_head=head_sha,
        base_sha=base_sha,
        token=token,
        source_proof_head=source_head,
    )
    if not equivalent.equivalent or equivalent.binding is None:
        return PushLaneDecision(False, f"cross-HEAD equivalence refused: {equivalent.reason}", reuse.PROOF_MODE_FULL)
    binding = equivalent.binding
    if (
        binding.current_head != head_sha
        or binding.source_proof_head != source_head
        or binding.base_sha != base_sha
        or binding.pr_number != pr_number
        or binding.repository != repository
        or not binding.proof_digest
    ):
        return PushLaneDecision(
            False, "equivalence binding does not match the pushed exact HEAD/PR", reuse.PROOF_MODE_FULL
        )
    still_matches, reason = _pr_identity_still_matches(repository, token, pr_number, head_sha, base_sha)
    if not still_matches:
        return PushLaneDecision(False, reason, reuse.PROOF_MODE_FULL)
    return PushLaneDecision(
        True,
        f"cross-HEAD proof reuse: {equivalent.reason}",
        reuse.PROOF_MODE_REUSE,
        binding.proof_digest,
        binding,
    )


def decide(
    candidate_root: Path,
    *,
    head_sha: str,
    repository: str,
    pr_number: int,
    token: str,
    fetch: reuse.Fetch | None = None,
) -> tuple[bool, str]:
    """Whether the trusted candidate lane may stand on the hosted exact-head proof."""
    try:
        current = governance.request_json(repository, token, "GET", f"pulls/{pr_number}")
        if (
            not isinstance(current, dict)
            or not isinstance(current.get("head"), dict)
            or current["head"].get("sha") != head_sha
        ):
            return False, "PR current head differs from event exact head"
        base = current.get("base")
        base_sha = base.get("sha") if isinstance(base, dict) else None
        if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            return False, "PR base identity unavailable or malformed"
        ok, changed_paths, error = governance.read_pr_changed_paths(repository, token, pr_number)
    except Exception as exc:  # noqa: BLE001 - any failure must run the full trusted gates
        return False, f"changed-file evidence is unavailable ({type(exc).__name__}: {exc})"
    if not ok or changed_paths is None:
        return False, f"changed-file evidence is unavailable ({error or 'no evidence'})"

    touched = sorted(path for path in changed_paths if is_trusted_definition_path(path))
    if touched:
        return False, f"candidate changes trusted validation definition paths: {', '.join(touched)}"

    # Issue #606: a metadata-only HEAD change (e.g. a review-request-only
    # commit) leaves the tested inputs unchanged. The metadata classification
    # applies to the source-proof-to-current commit delta, not to the cumulative
    # PR diff: an earlier metadata commit must not disguise a later code change.
    # Reuse is permitted only when the Canonical Proof Outcome Authority verifies
    # cross-HEAD equivalence and issues a NEW current-HEAD-bound decision.
    # Every missing/ambiguous identity fails closed into a fresh run.
    equivalence_refusal = ""
    if changed_paths:
        source_head, source_problem = reuse.find_previous_proven_head(repository, token, head_sha)
        if not source_head:
            return False, f"metadata-only candidate has no usable source proof ({source_problem})"
        delta_paths, problem = changed_paths_between(candidate_root, source_head, head_sha)
        if problem:
            return False, problem
        if not impact.classify_review_metadata_only(delta_paths or []):
            return False, "candidate HEAD delta since the source proof is not metadata-only"
        equivalent = reuse.verify_for_candidate(
            candidate_root,
            repository=repository,
            pr_number=pr_number,
            current_head=head_sha,
            base_sha=base_sha,
            token=token,
            source_proof_head=source_head,
        )
        if equivalent.equivalent:
            # A successful equivalence skips the trusted gate chain, so it must
            # pass exactly the same live HEAD/base recheck as ordinary reuse.
            still_matches, reason = _pr_identity_still_matches(repository, token, pr_number, head_sha, base_sha)
            if not still_matches:
                return False, reason
            return True, f"cross-HEAD proof reuse: {equivalent.reason}"
        # Cross-HEAD equivalence is an additional opportunity, not a regression:
        # a metadata-only HEAD may already have its own valid exact-head proof.
        # Fall through to the existing exact-head resolver before running fresh.
        equivalence_refusal = equivalent.reason

    decision = reuse.resolve(
        candidate_root,
        event_name="pull_request",
        head_sha=head_sha,
        repository=repository,
        token=token,
        fetch=fetch,
    )
    if decision.reusable:
        still_matches, reason = _pr_identity_still_matches(repository, token, pr_number, head_sha, base_sha)
        if not still_matches:
            return False, reason
        return True, decision.reason
    if equivalence_refusal:
        return False, f"cross-HEAD proof reuse refused (run fresh): {equivalence_refusal}"
    return False, decision.reason


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decide whether the trusted candidate lane may reuse the hosted exact-head "
            "branch preflight proof instead of re-running the identical full suite."
        )
    )
    parser.add_argument("--pr", type=int)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--push",
        action="store_true",
        help="Adjudicate the push lane from trusted default-branch code; the PR is resolved from the exact HEAD.",
    )
    parser.add_argument(
        "--candidate-root",
        type=Path,
        required=True,
        help="The exact candidate checkout the hosted exact-head proof validated.",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        if args.push:
            decision = decide_push(
                args.candidate_root,
                head_sha=args.head_sha,
                repository=args.repository,
                token=token,
            )
            emit(
                decision.reusable,
                decision.reason,
                execution_mode=decision.execution_mode,
                proof_digest=decision.proof_digest,
            )
            return 0
        if not isinstance(args.pr, int) or args.pr <= 0:
            print("[Preflight Reuse Decision] FAIL: --pr must be a positive integer", flush=True)
            return 2
        reusable, reason = decide(
            args.candidate_root,
            head_sha=args.head_sha,
            repository=args.repository,
            pr_number=args.pr,
            token=token,
        )
    except Exception as exc:  # noqa: BLE001 - reuse is optional; full gates are the fail-closed path
        reusable, reason = False, f"reuse decision failed ({type(exc).__name__}: {exc})"
        if args.push:
            emit(False, reason, execution_mode=reuse.PROOF_MODE_FULL)
            return 0
    emit(reusable, reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
