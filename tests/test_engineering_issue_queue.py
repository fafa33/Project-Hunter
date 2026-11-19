"""Issue #437: deterministic owner-controlled selection and ingress handoff."""

from dataclasses import replace

import pytest

from hunter.automation.engineering_issue_queue import (
    IssueCandidate,
    QueueAuthorityError,
    handoff_selected_issue,
    select_next_issue,
)


def candidate(number: int, **kwargs: object) -> IssueCandidate:
    return replace(IssueCandidate(number, True, True, True, True, "P1", 0), **kwargs)


def test_priority_and_stable_tie_break_and_permutation() -> None:
    a, b, c = candidate(7), candidate(3), candidate(9, priority="P0")
    assert select_next_issue((a, b, c), active_lifecycles=0).issue_number == 9
    x = select_next_issue((a, b), active_lifecycles=0)
    y = select_next_issue((b, a), active_lifecycles=0)
    assert x.issue_number == 3
    assert x.identity == y.identity


def test_blocker_skipped_and_missing_and_cycles_refused() -> None:
    assert (
        select_next_issue(
            (candidate(1, priority="P0", blockers=(2,)), candidate(2), candidate(3)), active_lifecycles=0
        ).issue_number
        == 2
    )
    with pytest.raises(QueueAuthorityError, match="reference"):
        select_next_issue((candidate(1, blockers=(4,)),), active_lifecycles=0)
    with pytest.raises(QueueAuthorityError, match="cycle"):
        select_next_issue((candidate(1, blockers=(2,)), candidate(2, blockers=(1,))), active_lifecycles=0)


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_approved": False},
        {"ready": False},
        {"engineering": False},
        {"open": False},
        {"active_owner": True},
    ],
)
def test_eligibility_fail_closed(changes: dict[str, object]) -> None:
    result = select_next_issue((candidate(1, **changes),), active_lifecycles=0)
    assert result.issue_number is None


def test_active_owner_and_override() -> None:
    issues = (candidate(1, priority="P0"), candidate(2))
    assert select_next_issue(issues, active_lifecycles=1).issue_number is None
    selected = select_next_issue(issues, active_lifecycles=0, owner_override=2)
    assert selected.issue_number == 2
    assert selected.reason == "owner-override"
    with pytest.raises(QueueAuthorityError, match="not eligible"):
        select_next_issue((candidate(1, ready=False),), active_lifecycles=0, owner_override=1)


@pytest.mark.parametrize(
    "issues",
    [
        (candidate(1, priority="UNKNOWN"),),
        (candidate(1), candidate(1)),
        (candidate(1, order=-1),),
        (candidate(1, blockers=(1,)),),
    ],
)
def test_invalid_snapshot_refused(issues: tuple[IssueCandidate, ...]) -> None:
    with pytest.raises(QueueAuthorityError):
        select_next_issue(issues, active_lifecycles=0)


def test_no_selection_and_provider_independence() -> None:
    result = select_next_issue((), active_lifecycles=0)
    assert result.issue_number is None
    assert result.reason == "no-eligible-issue"
    assert not hasattr(candidate(1), "provider")


def test_handoff_uses_only_identity_and_existing_ingress() -> None:
    selected = select_next_issue((candidate(5),), active_lifecycles=0)
    assert handoff_selected_issue(
        selected,
        verified_issue_request=lambda n: ("verified", n),
        governed_ingress=lambda request: ("ingress", request),
        trusted_candidates=(candidate(5),),
        active_lifecycles=0,
    ) == ("ingress", ("verified", 5))
    with pytest.raises(QueueAuthorityError):
        handoff_selected_issue(
            select_next_issue((), active_lifecycles=0),
            verified_issue_request=lambda n: n,
            governed_ingress=lambda x: x,
            trusted_candidates=(),
            active_lifecycles=0,
        )


def test_handoff_rejects_stale_or_forged_selection() -> None:
    from dataclasses import replace

    original = (candidate(5),)
    selected = select_next_issue(original, active_lifecycles=0)
    kwargs = dict(verified_issue_request=lambda n: n, governed_ingress=lambda x: x, active_lifecycles=0)
    with pytest.raises(QueueAuthorityError, match="stale or forged"):
        handoff_selected_issue(selected, trusted_candidates=(candidate(5, ready=False),), **kwargs)
    with pytest.raises(QueueAuthorityError, match="stale or forged"):
        handoff_selected_issue(replace(selected, identity="0" * 64), trusted_candidates=original, **kwargs)
