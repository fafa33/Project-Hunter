from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ZERO_SHA = "0" * 40
NORMAL_MODE = "normal"
TESTS_FIRST_RED_MODE = "tests-first-red"
MODE_MARKER = Path(".hunter-preflight-mode")


def _run_git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _parse_updates(lines: Iterable[str]) -> list[tuple[str, str, str]]:
    updates: list[tuple[str, str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise ValueError("malformed pre-push ref update")
        local_ref, local_sha, remote_ref, _remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        updates.append((local_ref, local_sha, remote_ref))
    return updates


def _require_clean_tree() -> None:
    if _run_git("status", "--porcelain=v1", "--untracked-files=normal"):
        raise RuntimeError("working tree must be clean before push preflight")


def _require_exact_head(updates: list[tuple[str, str, str]], head_sha: str) -> None:
    branch_updates = [(remote_ref, local_sha) for _local_ref, local_sha, remote_ref in updates if remote_ref.startswith("refs/heads/")]
    if not branch_updates:
        return
    mismatched = [(remote_ref, local_sha) for remote_ref, local_sha in branch_updates if local_sha != head_sha]
    if mismatched:
        refs = ", ".join(remote_ref for remote_ref, _local_sha in mismatched)
        raise RuntimeError(
            "pre-push enforcement only authorizes the checked-out exact HEAD; "
            f"checkout the branch being pushed first: {refs}"
        )


def _select_preflight_mode() -> str:
    if not MODE_MARKER.exists():
        return NORMAL_MODE
    raw = MODE_MARKER.read_text(encoding="utf-8")
    mode = raw.rstrip("\n")
    if mode != TESTS_FIRST_RED_MODE:
        raise RuntimeError(".hunter-preflight-mode must contain exactly tests-first-red")
    return mode


def _preflight_command(mode: str) -> tuple[str, ...]:
    return ("python", "scripts/hunter_pr_preflight.py", "--mode", mode)


def enforce_pre_push(lines: Iterable[str]) -> int:
    updates = _parse_updates(lines)
    if not updates:
        return 0

    repo_root = Path(_run_git("rev-parse", "--show-toplevel")).resolve()
    os.chdir(repo_root)
    before_head = _run_git("rev-parse", "HEAD")
    _require_clean_tree()
    _require_exact_head(updates, before_head)
    mode = _select_preflight_mode()

    completed = subprocess.run(_preflight_command(mode), check=False)
    if completed.returncode != 0:
        print(
            f"[Hunter Pre-Push] BLOCKED: canonical {mode} preflight exited {completed.returncode}",
            file=sys.stderr,
        )
        return completed.returncode or 1

    after_head = _run_git("rev-parse", "HEAD")
    if after_head != before_head:
        print("[Hunter Pre-Push] BLOCKED: HEAD changed during preflight", file=sys.stderr)
        return 2
    try:
        _require_clean_tree()
        _require_exact_head(updates, after_head)
    except RuntimeError as error:
        print(f"[Hunter Pre-Push] BLOCKED: {error}", file=sys.stderr)
        return 2

    print(f"[Hunter Pre-Push] PASS: exact HEAD {after_head} passed canonical {mode} preflight")
    return 0


def main() -> int:
    try:
        return enforce_pre_push(sys.stdin)
    except (RuntimeError, ValueError) as error:
        print(f"[Hunter Pre-Push] BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
