from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import _repository_status


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True)


def test_repository_status_skips_directories_outside_git_worktrees(tmp_path: Path) -> None:
    assert _repository_status(tmp_path) is None


def test_repository_status_tracks_normal_and_linked_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Test User")
    _git(repository, "config", "user.email", "test@example.com")
    (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "Initialize test repository")

    linked_worktree = tmp_path / "linked-worktree"
    _git(repository, "worktree", "add", "-b", "linked", str(linked_worktree))

    assert _repository_status(repository) == ""
    assert _repository_status(linked_worktree) == ""

    (linked_worktree / "runtime.txt").write_text("runtime\n", encoding="utf-8")
    assert _repository_status(linked_worktree) == "?? runtime.txt\n"
