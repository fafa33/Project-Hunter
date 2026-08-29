"""Deterministic fail-closed fallback for governed engineering handoffs.

n8n may orchestrate provider execution, but it cannot choose providers, widen the
prompt, or declare success. Success is proven only by a GitHub-visible HEAD
advance plus targeted validation after one provider reports completion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff

PROVIDER_ORDER = ("codex", "claude", "freebuff", "opencode", "jules")
ProviderState = Literal["available", "rate_limited", "failed"]
ProviderReport = Literal["completed", "rate_limited", "failed"]


class AgentFallbackExhaustedError(RuntimeError):
    """Raised when every governed provider attempt fails closed."""

    def __init__(self, attempts: tuple[AgentAttempt, ...]) -> None:
        self.attempts = attempts
        super().__init__("agent provider pool exhausted without verified GitHub success")


@dataclass(frozen=True, slots=True)
class AgentExecutionReport:
    """Untrusted provider report returned to the orchestrator."""

    status: ProviderReport
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"completed", "rate_limited", "failed"}:
            raise ValueError("unknown provider report status")


@dataclass(frozen=True, slots=True)
class AgentAttempt:
    provider: str
    state: ProviderState
    head_before: str
    head_after: str
    validation_passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AgentFallbackResult:
    provider: str
    head_before: str
    head_after: str
    attempts: tuple[AgentAttempt, ...]


class GovernedAgentFallbackDispatcher:
    """Run one canonical handoff through the fixed provider pool."""

    __slots__ = ("_execute", "_read_head", "_validate")

    def __init__(
        self,
        *,
        execute: Callable[[str, str], AgentExecutionReport],
        read_head: Callable[[], str],
        validate: Callable[[str], bool],
    ) -> None:
        self._execute = execute
        self._read_head = read_head
        self._validate = validate

    @property
    def provider_states(self) -> MappingProxyType[str, ProviderState]:
        return MappingProxyType({provider: "available" for provider in PROVIDER_ORDER})

    def dispatch_document(self, document: str | bytes) -> AgentFallbackResult:
        """Dispatch the exact canonical handoff; never regenerate it during fallback."""
        handoff = PromptAutomationEnvelopeHandoff.from_json(document)
        canonical_document = handoff.to_json()
        baseline_head = self._read_head()
        attempts: list[AgentAttempt] = []

        for provider in PROVIDER_ORDER:
            report = self._execute(provider, canonical_document)
            if report.status == "rate_limited":
                attempts.append(
                    AgentAttempt(provider, "rate_limited", baseline_head, baseline_head, False, report.detail)
                )
                continue
            if report.status == "failed":
                attempts.append(AgentAttempt(provider, "failed", baseline_head, baseline_head, False, report.detail))
                continue

            visible_head = self._read_head()
            if visible_head == baseline_head:
                attempts.append(
                    AgentAttempt(
                        provider,
                        "failed",
                        baseline_head,
                        visible_head,
                        False,
                        report.detail or "provider completed without GitHub-visible HEAD advancement",
                    )
                )
                continue

            validation_passed = bool(self._validate(visible_head))
            if not validation_passed:
                attempts.append(
                    AgentAttempt(
                        provider,
                        "failed",
                        baseline_head,
                        visible_head,
                        False,
                        report.detail or "targeted validation failed",
                    )
                )
                baseline_head = visible_head
                continue

            attempts.append(AgentAttempt(provider, "available", baseline_head, visible_head, True, report.detail))
            return AgentFallbackResult(
                provider=provider,
                head_before=baseline_head,
                head_after=visible_head,
                attempts=tuple(attempts),
            )

        raise AgentFallbackExhaustedError(tuple(attempts))


__all__ = [
    "AgentAttempt",
    "AgentExecutionReport",
    "AgentFallbackExhaustedError",
    "AgentFallbackResult",
    "GovernedAgentFallbackDispatcher",
    "PROVIDER_ORDER",
]
