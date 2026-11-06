"""Security admission regressions for the optional Issue #390 fallback."""

from dataclasses import replace

import pytest

from hunter.automation.issue_agent_fallback_policy import FallbackEvidence, permit_codex_fallback


@pytest.fixture
def valid():
    return FallbackEvidence("kilo", "codex", "RATE_LIMIT", True, False, True, True, 1)


def test_classified_rate_limit_may_fallback(valid):
    assert permit_codex_fallback(valid)


@pytest.mark.parametrize(
    "failure",
    [
        "PROVIDER_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "NO_CHANGES",
        "SECRET_IN_RESULT",
        "EXECUTOR_RESULT_REJECTED",
        "AUTHENTICATION",
        "PERMISSION",
        "UNCLASSIFIED",
        "",
        "ISOLATION_NOT_READY",
    ],
)
def test_generic_or_security_failures_never_fallback(valid, failure):
    assert not permit_codex_fallback(replace(valid, provider_failure=failure))


@pytest.mark.parametrize(
    "field,value",
    [
        ("primary", "codex"),
        ("secondary", "kilo"),
        ("authorization_valid", False),
        ("proof_published", True),
        ("isolation_verified", False),
        ("workspace_exclusive", False),
        ("attempts_started", 0),
        ("attempts_started", 2),
    ],
)
def test_no_bypass_of_authority_or_exclusivity(valid, field, value):
    assert not permit_codex_fallback(replace(valid, **{field: value}))


@pytest.mark.parametrize("failure", ["QUOTA_EXCEEDED", "MODEL_NOT_FOUND", "NETWORK"])
def test_other_classified_provider_failures(valid, failure):
    assert permit_codex_fallback(replace(valid, provider_failure=failure))
