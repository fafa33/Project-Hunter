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
from hunter.evidence_intelligence.smart_prompt_routing import (
    PromptAutomationVerifier,
    _issue_prompt_automation_envelope,
)

_AUTOMATION_SIGNING_KEY_HEX = "11" * 32
_AUTOMATION_VERIFYING_KEY_HEX = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"


def _handoff(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", _AUTOMATION_SIGNING_KEY_HEX)
    envelope = _issue_prompt_automation_envelope(
        task_request_id="task-1",
        route_registry_identity="routes-1",
        profile_registry_identity="profiles-1",
        route_identity="engineering-review-fix-route-1",
        profile_identity="engineering-review-fix-1",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )
    return PromptAutomationEnvelopeHandoff.from_envelope(envelope).to_json()


def _verifier() -> PromptAutomationVerifier:
    return PromptAutomationVerifier.from_environment(
        environ={"HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": _AUTOMATION_VERIFYING_KEY_HEX}
    )


def test_rate_limit_falls_through_with_identical_governed_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _handoff(monkeypatch)
    documents: list[str] = []
    heads = iter(("head-a", "head-a", "head-b"))

    def execute(provider: str, handoff_document: str) -> AgentExecutionReport:
        documents.append(handoff_document)
        if provider == "codex":
            return AgentExecutionReport("rate_limited")
        return AgentExecutionReport("completed")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: next(heads),
        validate=lambda head: head == "head-b",
        verifier=_verifier(),
    )

    result = dispatcher.dispatch_document(document)

    assert result.provider == "claude"
    assert [attempt.state for attempt in result.attempts] == ["rate_limited", "available"]
    assert documents == [document, document]
    assert dispatcher.provider_states["codex"] == "rate_limited"
    assert dispatcher.provider_states["claude"] == "available"


def test_local_only_completion_is_rejected_and_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    heads = iter(("head-a", "head-a", "head-b"))

    def execute(provider: str, document: str) -> AgentExecutionReport:
        calls.append(provider)
        return AgentExecutionReport("completed")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: next(heads),
        validate=lambda head: head == "head-b",
        verifier=_verifier(),
    )

    result = dispatcher.dispatch_document(_handoff(monkeypatch))

    assert calls == ["codex", "claude"]
    assert result.provider == "claude"
    assert result.attempts[0].state == "failed"
    assert dispatcher.provider_states["codex"] == "failed"
    assert "HEAD advancement" in result.attempts[0].detail


def test_failed_validation_requires_next_provider_to_advance_head_again(monkeypatch: pytest.MonkeyPatch) -> None:
    heads = iter(("head-a", "head-b", "head-c"))
    validations: list[str] = []

    def validate(head: str) -> bool:
        validations.append(head)
        return head == "head-c"

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: AgentExecutionReport("completed"),
        read_head=lambda: next(heads),
        validate=validate,
        verifier=_verifier(),
    )

    result = dispatcher.dispatch_document(_handoff(monkeypatch))

    assert result.provider == "claude"
    assert validations == ["head-b", "head-c"]
    assert result.head_before == "head-b"
    assert result.head_after == "head-c"


def test_failed_provider_push_cannot_be_claimed_by_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    heads = iter(("head-a", "head-b", "head-b", "head-b", "head-b", "head-b"))

    def execute(provider: str, document: str) -> AgentExecutionReport:
        calls.append(provider)
        if provider == "codex":
            return AgentExecutionReport("failed", "post-push step failed")
        if provider == "claude":
            return AgentExecutionReport("completed")
        return AgentExecutionReport("failed")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: next(heads),
        validate=lambda head: pytest.fail("stale provider commit must not validate"),
        verifier=_verifier(),
    )

    with pytest.raises(AgentFallbackExhaustedError) as captured:
        dispatcher.dispatch_document(_handoff(monkeypatch))

    assert calls == list(PROVIDER_ORDER)
    assert captured.value.attempts[0].head_after == "head-b"
    assert captured.value.attempts[1].state == "failed"
    assert "HEAD advancement" in captured.value.attempts[1].detail


def test_exhausted_pool_fails_closed_with_actionable_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(provider: str, document: str) -> AgentExecutionReport:
        if provider == "codex":
            return AgentExecutionReport("rate_limited", "weekly limit")
        return AgentExecutionReport("failed", f"{provider} unavailable")

    dispatcher = GovernedAgentFallbackDispatcher(
        execute=execute,
        read_head=lambda: "head-a",
        validate=lambda head: pytest.fail("validation must not run without a visible commit"),
        verifier=_verifier(),
    )

    with pytest.raises(AgentFallbackExhaustedError) as captured:
        dispatcher.dispatch_document(_handoff(monkeypatch))

    assert tuple(attempt.provider for attempt in captured.value.attempts) == PROVIDER_ORDER
    assert captured.value.attempts[0].state == "rate_limited"
    assert all(attempt.state == "failed" for attempt in captured.value.attempts[1:])
    assert dispatcher.provider_states["codex"] == "rate_limited"
    assert all(dispatcher.provider_states[provider] == "failed" for provider in PROVIDER_ORDER[1:])


def test_provider_order_is_fixed_and_caller_cannot_extend_handoff_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    assert PROVIDER_ORDER == ("codex", "claude", "freebuff", "opencode", "jules")
    decoded = json.loads(_handoff(monkeypatch))
    decoded["provider"] = "caller-choice"
    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: pytest.fail("invalid handoff must fail before execution"),
        read_head=lambda: "head-a",
        validate=lambda head: False,
        verifier=_verifier(),
    )

    with pytest.raises(PromptAutomationHandoffError, match="schema mismatch"):
        dispatcher.dispatch_document(json.dumps(decoded))


def test_forged_signature_is_rejected_before_provider_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    decoded = json.loads(_handoff(monkeypatch))
    decoded["issuer_signature"] = "00" * 64
    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: pytest.fail("forged handoff must fail before execution"),
        read_head=lambda: pytest.fail("forged handoff must fail before reading GitHub HEAD"),
        validate=lambda head: False,
        verifier=_verifier(),
    )

    with pytest.raises(PromptAutomationHandoffError, match="signature could not be verified"):
        dispatcher.dispatch_document(json.dumps(decoded))
