#!/usr/bin/env python3
"""Repository-owned OpenCode adapter for the governed Issue Agent fallback runtime.

The fallback runtime passes only the signed non-content Smart Prompt handoff on
stdin. This adapter reconstructs the exact retained prompt artifact from the
canonical Evidence database, invokes OpenCode in an isolated checkout without
publication credentials, and publishes only the configured execution branch.
It never accepts prompt authority or signing material.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff
from hunter.evidence_intelligence.pre_model_persistence import EvidencePreModelPersistenceRepository
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

RATE_LIMIT_EXIT_CODE = 75
_DB_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
_REPO_ENV = "HUNTER_AGENT_REPO_DIR"
_REPOSITORY_ENV = "HUNTER_ISSUE_AGENT_REPOSITORY"
_BRANCH_ENV = "HUNTER_AGENT_BRANCH"
_EXECUTABLE_ENV = "HUNTER_OPENCODE_EXECUTABLE"
_MODEL_ENV = "HUNTER_OPENCODE_MODEL"
_PUSH_TOKEN_ENV = "HUNTER_AGENT_GITHUB_PUSH_TOKEN"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MODEL_CREDENTIAL_ENV = {
    _PUSH_TOKEN_ENV,
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SSH_AUTH_SOCK",
}


class ProviderAdapterError(RuntimeError):
    """Raised when the adapter cannot prove a safe provider execution."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderAdapterError(f"{name} is required")
    return value


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ProviderAdapterError(detail)
    return completed.stdout.strip()


def _exact_prompt(document: str, database: Path) -> str:
    handoff = PromptAutomationEnvelopeHandoff.from_json(document)
    repository = EvidenceIntelligenceRepository(database)
    reconstruction = EvidencePreModelPersistenceRepository(repository).strict_known_reconstruction(
        handoff.build_record_id,
        datetime.now(UTC),
    )
    prompt = reconstruction.exact_prompt
    if not prompt:
        raise ProviderAdapterError(
            f"exact prompt reconstruction unavailable for build {handoff.build_record_id} ({reconstruction.reason_code})"
        )
    return prompt


def _canonical_push_url() -> str:
    repository = _required_env(_REPOSITORY_ENV)
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ProviderAdapterError("configured repository must be an owner/name GitHub repository")
    return f"https://github.com/{repository}.git"


def _push(repo: Path, branch: str, push_url: str) -> None:
    token = os.environ.get(_PUSH_TOKEN_ENV, "").strip()
    if not token:
        _git(repo, "push", push_url, f"HEAD:refs/heads/{branch}")
        return

    with tempfile.TemporaryDirectory(prefix="hunter-git-askpass-") as directory:
        askpass = Path(directory) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$HUNTER_GIT_PUSH_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["HUNTER_GIT_PUSH_TOKEN"] = token
        _git(repo, "push", push_url, f"HEAD:refs/heads/{branch}", env=env)


def _model_environment(credential_home: Path) -> dict[str, str]:
    child_env = dict(os.environ)
    for name in tuple(child_env):
        if name in _MODEL_CREDENTIAL_ENV or name.startswith("GIT_"):
            child_env.pop(name, None)
    child_env["HOME"] = str(credential_home)
    child_env["XDG_CONFIG_HOME"] = str(credential_home / ".config")
    child_env["GIT_CONFIG_GLOBAL"] = os.devnull
    child_env["GIT_CONFIG_NOSYSTEM"] = "1"
    child_env["GIT_TERMINAL_PROMPT"] = "0"
    return child_env


def _clone_attempt(repo: Path, sandbox: Path, branch: str, head_before: str, push_url: str) -> Path:
    completed = subprocess.run(
        ("git", "clone", "--no-hardlinks", str(repo), str(sandbox)),
        cwd=repo.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "isolated clone failed"
        raise ProviderAdapterError(detail)
    _git(sandbox, "checkout", "-B", branch, head_before)
    _git(sandbox, "remote", "set-url", "origin", push_url)
    if _git(sandbox, "rev-parse", "HEAD") != head_before:
        raise ProviderAdapterError("isolated provider checkout does not match pre-attempt HEAD")
    return sandbox


def _sync_primary_checkout(repo: Path, branch: str, expected_head: str, push_url: str) -> None:
    if _git(repo, "branch", "--show-current") != branch:
        raise ProviderAdapterError("primary checkout changed branch before publication sync")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ProviderAdapterError("primary checkout became dirty before publication sync")
    _git(repo, "fetch", "--no-tags", push_url, f"refs/heads/{branch}")
    if _git(repo, "rev-parse", "FETCH_HEAD") != expected_head:
        raise ProviderAdapterError("published GitHub head does not match provider result")
    _git(repo, "reset", "--hard", expected_head)


def run(document: str) -> int:
    repo = Path(_required_env(_REPO_ENV)).resolve()
    branch = _required_env(_BRANCH_ENV)
    database = Path(_required_env(_DB_ENV)).resolve()
    push_url = _canonical_push_url()
    if not repo.is_dir():
        raise ProviderAdapterError("configured repository checkout does not exist")
    if not database.is_file():
        raise ProviderAdapterError("configured Evidence database does not exist")

    if _git(repo, "branch", "--show-current") != branch:
        raise ProviderAdapterError("provider checkout is not on the authorized execution branch")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ProviderAdapterError("provider checkout must be clean before execution")
    head_before = _git(repo, "rev-parse", "HEAD")

    prompt = _exact_prompt(document, database)
    executable_name = os.environ.get(_EXECUTABLE_ENV, "opencode").strip() or "opencode"
    executable = shutil.which(executable_name)
    if executable is None:
        raise ProviderAdapterError("OpenCode executable is unavailable")

    argv = [executable, "run"]
    model = os.environ.get(_MODEL_ENV, "").strip()
    if model:
        argv.extend(("--model", model))
    argv.append(prompt)

    with tempfile.TemporaryDirectory(prefix="hunter-opencode-attempt-") as directory:
        root = Path(directory)
        sandbox = _clone_attempt(repo, root / "repo", branch, head_before, push_url)
        credential_home = root / "credential-home"
        credential_home.mkdir(mode=0o700)
        (credential_home / ".config").mkdir(mode=0o700)
        child_env = _model_environment(credential_home)

        completed = subprocess.run(argv, cwd=sandbox, env=child_env, check=False, timeout=900)
        if completed.returncode == RATE_LIMIT_EXIT_CODE:
            return RATE_LIMIT_EXIT_CODE
        if completed.returncode != 0:
            return 1

        if _git(sandbox, "branch", "--show-current") != branch:
            raise ProviderAdapterError("provider changed the authorized execution branch")
        if _git(sandbox, "status", "--porcelain=v1", "--untracked-files=normal"):
            raise ProviderAdapterError("provider left an uncommitted working tree")
        head_after = _git(sandbox, "rev-parse", "HEAD")
        if head_after == head_before:
            raise ProviderAdapterError("provider reported completion without advancing local HEAD")

        _push(sandbox, branch, push_url)
        _sync_primary_checkout(repo, branch, head_after, push_url)
    return 0


def main() -> int:
    try:
        document = sys.stdin.read()
        if not document.strip():
            raise ProviderAdapterError("canonical handoff stdin is empty")
        return run(document)
    except (OSError, subprocess.TimeoutExpired, ProviderAdapterError, ValueError) as error:
        print(f"OpenCode provider adapter failed closed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
