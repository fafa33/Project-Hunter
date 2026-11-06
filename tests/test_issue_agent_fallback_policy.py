"""Security admission regressions for the optional Issue #390 fallback (ADR 0041 F2)."""

from dataclasses import replace

import pytest

from hunter.automation.issue_agent_fallback_policy import FallbackEvidence, permit_codex_fallback


@pytest.fixture
def valid():
    return FallbackEvidence("kilo", "codex", "RATE_LIMIT", "RATE_LIMIT", 0, True, False, True, True, 1)


def test_probe_verified_rate_limit_may_fallback(valid):
    assert permit_codex_fallback(valid)


@pytest.mark.parametrize("failure", ["QUOTA_EXCEEDED", "NETWORK"])
def test_other_probe_verified_provider_failures(valid, failure):
    assert permit_codex_fallback(replace(valid, stderr_category=failure, probe_category=failure))


@pytest.mark.parametrize(
    "failure",
    [
        "MODEL_NOT_FOUND",
        "PROVIDER_UNAVAILABLE",
        "MODEL_TIMEOUT",
        "NO_CHANGES",
        "SECRET_IN_RESULT",
        "EXECUTOR_RESULT_REJECTED",
        "EXECUTION_NOT_STARTED",
        "AUTHENTICATION",
        "PERMISSION",
        "UNCLASSIFIED",
        "",
        "ISOLATION_NOT_READY",
    ],
)
def test_generic_or_security_failures_never_fallback_even_when_both_agree(valid, failure):
    assert not permit_codex_fallback(replace(valid, stderr_category=failure, probe_category=failure))


@pytest.mark.parametrize(
    "stderr,probe",
    [
        ("RATE_LIMIT", "UNCLASSIFIED"),  # model-forged stderr, provider is healthy
        ("RATE_LIMIT", "QUOTA_EXCEEDED"),  # disagreement is never resolved in favour of fallback
        ("UNCLASSIFIED", "RATE_LIMIT"),
        ("AUTHENTICATION", "RATE_LIMIT"),
    ],
)
def test_untrusted_stderr_alone_or_in_disagreement_never_grants_fallback(valid, stderr, probe):
    assert not permit_codex_fallback(replace(valid, stderr_category=stderr, probe_category=probe))


@pytest.mark.parametrize(
    "field,value",
    [
        ("primary", "codex"),
        ("secondary", "kilo"),
        ("primary_result_artifacts", 1),
        ("primary_result_artifacts", -1),
        ("authorization_valid", False),
        ("proof_published", True),
        ("isolation_verified", False),
        ("workspace_exclusive", False),
        ("attempts_started", 0),
        ("attempts_started", 2),
    ],
)
def test_no_bypass_of_authority_exclusivity_or_single_attempt(valid, field, value):
    assert not permit_codex_fallback(replace(valid, **{field: value}))
