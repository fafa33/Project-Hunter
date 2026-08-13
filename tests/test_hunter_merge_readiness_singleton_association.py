"""Governance evidence must never cross a pull-request boundary.

The invariant preserved from #257::

    GovernanceEvidence(PR=A, HEAD=H) must never satisfy
    MergeReadiness(PR=B, HEAD=H)   when A != B

#257 established that invariant by inference: it resolved the workflow run
behind a Governance status and required either a singleton pull-request
association or a head SHA belonging to exactly one open pull request, failing
closed when neither proof was available.

The current-state reconciler keeps the invariant and strengthens the proof. The
evaluator now stamps the pull-request number it evaluated directly into its
status description, so ownership is *stated* by the evidence rather than
reconstructed from run metadata. Everything that cannot state its owner --
including every legacy status published before this stamp existed -- is
unattributable and unusable. That is strictly more fail-closed than the
inference it replaces: the old head-uniqueness fallback accepted evidence that
named no pull request at all, and this does not.
"""

from __future__ import annotations

import hunter_merge_readiness as core
import pytest
from hunter_governance_revision import parse_marker, render_marker
from hunter_readiness_harness import FakeGitHub, install


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


SHARED_HEAD = "one_shared_head" * 2


def _two_pull_requests_on_one_head(gh: FakeGitHub) -> None:
    gh.add_pull_request(123, head_sha=SHARED_HEAD)
    gh.add_pull_request(456, head_sha=SHARED_HEAD)
    gh.green_required_checks(SHARED_HEAD)


# --- the marker itself ------------------------------------------------------


def test_marker_round_trips():
    assert parse_marker(render_marker(123, "0123456789ab") + " Approved for head abc.") == (123, "0123456789ab")


def test_unmarked_description_is_unattributable():
    assert parse_marker("Approved for head abc1234 on base def5678.") is None
    assert parse_marker("") is None
    assert parse_marker(None) is None


def test_malformed_marker_is_unattributable():
    assert parse_marker("[hgr:123:not-hex-at-all] Approved") is None
    assert parse_marker("[hgr::0123456789ab] Approved") is None
    assert parse_marker("[hgr:123:0123456789] Approved") is None


# --- evidence attribution ---------------------------------------------------


def test_evidence_for_one_pull_request_never_satisfies_the_other(gh):
    _two_pull_requests_on_one_head(gh)
    gh.publish_governance(123, "success")

    owner_state = core.read_current_state(123)
    other_state = core.read_current_state(456)

    # Attribution: only PR #123's own evidence is usable, and only by #123.
    assert owner_state.governance is not None
    assert owner_state.governance.pull_request_number == 123
    assert other_state.governance is None

    # Publication: neither may go green, because the readiness status is keyed
    # by (SHA, context) and both pull requests share that exact status object.
    assert core.decide(owner_state).state == "pending"
    assert core.decide(other_state).state == "pending"


def test_forged_pull_request_number_in_the_marker_is_rejected_for_the_other_pr(gh):
    """A marker naming PR #123 is rejected by PR #456 even on the same head."""
    _two_pull_requests_on_one_head(gh)
    gh.publish_governance(456, "success", marked_pull_request=123)

    state = core.read_current_state(456)

    assert state.governance is None
    assert any("produced for PR #123" in reason for reason in state.unusable_governance_reasons)


def test_a_shared_head_withholds_green_even_when_both_are_fully_evidenced(gh):
    """Evidence attribution succeeding is not sufficient to publish green.

    Both pull requests have their own valid Governance evidence and would each
    be ready in isolation. They still cannot go green: a commit status is keyed
    by (SHA, context), so branch protection reads the *same status object* for
    both, and a success would assert mergeability for a pull request whose own
    blockers were never evaluated.
    """
    _two_pull_requests_on_one_head(gh)
    gh.publish_governance(123, "success")
    gh.publish_governance(456, "success")

    first = core.read_current_state(123)
    second = core.read_current_state(456)

    assert first.governance is not None and first.governance.pull_request_number == 123
    assert second.governance is not None and second.governance.pull_request_number == 456
    assert first.shared_head_pull_requests == (456,)
    assert second.shared_head_pull_requests == (123,)
    assert core.decide(first).state == "pending"
    assert "shared with open PR #456" in core.decide(first).description
    assert core.decide(second).state == "pending"


def test_green_returns_once_the_head_is_no_longer_shared(gh):
    """The withholding is a property of current state, not a permanent penalty."""
    _two_pull_requests_on_one_head(gh)
    gh.publish_governance(123, "success")
    assert core.decide(core.read_current_state(123)).state == "pending"

    gh.pulls[456]["state"] = "closed"

    state = core.read_current_state(123)
    assert state.shared_head_pull_requests == ()
    assert core.decide(state).state == "success"


def test_legacy_unstamped_evidence_is_unusable_even_on_a_unique_head(gh):
    """Strictly stronger than the head-uniqueness fallback it replaces."""
    gh.add_pull_request(789, head_sha="unique_head" * 3)
    gh.green_required_checks("unique_head" * 3)
    gh.publish_governance(789, "success", marker=False)

    state = core.read_current_state(789)

    assert state.governance is None
    assert core.decide(state).state == "pending"


def test_resolution_rejects_every_unattributable_form(gh):
    revision = "0123456789ab"
    statuses = [
        {"id": 1, "context": "Hunter Governance Review", "state": "success", "description": "no marker"},
        {
            "id": 2,
            "context": "Hunter Governance Review",
            "state": "success",
            "description": render_marker(999, revision),
        },
        {
            "id": 3,
            "context": "Hunter Governance Review",
            "state": "success",
            "description": render_marker(123, "cccccccccccc"),
        },
        {"id": 4, "context": "Some Other Check", "state": "success", "description": render_marker(123, revision)},
    ]

    evidence, reasons = core.resolve_governance_evidence(statuses, 123, revision)

    assert evidence is None
    assert len(reasons) == 3


def test_resolution_accepts_only_the_exact_pair(gh):
    revision = "0123456789ab"
    statuses = [
        {
            "id": 9,
            "context": "Hunter Governance Review",
            "state": "success",
            "description": render_marker(123, revision),
        }
    ]

    evidence, reasons = core.resolve_governance_evidence(statuses, 123, revision)

    assert evidence == core.GovernanceEvidence(state="success", pull_request_number=123, revision=revision)
    assert reasons == ()


@pytest.mark.parametrize("order", [("failure", "success"), ("success", "failure")])
def test_disagreeing_verdicts_for_one_revision_are_settled_by_identity_not_recency(gh, order):
    """A transient REVIEW_FAILED must never permanently outrank a real approval.

    Both statuses name the same (pull request, revision) pair, so both describe
    the same deterministic evaluation. Whichever was written last is irrelevant:
    the outcome is the same in either order.
    """
    revision = "0123456789ab"
    statuses = [
        {
            "id": index,
            "context": "Hunter Governance Review",
            "state": state,
            "description": render_marker(123, revision),
        }
        for index, state in enumerate(order, start=1)
    ]

    evidence, _ = core.resolve_governance_evidence(statuses, 123, revision)

    assert evidence is not None
    assert evidence.state == "success"
