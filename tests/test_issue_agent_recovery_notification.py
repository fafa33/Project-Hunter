"""Owner-approved #706: exhausted recovery budget produces one bounded, sanitized Issue notification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_state as state

SPEC = importlib.util.spec_from_file_location(
    "hunter_issue_agent_lifecycle_notification_test",
    Path(__file__).resolve().parents[1] / "scripts/hunter_issue_agent_lifecycle.py",
)
assert SPEC and SPEC.loader
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)

AUTH = "hunter-issue-agent-authorization:" + "a" * 64
BRANCH = "issue-705-recovery"
HEAD = "b" * 40


class FakeGitHub:
    def __init__(self, comments=None):
        self.comments = list(comments or [])
        self.posts = []
        self.unknown = False

    def get(self, path):
        if self.unknown:
            return control.Read("unknown")
        assert "/issues/705/comments?per_page=100&page=1" in path
        return control.Read("ok", self.comments)

    def reply(self, path, body):
        assert path.endswith("/issues/705/comments")
        self.posts.append(body["body"])
        self.comments.append({"body": body["body"], "user": {"login": "github-actions[bot]"}})
        return control.Read("ok", {"id": 123})


def view(*, attempts=2, failed=True):
    parent = SimpleNamespace(
        authorization_id=AUTH,
        state=state.FAILED if failed else state.COMPLETED,
        evidence={
            state.FAILED: {"code": state.PREFLIGHT_RECOVERY_TRIGGER, "failed_from_state": state.PUBLISHED},
            state.PUBLISHED: {"head_sha": HEAD},
        },
        binding=lambda key: BRANCH,
    )
    return SimpleNamespace(claimed=[AUTH], authorizations={AUTH: parent}, attempts=attempts)


def test_exhausted_notification_once(monkeypatch):
    monkeypatch.setattr(state, "preflight_recoveries", lambda v, branch: [object()] * v.attempts)
    github = FakeGitHub()
    config = SimpleNamespace(repository="fafa33/Project-Hunter")
    lifecycle._notify_recovery_budget_exhausted(github, config, view(), 705)
    lifecycle._notify_recovery_budget_exhausted(github, config, view(), 705)
    assert len(github.posts) == 1
    assert AUTH in github.posts[0] and HEAD in github.posts[0]
    assert "Owner action" in github.posts[0]


def test_no_notification_before_exhaustion(monkeypatch):
    monkeypatch.setattr(state, "preflight_recoveries", lambda v, branch: [object()] * v.attempts)
    github = FakeGitHub()
    lifecycle._notify_recovery_budget_exhausted(
        github, SimpleNamespace(repository="fafa33/Project-Hunter"), view(attempts=1), 705
    )
    assert not github.posts


def test_indefinite_comment_read_fails_closed(monkeypatch):
    monkeypatch.setattr(state, "preflight_recoveries", lambda v, branch: [object()] * v.attempts)
    github = FakeGitHub()
    github.unknown = True
    with pytest.raises(control.FactsUnavailable):
        lifecycle._notify_recovery_budget_exhausted(
            github, SimpleNamespace(repository="fafa33/Project-Hunter"), view(), 705
        )
    assert not github.posts


def test_untrusted_comment_cannot_suppress_notification(monkeypatch):
    monkeypatch.setattr(state, "preflight_recoveries", lambda v, branch: [object()] * v.attempts)
    marker = f"<!-- hunter-preflight-recovery-exhausted:{AUTH} -->"
    github = FakeGitHub([{"body": marker, "user": {"login": "external-user"}}])
    lifecycle._notify_recovery_budget_exhausted(
        github, SimpleNamespace(repository="fafa33/Project-Hunter"), view(), 705
    )
    assert len(github.posts) == 1
