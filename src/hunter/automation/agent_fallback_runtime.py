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
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from hunter.automation.agent_fallback import (
    PROVIDER_ORDER,
    AgentExecutionReport,
    AgentFallbackExhaustedError,
    GovernedAgentFallbackDispatcher,
)
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

PROVIDER_COMMAND_ENV = MappingProxyType(
    {
        "codex": "HUNTER_AGENT_CODEX_COMMAND",
        "claude": "HUNTER_AGENT_CLAUDE_COMMAND",
        "freebuff": "HUNTER_AGENT_FREEBUFF_COMMAND",
        "opencode": "HUNTER_AGENT_OPENCODE_COMMAND",
        "jules": "HUNTER_AGENT_JULES_COMMAND",
    }
)
VALIDATION_COMMAND_ENV = "HUNTER_AGENT_VALIDATION_COMMAND"
ATTEMPT_TIMEOUT_ENV = "HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS"
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 900.0
RATE_LIMIT_EXIT_CODE = 75
RECEIPT_SCHEMA_VERSION = "hunter-agent-fallback-runtime-receipt-v1"
_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")


class AgentFallbackRuntimeError(RuntimeError):
    """Raised when operational fallback configuration or execution is invalid."""


@dataclass(frozen=True, slots=True)
class AgentFallbackRuntimeReceipt:
    """Non-secret proof of one verified fallback execution."""

    provider: str
    head_before: str
    head_after: str
    attempts: tuple[dict[str, object], ...]
    schema_version: str = RECEIPT_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        self._commands = {
            provider: _argv(self._environ.get(PROVIDER_COMMAND_ENV[provider]), name=PROVIDER_COMMAND_ENV[provider])
            for provider in PROVIDER_ORDER
        }
        self._validation_command = _argv(
            self._environ.get(VALIDATION_COMMAND_ENV),
            name=VALIDATION_COMMAND_ENV,
        )
        self._verifier = PromptAutomationVerifier.from_environment(environ=self._environ)

    def _child_environment(self, provider: str) -> dict[str, str]:
        child = dict(self._environ)
        child["HUNTER_AGENT_PROVIDER"] = provider
        child["HUNTER_AGENT_BRANCH"] = self._branch
        child["HUNTER_AGENT_REPO_DIR"] = str(self._repo_dir)
        return child

    def _execute(self, provider: str, document: str) -> AgentExecutionReport:
        try:
            completed = subprocess.run(
                self._commands[provider],
                cwd=self._repo_dir,
                env=self._child_environment(provider),
                input=document,
                text=True,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return AgentExecutionReport("failed", f"provider execution failed: {type(error).__name__}")
        detail = (completed.stderr or completed.stdout or "").strip()[:512]
        if completed.returncode == 0:
            return AgentExecutionReport("completed", detail)
        if completed.returncode == RATE_LIMIT_EXIT_CODE:
            return AgentExecutionReport("rate_limited", detail or "provider rate limited")
        return AgentExecutionReport("failed", detail or f"provider exit code {completed.returncode}")

    def _read_remote_head(self) -> str:
        try:
            completed = subprocess.run(
                ("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{self._branch}"),
                cwd=self._repo_dir,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AgentFallbackRuntimeError(f"cannot read GitHub-visible branch HEAD: {type(error).__name__}") from None
        if completed.returncode != 0:
            raise AgentFallbackRuntimeError("cannot read GitHub-visible branch HEAD")
        fields = completed.stdout.strip().split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{40}", fields[0]):
            raise AgentFallbackRuntimeError("remote branch HEAD response is invalid")
        return fields[0].lower()

    def _validate(self, head: str) -> bool:
        child = dict(self._environ)
        child["HUNTER_AGENT_EXPECTED_HEAD"] = head
        child["HUNTER_AGENT_BRANCH"] = self._branch
        try:
            completed = subprocess.run(
                self._validation_command,
                cwd=self._repo_dir,
                env=child,
                text=True,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def dispatch(self, document: str | bytes) -> AgentFallbackRuntimeReceipt:
        dispatcher = GovernedAgentFallbackDispatcher(
            execute=self._execute,
            read_head=self._read_remote_head,
            validate=self._validate,
            verifier=self._verifier,
        )
        result = dispatcher.dispatch_document(document)
        attempts = tuple(asdict(attempt) for attempt in result.attempts)
        return AgentFallbackRuntimeReceipt(
            provider=result.provider,
            head_before=result.head_before,
            head_after=result.head_after,
            attempts=attempts,
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
    except (AgentFallbackExhaustedError, AgentFallbackRuntimeError, OSError, ValueError) as error:
        print(f"agent fallback runtime failed: {error}")
        return 2


__all__ = [
    "ATTEMPT_TIMEOUT_ENV",
    "AgentFallbackRuntimeError",
    "AgentFallbackRuntimeReceipt",
    "OperationalAgentFallbackRuntime",
    "PROVIDER_COMMAND_ENV",
    "RATE_LIMIT_EXIT_CODE",
    "RECEIPT_SCHEMA_VERSION",
    "VALIDATION_COMMAND_ENV",
    "main",
]
