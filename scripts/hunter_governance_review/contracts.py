"""Shared contracts for the Hunter Governance Review merge gate.

The gate distinguishes exactly three internal outcomes:

- ``APPROVED`` — every deterministic validator passed, required repository
  evidence (canonical governance documents, referenced ADR/ADPR records) was
  resolved at the exact base commit, and the reviewed source-head /
  target-base pair still matches the pull request at decision time.
- ``CHANGES_REQUIRED`` — the pull request violates deterministic governance
  validation.
- ``REVIEW_FAILED`` — the review process could not produce a trustworthy
  verdict (missing repository evidence, stale review pair, or an internal
  error).

This module is the single authority for the GitHub check mapping:
``APPROVED`` -> ``success``; ``CHANGES_REQUIRED`` and ``REVIEW_FAILED`` ->
``failure``. ``REVIEW_FAILED`` is never converted into approval.

The gate is entirely deterministic, repository-native, and provider-
independent: no external LLM or other network service other than GitHub
itself is ever consulted, and no outcome here can be produced or withheld by
an external provider's availability, quota, billing state, or API error.
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
        """True when at least one blocking finding exists."""
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
    author_login: str = ""


@dataclass(frozen=True)
class ChangedFile:
    """A file changed by the pull request."""

    filename: str
    status: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class ContextEntry:
    """One canonical governance document or referenced ADR/ADPR record
    consulted (or attempted) while resolving required repository evidence.

    ``provenance`` distinguishes WHICH commit an entry's authority actually
    comes from -- ``"base"`` (the trusted, pre-PR canonical state) or
    ``"head"`` (content that exists only because this PR itself proposes it,
    not yet trusted historical authority). A mandatory canonical-hierarchy
    document is always ``"base"`` -- a PR can never expand what governance
    considers mandatory. A referenced ADR/ADPR record resolved from ``"head"``
    is a genuinely new record this PR is proposing, confirmed to actually be
    part of the PR's own changed files (see ``context.resolve_referenced_records``)
    and structurally validated before being trusted even provisionally --
    never fabricated authority from a canonical-looking filename alone.
    ``ref`` records the exact commit this entry's content was fetched from,
    consistent with ``provenance`` (``base_sha`` for ``"base"``, ``head_sha``
    for ``"head"``).
    """

    path: str
    ref: str
    mandatory: bool
    status: str  # "resolved" or "missing"
    sha256: str
    byte_length: int
    provenance: str = "base"  # "base" or "head"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "ref": self.ref,
            "mandatory": self.mandatory,
            "status": self.status,
            "provenance": self.provenance,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
        }


@dataclass(frozen=True)
class ContextManifest:
    """Deterministic evidence of which governance documents and referenced
    ADR/ADPR records were confirmed to exist at the exact base commit.
    """

    entries: tuple[ContextEntry, ...]
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


class CanonicalPRContract:
    """The single, authoritative, machine-verifiable contract for human-authored pull requests."""

    REQUIRED_SECTIONS = (
        "Summary",
        "Scope and architecture",
        "Acceptance-criteria matrix",
        "Verification",
        "Operational validation",
        "Remaining limitations and risks",
        "Implementer readiness declaration",
    )

    # Docs-only contributions scale their evidence down proportionally, per
    # DEVELOPMENT_GOVERNANCE.md proportionality rules.
    MINIMAL_SECTIONS = ("Summary", "Acceptance-criteria matrix", "Implementer readiness declaration")

    PROHIBITED_PLACEHOLDERS = (
        "replace with",
        "todo:",
        "lorem ipsum",
        "fix me",
        "placeholder",
        "example: ",
    )

    PROHIBITED_TITLES = {"", "wip", "draft", "untitled", "update", "updates", "change", "changes"}

    ALLOWED_ACCEPTANCE_STATUSES = ("pass", "fail", "blocked", "not applicable")

    ALLOWED_READINESS_DECLARATIONS = ("ready for review", "changes required", "blocked")
