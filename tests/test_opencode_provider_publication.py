"""Trusted OpenCode publication onto a per-authorization branch (contract I4).

The governed publication creates an authorization branch that does not exist
yet under a create-only lease, never takes over a branch someone else wrote,
and on failed validation undoes only its own publication: it deletes a branch
it created, or restores the exact previous head of a branch that existed.

These run against a real bare repository. Commit signing is the one thing the
test environment cannot perform, so the signing step is reduced to a plain
commit; every lease, push and rollback is the production code path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import hunter.automation.opencode_provider_runtime as runtime

BRANCH = "issue-423-0123456789abcdef"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Farhad5778\n\temail = 34549283+fafa33@users.noreply.github.com\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv(runtime._PUSH_TOKEN_ENV, raising=False)

    real_git = runtime._git

    def unsigned_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
        if args[:2] == ("commit", "-S"):
            args = ("commit", *args[2:])
        if args[:1] == ("verify-commit",):
            return ""
        return real_git(repo, *args, env=env)

    monkeypatch.setattr(runtime, "_git", unsigned_git)

    bare = tmp_path / "github.git"
    subprocess.run(("git", "init", "-q", "--bare", "-b", "main", str(bare)), check=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _run(workspace, "init", "-q", "-b", "main")
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run(workspace, "add", "-A")
    _run(workspace, "commit", "-q", "-m", "base")
    base = _run(workspace, "rev-parse", "HEAD")
    _run(workspace, "push", "-q", str(bare), "main")
    _run(workspace, "checkout", "-q", "-B", BRANCH, base)

    sandbox = tmp_path / "sandbox"
    shutil.copytree(workspace, sandbox, symlinks=True)
    (sandbox / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    root = tmp_path / "attempt"
    root.mkdir()
    return {
        "bare": bare,
        "url": f"file://{bare}",
        "workspace": workspace,
        "sandbox": sandbox,
        "root": root,
        "base": base,
    }


def _remote(bare: Path, branch: str = BRANCH) -> str | None:
    completed = subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"),
        cwd=bare,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or None


def test_remote_branch_head_reports_absence_and_exact_heads(repos: dict[str, Any]) -> None:
    assert runtime._remote_branch_head(repos["workspace"], BRANCH, repos["url"]) is None
    assert runtime._remote_branch_head(repos["workspace"], "main", repos["url"]) == repos["base"]


def test_publication_creates_the_authorization_branch_under_a_create_only_lease(
    repos: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "_validate_published_candidate", lambda *_args: None)
    head_after = runtime._publish_result(
        repos["workspace"], repos["sandbox"], BRANCH, repos["base"], repos["url"], repos["root"], None
    )
    assert _remote(repos["bare"]) == head_after
    assert _run(repos["bare"], "rev-parse", f"{head_after}^") == repos["base"]


def test_create_only_lease_never_takes_over_a_branch_someone_else_created(
    repos: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "_validate_published_candidate", lambda *_args: None)
    foreign = repos["root"].parent / "foreign"
    shutil.copytree(repos["workspace"], foreign, symlinks=True)
    (foreign / "module.py").write_text("VALUE = 99\n", encoding="utf-8")
    _run(foreign, "commit", "-q", "-am", "foreign write")
    _run(foreign, "push", "-q", repos["url"], f"HEAD:refs/heads/{BRANCH}")
    foreign_head = _remote(repos["bare"])

    with pytest.raises(runtime.ProviderAdapterError):
        runtime._publish_result(
            repos["workspace"], repos["sandbox"], BRANCH, repos["base"], repos["url"], repos["root"], None
        )
    assert _remote(repos["bare"]) == foreign_head


def test_failed_validation_deletes_only_the_branch_this_publication_created(
    repos: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(*_args: Any) -> None:
        raise runtime.ProviderAdapterError("published provider result failed exact-head targeted validation")

    monkeypatch.setattr(runtime, "_validate_published_candidate", reject)
    with pytest.raises(runtime.ProviderAdapterError):
        runtime._publish_result(
            repos["workspace"], repos["sandbox"], BRANCH, repos["base"], repos["url"], repos["root"], None
        )
    assert _remote(repos["bare"]) is None
    assert _remote(repos["bare"], "main") == repos["base"]


def test_failed_validation_restores_the_exact_previous_head_of_an_existing_branch(
    repos: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(repos["workspace"], "push", "-q", repos["url"], f"HEAD:refs/heads/{BRANCH}")

    def reject(*_args: Any) -> None:
        raise runtime.ProviderAdapterError("published provider result failed exact-head targeted validation")

    monkeypatch.setattr(runtime, "_validate_published_candidate", reject)
    with pytest.raises(runtime.ProviderAdapterError):
        runtime._publish_result(
            repos["workspace"],
            repos["sandbox"],
            BRANCH,
            repos["base"],
            repos["url"],
            repos["root"],
            repos["base"],
        )
    assert _remote(repos["bare"]) == repos["base"]
