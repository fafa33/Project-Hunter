"""Fail-closed, single-attempt provider fallback admission for Issue #390 (ADR 0041 F2, Proposed).

This module does not launch a second executor and is not wired into the
lifecycle. The trusted lifecycle must prove isolation and exclusive workspace
ownership before using this decision.
"""

from __future__ import annotations

from dataclasses import dataclass

# A generic nonzero exit or timeout is not evidence of provider rate limiting:
# it could conceal a security or sandbox failure. MODEL_NOT_FOUND is a primary
# configuration defect, so falling back would hide it.
RECOVERABLE_PROVIDER_FAILURES = frozenset({"RATE_LIMIT", "QUOTA_EXCEEDED", "NETWORK"})


@dataclass(frozen=True)
class FallbackEvidence:
    primary: str
    secondary: str
    # Untrusted hint: classified from stderr of a model-influenced process.
    stderr_category: str
    # Trusted: a model-free provider probe run by the executor's trusted step.
    probe_category: str
    primary_result_artifacts: int
    authorization_valid: bool
    proof_published: bool
    isolation_verified: bool
    workspace_exclusive: bool
    attempts_started: int


def permit_codex_fallback(evidence: FallbackEvidence) -> bool:
    """Allow one sequential fallback only with independently verified gates.

    No model text, Issue instructions, or generic executor advisory can grant
    authority: the untrusted stderr hint must agree with the trusted probe.
    Caller must independently validate every boolean against trusted signed
    state and isolation attestations.
    """
    return (
        evidence.primary == "kilo"
        and evidence.secondary == "codex"
        and evidence.probe_category in RECOVERABLE_PROVIDER_FAILURES
        and evidence.stderr_category == evidence.probe_category
        and evidence.primary_result_artifacts == 0
        and evidence.authorization_valid
        and not evidence.proof_published
        and evidence.isolation_verified
        and evidence.workspace_exclusive
        and evidence.attempts_started == 1
    )
