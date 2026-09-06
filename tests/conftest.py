from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

_INITIAL_STATUS: str | None = None


def _repository_status(root: Path) -> str | None:
    try:
        worktree = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if worktree.returncode != 0 or worktree.stdout.strip() != "true":
            return None
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def _is_xdist_worker(session: pytest.Session) -> bool:
    """Whether this process is a parallel worker rather than the controller.

    Issue #415 parallelised the suite. This check compares one whole-repository
    snapshot against another, so a worker running it would be reading a tree
    that its siblings are concurrently using -- a transient file belonging to
    another worker would look like this session leaking state. The controller
    is the only process that observes the session as a whole, so it is the only
    process that can answer the question the check is asking.
    """
    return hasattr(session.config, "workerinput")


def pytest_sessionstart(session: pytest.Session) -> None:
    global _INITIAL_STATUS
    if _is_xdist_worker(session):
        return
    _INITIAL_STATUS = _repository_status(Path(str(session.config.rootpath)))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _INITIAL_STATUS is None or _is_xdist_worker(session):
        return
    final_status = _repository_status(Path(str(session.config.rootpath)))
    if final_status is None or final_status != _INITIAL_STATUS:
        session.config.issue166_cleanliness_failure = (  # type: ignore[attr-defined]
            "full test session changed repository state\n"
            f"before:\n{_INITIAL_STATUS or '(clean)'}\n"
            f"after:\n{final_status if final_status is not None else '(repository unavailable)'}"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any) -> None:
    failure = getattr(terminalreporter.config, "issue166_cleanliness_failure", None)
    if failure:
        terminalreporter.write_sep("=", "repository cleanliness failure")
        terminalreporter.write_line(failure)


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_TEST_RUNTIME_ROOT", str(tmp_path))
