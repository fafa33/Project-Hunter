from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import hunter.automation.agent_fallback_runtime as runtime_module
from hunter.automation.agent_fallback import (
    AgentEnvironmentUnsuitableError,
    GovernedAgentFallbackDispatcher,
)
from hunter.automation.environment_capabilities import AVAILABLE_CAPABILITIES_ENV, REQUIRED_CAPABILITIES_ENV
from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff
from hunter.evidence_intelligence.smart_prompt_routing import (
    PromptAutomationVerifier,
    _issue_prompt_automation_envelope,
)

_SIGNING_KEY = "11" * 32
_VERIFYING_KEY = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
_REMOTE = "https://github.com/fafa33/Project-Hunter.git"


def _handoff(monkeypatch: pytest.MonkeyPatch) -> str:
    """Build one valid signed handoff for capability-preflight regression tests."""
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", _SIGNING_KEY)
    envelope = _issue_prompt_automation_envelope(
        task_request_id="task-env-authority",
        route_registry_identity="routes-1",
        profile_registry_identity="profiles-1",
        route_identity="engineering-review-fix-route-1",
        profile_identity="engineering-review-fix-1",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )
    return PromptAutomationEnvelopeHandoff.from_envelope(envelope).to_json()


def _verifier() -> PromptAutomationVerifier:
    """Return the process-bound verifier matching the regression signing key."""
    return PromptAutomationVerifier.from_environment(environ={"HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": _VERIFYING_KEY})


def _runtime_environment() -> dict[str, str]:
    """Return a complete injected runtime configuration with missing required egress."""
    values = {
        "PATH": "/usr/bin:/bin",
        "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY": _SIGNING_KEY,
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY": _VERIFYING_KEY,
        "HUNTER_AGENT_VALIDATION_COMMAND": '["validate-wrapper"]',
        "HUNTER_AGENT_ATTEMPT_TIMEOUT_SECONDS": "30",
        REQUIRED_CAPABILITIES_ENV: '["egress"]',
        AVAILABLE_CAPABILITIES_ENV: "[]",
    }
    for provider, name in runtime_module.PROVIDER_COMMAND_ENV.items():
        values[name] = f'["{provider}-wrapper"]'
    return values


def test_dispatcher_prefers_injected_environment_over_conflicting_process_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected capability authority must win over unrelated ambient process values."""
    monkeypatch.setenv(REQUIRED_CAPABILITIES_ENV, '["egress"]')
    monkeypatch.setenv(AVAILABLE_CAPABILITIES_ENV, '["egress"]')
    injected = {
        REQUIRED_CAPABILITIES_ENV: '["egress"]',
        AVAILABLE_CAPABILITIES_ENV: "[]",
    }
    dispatcher = GovernedAgentFallbackDispatcher(
        execute=lambda provider, document: pytest.fail("provider must not execute"),
        read_head=lambda: pytest.fail("remote HEAD must not be read"),
        validate=lambda head: False,
        verifier=_verifier(),
        environ=injected,
    )

    with pytest.raises(AgentEnvironmentUnsuitableError, match="missing_capability:egress"):
        dispatcher.dispatch_document(_handoff(monkeypatch))


def test_operational_runtime_propagates_its_injected_environment_to_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Operational runtime must pass its configured environment into capability preflight."""
    captured: dict[str, Any] = {}

    class CapturingDispatcher:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def dispatch_document(self, document: str | bytes) -> None:
            del document
            raise AgentEnvironmentUnsuitableError(("missing_capability:egress",))

    monkeypatch.setattr(runtime_module, "_pinned_github_remote", lambda _repo: _REMOTE)
    monkeypatch.setattr(runtime_module, "GovernedAgentFallbackDispatcher", CapturingDispatcher)
    environ = _runtime_environment()
    runtime = runtime_module.OperationalAgentFallbackRuntime(
        repo_dir=tmp_path,
        branch="issue-387-environment-preflight",
        environ=environ,
    )

    with pytest.raises(AgentEnvironmentUnsuitableError, match="missing_capability:egress"):
        runtime.dispatch("signed-handoff-placeholder")

    assert captured["environ"] == environ
