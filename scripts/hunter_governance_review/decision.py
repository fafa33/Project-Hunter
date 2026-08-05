"""Decision Engine.

Combines the Deterministic Governance Engine result, the LLM Architecture
Audit verdict, and review-pair freshness into exactly one of the three allowed
outcomes (``APPROVED``, ``CHANGES_REQUIRED``, ``REVIEW_FAILED``).

Rules enforced here:

- The LLM never bypasses a deterministic failure: any blocking deterministic
  finding produces ``CHANGES_REQUIRED`` regardless of the LLM verdict.
- A stale source-head or target-base pair produces ``REVIEW_FAILED``.
- Only a valid ``APPROVED`` LLM verdict, with all deterministic validators
  passing and a fresh pair, may produce ``APPROVED``.
- Missing, errored, or unsupported audit output produces ``REVIEW_FAILED``.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter_governance_review.contracts import DeterministicResult, Outcome
from hunter_governance_review.llm_audit import AuditVerdict


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str


def decide(
    *,
    deterministic: DeterministicResult,
    audit: AuditVerdict | None,
    audit_error: str | None,
    pair_fresh: bool,
    pair_fresh_error: str | None = None,
) -> Decision:
    """Return the single decision for the current review inputs."""
    if not pair_fresh:
        return Decision(
            Outcome.REVIEW_FAILED,
            pair_fresh_error
            or "stale source or target pair: the PR advanced while the review was running; "
            "the review is invalid for the current head/base and must be re-run.",
        )
    if deterministic.blocking:
        return Decision(
            Outcome.CHANGES_REQUIRED,
            "deterministic governance validation failed; the LLM audit cannot bypass it.",
        )
    if audit is None:
        return Decision(
            Outcome.REVIEW_FAILED,
            audit_error or "LLM architecture audit did not produce a verdict.",
        )
    if audit.verdict == "APPROVED":
        return Decision(
            Outcome.APPROVED,
            "deterministic validation passed and the hostile architecture audit approved " "the exact review pair.",
        )
    return Decision(
        Outcome.CHANGES_REQUIRED,
        "the hostile architecture audit returned CHANGES_REQUIRED with blocking findings.",
    )
