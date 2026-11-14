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


def test_poll_interval_is_bounded_below_latency_budget():
    assert 0 < poll.INTERVAL_SECONDS <= 10
    assert poll.MAX_ACTIVE_PRS <= 5
    assert poll.MAX_DISPATCHES_PER_HOUR <= 60


def test_checkpoint_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    poll.save_state({745: "revision"}, [1000.0], path)
    assert poll.load_state(path) == ({745: "revision"}, [1000.0])
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_checkpoint_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{invalid")
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        poll.load_state(path)


def test_missing_checkpoint_is_initial_baseline(tmp_path):
    assert poll.load_state(tmp_path / "absent") == ({}, [])


def test_budget_is_reserved_before_dispatch_and_never_replayed(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    calls = []
    monkeypatch.setattr(poll, "STATE_PATH", path)
    monkeypatch.setattr(poll, "active_prs", lambda: (746,))
    monkeypatch.setattr(poll, "review_revision", lambda number: "changed")
    monkeypatch.setattr(poll, "dispatch", lambda number: calls.append(poll.load_state(path)))
    monkeypatch.setattr(poll.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    poll.save_state({746: "before"}, [], path)
    with pytest.raises(KeyboardInterrupt):
        poll.loop()
    assert len(calls) == 1
    assert calls[0][0] == {746: "changed"}
    assert len(calls[0][1]) == 1
    with pytest.raises(KeyboardInterrupt):
        poll.loop()
    assert len(calls) == 1


def test_checkpoint_write_failure_prevents_dispatch(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    poll.save_state({746: "before"}, [], path)
    monkeypatch.setattr(poll, "STATE_PATH", path)
    monkeypatch.setattr(poll, "active_prs", lambda: (746,))
    monkeypatch.setattr(poll, "review_revision", lambda number: "changed")
    monkeypatch.setattr(poll, "save_state", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(poll, "dispatch", lambda number: pytest.fail("unsafe dispatch"))
    with pytest.raises(OSError, match="disk full"):
        poll.loop()
