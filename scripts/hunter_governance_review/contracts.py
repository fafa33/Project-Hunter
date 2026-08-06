"""Shared contracts for the Hunter Governance Review merge gate.

The gate distinguishes exactly three internal outcomes:

- ``APPROVED`` — every deterministic validator passed, the LLM architecture
  audit returned a valid ``APPROVED`` verdict, and the reviewed source-head /
  target-base pair still matches the pull request at decision time.
- ``CHANGES_REQUIRED`` — the pull request violates deterministic governance
  validation, or the LLM audit returned ``CHANGES_REQUIRED``.
- ``REVIEW_FAILED`` — the review process could not produce a trustworthy
  verdict (missing API secret, network/API failure, timeout, malformed model
  output, unsupported response schema, stale review pair, missing repository
  evidence, or an internal error).

This module is the single authority for the GitHub check mapping:
``APPROVED`` -> ``success``; ``CHANGES_REQUIRED`` and ``REVIEW_FAILED`` ->
``failure``. ``REVIEW_FAILED`` is never converted into approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

CHECK_CONTEXT = "Hunter Governance Review"


def utc_now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    # noqa: UP017 — datetime.UTC requires Python 3.11+; timezone.utc keeps the
    # gate importable and testable on Python 3.10 sandboxes as well as CI's 3.11.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


class Outcome(Enum):
    """Exactly three internal outcomes exist. No other outcome is permitted."""

    APPROVED = "APPROVED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    REVIEW_FAILED = "REVIEW_FAILED"


class CheckState(Enum):
    SUCCESS = "success"
    FAILURE = "failure"


def outcome_to_check_state(outcome: Outcome) -> CheckState:
    """Map an internal outcome to the published GitHub check state.

    Only ``APPROVED`` may produce ``success``. Both failure outcomes publish
    ``failure``; ``REVIEW_FAILED`` is never converted into approval.
    """
    if outcome is Outcome.APPROVED:
        return CheckState.SUCCESS
    return CheckState.FAILURE


@dataclass(frozen=True)
class ReviewPair:
    """The exact review pair a verdict applies to.

    Every approval applies only to this exact pair. If either SHA changes
    before the result is published, the review is invalid.
    """

    repository: str
    pull_request_number: int
    source_branch: str
    source_head_sha: str
    target_branch: str
    target_base_sha: str
    workflow_run_id: str
    review_timestamp: str

    def describe(self) -> str:
        return (
            f"repo={self.repository} pr=#{self.pull_request_number} "
            f"head={self.source_head_sha[:12]}@{self.source_branch} "
            f"base={self.target_base_sha[:12]}@{self.target_branch} run={self.workflow_run_id}"
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "pull_request_number": str(self.pull_request_number),
            "source_branch": self.source_branch,
            "source_head_sha": self.source_head_sha,
            "target_branch": self.target_branch,
            "target_base_sha": self.target_base_sha,
            "workflow_run_id": self.workflow_run_id,
            "review_timestamp": self.review_timestamp,
        }


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class Finding:
    """A deterministic validator finding."""

    validator_id: str
    title: str
    severity: Severity
    detail: str

    def render(self) -> str:
        return f"[{self.validator_id}] {self.title}: {self.detail}"

    def to_dict(self) -> dict[str, str]:
        return {
            "validator_id": self.validator_id,
            "title": self.title,
            "severity": self.severity.value,
            "detail": self.detail,
        }


@dataclass
class DeterministicResult:
    """Result of the Deterministic Governance Engine."""

    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """True when at least one blocking finding exists.

        The LLM audit is never allowed to bypass a deterministic failure.
        """
        return any(f.severity is Severity.BLOCKING for f in self.findings)


@dataclass(frozen=True)
class PullRequest:
    """PR metadata as resolved from GitHub at review time."""

    number: int
    title: str
    body: str
    state: str
    draft: bool
    head_ref_name: str
    head_oid: str
    base_ref_name: str
    base_oid: str
    mergeable: str | None
    changed_files: int
    url: str


@dataclass(frozen=True)
class ChangedFile:
    """A file changed by the pull request."""

    filename: str
    status: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class CoverageManifest:
    """Deterministic evidence of how completely the diff was reviewed.

    ``complete`` is the fail-closed gate for approval: any failed or
    unreviewed chunk makes the whole review's diff coverage incomplete,
    regardless of what any individual chunk's verdict was.
    """

    total_files: int
    total_chunks: int
    chunks_reviewed: int
    chunks_failed: int
    chunk_errors: tuple[str, ...]
    files_covered: tuple[str, ...]
    files_missing_from_diff: tuple[str, ...]
    diff_bytes_total: int
    diff_bytes_covered: int

    @property
    def complete(self) -> bool:
        return self.chunks_failed == 0 and not self.files_missing_from_diff

    def to_dict(self) -> dict[str, object]:
        return {
            "total_files": self.total_files,
            "total_chunks": self.total_chunks,
            "chunks_reviewed": self.chunks_reviewed,
            "chunks_failed": self.chunks_failed,
            "chunk_errors": list(self.chunk_errors),
            "files_covered": list(self.files_covered),
            "files_missing_from_diff": list(self.files_missing_from_diff),
            "diff_bytes_total": self.diff_bytes_total,
            "diff_bytes_covered": self.diff_bytes_covered,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class ContextEntry:
    """One document consulted (or attempted) while resolving governance context."""

    path: str
    ref: str
    mandatory: bool
    status: str  # "resolved" or "missing"
    sha256: str
    byte_length: int
    included_chars: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "ref": self.ref,
            "mandatory": self.mandatory,
            "status": self.status,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "included_chars": self.included_chars,
        }


@dataclass(frozen=True)
class ContextManifest:
    """Deterministic evidence of which governance documents were consulted."""

    entries: tuple[ContextEntry, ...]
    brief: str
    missing_references: tuple[str, ...] = ()

    @property
    def missing_mandatory(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.mandatory and e.status == "missing")

    def to_dict(self) -> dict[str, object]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "missing_mandatory": list(self.missing_mandatory),
            "missing_references": list(self.missing_references),
        }
