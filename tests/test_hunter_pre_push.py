from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import hunter_pre_push
import pytest

ROOT = Path(__file__).resolve().parents[1]
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _update(
    sha: str = HEAD_A, *, local_ref: str = "refs/heads/feature", remote_ref: str = "refs/heads/feature"
) -> list[str]:
    return [f"{local_ref} {sha} {remote_ref} {hunter_pre_push.ZERO_SHA}\n"]


def _stub_issue_412_boundaries(monkeypatch) -> None:
    """Neutralise the Issue #412 provenance and receipt checks for this fixture.

    Both are enforced by ``enforce_pre_push`` and both have their own regressions
    in tests/test_issue_412_prevention_gate.py. Stubbing them here keeps each
    fixture below on the exact-head/clean-tree/preflight contract it was written
    for, instead of turning every one of them into an end-to-end git fixture.
    """

    monkeypatch.setattr(hunter_pre_push, "_validate_writer_provenance", lambda _head: None)
    monkeypatch.setattr(hunter_pre_push, "_validate_receipt_freshness", lambda _head: None)
    monkeypatch.setattr(hunter_pre_push, "report_pre_ready_review_state", lambda _head: None)


def test_pre_push_blocks_known_deterministic_failure_before_network_push(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)
    monkeypatch.setattr(
        hunter_pre_push.subprocess,
        "run",
        lambda command, *, check: SimpleNamespace(returncode=7),
    )

    assert hunter_pre_push.enforce_pre_push(_update()) == 7


def test_pre_push_proof_for_commit_a_cannot_authorize_commit_b(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="exact HEAD"):
        hunter_pre_push.enforce_pre_push(_update(HEAD_B))


def test_non_branch_source_refspec_targeting_remote_branch_cannot_bypass_exact_head(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="exact HEAD"):
        hunter_pre_push.enforce_pre_push(_update(HEAD_B, local_ref=HEAD_B, remote_ref="refs/heads/feature"))


def test_pre_push_rejects_dirty_tree_before_preflight(monkeypatch, tmp_path) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return HEAD_A
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return " M src/hunter/example.py"
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)

    with pytest.raises(RuntimeError, match="working tree must be clean"):
        hunter_pre_push.enforce_pre_push(_update())


def test_pre_push_rechecks_head_after_successful_preflight(monkeypatch, tmp_path) -> None:
    head_reads = iter((HEAD_A, HEAD_B))

    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return next(head_reads)
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    _stub_issue_412_boundaries(monkeypatch)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)
    monkeypatch.setattr(
        hunter_pre_push.subprocess,
        "run",
        lambda command, *, check: SimpleNamespace(returncode=0),
    )

    assert hunter_pre_push.enforce_pre_push(_update()) == 2


def test_tests_first_marker_selects_same_supported_mode_as_hosted_preflight(monkeypatch) -> None:
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: "tests-first-red\n")

    assert hunter_pre_push._select_preflight_mode(HEAD_A) == hunter_pre_push.TESTS_FIRST_RED_MODE
    assert hunter_pre_push._preflight_command(hunter_pre_push.TESTS_FIRST_RED_MODE) == (
        "python",
        "scripts/hunter_pr_preflight.py",
        "--mode",
        "tests-first-red",
    )


def test_invalid_tests_first_marker_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: "normal\n")

    with pytest.raises(RuntimeError, match="exactly tests-first-red"):
        hunter_pre_push._select_preflight_mode(HEAD_A)


def test_local_only_ignored_mode_marker_cannot_authorize_tests_first_red(monkeypatch, tmp_path) -> None:
    marker = tmp_path / ".hunter-preflight-mode"
    marker.write_text("tests-first-red\n", encoding="utf-8")
    monkeypatch.setattr(hunter_pre_push, "MODE_MARKER", marker)
    monkeypatch.setattr(hunter_pre_push, "_committed_marker", lambda _head: None)

    assert hunter_pre_push._select_preflight_mode(HEAD_A) == hunter_pre_push.NORMAL_MODE


def test_repository_hook_is_executable_and_calls_canonical_enforcer() -> None:
    hook = ROOT / ".githooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    text = hook.read_text(encoding="utf-8")
    assert "python scripts/hunter_pre_push.py" in text


def test_enforcer_builds_canonical_normal_preflight_command() -> None:
    assert hunter_pre_push._preflight_command(hunter_pre_push.NORMAL_MODE) == (
        "python",
        "scripts/hunter_pr_preflight.py",
        "--mode",
        "normal",
    )


def test_hook_installer_owns_repository_hooks_path() -> None:
    text = (ROOT / "scripts" / "install_hunter_git_hooks.py").read_text(encoding="utf-8")
    assert 'HOOKS_PATH = ".githooks"' in text
    assert '"git", "config", "core.hooksPath", HOOKS_PATH' in text
