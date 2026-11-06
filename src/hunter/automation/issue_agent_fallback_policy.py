"""Fail-closed, single-attempt provider fallback admission for Issue #390.

This module does not launch a second executor. The trusted lifecycle must
prove isolation and exclusive workspace ownership before using this decision.
"""

from __future__ import annotations

from dataclasses import dataclass

# A generic nonzero exit or timeout is not evidence of provider rate limiting:
# it could conceal a security or sandbox failure. Require classified telemetry.
RECOVERABLE_PROVIDER_FAILURES = frozenset({"RATE_LIMIT", "QUOTA_EXCEEDED", "MODEL_NOT_FOUND", "NETWORK"})


@dataclass(frozen=True)
class FallbackEvidence:
    primary: str
    secondary: str
    provider_failure: str
    authorization_valid: bool
    proof_published: bool
    isolation_verified: bool
    workspace_exclusive: bool
    attempts_started: int


def permit_codex_fallback(evidence: FallbackEvidence) -> bool:
    """Allow one sequential fallback only with independently verified gates.

    No model text, Issue instructions, or generic executor advisory can grant
    authority. Caller must independently validate every boolean against trusted
    signed state and isolation attestations.
    """
    return (
        evidence.primary == "kilo"
        and evidence.secondary == "codex"
        and evidence.provider_failure in RECOVERABLE_PROVIDER_FAILURES
        and evidence.authorization_valid
        and not evidence.proof_published
        and evidence.isolation_verified
        and evidence.workspace_exclusive
        and evidence.attempts_started == 1
    )
