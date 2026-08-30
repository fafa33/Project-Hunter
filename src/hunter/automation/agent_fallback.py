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

from hunter.automation.environment_capabilities import assess_environment_capabilities
from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff, PromptAutomationHandoffError
from hunter.evidence_intelligence.smart_prompt_machine import SmartPromptMachineError
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationEnvelope, PromptAutomationVerifier

PROVIDER_ORDER = ("codex", "claude", "freebuff", "opencode", "jules")
ProviderState = Literal["available", "rate_limited", "failed"]
ProviderReport = Literal["completed", "rate_limited", "failed"]


class AgentFallbackExhaustedError(RuntimeError):
    """Raised when every governed provider attempt fails closed."""

    def __init__(self, attempts: tuple[AgentAttempt, ...]) -> None:
        self.attempts = attempts
        super().__init__("agent provider pool exhausted without verified GitHub success")


class AgentEnvironmentUnsuitableError(AgentFallbackExhaustedError):
    """Raised before provider execution when the shared runtime cannot satisfy the task."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        self.attempts = ()
        RuntimeError.__init__(self, f"ENVIRONMENT_UNSUITABLE: {','.join(reasons)}")


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
    """Run one verified canonical handoff through the fixed provider pool."""

    __slots__ = ("_execute", "_read_head", "_validate", "_verifier", "_provider_states")

    def __init__(
        self,
        *,
        execute: Callable[[str, str], AgentExecutionReport],
        read_head: Callable[[], str],
        validate: Callable[[str], bool],
        verifier: PromptAutomationVerifier,
    ) -> None:
        if type(verifier) is not PromptAutomationVerifier:
            raise TypeError("agent fallback requires the process-bound issuer verifier")
        self._execute = execute
        self._read_head = read_head
        self._validate = validate
        self._verifier = verifier
        self._provider_states: dict[str, ProviderState] = {provider: "available" for provider in PROVIDER_ORDER}

    @property
    def provider_states(self) -> MappingProxyType[str, ProviderState]:
        return MappingProxyType(dict(self._provider_states))

    def dispatch_document(self, document: str | bytes) -> AgentFallbackResult:
        """Verify, preflight, and dispatch one exact handoff; never regenerate it during fallback."""
        handoff = PromptAutomationEnvelopeHandoff.from_json(document)
        envelope = handoff.to_envelope()
        try:
            PromptAutomationEnvelope.verify_issuer_signature(envelope, self._verifier)
        except SmartPromptMachineError:
            raise PromptAutomationHandoffError("automation handoff issuer signature could not be verified") from None

        environment = assess_environment_capabilities()
        if not environment.suitable:
            raise AgentEnvironmentUnsuitableError(environment.reasons)

        canonical_document = handoff.to_json()
        baseline_head = self._read_head()
        attempts: list[AgentAttempt] = []

        for provider in PROVIDER_ORDER:
            head_before = baseline_head
            report = self._execute(provider, canonical_document)
            visible_head = self._read_head()

            if report.status == "rate_limited":
                self._provider_states[provider] = "rate_limited"
                attempts.append(AgentAttempt(provider, "rate_limited", head_before, visible_head, False, report.detail))
                baseline_head = visible_head
                continue
            if report.status == "failed":
                self._provider_states[provider] = "failed"
                attempts.append(AgentAttempt(provider, "failed", head_before, visible_head, False, report.detail))
                baseline_head = visible_head
                continue

            if visible_head == head_before:
                self._provider_states[provider] = "failed"
                attempts.append(
                    AgentAttempt(
                        provider,
                        "failed",
                        head_before,
                        visible_head,
                        False,
                        report.detail or "provider completed without GitHub-visible HEAD advancement",
                    )
                )
                continue

            validation_passed = bool(self._validate(visible_head))
            if not validation_passed:
                self._provider_states[provider] = "failed"
                attempts.append(
                    AgentAttempt(
                        provider,
                        "failed",
                        head_before,
                        visible_head,
                        False,
                        report.detail or "targeted validation failed",
                    )
                )
                baseline_head = visible_head
                continue

            self._provider_states[provider] = "available"
            attempts.append(AgentAttempt(provider, "available", head_before, visible_head, True, report.detail))
            return AgentFallbackResult(
                provider=provider,
                head_before=head_before,
                head_after=visible_head,
                attempts=tuple(attempts),
            )

        raise AgentFallbackExhaustedError(tuple(attempts))


__all__ = [
    "AgentAttempt",
    "AgentEnvironmentUnsuitableError",
    "AgentExecutionReport",
    "AgentFallbackExhaustedError",
    "AgentFallbackResult",
    "GovernedAgentFallbackDispatcher",
    "PROVIDER_ORDER",
]
