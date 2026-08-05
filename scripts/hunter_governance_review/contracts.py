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
