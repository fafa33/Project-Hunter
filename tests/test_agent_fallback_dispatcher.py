from __future__ import annotations

import json

import pytest

from hunter.automation.agent_fallback import (
    PROVIDER_ORDER,
    AgentExecutionReport,
    AgentFallbackExhaustedError,
    GovernedAgentFallbackDispatcher,
)
from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff, PromptAutomationHandoffError


def _handoff() -> str:
    return PromptAutomationEnvelopeHandoff(
        task_request_id="task-1",
        route_registry_identity="routes-1",
        profile_registry_identity="profiles-1",
        route_identity="engineering-review-fix-route-1",
        profile_identity="engineering-review-fix-1",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
        issuer_signature="00" * 64,
    ).to_json()


def test_rate_limit_falls_through_with_identical_governed_handoff() -> None:
    documents: list[str] = []
    heads = iter(("head-a", "head-b"))

    def execute(provider: str, document: str) -> AgentExecutionReport:
        documents.append(document)
        if provider == "codex":
            return AgentExecutionReport("rate_limited")
        return AgentExecutionReport("completed")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: next(heads),
        validate=lambda head: head == "head-b",
    )

    result = dispatcher.dispatch_document(_handoff())

    assert result.provider == "claude"
    assert [attempt.state for attempt in result.attempts] == ["rate_limited", "available"]
    assert documents == [_handoff(), _handoff()]


def test_local_only_completion_is_rejected_and_falls_through() -> None:
    calls: list[str] = []
    heads = iter(("head-a", "head-a", "head-b"))

    def execute(provider: str, document: str) -> AgentExecutionReport:
        calls.append(provider)
        return AgentExecutionReport("completed")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: next(heads),
        validate=lambda head: head == "head-b",
    )

    result = dispatcher.dispatch_document(_handoff())

    assert calls == ["codex", "claude"]
    assert result.provider == "claude"
    assert result.attempts[0].state == "failed"
    assert "HEAD advancement" in result.attempts[0].detail


def test_failed_validation_requires_next_provider_to_advance_head_again() -> None:
    heads = iter(("head-a", "head-b", "head-c"))
    validations: list[str] = []

    def validate(head: str) -> bool:
        validations.append(head)
        return head == "head-c"

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: AgentExecutionReport("completed"),
        read_head=lambda: next(heads),
        validate=validate,
    )

    result = dispatcher.dispatch_document(_handoff())

    assert result.provider == "claude"
    assert validations == ["head-b", "head-c"]
    assert result.head_before == "head-b"
    assert result.head_after == "head-c"


def test_exhausted_pool_fails_closed_with_actionable_attempts() -> None:
    def execute(provider: str, document: str) -> AgentExecutionReport:
        if provider == "codex":
            return AgentExecutionReport("rate_limited", "weekly limit")
        return AgentExecutionReport("failed", f"{provider} unavailable")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: "head-a",
        validate=lambda head: pytest.fail("validation must not run without a visible commit"),
    )

    with pytest.raises(AgentFallbackExhaustedError) as captured:
        dispatcher.dispatch_document(_handoff())

    assert tuple(attempt.provider for attempt in captured.value.attempts) == PROVIDER_ORDER
    assert captured.value.attempts[0].state == "rate_limited"
    assert all(attempt.state == "failed" for attempt in captured.value.attempts[1:])


def test_provider_order_is_fixed_and_caller_cannot_extend_handoff_schema() -> None:
    assert PROVIDER_ORDER == ("codex", "claude", "freebuff", "opencode", "jules")
    decoded = json.loads(_handoff())
    decoded["provider"] = "caller-choice"
    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: pytest.fail("invalid handoff must fail before execution"),
        read_head=lambda: "head-a",
        validate=lambda head: False,
    )

    with pytest.raises(PromptAutomationHandoffError, match="schema mismatch"):
        dispatcher.dispatch_document(json.dumps(decoded))
