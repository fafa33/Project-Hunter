"""Event and publication times are not part of Merge Readiness semantics.

This file previously proved the *opposite*: that readiness correctly ordered a
Governance Review run's start time against a durable "semantic event time"
recorded from a ``pull_request_target`` payload, so that workflow scheduling
latency could not make a fresh review look stale. That whole mechanism existed to
reconstruct current state from delivery history, and it is gone.

Its replacement is an identity comparison (see ``hunter_governance_revision``),
so the tests below assert the inverse property: no timestamp anywhere in the
system -- status ``created_at``, workflow ``run_started_at``, PR ``updated_at``,
comment or review times, or their relative ordering -- can change a readiness
result. The one remaining time comparison in the controller is
``owner_acknowledged_comment``, which compares a comment's own content time
against the reaction applied to it; that is an object's own history, not
delivery choreography, and it is covered here too.
"""

from __future__ import annotations

import hunter_merge_readiness as core
import pytest
from hunter_readiness_harness import FakeGitHub, install, ready_pull_request


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


_TIMESTAMPS = (
    "1970-01-01T00:00:00Z",
    "2026-08-13T00:00:00Z",
    "2026-08-13T00:00:00Z",  # identical to the previous value: same-second events
    "2099-12-31T23:59:59Z",
)


def _stamp_everything(gh: FakeGitHub, number: int, value: str) -> None:
    head = gh.head_sha(number)
    gh.pulls[number]["updated_at"] = value
    gh.pulls[number]["created_at"] = value
    for status in gh.statuses.get(head, []):
        status["created_at"] = value
    for comment in gh.comments.get(number, []):
        comment["created_at"] = value
        comment["updated_at"] = value


def test_no_timestamp_assignment_changes_the_readiness_result(gh):
    ready_pull_request(gh)
    results = set()
    revisions = set()

    for value in _TIMESTAMPS:
        _stamp_everything(gh, 501, value)
        state = core.read_current_state(501)
        revisions.add(state.semantic_revision())
        results.add(core.decide(state))

    assert len(results) == 1
    assert len(revisions) == 1
    assert next(iter(results)).state == "success"


def test_governance_status_published_before_or_after_a_pr_edit_is_judged_only_by_identity(gh):
    """Publication order carries no meaning; only the stamped revision does."""
    head = ready_pull_request(gh)
    assert core.decide(core.read_current_state(501)).state == "success"

    # The Governance status is made to look far older than every other record.
    for status in gh.statuses[head]:
        if status["context"] == "Hunter Governance Review":
            status["created_at"] = "1999-01-01T00:00:00Z"
    gh.pulls[501]["updated_at"] = "2099-01-01T00:00:00Z"

    assert core.decide(core.read_current_state(501)).state == "success"


def test_a_late_republication_of_superseded_evidence_cannot_win_by_recency(gh):
    """A newer *write* of an older *evaluation* must not displace current truth.

    Persistence order is not semantic order. A Governance Review re-run of an
    older revision can land after the current one; recency must not decide.
    """
    ready_pull_request(gh)
    superseded_revision = gh.governance_revision_for(501)

    gh.pulls[501]["title"] = "fix: converge merge readiness (revised title)"
    gh.publish_governance(501, "success")  # evidence for the current revision
    assert core.decide(core.read_current_state(501)).state == "success"

    # Now a stale run republishes the old revision's success, with a higher id.
    gh.publish_governance(501, "success", revision=superseded_revision)

    state = core.read_current_state(501)
    assert state.governance is not None
    assert state.governance.revision != superseded_revision
    assert core.decide(state).state == "success"

    # And a stale *failure* for the old revision must not block the current one.
    gh.publish_governance(501, "failure", revision=superseded_revision)

    assert core.decide(core.read_current_state(501)).state == "success"


def test_head_ordering_between_statuses_never_substitutes_for_identity(gh):
    """Even the newest status on the head is ignored when it names another revision."""
    head = ready_pull_request(gh)
    gh.statuses[head] = []
    gh.publish_governance(501, "success", revision="ffffffffffff")

    state = core.read_current_state(501)

    assert state.governance is None
    assert core.decide(state).state == "pending"


def test_acknowledgement_time_is_compared_against_the_comment_it_acknowledges(gh):
    """The single remaining time comparison is an object's own content history."""
    ready_pull_request(gh)
    gh.add_comment(501, 900)
    gh.comments[501][0]["updated_at"] = "2026-08-13T10:00:00Z"

    gh.acknowledge(900, at="2026-08-13T09:59:59Z")
    assert core.decide(core.read_current_state(501)).state == "failure"

    gh.acknowledge(900, at="2026-08-13T10:00:00Z")
    assert core.decide(core.read_current_state(501)).state == "success"


def test_the_controller_module_no_longer_carries_timestamp_choreography():
    """Regression guard: the removed mechanisms must not quietly return."""
    for removed in (
        "parse_time",
        "get_latest_invalidation_time",
        "latest_semantic_invalidation_time",
        "backfill_semantic_baseline",
        "acquire_pr_lock",
        "release_pr_lock",
        "record_semantic_pr_invalidation",
        "_confirm_still_fresh_before_success",
        "RECONCILIATION_EVENT_NAMES",
    ):
        assert not hasattr(core, removed), f"{removed} was reintroduced into the controller"
