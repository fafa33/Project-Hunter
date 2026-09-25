"""Isolated per-authorization execution workspaces for the governed Issue Agent.

``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` (I1-I4). Every authorization executes
in its own disposable clone-capable workspace:

- forked at exactly the signed ``base_sha``, which must be a commit reachable
  from ``main`` -- never at whatever a shared branch happens to hold;
- on the deterministic branch ``derive_execution_target`` derived from the
  signed authorization;
- with the canonical credential-free GitHub ``origin`` the fallback runtime
  and targeted validation pin;
- only when the remote branch is absent or still at the base, so an existing
  foreign write is never taken over.

The workspace is created only after the authorization is durably dispatched
and is removed when the execution reaches a terminal outcome. Nothing is
prepared at startup and nothing is shared between authorizations.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from hunter.automation.agent_fallback_runtime import (
    AgentFallbackRuntimeReceipt,
    AgentFallbackRuntimeSettings,
    OperationalAgentFallbackRuntime,
)
from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_BASE_REF,
    IssueAgentConfigurationError,
    IssueAgentExecutionError,
    IssueAgentExecutionTarget,
    IssueAgentWorkspaceError,
    derive_execution_target,
    issue_agent_execution_branch,
)

_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_GIT_TIMEOUT_SECONDS = 600
_MAIN_TRACKING_REF = f"refs/remotes/origin/{ISSUE_AGENT_BASE_REF}"

RuntimeFactory = Callable[..., OperationalAgentFallbackRuntime]


def canonical_github_remote(repository: str) -> str:
    """The credential-free canonical GitHub remote for an ``owner/name`` slug."""
    slug = repository.strip()
    if _REPOSITORY_RE.fullmatch(slug) is None or ".." in slug:
        raise IssueAgentConfigurationError("repository must be an exact owner/name GitHub slug")
    return f"https://github.com/{slug}.git"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise IssueAgentWorkspaceError("WORKSPACE_UNAVAILABLE", f"git {args[0]} could not run") from None


def _require(completed: subprocess.CompletedProcess[str], step: str) -> str:
    if completed.returncode != 0:
        raise IssueAgentWorkspaceError("WORKSPACE_UNAVAILABLE", f"{step} failed")
    return completed.stdout.strip()


def workspace_path(workspace_root: Path, target: IssueAgentExecutionTarget) -> Path:
    """The one workspace directory an authorization may use, named by its digest."""
    digest = target.authorization_id.partition(":")[2]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise IssueAgentExecutionError("execution target does not carry a canonical authorization identity")
    return workspace_root / digest


def materialize_workspace(workspace_root: Path, target: IssueAgentExecutionTarget, *, remote_url: str) -> Path:
    """Create the isolated workspace for one target, or fail closed with a reason code."""
    if not isinstance(target, IssueAgentExecutionTarget):
        raise IssueAgentExecutionError("workspace materialization requires a derived execution target")
    root = workspace_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = workspace_path(root, target)
    if workspace.exists():
        # A leftover from an interrupted process. The ledger guarantees this
        # authorization never executed to completion here, so it is disposable.
        shutil.rmtree(workspace)
    workspace.mkdir()

    _require(_git(workspace, "init", "--quiet"), "workspace initialization")
    _require(_git(workspace, "remote", "add", "origin", remote_url), "canonical origin binding")
    _require(_git(workspace, "config", "core.hooksPath", ".githooks"), "push boundary binding")
    _require(
        _git(
            workspace,
            "fetch",
            "--quiet",
            "--no-tags",
            "origin",
            f"+refs/heads/{ISSUE_AGENT_BASE_REF}:{_MAIN_TRACKING_REF}",
        ),
        f"fetch of {ISSUE_AGENT_BASE_REF}",
    )

    base = target.base_sha
    if _git(workspace, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
        raise IssueAgentWorkspaceError("BASE_NOT_ON_MAIN", f"signed base {base} is not a commit on {remote_url}")
    ancestry = _git(workspace, "merge-base", "--is-ancestor", base, _MAIN_TRACKING_REF)
    if ancestry.returncode == 1:
        raise IssueAgentWorkspaceError("BASE_NOT_ON_MAIN", f"signed base {base} is not reachable from main")
    _require(ancestry, "base ancestry check")

    remote = _require(_git(workspace, "ls-remote", "origin", f"refs/heads/{target.branch}"), "remote branch read")
    lines = [line for line in remote.splitlines() if line.strip()]
    if lines:
        fields = lines[0].split()
        if len(lines) != 1 or len(fields) != 2 or fields[0].lower() != base:
            raise IssueAgentWorkspaceError(
                "REMOTE_BRANCH_CONFLICT",
                f"remote branch {target.branch} already exists at a head other than the signed base",
            )

    _require(_git(workspace, "checkout", "--quiet", "-B", target.branch, base), "branch checkout")
    if _require(_git(workspace, "rev-parse", "HEAD"), "head read") != base:
        raise IssueAgentWorkspaceError("WORKSPACE_UNAVAILABLE", "workspace HEAD is not the signed base")
    if _require(_git(workspace, "branch", "--show-current"), "branch read") != target.branch:
        raise IssueAgentWorkspaceError("WORKSPACE_UNAVAILABLE", "workspace is not on the execution branch")
    return workspace


class IssueAgentWorkspaceRuntime:
    """The Issue Agent fallback seam: one isolated workspace per authorization.

    The provider pool configuration is parsed once, at construction, so a
    misconfigured deployment fails at startup rather than after an ACK.
    """

    __slots__ = ("_root", "_remote_url", "_environ", "_settings", "_runtime_factory")

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        repository: str,
        environ: Mapping[str, str] | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self._root = Path(workspace_root)
        if not str(workspace_root).strip() or not self._root.is_absolute():
            raise IssueAgentConfigurationError("the Issue Agent workspace root must be an absolute path")
        self._remote_url = canonical_github_remote(repository)
        self._environ = dict(os.environ if environ is None else environ)
        self._settings = AgentFallbackRuntimeSettings.from_environment(self._environ)
        self._runtime_factory = runtime_factory or OperationalAgentFallbackRuntime

    @property
    def workspace_root(self) -> Path:
        return self._root

    def dispatch(self, document: str | bytes, target: IssueAgentExecutionTarget) -> AgentFallbackRuntimeReceipt:
        workspace = materialize_workspace(self._root, target, remote_url=self._remote_url)
        try:
            runtime = self._runtime_factory(
                repo_dir=workspace,
                branch=target.branch,
                base_sha=target.base_sha,
                environ=self._environ,
                settings=self._settings,
            )
            return runtime.dispatch(document)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


__all__ = [
    "IssueAgentExecutionTarget",
    "IssueAgentWorkspaceError",
    "IssueAgentWorkspaceRuntime",
    "canonical_github_remote",
    "derive_execution_target",
    "issue_agent_execution_branch",
    "materialize_workspace",
    "workspace_path",
]
