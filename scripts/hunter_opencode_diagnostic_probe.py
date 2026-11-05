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

The probe has **no path inputs**. It writes as the trusted runner, so its layout is fixed: the workspace, the
external git metadata and the isolated home are constant siblings of the workflow-created execution root, and its
base comes from the trusted checkout that contains this file. A caller cannot redirect a trusted ``mkdir``,
``chmod`` or ``git checkout`` at another directory, no git argument can be read as an option, and git metadata is
required to stay outside the model-controlled workspace.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
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
#: The execution root the diagnostic workflow creates for the runner (``install -d -m 0711 -o <runner>``).
EXECUTION_ROOT: Final = Path("/tmp/hunter-ia/execute")
#: The runtime the workflow installs and the production executor is launched with (``--model-argv`` in the trigger).
OPENCODE_EXECUTABLE: Final = Path("/opt/hunter/bin/opencode")
#: One provider-qualified model name: a single argv value with no control characters or leading option.
MODEL_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,200}")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
#: The probe commit is a local diagnostic identity, never the owner or the trusted publisher.
PROBE_COMMIT_NAME: Final = "hunter-diagnostic"
PROBE_COMMIT_EMAIL: Final = "hunter-diagnostic@invalid"
GIT_ENVIRONMENT: Final[Mapping[str, str]] = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": PROBE_COMMIT_NAME,
    "GIT_AUTHOR_EMAIL": PROBE_COMMIT_EMAIL,
    "GIT_COMMITTER_NAME": PROBE_COMMIT_NAME,
    "GIT_COMMITTER_EMAIL": PROBE_COMMIT_EMAIL,
}


class ProbeFailed(RuntimeError):
    """The probe could not establish its own trusted base. Its message never carries candidate content."""


def safe_path(value: Path, what: str, *, must_exist: bool) -> Path:
    """A canonical absolute directory path with no symlinked component, or nothing is created under it.

    ``resolve`` is compared with the text itself, so ``..``, ``.``, a linked ancestor or a linked leaf is refused
    rather than followed. ``must_exist`` distinguishes the fixed layout directories (present after the workflow
    provisions them) from the per-run children that the probe itself creates once.
    """

    text = str(value)
    if not text or _CONTROL.search(text):
        raise ProbeFailed(f"{what} is not a single safe path")
    if not value.is_absolute():
        raise ProbeFailed(f"{what} is not absolute")
    try:
        resolved = value.resolve()
    except (OSError, RuntimeError):
        raise ProbeFailed(f"{what} cannot be resolved") from None
    if resolved != value:
        raise ProbeFailed(f"{what} traverses a symlink or a non-canonical component")
    if must_exist and not value.is_dir():
        raise ProbeFailed(f"{what} is not an existing directory")
    if value.exists() and not value.is_dir():
        raise ProbeFailed(f"{what} is not a directory")
    return value


@dataclass(frozen=True, slots=True)
class TrustedLayout:
    """The probe's fixed directories, all derived from constants and this file's own trusted checkout."""

    root: Path
    source: Path
    workspace: Path
    git_dir: Path
    home: Path
    opencode: Path


def trusted_layout() -> TrustedLayout:
    """The one layout the hosted probe may use: provisioned root, fixed children, trusted checkout, pinned runtime."""

    root = safe_path(EXECUTION_ROOT, "execution root", must_exist=True)
    metadata = root.stat()
    if metadata.st_uid != os.geteuid():
        raise ProbeFailed("the execution root is not owned by the diagnostic user")
    if metadata.st_mode & 0o022:
        raise ProbeFailed("the execution root is writable by another principal")
    workspace = safe_path(root / "workspace", "workspace", must_exist=False)
    git_dir = safe_path(root / "trusted.git", "git metadata directory", must_exist=False)
    home = safe_path(root / "home", "home", must_exist=False)
    if git_dir == workspace or git_dir in workspace.parents:
        raise ProbeFailed("git metadata must stay outside the model workspace")
    # The base is the checkout that contains this file: the probe cannot take a base from anywhere it was not
    # installed alongside, and a crafted ``--source`` has no way in.
    source = safe_path(Path(__file__).resolve().parents[1], "trusted checkout", must_exist=True)
    if not (source / ".git").is_dir():
        raise ProbeFailed("the trusted checkout has no git metadata")
    opencode = OPENCODE_EXECUTABLE
    if opencode.is_symlink() or opencode.name != "opencode" or not opencode.is_file():
        raise ProbeFailed("the pinned OpenCode runtime is not a regular executable file")
    if opencode.resolve() != opencode or not os.access(opencode, os.X_OK):
        raise ProbeFailed("the pinned OpenCode runtime is not a canonical executable")
    return TrustedLayout(root=root, source=source, workspace=workspace, git_dir=git_dir, home=home, opencode=opencode)


def _git(git_dir: Path, workspace: Path, *args: str) -> bytes:
    """Git against the trusted metadata directory only; nothing inside the model's workspace configures it.

    The argv comes from the shared production builder (:func:`roles.trusted_git_command`), so an option-shaped
    argument or a traversing path is refused here exactly as it is in the executor.
    """

    try:
        command = roles.trusted_git_command(git_dir, workspace, args)
    except roles.RoleRefused:
        raise ProbeFailed(f"probe git {args[0] if args else 'invocation'} refused an unsafe invocation") from None
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

    source = safe_path(source, "trusted checkout", must_exist=True)
    workspace = safe_path(workspace, "workspace", must_exist=False)
    git_dir = safe_path(git_dir, "git metadata directory", must_exist=False)
    if workspace == git_dir or git_dir in workspace.parents or workspace.parent != git_dir.parent:
        raise ProbeFailed("git metadata must be an external sibling of the model workspace")
    workspace.mkdir(parents=True)
    _git(git_dir, workspace, "init", "--quiet")
    _git(
        git_dir, workspace,
        "fetch", "--quiet", "--no-tags", "--", roles.trusted_remote(str(source)), "+refs/heads/main:refs/probe/base",
    )  # fmt: skip
    _git(git_dir, workspace, "checkout", "--quiet", "--detach", "refs/probe/base", "--")
    (workspace / PROBE_FILE).write_bytes(PROBE_BEFORE)
    _git(git_dir, workspace, "add", "--", PROBE_FILE)
    _git(git_dir, workspace, "commit", "--quiet", "--no-gpg-sign", "-m", "diagnostic probe base")
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
    home = safe_path(home, "home", must_exist=False)
    if home == workspace or home in workspace.parents or workspace in home.parents:
        raise ProbeFailed("the isolated home must stay outside the model workspace")
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
    # Deliberately no path, executable or git options: the layout is fixed above, so a caller (or a workflow edit)
    # cannot redirect a trusted write. Only the model identity and its credential channel are configurable.
    parser.add_argument("--model", required=True)
    parser.add_argument("--secret-env", required=True, help="environment variable holding the model credential")
    parser.add_argument("--model-key-name", required=True, help="variable name the isolated child receives")
    parser.add_argument("--isolation-user", default="hunter-untrusted")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    credential = os.environ.get(arguments.secret_env)
    if MODEL_NAME.fullmatch(arguments.model or "") is None or not credential or not arguments.model_key_name:
        print("hosted diagnostic refused: configured model or model credential is absent")
        return 2
    try:
        layout = trusted_layout()
        outcome = run_probe(
            source=layout.source,
            workspace=layout.workspace,
            git_dir=layout.git_dir,
            home=layout.home,
            opencode=layout.opencode,
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
