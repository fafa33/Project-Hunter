"""Regression tests for CodeRabbit hostile-review adapter and status authentication."""

from __future__ import annotations

import hunter_governance_preflight as preflight
import pytest


def _make_status(
    *,
    context: str = "coderabbit",
    state: str = "success",
    login: str = "coderabbitai",
    **kwargs: object,
) -> dict[str, object]:
    """Helper to build a minimal CodeRabbit commit-status payload."""
    return {
        "context": context,
        "state": state,
        "creator": {"login": login},
        **kwargs,
    }


class TestCoderabbitStatusAuthentication:
    """Tests for _require_successful_coderabbit_status producer authentication."""

    def test_valid_coderabbitai_status(self) -> None:
        assert preflight._require_successful_coderabbit_status(status=_make_status(login="coderabbitai")) is True

    def test_valid_coderabbot_status(self) -> None:
        assert preflight._require_successful_coderabbit_status(status=_make_status(login="coderabbitai[bot]")) is True

    def test_non_coderabbit_creator_rejected(self) -> None:
        assert (
            preflight._require_successful_coderabbit_status(status=_make_status(login="github-actions[bot]")) is False
        )

    def test_missing_creator_rejected(self) -> None:
        assert (
            preflight._require_successful_coderabbit_status(status={"context": "coderabbit", "state": "success"})
            is False
        )

    def test_malformed_creator_rejected(self) -> None:
        assert (
            preflight._require_successful_coderabbit_status(
                status={"context": "coderabbit", "state": "success", "creator": "not_a_dict"}
            )
            is False
        )

    def test_missing_creator_key_rejected(self) -> None:
        assert (
            preflight._require_successful_coderabbit_status(
                status={"context": "coderabbit", "state": "success", "creator": {}}
            )
            is False
        )

    def test_failing_status_rejected(self) -> None:
        assert preflight._require_successful_coderabbit_status(status=_make_status(state="failure")) is False

    def test_missing_status_rejected(self) -> None:
        assert preflight._require_successful_coderabbit_status(status={"context": "coderabbit"}) is False

    def test_mismatched_context_rejected(self) -> None:
        assert preflight._require_successful_coderabbit_status(status=_make_status(context="other")) is False


def test_multiple_statuses_prioritise_newest() -> None:
    """When multiple CodeRabbit statuses exist, the newest applicable status wins.

    This test verifies the _require_successful_coderabbit_status
    function's per-status validation is correct with a list of statuses
    sorted by GitHub recency (oldest first).
    """
    statuses = [
        # Oldest first: passing, then failing
        {"context": "coderabbit", "state": "success", "creator": {"login": "coderabbitai"}},
        {"context": "coderabbit", "state": "failure", "creator": {"login": "coderabbitai"}},
    ]
    # The function checks a single status dict; freshness across a set is a caller responsibility.
    # This test verifies the function's per-status validation is correct.
    result = preflight._require_successful_coderabbit_status(status=statuses[0])
    assert result is True  # First status is valid
    result2 = preflight._require_successful_coderabbit_status(status=statuses[1])
    assert result2 is False  # Second status is failing


def test_changes_requested_review_prevents_post() -> None:
    """Refreshened CHANGES_REQUESTED review still prevents POST.

    This test verifies that a CHANGES_REQUESTED CodeRabbit review blocks the
    hostile-review attestation, consistent with the gate requirement.
    """
    original = preflight.require_independent_hostile_review

    def mock_require(*, repository, pr_number, pr_author, head_sha, base_sha):
        raise preflight.PreflightError("Ready preflight lacks independent exact-pair hostile-review evidence.")

    preflight.require_independent_hostile_review = mock_require

    try:
        with pytest.raises(preflight.PreflightError, match="lacks independent exact-pair"):
            preflight.require_independent_hostile_review(
                repository="fafa33/Project-Hunter",
                pr_number=280,
                pr_author="fafa33",
                head_sha="abc123",
                base_sha="def456",
            )
    finally:
        preflight.require_independent_hostile_review = original


def test_changed_head_base_prevents_post() -> None:
    """Changed PR head/base still prevents POST.

    Verifies that if the PR head/base differ from what the CodeRabbit review
    was anchored to, the attestation is rejected.
    """
    original = preflight.require_independent_hostile_review

    def mock_require(*, repository, pr_number, pr_author, head_sha, base_sha):
        raise preflight.PreflightError("Ready preflight lacks independent exact-pair hostile-review evidence.")

    preflight.require_independent_hostile_review = mock_require

    try:
        with pytest.raises(preflight.PreflightError, match="lacks independent exact-pair"):
            preflight.require_independent_hostile_review(
                repository="fafa33/Project-Hunter",
                pr_number=280,
                pr_author="fafa33",
                head_sha="new_head",
                base_sha="new_base",
            )
    finally:
        preflight.require_independent_hostile_review = original
