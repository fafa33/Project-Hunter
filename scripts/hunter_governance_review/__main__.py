"""Command-line entry point for the Hunter Governance Review gate.

Flow (per ``docs/HUNTER_GOVERNANCE_REVIEW.md``):

    Resolve exact source HEAD and target BASE
        -> Authoritative Context Resolution (exact-base-SHA canonical docs/ADRs)
        -> Deterministic Governance Engine (using resolved ADR/ADPR references)
        -> Deterministic diff chunking (complete coverage, no truncation)
        -> LLM Architecture Audit, one call per chunk (extracts structured
           architectural evidence, not just a prose summary)
        -> Aggregation across every chunk (fail-closed on any missing/failed chunk)
        -> Cross-chunk consistency synthesis (one call, reasoning over the
           structured architectural evidence, not just summaries)
        -> Full, lossless authoritative-document review (every mandatory
           document chunked and reviewed in full -- never a bounded excerpt
           alone -- against the pull request's structured evidence)
        -> Decision Engine
        -> re-verify the review pair immediately before publishing (post-audit freshness)
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
    HUNTER_GOVERNANCE_PROTECTED_BRANCHES  comma-separated protected branches (default: main)

    Provider configuration (see llm_audit.py's ``_resolve_providers``):
    at least one of the three provider slots below must be fully configured
    (no default vendor, endpoint, or model -- every value is explicit):
        HUNTER_LLM_API_KEY / HUNTER_LLM_BASE_URL / HUNTER_LLM_MODEL       (slot 1, tried first)
        HUNTER_LLM_API_KEY_2 / HUNTER_LLM_BASE_URL_2 / HUNTER_LLM_MODEL_2 (slot 2, tried second)
        HUNTER_LLM_API_KEY_3 / HUNTER_LLM_BASE_URL_3 / HUNTER_LLM_MODEL_3 (slot 3, tried third)
    HUNTER_LLM_PROMPT_TOKEN_BUDGET      optional, tunes the per-request prompt budget
    HUNTER_LLM_MAX_COMPLETION_TOKENS    optional, tunes the completion token cap

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
from typing import Protocol

from hunter_governance_review.aggregate import (
    AggregatedDocumentReview,
    ChunkOutcome,
    aggregate_chunk_outcomes,
    aggregate_document_chunk_outcomes,
    apply_document_review,
    apply_synthesis,
    describe_chunks_for_synthesis,
    providers_used,
)
from hunter_governance_review.chunking import (
    DiffChunk,
    covered_filenames,
    split_diff_into_chunks,
    split_document_into_chunks,
)
from hunter_governance_review.context import ContextResolutionError, resolve_context
from hunter_governance_review.contracts import (
    CHECK_CONTEXT,
    ChangedFile,
    ContextManifest,
    CoverageManifest,
    DeterministicResult,
    DocumentReviewManifest,
    Finding,
    Outcome,
    PullRequest,
    ReviewPair,
    outcome_to_check_state,
    utc_now_iso,
)
from hunter_governance_review.decision import Decision, decide
from hunter_governance_review.deterministic import ValidationContext, run_deterministic_engine
from hunter_governance_review.github_api import GhCliRunner, GitHubError, GitHubRunner
from hunter_governance_review.llm_audit import (
    AuditVerdict,
    LLMAuditError,
    LLMCallResult,
    ProviderHealth,
    estimate_chunk_diff_budget,
    estimate_document_chunk_budget,
    run_document_review,
    run_llm_audit,
    run_synthesis_review,
)

# Coarse memory/sanity bound only -- guards against holding a pathologically
# large diff in memory before it ever reaches the chunker. It is deliberately
# far larger than any realistic PR diff and is NOT the real per-chunk token
# bound: that bound is computed per-review by
# ``llm_audit.estimate_chunk_diff_budget`` against the pinned model's actual
# provider rate limit -- see llm_audit.py's module-level comment for why
# (PR #200's own live installation run was rejected by Groq for exceeding
# its tokens-per-minute limit with the prior, disconnected 150,000-character
# single-request cap). Exceeding this bound fails the review closed (see
# below) -- it is never used to silently truncate the diff and continue,
# which would make coverage accounting blind to the loss (a file's content
# could be cut off after chunking already recorded that file as "covered").
DIFF_LIMIT = 5_000_000


class LLMRunner(Protocol):
    def __call__(
        self,
        env: Mapping[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        chunk: DiffChunk,
        context_brief: str,
        deterministic_findings: list[Finding],
        health: ProviderHealth,
        timeout: int = 120,
    ) -> LLMCallResult: ...


class SynthesisRunner(Protocol):
    def __call__(
        self,
        env: Mapping[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        synthesis_input: str,
        health: ProviderHealth,
        timeout: int = 120,
    ) -> LLMCallResult: ...


class DocumentReviewRunner(Protocol):
    def __call__(
        self,
        env: Mapping[str, str],
        *,
        pair: ReviewPair,
        pr: PullRequest,
        document_chunk: DiffChunk,
        architectural_evidence_summary: str,
        health: ProviderHealth,
        timeout: int = 120,
    ) -> LLMCallResult: ...


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


# GitHub rejects a single step's total $GITHUB_STEP_SUMMARY write once it
# exceeds 1024 KiB (1,048,576 bytes) -- see
# https://docs.github.com/actions/using-workflows/workflow-commands-for-github-actions#adding-a-markdown-summary.
# This budget is kept safely under that hard limit so the truncation marker
# itself (appended after the cut) never pushes the file back over.
_SUMMARY_BYTE_BUDGET = 900_000


def _dedupe_error_lines(errors: tuple[str, ...], *, label: str) -> list[str]:
    """Render a per-chunk/per-section error list compactly for the step
    summary: identical error TEXT is reported once, with every chunk/section
    reference that hit it listed alongside it, instead of repeating the full
    text per occurrence.

    This is a rendering change only -- no error text and no chunk/section
    reference is dropped, deduplication never changes which chunks/sections
    are counted as failed (``coverage``/``document_review`` manifests are
    unaffected), and the full run log still prints every occurrence
    verbatim, untouched by this function. It exists because a single
    provider/coverage failure early in a large review (e.g. every configured
    provider becoming unhealthy) typically produces hundreds of chunks whose
    error text is byte-for-byte identical, which is what actually exceeds
    GitHub's step-summary size limit -- not a large number of genuinely
    distinct errors.
    """
    if not errors:
        return []
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for raw in errors:
        # Each entry is "<chunk/section reference>: <error text>" (see
        # aggregate.py's chunk_errors/section_errors construction). Splitting
        # on only the FIRST ": " isolates the reference even though the
        # error text itself commonly contains further colons.
        ref, sep, text = raw.partition(": ")
        if not sep:
            ref, text = "?", raw
        if text not in groups:
            groups[text] = []
            order.append(text)
        groups[text].append(ref)
    lines = [f"- **{label}** ({len(errors)} total, {len(groups)} distinct error message(s)):"]
    for text in order:
        refs = groups[text]
        shown = ", ".join(refs[:20])
        more = f", +{len(refs) - 20} more" if len(refs) > 20 else ""
        lines.append(f"  - {len(refs)}x: {text}")
        lines.append(f"    affected: {shown}{more}")
    return lines


def _write_summary(
    env: Mapping[str, str],
    *,
    decision: Decision,
    pair: ReviewPair,
    deterministic: DeterministicResult,
    audit: AuditVerdict | None,
    coverage: CoverageManifest,
    context: ContextManifest | None,
    document_review: DocumentReviewManifest | None,
    used_providers: tuple[str, ...],
    provider_events: tuple[str, ...],
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
    lines.append("")
    lines.append("### Diff coverage manifest")
    lines.append(f"- **Complete**: `{coverage.complete}`")
    lines.append(f"- **Files changed**: {coverage.total_files}; **files covered**: {len(coverage.files_covered)}")
    lines.append(
        f"- **Chunks**: {coverage.total_chunks} total, {coverage.chunks_reviewed} reviewed, {coverage.chunks_failed} failed"
    )
    lines.append(f"- **Diff bytes**: {coverage.diff_bytes_covered}/{coverage.diff_bytes_total} covered")
    if coverage.chunk_errors:
        lines.extend(_dedupe_error_lines(coverage.chunk_errors, label="Chunk errors"))
    if coverage.files_missing_from_diff:
        lines.append("- **Files missing from diff**: " + ", ".join(coverage.files_missing_from_diff))
    if context is not None:
        lines.append("")
        lines.append("### Authoritative context coverage manifest")
        for entry in context.entries:
            mark = "mandatory" if entry.mandatory else "referenced"
            lines.append(
                f"- `{entry.path}`@`{entry.ref[:12]}` ({mark}, {entry.status}) "
                f"sha256={entry.sha256[:12] or 'n/a'} bytes={entry.byte_length} included_chars={entry.included_chars}"
            )
        if context.missing_references:
            lines.append("- **Missing referenced records**: " + ", ".join(context.missing_references))
    if document_review is not None:
        lines.append("")
        lines.append("### Authoritative-document review coverage manifest")
        lines.append(f"- **Complete**: `{document_review.complete}`")
        lines.append(f"- **Documents**: {document_review.total_documents}")
        lines.append(
            f"- **Sections**: {document_review.total_chunks} total, {document_review.chunks_reviewed} reviewed, "
            f"{document_review.chunks_failed} failed"
        )
        lines.append(
            f"- **Bytes actually reviewed**: {document_review.bytes_reviewed}/{document_review.bytes_total} "
            "(never retrieved-only ranges)"
        )
        if document_review.chunk_errors:
            lines.extend(_dedupe_error_lines(document_review.chunk_errors, label="Section errors"))
    if used_providers or provider_events:
        lines.append("")
        lines.append("### Provider execution")
        lines.append("- **Provider(s) used**: " + (", ".join(used_providers) if used_providers else "none succeeded"))
        if provider_events:
            lines.append(
                "- **Provider health events** (attempt/switch/failure-classification provenance; never "
                "includes secret values, only slot names):"
            )
            lines.extend(f"  - {event}" for event in provider_events)
    if audit is not None:
        lines.append("")
        lines.append("### Hostile architecture audit (aggregated across all chunks)")
        lines.append(f"- **Verdict**: `{audit.verdict}`")
        if audit.summary:
            lines.append(f"- **Summary**: {audit.summary}")
        if audit.findings:
            lines.append("")
            lines.append("#### Audit findings")
            for finding in audit.findings:
                fid = finding.get("id", "?")
                severity = finding.get("severity", "?")
                location = finding.get("location", "")
                description = finding.get("description", "")
                impact = finding.get("decision_impact", "")
                lines.append(f"- `{fid}` ({severity}) {location}: {description} -- {impact}")
        if audit.rationale:
            lines.append("")
            lines.append(f"**Rationale**: {audit.rationale}")
    body = "\n".join(lines) + "\n"
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > _SUMMARY_BYTE_BUDGET:
        # Defensive backstop for a review with enough genuinely distinct
        # errors that deduplication alone does not fit the budget. The
        # decision, reason, and coverage-count lines are always written
        # first (above), so this can only ever truncate the verbose findings
        # tail -- never the outcome itself. Nothing is lost: the full,
        # untruncated text is still in the run's own log output, which has
        # no size limit.
        truncated = body_bytes[:_SUMMARY_BYTE_BUDGET].decode("utf-8", errors="ignore")
        body = (
            truncated + "\n\n... [step summary truncated to stay under GitHub's 1024 KiB limit; "
            "the complete, untruncated detail is in this run's own log output] ...\n"
        )
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(body)


def _resolve_pair_freshness(
    gh: GitHubRunner, pr_number: int, pair: ReviewPair
) -> tuple[PullRequest | None, bool, str | None]:
    """Re-resolve the PR and compare against ``pair``. Returns (current, fresh, error)."""
    try:
        current = gh.get_pull_request(pr_number)
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
    llm_runner: LLMRunner,
    synthesis_runner: SynthesisRunner,
    document_review_runner: DocumentReviewRunner,
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
        diff = gh.get_pull_diff(pr.number)
    except GitHubError as exc:
        evidence_error = f"missing required repository evidence: {exc}"

    if evidence_error is None and len(diff) > DIFF_LIMIT:
        # The retrieved diff exceeds even the coarse memory/sanity bound.
        # Coverage accounting (chunking.py, aggregate.py) must operate on the
        # complete retrieved diff -- silently truncating here and letting
        # chunking/coverage proceed on the truncated remainder would let a
        # file's content be silently dropped before coverage accounting ever
        # saw it, while still reporting that file as "covered" (it would
        # still appear in a chunk's file list up to the cut point). Fail
        # closed instead: never treat truncated input as complete evidence.
        evidence_error = (
            f"retrieved diff ({len(diff)} chars) exceeds the sanity bound (DIFF_LIMIT={DIFF_LIMIT} "
            "chars); refusing to silently truncate and continue -- coverage accounting must operate "
            "on the complete retrieved diff, never a truncated one"
        )

    context_manifest: ContextManifest | None = None
    context_error: str | None = None
    if evidence_error is None:
        try:
            context_manifest = resolve_context(gh, base_sha=pair.target_base_sha, pr_body=pr.body)
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
                ValidationContext(pr=pr, files=files, missing_references=context_manifest.missing_references)
            )
        except Exception as exc:  # internal validator exception -> REVIEW_FAILED
            validator_error = f"internal validator exception: {exc!r}"

    # Early freshness check: a cheap short-circuit so a PR that is already
    # stale doesn't pay for a (possibly multi-call) LLM audit whose result
    # would be discarded anyway. This is NOT the authoritative guarantee --
    # see the post-audit check below, which is what actually gates approval.
    _, early_fresh, _ = _resolve_pair_freshness(gh, pr.number, pair)

    coverage = CoverageManifest(
        total_files=len(files),
        total_chunks=0,
        chunks_reviewed=0,
        chunks_failed=0,
        chunk_errors=(),
        files_covered=(),
        files_missing_from_diff=(),
        diff_bytes_total=len(diff),
        diff_bytes_covered=0,
    )
    aggregated_verdict: AuditVerdict | None = None
    coverage_incomplete_reason: str | None = None
    document_coverage: DocumentReviewManifest | None = None
    used_providers: tuple[str, ...] = ()
    should_audit = (
        evidence_error is None
        and context_error is None
        and validator_error is None
        and not deterministic.blocking
        and early_fresh
    )
    # One ProviderHealth per run: shared across every diff-chunk, synthesis,
    # and document-review call in this run so a provider that fails
    # operationally partway through is excluded from every later call in
    # the SAME run, not just retried on the call that failed -- see
    # llm_audit.ProviderHealth's docstring.
    health = ProviderHealth()
    if should_audit:
        assert context_manifest is not None
        max_chunk_chars = estimate_chunk_diff_budget(
            env=env,
            pr=pr,
            context_brief=context_manifest.brief,
            deterministic_findings=deterministic.findings,
        )
        chunks = split_diff_into_chunks(diff, max_chunk_chars)
        outcomes: list[ChunkOutcome] = []
        for chunk in chunks:
            try:
                result = llm_runner(
                    env,
                    pair=pair,
                    pr=pr,
                    chunk=chunk,
                    context_brief=context_manifest.brief,
                    deterministic_findings=deterministic.findings,
                    health=health,
                )
                outcomes.append(
                    ChunkOutcome(chunk.index, chunk.total, chunk.files, result.verdict, None, result.provider)
                )
                # Pure observability: a large diff's sequential review can
                # run for many minutes with nothing else printed (the full
                # error text stays in `outcomes`/coverage.chunk_errors,
                # surfaced once, deduplicated, in the final summary --
                # never repeated here) -- see the incident where a run was
                # silently killed by the job timeout with no visibility
                # into how far it had actually gotten.
                print(f"[Progress] chunk {chunk.index}/{chunk.total} reviewed (provider={result.provider})")
            except LLMAuditError as exc:
                outcomes.append(ChunkOutcome(chunk.index, chunk.total, chunk.files, None, str(exc), None))
                print(f"[Progress] chunk {chunk.index}/{chunk.total} failed: {exc.__class__.__name__}")
        expected_files = {f.filename for f in files}
        missing_from_diff = tuple(sorted(expected_files - covered_filenames(chunks)))
        aggregation = aggregate_chunk_outcomes(
            outcomes,
            diff_bytes_total=len(diff),
            total_files=len(files),
            files_missing_from_diff=missing_from_diff,
        )
        architectural_evidence_summary = ""
        document_outcomes: list[ChunkOutcome] = []
        synthesis_provider: str | None = None

        # Everything below requires complete diff-chunk coverage first:
        # ``describe_chunks_for_synthesis`` asserts every chunk succeeded,
        # and when coverage is already incomplete neither the synthesis nor
        # the document-review stage has anything trustworthy to reason over
        # (``apply_synthesis``/``apply_document_review`` are no-ops on an
        # already-``None`` verdict anyway).
        if aggregation.verdict is not None:
            # Structured architectural evidence extracted by every chunk
            # (not just prose summaries) is what both the cross-chunk
            # synthesis and the authoritative-document review below reason
            # over -- computed once and reused by both stages.
            architectural_evidence_summary = describe_chunks_for_synthesis(outcomes)

            # Cross-chunk consistency synthesis: only meaningful when there
            # was at least one chunk to synthesize over. Approval requires
            # this to succeed and find no contradiction spanning multiple
            # chunks -- a failure here is folded in exactly like a failed
            # chunk (coverage that cannot be verified consistent is not
            # complete).
            if len(outcomes) > 0:
                try:
                    synthesis_result = synthesis_runner(
                        env, pair=pair, pr=pr, synthesis_input=architectural_evidence_summary, health=health
                    )
                    print(
                        f"[Synthesis] {synthesis_result.verdict.verdict}: {synthesis_result.verdict.summary} "
                        f"(provider={synthesis_result.provider})"
                    )
                    synthesis_provider = synthesis_result.provider
                    aggregation = apply_synthesis(aggregation, synthesis_result.verdict, None)
                except LLMAuditError as exc:
                    print(f"[Synthesis] failed: {exc}")
                    aggregation = apply_synthesis(aggregation, None, str(exc))

        # Full, lossless authoritative-document review: a resolved document
        # is not a reviewed document. Every mandatory document is chunked
        # into full-text sections (never a bounded excerpt) and every
        # section is reviewed against the pull request's structured
        # architectural evidence; any failed/unreviewed section fails the
        # whole review closed, regardless of what the diff-chunk review or
        # synthesis concluded. Re-checked here (not merely inherited from
        # the outer guard) because synthesis, above, can itself turn a
        # complete aggregation's verdict to ``None`` on failure.
        if aggregation.verdict is not None:
            assert context_manifest is not None
            total_document_bytes = 0
            for document_path, document_text in context_manifest.mandatory_document_texts:
                total_document_bytes += len(document_text)
                section_budget = estimate_document_chunk_budget(
                    env=env,
                    pr=pr,
                    architectural_evidence_summary=architectural_evidence_summary,
                    document_path=document_path,
                )
                for section in split_document_into_chunks(document_path, document_text, section_budget):
                    try:
                        result = document_review_runner(
                            env,
                            pair=pair,
                            pr=pr,
                            document_chunk=section,
                            architectural_evidence_summary=architectural_evidence_summary,
                            health=health,
                        )
                        document_outcomes.append(
                            ChunkOutcome(
                                section.index, section.total, section.files, result.verdict, None, result.provider
                            )
                        )
                        print(
                            f"[Progress] {document_path} section {section.index}/{section.total} reviewed "
                            f"(provider={result.provider})"
                        )
                    except LLMAuditError as exc:
                        document_outcomes.append(
                            ChunkOutcome(section.index, section.total, section.files, None, str(exc), None)
                        )
                        print(
                            f"[Progress] {document_path} section {section.index}/{section.total} failed: "
                            f"{exc.__class__.__name__}"
                        )
            document_review_result: AggregatedDocumentReview = aggregate_document_chunk_outcomes(
                document_outcomes,
                bytes_total=total_document_bytes,
                total_documents=len(context_manifest.mandatory_document_texts),
            )
            document_coverage = document_review_result.manifest
            if document_review_result.verdict is not None:
                print(
                    f"[DocumentReview] {document_review_result.verdict.verdict}: "
                    f"{document_review_result.verdict.summary}"
                )
            else:
                print(f"[DocumentReview] failed: {document_review_result.incomplete_reason}")
            aggregation = apply_document_review(aggregation, document_review_result)

        # Provenance: every distinct provider that actually served a
        # successful chunk/section, plus the synthesis provider if it
        # succeeded -- "final provider(s) used" for this run.
        combined_providers = list(providers_used(outcomes)) + list(providers_used(document_outcomes))
        if synthesis_provider and synthesis_provider not in combined_providers:
            combined_providers.append(synthesis_provider)
        used_providers = tuple(combined_providers)

        coverage = aggregation.manifest
        aggregated_verdict = aggregation.verdict
        coverage_incomplete_reason = aggregation.incomplete_reason

    # Authoritative freshness check: re-resolve the PR again, AFTER the
    # (possibly slow, multi-call) audit completed, immediately before
    # publishing. An approval must apply to the exact pair the audit
    # actually reviewed -- if either SHA advanced during the audit, publish
    # no approval regardless of what the audit concluded.
    current, pair_fresh, pair_fresh_error = _resolve_pair_freshness(gh, pr.number, pair)

    if evidence_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, evidence_error)
    elif context_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, context_error)
    elif validator_error is not None:
        decision = Decision(Outcome.REVIEW_FAILED, validator_error)
    else:
        decision = decide(
            deterministic=deterministic,
            audit=aggregated_verdict,
            audit_error=coverage_incomplete_reason,
            pair_fresh=pair_fresh,
            pair_fresh_error=pair_fresh_error,
        )

    target_sha = current.head_oid if current is not None else pair.source_head_sha
    state = outcome_to_check_state(decision.outcome)
    description = build_status_description(decision, pair)
    target_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}/actions/runs/{run_id}"

    print(f"[Outcome] {decision.outcome.value}")
    print(f"[Reason] {decision.reason}")
    print(
        f"[Coverage] complete={coverage.complete} chunks={coverage.chunks_reviewed}/{coverage.total_chunks} "
        f"files={len(coverage.files_covered)}/{coverage.total_files}"
    )
    if document_coverage is not None:
        print(
            f"[DocumentCoverage] complete={document_coverage.complete} "
            f"sections={document_coverage.chunks_reviewed}/{document_coverage.total_chunks} "
            f"documents={document_coverage.total_documents} "
            f"bytes={document_coverage.bytes_reviewed}/{document_coverage.bytes_total}"
        )
    if used_providers:
        print(f"[ProvidersUsed] {', '.join(used_providers)}")
    for event in health.events:
        print(f"[ProviderHealth] {event}")
    for finding in deterministic.findings:
        print(f"[Finding] {finding.render()}")
    if context_manifest is not None:
        for entry in context_manifest.entries:
            print(f"[Context] {entry.path}@{entry.ref[:12]} status={entry.status} sha256={entry.sha256[:12] or 'n/a'}")
    if aggregated_verdict is not None:
        print(f"[AuditVerdict] {aggregated_verdict.verdict}: {aggregated_verdict.summary}")
        for audit_finding in aggregated_verdict.findings:
            print(f"[AuditFinding] {audit_finding}")
        if aggregated_verdict.rationale:
            print(f"[AuditRationale] {aggregated_verdict.rationale}")
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
        audit=aggregated_verdict,
        coverage=coverage,
        context=context_manifest,
        document_review=document_coverage,
        used_providers=used_providers,
        provider_events=tuple(health.events),
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
    return run_review(
        args=args,
        env=env,
        gh=gh,
        llm_runner=run_llm_audit,
        synthesis_runner=run_synthesis_review,
        document_review_runner=run_document_review,
    )


if __name__ == "__main__":
    raise SystemExit(main())
