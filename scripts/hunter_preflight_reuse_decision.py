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
from collections.abc import Sequence
from pathlib import Path

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
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "requirements/ci-constraints.txt",
        "scripts/hunter_validation_receipt.py",
        "scripts/hunter_validation_reuse.py",
        "scripts/hunter_ci_impact.py",
        "scripts/hunter_preflight_reuse_decision.py",
        "tests/conftest.py",
    }
)


def emit(reusable: bool, reason: str) -> None:
    print(f"[Preflight Reuse Decision] {'REUSE' if reusable else 'RUN-FRESH'}: {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"reusable={'true' if reusable else 'false'}\n")
        handle.write(f"reason={reason}\n")


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
        ok, changed_paths, error = governance.read_pr_changed_paths(repository, token, pr_number)
    except Exception as exc:  # noqa: BLE001 - any failure must run the full trusted gates
        return False, f"changed-file evidence is unavailable ({type(exc).__name__}: {exc})"
    if not ok or changed_paths is None:
        return False, f"changed-file evidence is unavailable ({error or 'no evidence'})"

    touched = sorted(path for path in changed_paths if path in TRUSTED_DEFINITION_PATHS)
    if touched:
        return False, f"candidate changes trusted validation definition paths: {', '.join(touched)}"

    decision = reuse.resolve(
        candidate_root,
        event_name="pull_request",
        head_sha=head_sha,
        repository=repository,
        token=token,
        fetch=fetch,
    )
    return decision.reusable, decision.reason


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decide whether the trusted candidate lane may reuse the hosted exact-head "
            "branch preflight proof instead of re-running the identical full suite."
        )
    )
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument(
        "--candidate-root",
        type=Path,
        required=True,
        help="The exact candidate checkout the hosted exact-head proof validated.",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        reusable, reason = decide(
            args.candidate_root,
            head_sha=args.head_sha,
            repository=args.repository,
            pr_number=args.pr,
            token=token,
        )
    except Exception as exc:  # noqa: BLE001 - reuse is optional; full gates are the fail-closed path
        reusable, reason = False, f"reuse decision failed ({type(exc).__name__}: {exc})"
    emit(reusable, reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
