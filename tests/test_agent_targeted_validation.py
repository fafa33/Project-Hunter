"""Targeted validation of an authorization branch's history (contract I2).

An authorization branch is its signed base plus linear commits: main is never
merged into it and it is never rebased, because a changed base is a new
authorization and a new branch. Paired positive and negative fixtures pin the
semantic boundary rather than the wording.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hunter.automation import agent_targeted_validation as validation


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Farhad5778\n\temail = 34549283+fafa33@users.noreply.github.com\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _commit(path, "base.txt", "base\n")
    return path


def test_linear_commits_on_the_signed_base_are_admitted(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "issue-423-0123456789abcdef")
    _commit(repo, "one.txt", "1\n")
    head = _commit(repo, "two.txt", "2\n")
    monkeypatch.setenv("HUNTER_AGENT_BASE_SHA", base)
    validation._require_linear_from_signed_base(repo, head)


def test_merging_main_into_the_branch_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "issue-423-0123456789abcdef")
    _commit(repo, "one.txt", "1\n")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "later.txt", "later main\n")
    _git(repo, "checkout", "-q", "issue-423-0123456789abcdef")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "main")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("HUNTER_AGENT_BASE_SHA", base)
    with pytest.raises(validation.ValidationAdapterError, match="merge commit"):
        validation._require_linear_from_signed_base(repo, head)


def test_a_head_rebased_off_the_signed_base_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "later.txt", "later main\n")
    _git(repo, "checkout", "-q", "--orphan", "issue-423-0123456789abcdef")
    head = _commit(repo, "unrelated.txt", "unrelated\n")
    monkeypatch.setenv("HUNTER_AGENT_BASE_SHA", base)
    with pytest.raises(validation.ValidationAdapterError, match="does not descend"):
        validation._require_linear_from_signed_base(repo, head)


def test_a_head_that_does_not_advance_the_base_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("HUNTER_AGENT_BASE_SHA", base)
    with pytest.raises(validation.ValidationAdapterError, match="does not advance"):
        validation._require_linear_from_signed_base(repo, base)


@pytest.mark.parametrize("malformed", ["HEAD", "main", "A" * 40, "a" * 39, "a" * 40 + "\n0"])
def test_a_malformed_signed_base_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch, malformed: str) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("HUNTER_AGENT_BASE_SHA", malformed)
    with pytest.raises(validation.ValidationAdapterError, match="exact lowercase commit SHA"):
        validation._require_linear_from_signed_base(repo, head)


def test_a_non_authorization_execution_keeps_its_existing_contract(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator-supplied n8n fallback path carries no signed base.
    monkeypatch.delenv("HUNTER_AGENT_BASE_SHA", raising=False)
    validation._require_linear_from_signed_base(repo, _git(repo, "rev-parse", "HEAD"))
