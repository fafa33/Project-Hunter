"""Regression tests for trusted dependency-bot identity normalization."""

from __future__ import annotations

import json

from hunter_governance_review.contracts import ChangedFile
from hunter_governance_review.deterministic import ValidationContext, is_trusted_dependency_pr
from hunter_governance_review.github_api import GhCliRunner, _normalize_author_login


def _payload(author_login: str) -> str:
    return json.dumps(
        {
            "number": 202,
            "title": "build(deps): bump dependencies",
            "body": "Bumps dependencies.",
            "state": "OPEN",
            "isDraft": False,
            "headRefName": "dependabot/pip/requirements/example",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
            "baseRefOid": "b" * 40,
            "mergeable": "MERGEABLE",
            "changedFiles": 1,
            "url": "https://github.com/fafa33/Project-Hunter/pull/202",
            "author": {"login": author_login},
        }
    )


def test_normalize_dependabot_github_app_identity() -> None:
    assert _normalize_author_login("app/dependabot") == "dependabot[bot]"
    assert _normalize_author_login("dependabot[bot]") == "dependabot[bot]"


def test_normalize_renovate_github_app_identity() -> None:
    assert _normalize_author_login("app/renovate") == "renovate[bot]"
    assert _normalize_author_login("renovate[bot]") == "renovate[bot]"


def test_unknown_app_identity_is_not_promoted() -> None:
    assert _normalize_author_login("app/not-dependabot") == "app/not-dependabot"


def test_gh_cli_dependabot_identity_reaches_trusted_dependency_path(monkeypatch) -> None:
    runner = GhCliRunner("fafa33/Project-Hunter", token="test")
    monkeypatch.setattr(runner, "_run", lambda _args: _payload("app/dependabot"))

    pr = runner.get_pull_request(202)
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]

    assert pr.author_login == "dependabot[bot]"
    assert is_trusted_dependency_pr(ValidationContext(pr=pr, files=files)) is True


def test_spoofed_human_identity_remains_untrusted(monkeypatch) -> None:
    runner = GhCliRunner("fafa33/Project-Hunter", token="test")
    monkeypatch.setattr(runner, "_run", lambda _args: _payload("human_attacker"))

    pr = runner.get_pull_request(202)
    files = [ChangedFile("requirements/ci-constraints.txt", "modified", 1, 1)]

    assert pr.author_login == "human_attacker"
    assert is_trusted_dependency_pr(ValidationContext(pr=pr, files=files)) is False
