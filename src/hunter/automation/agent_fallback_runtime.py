"""Operational provider adapters for the governed n8n fallback dispatcher.

n8n invokes this runtime with the exact signed Smart Prompt Machine handoff. Each
provider adapter is configured as an argv JSON array in environment variables;
provider processes receive the unchanged canonical handoff on stdin and cannot
choose provider order. Success still requires a GitHub-visible remote branch
HEAD advance plus a configured targeted validation command.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from hunter.automation.agent_fallback import (
    PROVIDER_ORDER,
    AgentExecutionReport,
    AgentFallbackExhaustedError,
    GovernedAgentFallbackDispatcher,
)
from hunter.automation.n8n_handoff import PromptAutomationHandoffError
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier, PromptTaskAuthorityError

PROVIDER_COMMAND_ENV = MappingProxyType(
    {
        "codex": "HUNTER_AGENT_CODEX_COMMAND",
        "claude": "HUNTER_AGENT_CLAUDE_COMMAND",
        "freebuff": "HUNTER_AGENT_FREEBUFF_COMMAND",
        "opencode": "HUNTER_AGENT_OPENCODE_COMMAND",
        "jules": "HUNTER_AGENT_JULES_COMMAND",
    }
)
PROVIDER_ENV_ALLOWLIST_ENV = MappingProxyType(
    {
        "codex": "HUNTER_AGENT_CODEX_ENV_ALLOWLIST",
        "claude": "HUNTER_AGENT_CLAUDE_ENV_ALLOWLIST",
        "freebuff": "HUNTER_AGENT_FREEBUFF_ENV_ALLOWLIST",
        "opencode": "HUNTER_AGENT_OPENCODE_ENV_ALLOWLIST",
        "jules": "HUNTER_AGENT_JULES_ENV_ALLOWLIST",
    }
)
VALIDATION_COMMAND_ENV = "HUNTER_AGENT_VALIDATION_COMMAND"
VALIDATION_ENV_ALLOWLIST_ENV = "HUNTER_AGENT_VALIDATION_ENV_ALLOWLIST"
ATTEMPT_TIMEOUT_ENV = "HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS"
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 900.0
RATE_LIMIT_EXIT_CODE = 75
RECEIPT_SCHEMA_VERSION = "hunter-agent-fallback-runtime-receipt-v1"
_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SAFE_BASE_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSH_AUTH_SOCK")
_FORBIDDEN_CHILD_ENV_PREFIXES = ("HUNTER_PROMPT_",)


class AgentFallbackRuntimeError(RuntimeError):
    """Raised when operational fallback configuration or execution is invalid."""


@dataclass(frozen=True, slots=True)
class AgentFallbackRuntimeReceipt:
    """Non-secret proof of one verified fallback execution."""

    provider: str
    head_before: str
    head_after: str
    attempts: tuple[dict[str, object], ...]
    validation_succeeded: bool
    schema_version: str = RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.validation_succeeded is not True:
            raise AgentFallbackRuntimeError("successful fallback receipts require validation_succeeded=true")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class _ProcessOutcome:
    returncode: int | None
    timed_out: bool = False


def _argv(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise AgentFallbackRuntimeError(f"{name} must be configured as a JSON argv array")
    try:
        decoded = json.loads(value)
    except ValueError:
        raise AgentFallbackRuntimeError(f"{name} must be valid JSON") from None
    if not isinstance(decoded, list) or not decoded or any(not isinstance(item, str) or not item for item in decoded):
        raise AgentFallbackRuntimeError(f"{name} must be a non-empty JSON array of strings")
    return tuple(decoded)


def _environment_allowlist(value: object, *, name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, str):
        raise AgentFallbackRuntimeError(f"{name} must be configured as a JSON environment-name array")
    try:
        decoded = json.loads(value)
    except ValueError:
        raise AgentFallbackRuntimeError(f"{name} must be valid JSON") from None
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise AgentFallbackRuntimeError(f"{name} must be a JSON array of environment names")
    if len(set(decoded)) != len(decoded):
        raise AgentFallbackRuntimeError(f"{name} must not contain duplicate environment names")
    for item in decoded:
        if _ENV_NAME.fullmatch(item) is None:
            raise AgentFallbackRuntimeError(f"{name} contains an invalid environment name")
        if item.startswith(_FORBIDDEN_CHILD_ENV_PREFIXES):
            raise AgentFallbackRuntimeError(f"{name} cannot expose prompt authority environment variables")
    return tuple(decoded)


def _timeout(environ: Mapping[str, str]) -> float:
    value = environ.get(ATTEMPT_TIMEOUT_ENV, str(DEFAULT_ATTEMPT_TIMEOUT_SECONDS))
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise AgentFallbackRuntimeError("agent attempt timeout must be a positive finite number") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise AgentFallbackRuntimeError("agent attempt timeout must be a positive finite number")
    return timeout


def _branch(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or ".." in value
        or _BRANCH.fullmatch(value) is None
    ):
        raise AgentFallbackRuntimeError("branch name is invalid")
    return value


def _terminate_process_group(process_group_id: int) -> None:
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_isolated(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    input_text: str | None = None,
) -> _ProcessOutcome:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except OSError:
        return _ProcessOutcome(None)
    try:
        process.communicate(input=input_text, timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        _terminate_process_group(process.pid)
        process.communicate()
        return _ProcessOutcome(None, timed_out=True)
    finally:
        if process.poll() is not None:
            _terminate_process_group(process.pid)
    return _ProcessOutcome(returncode)


def _pinned_github_remote(repo_dir: Path) -> str:
    try:
        completed = subprocess.run(
            ("git", "remote", "get-url", "origin"),
            cwd=repo_dir,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AgentFallbackRuntimeError("cannot pin authorized GitHub remote") from None
    if completed.returncode != 0:
        raise AgentFallbackRuntimeError("cannot pin authorized GitHub remote")
    remote = completed.stdout.strip()
    if not remote:
        raise AgentFallbackRuntimeError("authorized GitHub remote is empty")
    if remote.startswith("git@github.com:"):
        if re.fullmatch(r"git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", remote) is None:
            raise AgentFallbackRuntimeError("authorized GitHub remote is invalid")
        return remote
    try:
        parsed = urlsplit(remote)
    except ValueError:
        raise AgentFallbackRuntimeError("authorized GitHub remote is invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise AgentFallbackRuntimeError("authorized GitHub remote must be credential-free github.com")
    return remote


class OperationalAgentFallbackRuntime:
    """Bind the deterministic dispatcher to real local provider command adapters."""

    def __init__(
        self,
        *,
        repo_dir: str | Path,
        branch: str,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._repo_dir = Path(repo_dir).resolve()
        if not self._repo_dir.is_dir():
            raise AgentFallbackRuntimeError("repository directory does not exist")
        self._branch = _branch(branch)
        self._environ = dict(os.environ if environ is None else environ)
        self._timeout = _timeout(self._environ)
        self._remote_url = _pinned_github_remote(self._repo_dir)
        self._commands = {
            provider: _argv(self._environ.get(PROVIDER_COMMAND_ENV[provider]), name=PROVIDER_COMMAND_ENV[provider])
            for provider in PROVIDER_ORDER
        }
        self._provider_env_allowlists = {
            provider: _environment_allowlist(
                self._environ.get(PROVIDER_ENV_ALLOWLIST_ENV[provider]),
                name=PROVIDER_ENV_ALLOWLIST_ENV[provider],
            )
            for provider in PROVIDER_ORDER
        }
        self._validation_command = _argv(
            self._environ.get(VALIDATION_COMMAND_ENV),
            name=VALIDATION_COMMAND_ENV,
        )
        self._validation_env_allowlist = _environment_allowlist(
            self._environ.get(VALIDATION_ENV_ALLOWLIST_ENV),
            name=VALIDATION_ENV_ALLOWLIST_ENV,
        )
        self._verifier = PromptAutomationVerifier.from_environment(environ=self._environ)

    def _base_child_environment(self, allowlist: tuple[str, ...]) -> dict[str, str]:
        names = tuple(dict.fromkeys((*_SAFE_BASE_ENV, *allowlist)))
        return {name: self._environ[name] for name in names if name in self._environ}

    def _child_environment(self, provider: str) -> dict[str, str]:
        child = self._base_child_environment(self._provider_env_allowlists[provider])
        child["HUNTER_AGENT_PROVIDER"] = provider
        child["HUNTER_AGENT_BRANCH"] = self._branch
        child["HUNTER_AGENT_REPO_DIR"] = str(self._repo_dir)
        return child

    def _execute(self, provider: str, document: str) -> AgentExecutionReport:
        outcome = _run_isolated(
            self._commands[provider],
            cwd=self._repo_dir,
            env=self._child_environment(provider),
            input_text=document,
            timeout=self._timeout,
        )
        if outcome.timed_out:
            return AgentExecutionReport("failed", "provider_timeout")
        if outcome.returncode is None:
            return AgentExecutionReport("failed", "provider_unavailable")
        if outcome.returncode == 0:
            return AgentExecutionReport("completed")
        if outcome.returncode == RATE_LIMIT_EXIT_CODE:
            return AgentExecutionReport("rate_limited", "provider_rate_limited")
        return AgentExecutionReport("failed", "provider_failed")

    def _read_remote_head(self) -> str:
        try:
            completed = subprocess.run(
                ("git", "ls-remote", "--exit-code", self._remote_url, f"refs/heads/{self._branch}"),
                cwd=self._repo_dir,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AgentFallbackRuntimeError("cannot read GitHub-visible branch HEAD") from None
        if completed.returncode != 0:
            raise AgentFallbackRuntimeError("cannot read GitHub-visible branch HEAD")
        fields = completed.stdout.strip().split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{40}", fields[0]):
            raise AgentFallbackRuntimeError("remote branch HEAD response is invalid")
        return fields[0].lower()

    def _validate(self, head: str) -> bool:
        child = self._base_child_environment(self._validation_env_allowlist)
        child["HUNTER_AGENT_EXPECTED_HEAD"] = head
        child["HUNTER_AGENT_BRANCH"] = self._branch
        outcome = _run_isolated(
            self._validation_command,
            cwd=self._repo_dir,
            env=child,
            timeout=self._timeout,
        )
        return outcome.returncode == 0 and not outcome.timed_out

    def dispatch(self, document: str | bytes) -> AgentFallbackRuntimeReceipt:
        dispatcher = GovernedAgentFallbackDispatcher(
            execute=self._execute,
            read_head=self._read_remote_head,
            validate=self._validate,
            verifier=self._verifier,
            environ=self._environ,
        )
        result = dispatcher.dispatch_document(document)
        attempts = tuple(asdict(attempt) for attempt in result.attempts)
        return AgentFallbackRuntimeReceipt(
            provider=result.provider,
            head_before=result.head_before,
            head_after=result.head_after,
            attempts=attempts,
            validation_succeeded=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter agent-fallback-run")
    parser.add_argument("handoff", help="path to the exact signed Smart Prompt Machine handoff JSON")
    parser.add_argument("--repo", default=".", help="Hunter repository checkout used by provider commands")
    parser.add_argument("--branch", required=True, help="remote feature branch providers must advance")
    parser.add_argument("--receipt-out", help="write the non-secret execution receipt to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        document = Path(arguments.handoff).read_bytes()
        receipt = OperationalAgentFallbackRuntime(
            repo_dir=arguments.repo,
            branch=arguments.branch,
        ).dispatch(document)
        text = receipt.to_json()
        print(text)
        if arguments.receipt_out:
            Path(arguments.receipt_out).write_text(text + "\n", encoding="utf-8")
        return 0
    except (
        AgentFallbackExhaustedError,
        AgentFallbackRuntimeError,
        PromptAutomationHandoffError,
        PromptTaskAuthorityError,
        OSError,
        ValueError,
    ) as error:
        print(f"agent fallback runtime failed: {error}")
        return 2


__all__ = [
    "ATTEMPT_TIMEOUT_ENV",
    "AgentFallbackRuntimeError",
    "AgentFallbackRuntimeReceipt",
    "OperationalAgentFallbackRuntime",
    "PROVIDER_COMMAND_ENV",
    "PROVIDER_ENV_ALLOWLIST_ENV",
    "RATE_LIMIT_EXIT_CODE",
    "RECEIPT_SCHEMA_VERSION",
    "VALIDATION_COMMAND_ENV",
    "VALIDATION_ENV_ALLOWLIST_ENV",
    "main",
]
