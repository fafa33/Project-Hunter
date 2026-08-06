"""Unit tests for cross-chunk aggregation and the coverage manifest."""

from __future__ import annotations

from hunter_governance_review.aggregate import ChunkOutcome, aggregate_chunk_outcomes
from hunter_governance_review.llm_audit import AuditVerdict


def _verdict(verdict: str = "APPROVED", findings: list[dict[str, str]] | None = None) -> AuditVerdict:
    return AuditVerdict(verdict=verdict, summary="s", findings=findings or [], rationale="r")


def _blocking(fid: str = "F-001") -> dict[str, str]:
    return {"id": fid, "severity": "blocking", "location": "a.py", "description": "d", "decision_impact": "i"}


def _non_blocking(fid: str = "F-002") -> dict[str, str]:
    return {"id": fid, "severity": "non-blocking", "location": "a.py", "description": "d", "decision_impact": "i"}


def test_zero_chunks_is_trivially_approved() -> None:
    result = aggregate_chunk_outcomes([], diff_bytes_total=0, total_files=0)
    assert result.incomplete_reason is None
    assert result.verdict is not None
    assert result.verdict.verdict == "APPROVED"
    assert result.manifest.complete is True
    assert result.manifest.total_chunks == 0


def test_all_chunks_approved_aggregates_to_approved() -> None:
    outcomes = [
        ChunkOutcome(1, 2, ("a.py",), _verdict("APPROVED"), None),
        ChunkOutcome(2, 2, ("b.py",), _verdict("APPROVED"), None),
    ]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=100, total_files=2)
    assert result.incomplete_reason is None
    assert result.verdict is not None
    assert result.verdict.verdict == "APPROVED"
    assert result.manifest.complete is True
    assert result.manifest.chunks_reviewed == 2
    assert result.manifest.chunks_failed == 0
    assert result.manifest.diff_bytes_covered == 100


def test_one_blocking_chunk_makes_aggregate_changes_required_with_namespaced_id() -> None:
    outcomes = [
        ChunkOutcome(1, 2, ("a.py",), _verdict("CHANGES_REQUIRED", [_blocking()]), None),
        ChunkOutcome(2, 2, ("b.py",), _verdict("APPROVED"), None),
    ]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=100, total_files=2)
    assert result.verdict is not None
    assert result.verdict.verdict == "CHANGES_REQUIRED"
    assert result.incomplete_reason is None
    assert len(result.verdict.findings) == 1
    assert result.verdict.findings[0]["id"] == "C1-F-001"


def test_any_failed_chunk_makes_coverage_incomplete_with_no_verdict() -> None:
    outcomes = [
        ChunkOutcome(1, 2, ("a.py",), None, "LLM API returned HTTP 500"),
        ChunkOutcome(2, 2, ("b.py",), _verdict("APPROVED"), None),
    ]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=100, total_files=2)
    assert result.verdict is None
    assert result.incomplete_reason is not None
    assert "1/2" in result.incomplete_reason
    assert result.manifest.complete is False
    assert result.manifest.chunks_failed == 1
    assert result.manifest.diff_bytes_covered == 0


def test_missing_file_from_diff_makes_coverage_incomplete() -> None:
    outcomes = [ChunkOutcome(1, 1, ("a.py",), _verdict("APPROVED"), None)]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=100, total_files=2, files_missing_from_diff=("b.py",))
    assert result.verdict is None
    assert result.incomplete_reason is not None
    assert "b.py" in result.incomplete_reason
    assert result.manifest.complete is False


def test_non_blocking_findings_still_surface_when_approved() -> None:
    outcomes = [ChunkOutcome(1, 1, ("a.py",), _verdict("APPROVED", [_non_blocking()]), None)]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=10, total_files=1)
    assert result.verdict is not None
    assert result.verdict.verdict == "APPROVED"
    assert len(result.verdict.findings) == 1


def test_manifest_records_which_files_were_covered() -> None:
    outcomes = [
        ChunkOutcome(1, 2, ("a.py", "b.py"), _verdict("APPROVED"), None),
        ChunkOutcome(2, 2, ("c.py",), _verdict("APPROVED"), None),
    ]
    result = aggregate_chunk_outcomes(outcomes, diff_bytes_total=100, total_files=3)
    assert result.manifest.files_covered == ("a.py", "b.py", "c.py")
