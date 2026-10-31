#!/usr/bin/env python3
"""Hosted, bounded OpenCode diagnostic probe on the production execution contract (Issue #574).

A hosted Issue Agent execution launched OpenCode, exited non-zero in about two seconds and reported
``stderr_category=UNCLASSIFIED``, while the earlier diagnostic claimed ``git_changed=True`` for a file that was
created untracked before the model ever ran. Neither observation proved anything about the provider or the
workspace contract. This probe exists to remove exactly those two ambiguities:

* it launches through the **production contract itself** (``opencode_invocation``): the same workspace binding,
  argv, private prompt attachment, permission map and denied shell the executor uses, so a hosted result cannot
  describe a different runtime than production; and
* it asserts an edit to a **tracked** file, committed before the model runs, using the **production collector**
  (``collect_result``) instead of an ad-hoc ``git status``. A pre-created untracked file can no longer be
  mistaken for a model edit, and a stray write outside the task is never reported as success.

Only closed-vocabulary, non-content telemetry is printed: exit status, timeout, edit outcome, changed-file count
and contract booleans. Model stdout, stderr, the attached task, and any credential are never printed, and the
credential reaches the isolated child only through the launcher environment.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles

PROBE_FILE: Final = "probe.txt"
PROBE_BEFORE: Final = b"before\n"
PROBE_AFTER: Final = b"after\n"
PROBE_TASK: Final = "Replace before with after in probe.txt. Do not modify any other file."
DEFAULT_TIMEOUT_SECONDS: Final = 240.0
#: The probe commit is a local diagnostic identity, never the owner or the trusted publisher.
PROBE_COMMIT_NAME: Final = "hunter-diagnostic"
PROBE_COMMIT_EMAIL: Final = "hunter-diagnostic@invalid"
GIT_ENVIRONMENT: Final[Mapping[str, str]] = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


class ProbeFailed(RuntimeError):
    """The probe could not establish its own trusted base. Its message never carries candidate content."""


def _git(git_dir: Path, workspace: Path, *args: str) -> bytes:
    """Git against the trusted metadata directory only; nothing inside the model's workspace configures it."""

    command = (
        "git", f"--git-dir={git_dir}", f"--work-tree={workspace}",
        "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args,
    )  # fmt: skip
    completed = subprocess.run(
        command, cwd=workspace, env=dict(GIT_ENVIRONMENT), capture_output=True, check=False, timeout=300
    )
    if completed.returncode != 0:
        raise ProbeFailed(f"probe git {args[0]} failed")
    return completed.stdout


def prepare(source: Path, workspace: Path, git_dir: Path) -> str:
    """Materialize the probe base exactly as production does: trusted git metadata outside the workspace.

    ``probe.txt`` is committed at that base, so "changed" can only mean the model edited a repository file.
    Returns the exact base commit the collector diffs against.
    """

    workspace.mkdir(parents=True)
    _git(git_dir, workspace, "init", "--quiet")
    _git(git_dir, workspace, "fetch", "--quiet", "--no-tags", str(source), "+refs/heads/main:refs/probe/base")
    _git(git_dir, workspace, "checkout", "--quiet", "--detach", "refs/probe/base")
    (workspace / PROBE_FILE).write_bytes(PROBE_BEFORE)
    _git(git_dir, workspace, "add", "--", PROBE_FILE)
    _git(
        git_dir, workspace,
        "-c", f"user.name={PROBE_COMMIT_NAME}", "-c", f"user.email={PROBE_COMMIT_EMAIL}",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "diagnostic probe base",
    )  # fmt: skip
    return _git(git_dir, workspace, "rev-parse", "HEAD").decode().strip()


def contract_telemetry(invocation: roles.OpenCodeInvocation, workspace: Path) -> dict[str, bool]:
    """Booleans derived from the exact launch value, so the hosted log shows which contract actually ran."""

    config = json.loads(invocation.public_env.get("OPENCODE_CONFIG_CONTENT") or "{}")
    permission = config.get("permission") or {}
    shell_value = invocation.public_env.get("SHELL")
    shell = Path(shell_value) if shell_value else None
    return {
        "pure": "--pure" in invocation.argv,
        "workspace_bound": "--dir" in invocation.argv and str(workspace) in invocation.argv,
        "workspace_only_tools": permission.get("*") == "deny" and permission.get("external_directory") == "deny",
        "shell_denied": (
            shell is not None
            and shell.is_file()
            and shell.stat().st_mode & 0o222 == 0
            and workspace not in shell.parents
        ),
        "project_config_disabled": invocation.public_env.get("OPENCODE_DISABLE_PROJECT_CONFIG") == "1",
    }


def assess(workspace: Path, git_dir: Path, base_sha: str) -> tuple[bool, int]:
    """The production collector decides the outcome: one tracked file, edited to the expected content.

    ``changed_files`` is ``-1`` when the trusted view could not be established: an unknown is never reported as
    "no change", and never as success.
    """

    try:
        document = roles.collect_result(
            workspace,
            git_dir,
            authorization_id="hosted-diagnostic-probe",
            branch="hosted-diagnostic-probe",
            base_sha=base_sha,
        )
    except roles.RoleRefused as refusal:
        # ``NO_CHANGES`` is a definitive collector answer; every other refusal means the trusted view itself could
        # not be established, which must not be reported as "the model changed nothing".
        return (False, 0) if refusal.code == "NO_CHANGES" else (False, -1)
    files = json.loads(document)["files"]
    edited = (
        len(files) == 1 and files[0]["path"] == PROBE_FILE and base64.b64decode(files[0]["content_b64"]) == PROBE_AFTER
    )
    return edited, len(files)


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    exit_zero: bool
    timed_out: bool
    tracked_edit: bool
    changed_files: int

    @property
    def succeeded(self) -> bool:
        return self.exit_zero and not self.timed_out and self.tracked_edit and self.changed_files == 1


def probe_invocation(*, opencode: Path, model: str, home: Path, workspace: Path) -> roles.OpenCodeInvocation:
    """The probe's launch, built only by the production contract so it cannot drift from the executor."""

    return roles.opencode_invocation(
        (str(opencode), "run", "--model", model, "--agent", "build"),
        prompt=PROBE_TASK.encode("utf-8"),
        home=home,
        workspace=workspace,
    )


def run_probe(
    *,
    source: Path,
    workspace: Path,
    git_dir: Path,
    home: Path,
    opencode: Path,
    model: str,
    secret_environment: Mapping[str, str],
    isolation: roles.Isolation,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeOutcome:
    """Run the model once through isolation on the production contract and assess a tracked repository edit."""

    base_sha = prepare(source, workspace, git_dir)
    home.mkdir(mode=0o700)
    invocation = probe_invocation(opencode=opencode, model=model, home=home, workspace=workspace)
    telemetry = " ".join(f"{name}={value}" for name, value in contract_telemetry(invocation, workspace).items())
    print(f"hosted diagnostic contract: {telemetry}", flush=True)
    timed_out = False
    status = 1
    isolation.prepare(workspace, home)
    try:
        status = isolation.run(
            invocation.argv,
            cwd=workspace,
            public_env=invocation.public_env,
            secret_env=secret_environment,
            stdin=invocation.stdin,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        isolation.stop()  # nothing the model started survives into collection
    tracked_edit, changed_files = assess(workspace, git_dir, base_sha)
    outcome = ProbeOutcome(
        exit_zero=status == 0 and not timed_out,
        timed_out=timed_out,
        tracked_edit=tracked_edit,
        changed_files=changed_files,
    )
    print(
        f"hosted diagnostic result: exit_zero={outcome.exit_zero} timed_out={outcome.timed_out} "
        f"tracked_edit={outcome.tracked_edit} changed_files={outcome.changed_files}",
        flush=True,
    )
    return outcome


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter-opencode-diagnostic-probe")
    parser.add_argument("--source", type=Path, required=True, help="trusted checkout the probe base comes from")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--git-dir", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--opencode", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--secret-env", required=True, help="environment variable holding the model credential")
    parser.add_argument("--model-key-name", required=True, help="variable name the isolated child receives")
    parser.add_argument("--isolation-user", default="hunter-untrusted")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    credential = os.environ.get(arguments.secret_env)
    if not arguments.model or not credential or not arguments.model_key_name:
        print("hosted diagnostic refused: configured model or model credential is absent")
        return 2
    if arguments.opencode.name != "opencode":
        print("hosted diagnostic refused: the configured runtime is not the pinned OpenCode executable")
        return 2
    try:
        outcome = run_probe(
            source=arguments.source,
            workspace=arguments.workspace,
            git_dir=arguments.git_dir,
            home=arguments.home,
            opencode=arguments.opencode,
            model=arguments.model,
            secret_environment={arguments.model_key_name: credential},
            isolation=roles.SudoIsolation(arguments.isolation_user),
            timeout=arguments.timeout,
        )
    except (roles.RoleRefused, ProbeFailed, core.ReplacementExecutorError) as refusal:
        # Closed codes only: a harness failure message could otherwise describe candidate content.
        code = getattr(refusal, "code", type(refusal).__name__)
        print(f"hosted diagnostic refused: {code}")
        return 2
    if not outcome.succeeded:
        print("hosted diagnostic: the production contract did not produce the expected tracked edit")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
