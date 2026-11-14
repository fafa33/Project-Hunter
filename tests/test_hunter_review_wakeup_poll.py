"""M5 bounded wakeup tests; this detector is never a merge authority."""

import hunter_review_wakeup_poll as poll
import pytest


def response(*, resolved=False, state="COMMENTED", more=False):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": "a" * 40,
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": more},
                        "nodes": [
                            {
                                "id": "thread",
                                "isResolved": resolved,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [
                                        {
                                            "id": "finding",
                                            "body": "security finding",
                                            "updatedAt": "2026-11-14T00:00:00Z",
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "reviews": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"id": "review", "state": state, "submittedAt": "2026-11-14T00:00:00Z"}],
                    },
                }
            }
        }
    }


def test_new_review_and_resolution_change_revision(monkeypatch):
    monkeypatch.setattr(poll, "gh", lambda *args: response(resolved=False))
    first = poll.review_revision(745)
    monkeypatch.setattr(poll, "gh", lambda *args: response(resolved=True))
    assert poll.review_revision(745) != first
    monkeypatch.setattr(poll, "gh", lambda *args: response(state="CHANGES_REQUESTED"))
    assert poll.review_revision(745) != first


def test_review_pagination_fails_closed(monkeypatch):
    monkeypatch.setattr(poll, "gh", lambda *args: response(more=True))
    with pytest.raises(ValueError, match="pagination"):
        poll.review_revision(745)


def test_active_pr_budget_fails_closed(monkeypatch):
    monkeypatch.setattr(poll, "gh", lambda *args: [{"number": i} for i in range(1, 7)])
    with pytest.raises(ValueError, match="budget"):
        poll.active_prs()


def test_dispatch_targets_trusted_main_and_identity_only(monkeypatch):
    captured = []
    monkeypatch.setattr(poll.subprocess, "run", lambda argv, **kwargs: captured.append(argv))
    poll.dispatch(745)
    assert captured == [
        (
            "gh",
            "workflow",
            "run",
            "hunter-merge-readiness.yml",
            "--repo",
            poll.REPOSITORY,
            "--ref",
            "main",
            "-f",
            "pr_number=745",
        )
    ]
