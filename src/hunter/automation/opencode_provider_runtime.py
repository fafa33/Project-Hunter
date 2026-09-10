"""Fail-closed OpenCode provider adapter for the governed Issue Agent runtime."""

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
_SANDBOX_EXECUTABLE_ENV = "HUNTER_OPENCODE_SANDBOX_EXECUTABLE"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PUBLICATION_CREDENTIAL_ENV = {
    _PUSH_TOKEN_ENV,
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SSH_AUTH_SOCK",
}
_PRIVATE_RUNTIME_ENV = {
    _DB_ENV,
    _REPO_ENV,
    _REPOSITORY_ENV,
    "HUNTER_ISSUE_AGENT_REPO_DIR",
}
_CANONICAL_WRITER_NAME = "Farhad5778"
_CANONICAL_WRITER_EMAIL = "34549283+fafa33@users.noreply.github.com"


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
            f"exact prompt reconstruction unavailable for build {handoff.build_record_id} "
            f"({reconstruction.reason_code})"
        )
    return prompt


def _canonical_push_url() -> str:
    repository = _required_env(_REPOSITORY_ENV)
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ProviderAdapterError("configured repository must be an owner/name GitHub repository")
    return f"https://github.com/{repository}.git"


def _publication_environment(token: str, askpass: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HUNTER_GIT_PUSH_TOKEN"] = token
    return env


def _push_trusted(
    repo: Path,
    branch: str,
    push_url: str,
    expected_remote_head: str,
    *,
    source_ref: str = "HEAD",
    run_pre_push: bool = True,
) -> None:
    token = os.environ.get(_PUSH_TOKEN_ENV, "").strip()
    args = [
        "push",
        f"--force-with-lease=refs/heads/{branch}:{expected_remote_head}",
    ]
    if not run_pre_push:
        args.append("--no-verify")
    args.extend((push_url, f"{source_ref}:refs/heads/{branch}"))
    if not token:
        _git(repo, *args)
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
        _git(repo, *args, env=_publication_environment(token, askpass))


def _model_environment(credential_home: Path) -> dict[str, str]:
    child_env = dict(os.environ)
    for name in tuple(child_env):
        if name in _PUBLICATION_CREDENTIAL_ENV or name in _PRIVATE_RUNTIME_ENV or name.startswith("GIT_"):
            child_env.pop(name, None)
    child_env["HOME"] = "/home/hunter"
    child_env["XDG_CONFIG_HOME"] = "/home/hunter/.config"
    child_env["GIT_CONFIG_GLOBAL"] = os.devnull
    child_env["GIT_CONFIG_NOSYSTEM"] = "1"
    child_env["GIT_TERMINAL_PROMPT"] = "0"
    return child_env


def _clone_attempt(repo: Path, sandbox: Path, branch: str, head_before: str) -> Path:
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
    if _git(sandbox, "rev-parse", "HEAD") != head_before:
        raise ProviderAdapterError("isolated provider checkout does not match pre-attempt HEAD")
    return sandbox


def _sandbox_command(executable: str, argv: list[str], sandbox: Path, credential_home: Path) -> list[str]:
    sandbox_executable_name = os.environ.get(_SANDBOX_EXECUTABLE_ENV, "bwrap").strip() or "bwrap"
    sandbox_executable = shutil.which(sandbox_executable_name)
    if sandbox_executable is None:
        raise ProviderAdapterError("filesystem sandbox executable is unavailable")

    command = [
        sandbox_executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for system_path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
        if Path(system_path).exists():
            command.extend(("--ro-bind", system_path, system_path))
    command.extend(
        (
            "--bind",
            str(sandbox),
            "/workspace",
            "--bind",
            str(credential_home),
            "/home/hunter",
            "--chdir",
            "/workspace",
            executable,
            *argv[1:],
        )
    )
    return command


def _mirror_worktree(source: Path, destination: Path) -> None:
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in source.iterdir():
        if child.name == ".git":
            continue
        target = destination / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, symlinks=True)
        elif child.is_symlink():
            target.symlink_to(os.readlink(child))
        else:
            shutil.copy2(child, target)


def _trusted_publication_clone(repo: Path, destination: Path, branch: str, head_before: str) -> Path:
    completed = subprocess.run(
        ("git", "clone", "--no-hardlinks", str(repo), str(destination)),
        cwd=repo.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "trusted publication clone failed"
        raise ProviderAdapterError(detail)
    _git(destination, "checkout", "-B", branch, head_before)
    _git(destination, "config", "core.hooksPath", ".githooks")
    _git(destination, "config", "credential.helper", "")
    _git(destination, "config", "user.name", _CANONICAL_WRITER_NAME)
    _git(destination, "config", "user.email", _CANONICAL_WRITER_EMAIL)
    return destination


def _validate_published_candidate(repo: Path, branch: str, expected_head: str) -> None:
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in _PUBLICATION_CREDENTIAL_ENV and not name.startswith("GIT_")
    }
    env["HUNTER_AGENT_EXPECTED_HEAD"] = expected_head
    env["HUNTER_AGENT_BRANCH"] = branch
    completed = subprocess.run(
        (sys.executable, "-m", "hunter.automation.agent_targeted_validation"),
        cwd=repo,
        env=env,
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        raise ProviderAdapterError("published provider result failed exact-head targeted validation")


def _publish_result(
    repo: Path,
    sandbox: Path,
    branch: str,
    head_before: str,
    push_url: str,
    root: Path,
) -> str:
    publisher = _trusted_publication_clone(repo, root / "publisher", branch, head_before)
    trusted_hooks = (publisher / ".githooks").read_bytes() if (publisher / ".githooks").is_file() else None
    _mirror_worktree(sandbox, publisher)
    if trusted_hooks is not None:
        (publisher / ".githooks").write_bytes(trusted_hooks)
    _git(publisher, "add", "-A")
    if not _git(publisher, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ProviderAdapterError("provider reported completion without changing repository content")
    _git(publisher, "commit", "-S", "-m", "chore: apply governed OpenCode provider result")
    head_after = _git(publisher, "rev-parse", "HEAD")
    _git(publisher, "verify-commit", head_after)
    _push_trusted(publisher, branch, push_url, head_before)
    try:
        _validate_published_candidate(publisher, branch, head_after)
    except ProviderAdapterError:
        _push_trusted(
            publisher,
            branch,
            push_url,
            head_after,
            source_ref=head_before,
            run_pre_push=False,
        )
        raise
    return head_after


def _sync_primary_checkout(repo: Path, branch: str, head_before: str, expected_head: str, push_url: str) -> None:
    if _git(repo, "branch", "--show-current") != branch:
        raise ProviderAdapterError("primary checkout changed branch before publication sync")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ProviderAdapterError("primary checkout became dirty before publication sync")
    if _git(repo, "rev-parse", "HEAD") != head_before:
        raise ProviderAdapterError("primary checkout advanced concurrently; refusing destructive sync")
    _git(repo, "fetch", "--no-tags", push_url, f"refs/heads/{branch}")
    if _git(repo, "rev-parse", "FETCH_HEAD") != expected_head:
        raise ProviderAdapterError("published GitHub head does not match provider result")
    if _git(repo, "rev-parse", "HEAD") != head_before:
        raise ProviderAdapterError("primary checkout advanced during fetch; refusing destructive sync")
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
        sandbox = _clone_attempt(repo, root / "repo", branch, head_before)
        credential_home = root / "credential-home"
        credential_home.mkdir(mode=0o700)
        (credential_home / ".config").mkdir(mode=0o700)
        child_env = _model_environment(credential_home)
        command = _sandbox_command(executable, argv, sandbox, credential_home)

        completed = subprocess.run(command, env=child_env, check=False, timeout=900)
        if completed.returncode == RATE_LIMIT_EXIT_CODE:
            return RATE_LIMIT_EXIT_CODE
        if completed.returncode != 0:
            return 1

        head_after = _publish_result(repo, sandbox, branch, head_before, push_url, root)
        _sync_primary_checkout(repo, branch, head_before, head_after, push_url)
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
