from __future__ import annotations

import hunter_writer_commit_finalizer as finalizer


def test_no_judgement_is_nonblocking(monkeypatch):
    monkeypatch.delenv("HUNTER_REVIEW_JUDGEMENT", raising=False)
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main() == 0


def test_partial_judgement_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("HUNTER_REVIEW_JUDGEMENT", "judgement.json")
    monkeypatch.delenv("HUNTER_GOVERNING_ISSUE", raising=False)
    assert finalizer.main() == 2


def test_commit_hook_uses_shared_finalizer():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hook = (root / ".githooks/pre-commit").read_text(encoding="utf-8")
    assert "scripts/hunter_writer_commit_finalizer.py" in hook
    assert "hunter_pre_ready_review as review" in (root / "scripts/hunter_writer_commit_finalizer.py").read_text(
        encoding="utf-8"
    )


def test_staged_snapshot_binds_index_not_worktree(tmp_path):
    """A remediation staged after HEAD must be visible to the review finalizer."""
    import subprocess

    def git(*args):
        return subprocess.check_output(("git", *args), cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Hunter Test")
    git("config", "user.email", "hunter@example.invalid")
    path = tmp_path / "change.txt"
    path.write_text("base\n")
    git("add", "change.txt")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    path.write_text("staged remediation\n")
    git("add", "change.txt")
    path.write_text("unstaged later edit\n")
    staged_head = finalizer._staged_review_head(tmp_path)
    assert git("rev-parse", "HEAD") == base
    assert git("show", f"{staged_head}:change.txt") == "staged remediation"
    assert path.read_text() == "unstaged later edit\n"
    assert git("diff", "--name-only", base, staged_head) == "change.txt"


def test_staged_snapshot_repeat_does_not_change_index(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(("git", *args), cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Hunter Test")
    git("config", "user.email", "hunter@example.invalid")
    (tmp_path / "a").write_text("one")
    git("add", "a")
    git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
    (tmp_path / "a").write_text("two")
    git("add", "a")
    before = git("write-tree")
    first = finalizer._staged_review_head(tmp_path)
    second = finalizer._staged_review_head(tmp_path)
    assert git("write-tree") == before
    assert git("show", f"{first}:a") == git("show", f"{second}:a") == "two"
