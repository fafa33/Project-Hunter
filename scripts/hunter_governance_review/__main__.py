"""Command-line entry point for the Hunter Governance Review gate.

Flow (per ``docs/HUNTER_GOVERNANCE_REVIEW.md``):

    Resolve exact source HEAD and target BASE
        -> Deterministic Governance Engine
        -> LLM Architecture Audit (only when deterministic validation passes)
        -> Decision Engine
        -> re-verify the review pair at decision time
        -> publish the required status check "Hunter Governance Review"

Usage::

    python -m hunter_governance_review --pr <number> [--repository owner/repo]
        [--root <repository-path>] [--dry-run]

Environment:
    GITHUB_TOKEN               required; repository-scoped token used by ``gh``
    GITHUB_REPOSITORY          owner/repo (used when ``--repository`` is omitted)
    GITHUB_RUN_ID              workflow run id recorded in the review pair
    GITHUB_SERVER_URL          server base used for the status target URL
    GITHUB_STEP_SUMMARY        path to append a summary to (GitHub Actions)
    HUNTER_LLM_API_KEY         LLM API key (fallbacks: GROQ_API_KEY, OPENAI_API_KEY)
    HUNTER_LLM_BASE_URL        OpenAI-compatible base URL (default: Groq)
    HUNTER_LLM_MODEL           model id (default: llama-3.3-70b-versatile)
    HUNTER_GOVERNANCE_PROTECTED_BRANCHES  comma-separated protected branches (default: main)

Exit codes:
    0  review completed and status published (or the gate is not required for
       a PR targeting a non-protected branch)
    2  could not resolve the PR, or a required environment value is missing
    3  the status check could not be published
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from hunter_governance_review.contracts import (
    CHECK_CONTEXT,
    ChangedFile,
    DeterministicResult,
    Finding,
    Outcome,
    PullRequest,
    ReviewPair,
    outcome_to_check_state,
    utc_now_iso,
)
from hunter_governance_review.decision import Decision, decide
from hunter_governance_review.deterministic import ValidationContext, run_deterministic_engine
from hunter_governance_review.github_api import GhCliRunner, GitHubError, GitHubRunner, truncate_diff
from hunter_governance_review.llm_audit import AuditVerdict, LLMAuditError, run_llm_audit

# Coarse memory/sanity bound only -- guards against holding a pathologically
# large diff in memory before it ever reaches the audit prompt builder. It is
# deliberately far larger than any realistic PR diff and is NOT the real
# per-request token bound: that bound is computed in
# ``hunter_governance_review.llm_audit.build_audit_prompt`` from the full
# assembled prompt against the pinned model's actual provider rate limit --
# see the module-level comment there for why (PR #200's own live installation
# run was rejected by Groq for exceeding its tokens-per-minute limit with the
# prior, disconnected 150,000-character cap).
DIFF_LIMIT = 5_000_000


class LLMRunner(Protocol):
    def __call__(
        self,
        env: Mapping[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        files: list[ChangedFile],
        diff: str,
        deterministic_findings: list[Finding],
        timeout: int = 120,
    ) -> AuditVerdict: ...


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hunter-governance-review",
        description="Run the Hunter Governance Review merge gate for a pull request.",
    )
    parser.add_argument("--pr", required=True, type=int, help="Pull request number to review.")
    parser.add_argument(
        "--repository",
        default=None,
        help="owner/repo. Defaults to GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Repository root used for evidence lookups. Defaults to the current directory.",
    )
    parser.add_argument(
        "--protected-branches",
        default=None,
        help="Comma-separated protected target branches. Defaults to main or " "HUNTER_GOVERNANCE_PROTECTED_BRANCHES.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and decide without publishing a status check.",
    )
    return parser.parse_args(argv)


def _protected_branches(raw: str | None) -> tuple[str, ...]:
    value = (raw or "").strip()
    if not value:
        return ("main",)
    return tuple(branch.strip() for branch in value.split(",") if branch.strip())


def build_status_description(decision: Decision, pair: ReviewPair) -> str:
    """Build a <=140-character status description for the GitHub statuses API."""
    head = pair.source_head_sha[:7]
    base = pair.target_base_sha[:7]
    if decision.outcome is Outcome.APPROVED:
        text = (
            f"Approved for head {head} on base {base}: deterministic validation and "
            "hostile architecture audit passed."
        )
    elif decision.outcome is Outcome.CHANGES_REQUIRED:
        text = f"Changes required (head {head} on base {base}): {decision.reason}"
    else:
        text = f"Review failed (head {head} on base {base}): {decision.reason} " "No verdict produced; merge blocked."
    return text[:140]


def _write_summary(
    env: Mapping[str, str],
    *,
    decision: Decision,
    pair: ReviewPair,
    deterministic: DeterministicResult,
    published_state: str,
) -> None:
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## Hunter Governance Review",
        "",
        f"- **Outcome**: `{decision.outcome.value}`",
        f"- **Reviewed head**: `{pair.source_head_sha}` (`{pair.source_branch}`)",
        f"- **Reviewed base**: `{pair.target_base_sha}` (`{pair.target_branch}`)",
        f"- **Workflow run**: `{pair.workflow_run_id}`",
        f"- **Published check**: `{CHECK_CONTEXT}` -> `{published_state}`",
        "",
        f"**Reason**: {decision.reason}",
    ]
    if deterministic.findings:
        lines.append("")
        lines.append("### Deterministic findings")
        lines.extend(f"- {finding.render()}" for finding in deterministic.findings)
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run_review(
    *,
    args: argparse.Namespace,
    env: Mapping[str, str],
    gh: GitHubRunner,
    llm_runner: LLMRunner,
) -> int:
    """Execute the full gate for one pull request and publish the status check."""
    repository = args.repository or env.get("GITHUB_REPOSITORY") or gh.repository
    if not repository:
        print("::error::no repository: pass --repository or set GITHUB_REPOSITORY.")
        return 2
    protected = _protected_branches(args.protected_branches or env.get("HUNTER_GOVERNANCE_PROTECTED_BRANCHES"))
    run_id = env.get("GITHUB_RUN_ID") or "local"
    root = Path(args.root).resolve() if args.root else Path.cwd()

    try:
        pr = gh.get_pull_request(args.pr)
    except GitHubError as exc:
        print(f"::error::could not resolve pull request #{args.pr}: {exc}")
        return 2

    if pr.base_ref_name not in protected:
        print(
            f"::notice::PR #{pr.number} targets {pr.base_ref_name!r}, which is not a protected "
            f"branch ({', '.join(sorted(protected))}); the Hunter Governance Review gate is not "
            "required for this pull request. No status check was published."
        )
        return 0

    pair = ReviewPair(
        repository=repository,
        pull_request_number=pr.number,
        source_branch=pr.head_ref_name,
        source_head_sha=pr.head_oid,
        target_branch=pr.base_ref_name,
        target_base_sha=pr.base_oid,
        workflow_run_id=run_id,
        review_timestamp=utc_now_iso(),
    )
    print(f"[ReviewPair] {pair.describe()}")

    evidence_error: str | None = None
    files: list[ChangedFile] = []
    diff = ""
    try:
        files = gh.get_pull_files(pr.number)
        diff = truncate_diff(gh.get_pull_diff(pr.number), DIFF_LIMIT)
    except GitHubError as exc:
        evidence_error = f"missing required repository evidence: {exc}"

    deterministic = DeterministicResult()
    validator_error: str | None = None
    if evidence_error is None:
        try:
            deterministic = run_deterministic_engine(ValidationContext(pr=pr, files=files, repository_root=root))
        except Exception as exc:  # internal validator exception -> REVIEW_FAILED
            validator_error = f"internal validator exception: {exc!r}"

    # Re-resolve the PR at decision time so the approval can only ever apply
    # to the exact current source HEAD and target BASE.
    current: PullRequest | None = None
    try:
        current = gh.get_pull_request(pr.number)
    except GitHubError as exc:
        print(f"::warning::could not re-resolve PR #{pr.number} at decision time: {exc}")
    pair_fresh = False
    pair_fresh_error: str | None = None
    if current is None:
        pair_fresh_error = f"could not re-resolve PR #{pr.number} to verify the review pair at decision time"
    elif current.head_oid != pair.source_head_sha or current.base_oid != pair.target_base_sha:
        pair_fresh_error = (
            "stale source or target pair: the PR advanced while the review was running "
            f"(head {pair.source_head_sha[:12]} -> {current.head_oid[:12]}, "
            f"base {pair.target_base_sha[:12]} -> {current.base_oid[:12]}); the review is "
            "invalid for the current head/base and must be re-run."
        )
    else:
        pair_fresh = True

    audit: AuditVerdict | None = None
    audit_error: str | None = None
    # The LLM is consulted only when the review can still matter: evidence is
    # available, deterministic validation passed, and the pair is still fresh.
    if evidence_error is None and validator_error is None and not deterministic.blocking and pair_fresh:
        try:
            audit = llm_runner(
                env,
                pair=pair,
                pr=pr,
                files=files,
                diff=diff,
                deterministic_findings=deterministic.findings,
            )
        except LLMAuditError as exc:
            audit_error = str(exc)

    if evidence_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, evidence_error)
    elif validator_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, validator_error)
    else:
        decision = decide(
            deterministic=deterministic,
            audit=audit,
            audit_error=audit_error,
            pair_fresh=pair_fresh,
            pair_fresh_error=pair_fresh_error,
        )

    target_sha = current.head_oid if current is not None else pair.source_head_sha
    state = outcome_to_check_state(decision.outcome)
    description = build_status_description(decision, pair)
    target_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/actions/runs/{run_id}"

    print(f"[Outcome] {decision.outcome.value}")
    print(f"[Reason] {decision.reason}")
    for finding in deterministic.findings:
        print(f"[Finding] {finding.render()}")
    print(f"[StatusCheck] context={CHECK_CONTEXT!r} state={state.value} on {target_sha[:12]}")

    if not args.dry_run:
        try:
            gh.post_commit_status(
                sha=target_sha,
                state=state.value,
                context=CHECK_CONTEXT,
                description=description,
                target_url=target_url,
            )
        except GitHubError as exc:
            print(f"::error::could not publish the {CHECK_CONTEXT} status check: {exc}")
            return 3

    _write_summary(
        env,
        decision=decision,
        pair=pair,
        deterministic=deterministic,
        published_state=state.value,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    env = dict(os.environ)
    repository = args.repository or env.get("GITHUB_REPOSITORY")
    if not repository:
        print("::error::no repository: pass --repository or set GITHUB_REPOSITORY.")
        return 2
    token = env.get("GITHUB_TOKEN")
    if not token and not args.dry_run:
        print("::error::GITHUB_TOKEN is not set; the gate cannot publish its status check.")
        return 2
    gh = GhCliRunner(repository, token=token)
    return run_review(args=args, env=env, gh=gh, llm_runner=run_llm_audit)


if __name__ == "__main__":
    raise SystemExit(main())
