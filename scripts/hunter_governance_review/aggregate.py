"""Aggregation of per-chunk hostile-audit results into one verdict.

Complete diff coverage requires every chunk to be reviewed successfully.
``APPROVED`` is permitted only when every chunk's review completed and every
chunk's own verdict was internally consistent (see
``llm_audit.validate_audit_payload``). Any missing, failed, or unreviewed
chunk fails the whole audit closed -- there is no partial credit toward
approval, and no aggregate verdict is produced at all in that case
(``AggregatedAudit.verdict`` is ``None``; the caller must map that to
``REVIEW_FAILED``).

The aggregate verdict is computed here, deterministically, from the union of
every chunk's findings -- it is never taken from any single chunk's
self-reported label, because no single chunk ever saw the whole diff.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter_governance_review.contracts import CoverageManifest
from hunter_governance_review.llm_audit import AuditVerdict


@dataclass(frozen=True)
class ChunkOutcome:
    """The result of reviewing one diff chunk: exactly one of verdict/error is set."""

    chunk_index: int
    chunk_total: int
    files: tuple[str, ...]
    verdict: AuditVerdict | None
    error: str | None


@dataclass(frozen=True)
class AggregatedAudit:
    """The combined result of every chunk review.

    ``verdict`` is ``None`` exactly when coverage is incomplete
    (``incomplete_reason`` is set) -- there is no meaningful audit verdict to
    report when part of the diff was never successfully reviewed.
    """

    verdict: AuditVerdict | None
    manifest: CoverageManifest
    incomplete_reason: str | None


def aggregate_chunk_outcomes(
    outcomes: list[ChunkOutcome],
    *,
    diff_bytes_total: int,
    total_files: int,
    files_missing_from_diff: tuple[str, ...] = (),
) -> AggregatedAudit:
    """Combine every chunk's outcome into one aggregate audit result."""
    total_chunks = len(outcomes)
    failed = [o for o in outcomes if o.error is not None or o.verdict is None]

    files_covered: set[str] = set()
    for outcome in outcomes:
        if outcome.error is None and outcome.verdict is not None:
            files_covered.update(outcome.files)

    chunk_errors = tuple(
        f"chunk {o.chunk_index}/{o.chunk_total} ({', '.join(o.files) or '?'}): {o.error}" for o in failed
    )

    manifest = CoverageManifest(
        total_files=total_files,
        total_chunks=total_chunks,
        chunks_reviewed=total_chunks - len(failed),
        chunks_failed=len(failed),
        chunk_errors=chunk_errors,
        files_covered=tuple(sorted(files_covered)),
        files_missing_from_diff=files_missing_from_diff,
        diff_bytes_total=diff_bytes_total,
        diff_bytes_covered=diff_bytes_total if not failed and not files_missing_from_diff else 0,
    )

    if files_missing_from_diff:
        reason = (
            f"{len(files_missing_from_diff)} file(s) listed as changed by the pull request never "
            "appeared in any reviewed diff chunk: " + ", ".join(files_missing_from_diff)
        )
        return AggregatedAudit(verdict=None, manifest=manifest, incomplete_reason=reason)

    if total_chunks == 0:
        return AggregatedAudit(
            verdict=AuditVerdict(
                verdict="APPROVED",
                summary="no diff content to review",
                findings=[],
                rationale="the pull request's diff was empty; nothing required a hostile audit verdict",
            ),
            manifest=manifest,
            incomplete_reason=None,
        )

    if failed:
        reason = f"{len(failed)}/{total_chunks} diff chunk(s) failed or were not reviewed: " + "; ".join(chunk_errors)
        return AggregatedAudit(verdict=None, manifest=manifest, incomplete_reason=reason)

    all_findings: list[dict[str, str]] = []
    any_blocking = False
    summaries: list[str] = []
    rationales: list[str] = []
    for outcome in outcomes:
        verdict = outcome.verdict
        assert verdict is not None  # guaranteed: `failed` above is empty
        for finding in verdict.findings:
            namespaced = dict(finding)
            namespaced["id"] = f"C{outcome.chunk_index}-{finding.get('id', '?')}"
            all_findings.append(namespaced)
            if finding.get("severity") == "blocking":
                any_blocking = True
        if verdict.summary:
            summaries.append(f"chunk {outcome.chunk_index}/{outcome.chunk_total}: {verdict.summary}")
        if verdict.rationale:
            rationales.append(f"chunk {outcome.chunk_index}/{outcome.chunk_total}: {verdict.rationale}")

    aggregated = AuditVerdict(
        verdict="CHANGES_REQUIRED" if any_blocking else "APPROVED",
        summary="; ".join(summaries) if summaries else f"reviewed {total_chunks} chunk(s); no blocking findings",
        findings=all_findings,
        rationale=" | ".join(rationales),
    )
    return AggregatedAudit(verdict=aggregated, manifest=manifest, incomplete_reason=None)
