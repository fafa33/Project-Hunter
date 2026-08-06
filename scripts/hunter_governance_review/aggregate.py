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

Once every chunk succeeds, ``apply_synthesis`` folds in the result of the
one, final cross-chunk consistency synthesis call
(``llm_audit.run_synthesis_review``): approval additionally requires that
synthesis to have succeeded and found no unresolved contradiction spanning
multiple chunks.

``aggregate_document_chunk_outcomes``/``apply_document_review`` apply the
identical fail-closed principle to authoritative-document review
(``llm_audit.run_document_review``): approval additionally requires every
section of every mandatory document to have actually been reviewed, not
merely retrieved or excerpted.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter_governance_review.contracts import CoverageManifest, DocumentReviewManifest
from hunter_governance_review.llm_audit import AuditVerdict


@dataclass(frozen=True)
class ChunkOutcome:
    """The result of reviewing one diff chunk: exactly one of verdict/error is set.

    ``provider`` is the name of the configured provider slot that actually
    produced ``verdict`` (``None`` when ``verdict`` is ``None``, i.e. this
    outcome failed) -- provenance for which provider served each individual
    chunk, since provider failover means different chunks in the same run
    can be served by different providers.
    """

    chunk_index: int
    chunk_total: int
    files: tuple[str, ...]
    verdict: AuditVerdict | None
    error: str | None
    provider: str | None = None


def providers_used(outcomes: list[ChunkOutcome]) -> tuple[str, ...]:
    """The distinct providers that actually served at least one successful
    outcome, in first-seen order -- provenance for "final provider(s) used"
    across a run's chunks/sections."""
    seen: list[str] = []
    for outcome in outcomes:
        if outcome.provider and outcome.provider not in seen:
            seen.append(outcome.provider)
    return tuple(seen)


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


def describe_chunks_for_synthesis(outcomes: list[ChunkOutcome]) -> str:
    """Render per-chunk summaries/findings/file-coverage as compact text.

    Input to the one cross-chunk consistency synthesis call
    (``llm_audit.run_synthesis_review``) -- never the raw diff, only what
    each chunk already reported. Callers must only call this after
    confirming every outcome succeeded (``aggregate_chunk_outcomes``'s
    ``verdict is not None`` result already guarantees this); it asserts
    rather than silently skipping a missing verdict, since synthesizing over
    partial data would be exactly the "silent evidence loss" this gate must
    never produce.
    """
    lines: list[str] = []
    for outcome in outcomes:
        verdict = outcome.verdict
        assert verdict is not None, "describe_chunks_for_synthesis requires every chunk to have succeeded"
        lines.append(f"Chunk {outcome.chunk_index}/{outcome.chunk_total} -- files: {', '.join(outcome.files)}")
        lines.append(f"  summary: {verdict.summary}")
        non_empty_evidence = {k: v for k, v in verdict.architectural_evidence.items() if v}
        if non_empty_evidence:
            lines.append("  architectural evidence:")
            for category, items in non_empty_evidence.items():
                lines.append(f"    {category}: {'; '.join(items)}")
        else:
            lines.append("  architectural evidence: none")
        if verdict.findings:
            for finding in verdict.findings:
                lines.append(
                    f"  finding [{finding.get('id')}] ({finding.get('severity')}) "
                    f"{finding.get('location')}: {finding.get('description')} -- {finding.get('decision_impact')}"
                )
        else:
            lines.append("  findings: none")
    return "\n".join(lines)


def apply_synthesis(
    aggregated: AggregatedAudit,
    synthesis: AuditVerdict | None,
    synthesis_error: str | None,
) -> AggregatedAudit:
    """Fold the cross-chunk consistency synthesis result into the aggregated audit.

    Approval requires complete chunk coverage (already enforced by
    ``aggregated.verdict is not None``), a successful synthesis call, and no
    unresolved cross-chunk contradiction. A synthesis failure is treated
    exactly like a failed chunk: coverage that cannot be verified consistent
    is not complete, so the result becomes incomplete (``verdict=None``),
    which the caller maps to ``REVIEW_FAILED``.

    A no-op passthrough when ``aggregated.verdict`` is already ``None``
    (coverage was incomplete before synthesis was ever attempted) or when
    ``synthesis`` is ``None`` with no error (synthesis was skipped, e.g. a
    zero-chunk review with nothing to synthesize).
    """
    if aggregated.verdict is None:
        return aggregated
    if synthesis_error is not None:
        return AggregatedAudit(
            verdict=None,
            manifest=aggregated.manifest,
            incomplete_reason=f"cross-chunk consistency synthesis failed: {synthesis_error}",
        )
    if synthesis is None:
        return aggregated
    if synthesis.verdict == "CHANGES_REQUIRED":
        namespaced = [{**finding, "id": f"SYN-{finding.get('id', '?')}"} for finding in synthesis.findings]
        merged = AuditVerdict(
            verdict="CHANGES_REQUIRED",
            summary=f"{aggregated.verdict.summary}; cross-chunk synthesis: {synthesis.summary}",
            findings=[*aggregated.verdict.findings, *namespaced],
            rationale=f"{aggregated.verdict.rationale} | synthesis: {synthesis.rationale}",
        )
        return AggregatedAudit(verdict=merged, manifest=aggregated.manifest, incomplete_reason=None)
    merged = AuditVerdict(
        verdict=aggregated.verdict.verdict,
        summary=aggregated.verdict.summary,
        findings=aggregated.verdict.findings,
        rationale=(
            f"{aggregated.verdict.rationale} | synthesis: no cross-chunk contradictions found " f"({synthesis.summary})"
        ),
    )
    return AggregatedAudit(verdict=merged, manifest=aggregated.manifest, incomplete_reason=None)


@dataclass(frozen=True)
class AggregatedDocumentReview:
    """The combined result of reviewing every section of every mandatory document.

    ``verdict`` is ``None`` exactly when document-review coverage is
    incomplete (``incomplete_reason`` is set) -- a document that was merely
    retrieved, or only partially reviewed, never produces a meaningful
    verdict here; the caller must map ``None`` to ``REVIEW_FAILED``.
    """

    verdict: AuditVerdict | None
    manifest: DocumentReviewManifest
    incomplete_reason: str | None


def aggregate_document_chunk_outcomes(
    outcomes: list[ChunkOutcome],
    *,
    bytes_total: int,
    total_documents: int,
) -> AggregatedDocumentReview:
    """Combine every document section's review outcome into one result.

    Identical fail-closed structure to ``aggregate_chunk_outcomes``: any
    failed or missing section makes the whole document-review pass
    incomplete (``verdict=None``) -- there is no partial credit toward
    "authoritative evidence was reviewed." ``bytes_reviewed`` in the returned
    manifest counts only bytes that passed through a successful review call,
    never bytes that were merely retrieved or excerpted.
    """
    total_chunks = len(outcomes)
    failed = [o for o in outcomes if o.error is not None or o.verdict is None]
    chunk_errors = tuple(
        f"section {o.chunk_index}/{o.chunk_total} of `{', '.join(o.files) or '?'}`: {o.error}" for o in failed
    )
    bytes_reviewed = 0 if failed else bytes_total

    manifest = DocumentReviewManifest(
        total_documents=total_documents,
        total_chunks=total_chunks,
        chunks_reviewed=total_chunks - len(failed),
        chunks_failed=len(failed),
        chunk_errors=chunk_errors,
        bytes_total=bytes_total,
        bytes_reviewed=bytes_reviewed,
    )

    if total_chunks == 0:
        return AggregatedDocumentReview(
            verdict=AuditVerdict(
                verdict="APPROVED",
                summary="no authoritative document content required review",
                findings=[],
                rationale="no mandatory document produced any section to review",
            ),
            manifest=manifest,
            incomplete_reason=None,
        )

    if failed:
        reason = (
            f"{len(failed)}/{total_chunks} authoritative-document section(s) failed or were not "
            "reviewed: " + "; ".join(chunk_errors)
        )
        return AggregatedDocumentReview(verdict=None, manifest=manifest, incomplete_reason=reason)

    all_findings: list[dict[str, str]] = []
    any_blocking = False
    summaries: list[str] = []
    rationales: list[str] = []
    for outcome in outcomes:
        verdict = outcome.verdict
        assert verdict is not None  # guaranteed: `failed` above is empty
        document_path = outcome.files[0] if outcome.files else "?"
        for finding in verdict.findings:
            namespaced = dict(finding)
            namespaced["id"] = f"DOC{outcome.chunk_index}-{finding.get('id', '?')}"
            all_findings.append(namespaced)
            if finding.get("severity") == "blocking":
                any_blocking = True
        if verdict.summary:
            summaries.append(
                f"`{document_path}` section {outcome.chunk_index}/{outcome.chunk_total}: {verdict.summary}"
            )
        if verdict.rationale:
            rationales.append(
                f"`{document_path}` section {outcome.chunk_index}/{outcome.chunk_total}: {verdict.rationale}"
            )

    aggregated = AuditVerdict(
        verdict="CHANGES_REQUIRED" if any_blocking else "APPROVED",
        summary=(
            "; ".join(summaries)
            if summaries
            else f"reviewed {total_chunks} authoritative-document section(s); no blocking findings"
        ),
        findings=all_findings,
        rationale=" | ".join(rationales),
    )
    return AggregatedDocumentReview(verdict=aggregated, manifest=manifest, incomplete_reason=None)


def apply_document_review(
    aggregated: AggregatedAudit,
    document_review: AggregatedDocumentReview,
) -> AggregatedAudit:
    """Fold the authoritative-document review result into the aggregated audit.

    Approval additionally requires every mandatory document's every section
    to have actually been reviewed (``document_review.manifest.complete``)
    and no violation found. A no-op passthrough when ``aggregated.verdict``
    is already ``None`` (diff coverage or synthesis already failed closed --
    document review is not even attempted in that case, so there is nothing
    to fold in).
    """
    if aggregated.verdict is None:
        return aggregated
    if document_review.verdict is None:
        return AggregatedAudit(
            verdict=None,
            manifest=aggregated.manifest,
            incomplete_reason=("authoritative-document review incomplete: " f"{document_review.incomplete_reason}"),
        )
    if document_review.verdict.verdict == "CHANGES_REQUIRED":
        namespaced = [
            {**finding, "id": f"CTX-{finding.get('id', '?')}"} for finding in document_review.verdict.findings
        ]
        merged = AuditVerdict(
            verdict="CHANGES_REQUIRED",
            summary=f"{aggregated.verdict.summary}; document review: {document_review.verdict.summary}",
            findings=[*aggregated.verdict.findings, *namespaced],
            rationale=f"{aggregated.verdict.rationale} | document review: {document_review.verdict.rationale}",
        )
        return AggregatedAudit(verdict=merged, manifest=aggregated.manifest, incomplete_reason=None)
    merged = AuditVerdict(
        verdict=aggregated.verdict.verdict,
        summary=aggregated.verdict.summary,
        findings=aggregated.verdict.findings,
        rationale=(
            f"{aggregated.verdict.rationale} | document review: no violation found "
            f"({document_review.verdict.summary})"
        ),
    )
    return AggregatedAudit(verdict=merged, manifest=aggregated.manifest, incomplete_reason=None)
