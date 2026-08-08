"""Decision Engine.

Combines the Deterministic Governance Engine result and review-pair
freshness into exactly one of the three allowed outcomes (``APPROVED``,
``CHANGES_REQUIRED``, ``REVIEW_FAILED``).

Rules enforced here:

- A stale source-head or target-base pair produces ``REVIEW_FAILED``.
- Any blocking deterministic finding produces ``CHANGES_REQUIRED``.
- Otherwise, with a fresh pair and no blocking deterministic finding,
  the outcome is ``APPROVED``.

This module has no dependency, direct or transitive, on any external LLM or
other network service other than what already fed ``deterministic`` and
``pair_fresh`` -- the decision itself is a pure function of repository-native
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from hunter_governance_review.contracts import DeterministicResult, Outcome


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str


def decide(
    *,
    deterministic: DeterministicResult,
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
            "deterministic governance validation failed.",
        )
    return Decision(
        Outcome.APPROVED,
        "deterministic governance validation passed for the exact review pair.",
    )
