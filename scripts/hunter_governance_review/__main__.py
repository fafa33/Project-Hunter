"""Command-line entry point for the Hunter Governance Review gate.

Flow (per ``docs/HUNTER_GOVERNANCE_REVIEW.md``):

    Resolve exact source HEAD and target BASE
        -> Authoritative Context Resolution (exact-base-SHA canonical docs/ADRs)
        -> Deterministic Governance Engine (using resolved ADR/ADPR references)
        -> Decision Engine
        -> re-verify the review pair immediately before publishing
        -> publish the required status check "Hunter Governance Review"

This gate is entirely deterministic, repository-native, and CI-native: it
never calls an external LLM or any other network service besides GitHub
itself, requires no API key or provider secret, and no external provider's
availability, quota, billing state, or API error can ever affect its
verdict. The only two outcomes that ever block a merge are a genuine
deterministic governance violation, or required repository evidence
(canonical documents, referenced ADR/ADPR records) that could not be
confirmed to exist at the exact base commit.

Usage::

    python -m hunter_governance_review --pr <number> [--repository owner/repo]
        [--root <repository-path>] [--dry-run]

Environment:
    GITHUB_TOKEN               required; repository-scoped token used by ``gh``
    GITHUB_REPOSITORY          owner/repo (used when ``--repository`` is omitted)
    GITHUB_RUN_ID               workflow run id recorded in the review pair
    GITHUB_SERVER_URL          server base used for the status target URL
    GITHUB_STEP_SUMMARY        path to append a summary to (GitHub Actions)
    HUNTER_GOVERNANCE_PROTECTED_BRANCHES  comma-separated protected branches (default: main)

Exit codes:
    0  review completed and status published (or the gate is not required for
       a PR targeting a non-protected branch)
    2  could not resolve the PR, or a required environment value is missing
    3  the status check could not be published (permanent failure)
    4  the semantic outcome was produced but GitHub is unavailable and the
       status check could not be published after bounded retries; the verdict
       is reported as "published: unavailable", never as a semantic failure
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping

from hunter_governance_revision import (
    GovernanceInputs,
    conflicting_from_graphql,
    governance_revision,
    render_marker,
)

from hunter_governance_review.context import ContextResolutionError, resolve_context
from hunter_governance_review.contracts import (
    CHECK_CONTEXT,
    ChangedFile,
    ContextManifest,
    DeterministicResult,
    Outcome,
    PullRequest,
    ReviewPair,
    outcome_to_check_state,
    utc_now_iso,
)
from hunter_governance_review.decision import Decision, decide
from hunter_governance_review.deterministic import ValidationContext, run_deterministic_engine
from hunter_governance_review.github_api import GhCliRunner, GitHubError, GitHubRunner, GitHubUnavailable


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
        help="Unused by the review pipeline itself (context/ADR resolution is exact-SHA, API-based); "
        "accepted for backward-compatible invocation only.",
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


def build_status_description(decision: Decision, pair: ReviewPair, revision: str) -> str:
    """Build a <=140-character status description for the GitHub statuses API.

    The identity marker is written first so that GitHub's 140-character limit
    can only truncate the human-readable reason. A consumer of this status
    (Hunter Merge Readiness) needs the marker to prove *which* pull request this
    verdict evaluated and *which* governance-relevant state it evaluated; it
    needs no part of the prose, so the prose is what yields under truncation.
    """
    head = pair.source_head_sha[:7]
    base = pair.target_base_sha[:7]
    if decision.outcome is Outcome.APPROVED:
        text = f"Approved for head {head} on base {base}: deterministic governance validation passed."
    elif decision.outcome is Outcome.CHANGES_REQUIRED:
        text = f"Changes required (head {head} on base {base}): {decision.reason}"
    else:
        text = f"Review failed (head {head} on base {base}): {decision.reason} " "No verdict produced; merge blocked."
    marker = render_marker(pair.pull_request_number, revision) + " "
    return (marker + text)[:140]


def _write_summary(
    env: Mapping[str, str],
    *,
    decision: Decision,
    pair: ReviewPair,
    deterministic: DeterministicResult,
    context: ContextManifest | None,
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
        lines.extend(
            [
                "",
                "### Deterministic finding metadata",
                "",
                "```json",
                json.dumps(deterministic.to_dict(), sort_keys=True, indent=2),
                "```",
            ]
        )
    if context is not None:
        lines.append("")
        lines.append("### Authoritative context coverage manifest")
        for entry in context.entries:
            mark = "mandatory" if entry.mandatory else "referenced"
            provenance = " proposed-by-PR-HEAD" if entry.provenance == "head" else ""
            lines.append(
                f"- `{entry.path}`@`{entry.ref[:12]}` ({mark}, {entry.status}{provenance}) "
                f"sha256={entry.sha256[:12] or 'n/a'} bytes={entry.byte_length}"
            )
        if context.missing_references:
            lines.append("- **Missing referenced records**: " + ", ".join(context.missing_references))
    body = "\n".join(lines) + "\n"
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(body)


def _resolve_pair_freshness(
    gh: GitHubRunner, pr_number: int, pair: ReviewPair
) -> tuple[PullRequest | None, bool, str | None]:
    """Re-resolve the PR and compare against ``pair``. Returns (current, fresh, error).

    Raises :class:`GitHubUnavailable` when GitHub infrastructure itself is
    unavailable, so the caller can distinguish an acquisition outage from a
    semantic verdict instead of misreading it as a failed review.
    """
    try:
        current = gh.get_pull_request(pr_number)
    except GitHubUnavailable:
        raise
    except GitHubError as exc:
        return None, False, f"could not re-resolve PR #{pr_number} to verify the review pair: {exc}"
    if current.head_oid != pair.source_head_sha or current.base_oid != pair.target_base_sha:
        return (
            current,
            False,
            "stale source or target pair: the PR advanced while the review was running "
            f"(head {pair.source_head_sha[:12]} -> {current.head_oid[:12]}, "
            f"base {pair.target_base_sha[:12]} -> {current.base_oid[:12]}); the review is "
            "invalid for the current head/base and must be re-run.",
        )
    return current, True, None


def run_review(
    *,
    args: argparse.Namespace,
    env: Mapping[str, str],
    gh: GitHubRunner,
) -> int:
    """Execute the full gate for one pull request and publish the status check."""
    repository = args.repository or env.get("GITHUB_REPOSITORY") or gh.repository
    if not repository:
        print("::error::no repository: pass --repository or set GITHUB_REPOSITORY.")
        return 2
    protected = _protected_branches(args.protected_branches or env.get("HUNTER_GOVERNANCE_PROTECTED_BRANCHES"))
    run_id = env.get("GITHUB_RUN_ID") or "local"

    try:
        pr = gh.get_pull_request(args.pr)
    except GitHubUnavailable as exc:
        print(f"::error::could not resolve pull request #{args.pr}: GitHub unavailable: {exc}")
        return 4
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
    try:
        files = gh.get_pull_files(pr.number)
    except GitHubUnavailable as exc:
        print(f"::error::could not retrieve changed files for PR #{pr.number}: GitHub unavailable: {exc}")
        return 4
    except GitHubError as exc:
        evidence_error = f"missing required repository evidence: {exc}"

    context_manifest: ContextManifest | None = None
    context_error: str | None = None
    if evidence_error is None:
        try:
            context_manifest = resolve_context(
                gh,
                base_sha=pair.target_base_sha,
                pr_body=pr.body,
                head_sha=pair.source_head_sha,
                changed_paths=frozenset(f.filename for f in files),
            )
        except GitHubUnavailable as exc:
            print(f"::error::GitHub unavailable while resolving authoritative governance context: {exc}")
            return 4
        except ContextResolutionError as exc:
            context_error = f"authoritative governance context could not be resolved: {exc}"
        except GitHubError as exc:
            context_error = f"could not retrieve authoritative governance context: {exc}"

    deterministic = DeterministicResult()
    validator_error: str | None = None
    if evidence_error is None and context_error is None:
        assert context_manifest is not None
        try:
            deterministic = run_deterministic_engine(
                ValidationContext(
                    pr=pr,
                    files=files,
                    missing_references=context_manifest.missing_references,
                )
            )
        except Exception as exc:  # internal validator exception -> REVIEW_FAILED
            validator_error = f"internal validator exception: {exc!r}"

    current, pair_fresh, pair_fresh_error = (None, True, None)
    try:
        current, pair_fresh, pair_fresh_error = _resolve_pair_freshness(gh, pr.number, pair)
    except GitHubUnavailable as exc:
        print(f"::error::GitHub unavailable while verifying the review pair for PR #{pr.number}: {exc}")
        return 4

    if evidence_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, evidence_error)
    elif context_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, context_error)
    elif validator_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, validator_error)
    else:
        decision = decide(
            deterministic=deterministic,
            pair_fresh=pair_fresh,
            pair_fresh_error=pair_fresh_error,
        )

    target_sha = current.head_oid if current is not None else pair.source_head_sha
    state = outcome_to_check_state(decision.outcome)
    revision = governance_revision(
        GovernanceInputs(
            pull_request_number=pr.number,
            head_sha=pair.source_head_sha,
            base_sha=pair.target_base_sha,
            base_ref=pr.base_ref_name,
            title=pr.title,
            body=pr.body,
            draft=pr.draft,
            conflicting=conflicting_from_graphql(pr.mergeable),
            changed_paths=tuple(f.filename for f in files),
        )
    )
    description = build_status_description(decision, pair, revision)
    target_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/" f"{repository}/actions/runs/{run_id}"

    print(f"[Outcome] {decision.outcome.value}")
    print(f"[Reason] {decision.reason}")
    for finding in deterministic.findings:
        print(f"[Finding] {finding.render()}")
    if deterministic.classification_errors:
        print(
            "[FindingMetadata] incomplete blocking classifications: " + ", ".join(deterministic.classification_errors)
        )
    if context_manifest is not None:
        for entry in context_manifest.entries:
            print(
                f"[Context] {entry.path}@{entry.ref[:12]} status={entry.status} "
                f"provenance={entry.provenance} sha256={entry.sha256[:12] or 'n/a'}"
            )
    print(f"[Revision] governance revision {revision} for PR #{pr.number}")
    print(f"[StatusCheck] context={CHECK_CONTEXT!r} state={state.value} " f"on {target_sha[:12]}")

    if not args.dry_run:
        try:
            gh.post_commit_status(
                sha=target_sha,
                state=state.value,
                context=CHECK_CONTEXT,
                description=description,
                target_url=target_url,
            )
        except GitHubUnavailable as exc:
            print(
                f"::error::semantic outcome is {decision.outcome.value} but the {CHECK_CONTEXT} "
                f"status check could not be published because GitHub is unavailable: {exc}"
            )
            _write_summary(
                env,
                decision=decision,
                pair=pair,
                deterministic=deterministic,
                context=context_manifest,
                published_state="unavailable",
            )
            return 4
        except GitHubError as exc:
            print(f"::error::could not publish the {CHECK_CONTEXT} status check: {exc}")
            return 3

    _write_summary(
        env,
        decision=decision,
        pair=pair,
        deterministic=deterministic,
        context=context_manifest,
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
    return run_review(args=args, env=env, gh=gh)


if __name__ == "__main__":
    raise SystemExit(main())
