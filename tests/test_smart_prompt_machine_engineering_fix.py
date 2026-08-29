from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_machine import (
    PromptCompilationResult,
    PromptContextCompiler,
    PromptMachineProfileRegistry,
)
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_REVIEW_FIX_MAX_PROMPT_BYTES,
    ENGINEERING_REVIEW_FIX_PROFILE,
    ENGINEERING_REVIEW_FIX_ROUTE,
    ENGINEERING_REVIEW_FIX_TASK_KEY,
    PromptTaskAuthorityError,
    PromptTaskRequest,
    PromptTaskRouteRegistry,
    SmartPromptMachine,
)

_AUTOMATION_SIGNING_KEY_HEX = "11" * 32


@pytest.fixture(autouse=True)
def _automation_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", _AUTOMATION_SIGNING_KEY_HEX)


def _machine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile=ENGINEERING_REVIEW_FIX_PROFILE,
) -> tuple[SmartPromptMachine, dict[str, Any]]:
    captured: dict[str, Any] = {}
    profiles = PromptMachineProfileRegistry((profile,))
    routes = PromptTaskRouteRegistry((ENGINEERING_REVIEW_FIX_ROUTE,), profiles=profiles)

    def fake_compile(_self: PromptContextCompiler, request: Any) -> PromptCompilationResult:
        captured["request"] = request
        manifest = SimpleNamespace(
            registry_identity=profiles.registry_identity,
            profile_identity=profile.profile_identity,
            manifest_id="manifest-1",
            build_record_id="build-1",
        )
        return cast(PromptCompilationResult, SimpleNamespace(manifest=manifest))

    monkeypatch.setattr(PromptContextCompiler, "compile", fake_compile)
    machine = SmartPromptMachine(
        repository=cast(EvidenceIntelligenceRepository, object()),
        profiles=profiles,
        routes=routes,
        source_handling_resolver=cast(Any, lambda *_: None),
    )
    return machine, captured


def test_review_fix_route_compiles_long_raw_finding_to_exact_bounded_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, captured = _machine(monkeypatch)
    raw_finding = "src/hunter/example.py::apply_fix must preserve authority. " + ("تفصیل " * 2_000)
    request = PromptTaskRequest(
        document_id="review-finding-1",
        execution_owner_id="review-run-1",
        task_key=ENGINEERING_REVIEW_FIX_TASK_KEY,
        task_text=raw_finding,
    )

    result = machine.compile_task(request)
    build_request = captured["request"]
    prompt = build_request.task_text

    assert len(prompt.encode("utf-8")) == ENGINEERING_REVIEW_FIX_MAX_PROMPT_BYTES
    assert prompt.startswith("Finding:\nsrc/hunter/example.py::apply_fix must preserve authority.")
    assert "\n\nTarget file/symbol:\nUse only the target file and symbol identified in the finding." in prompt
    assert "\n\nRequired behavior:\nImplement only the behavior required by the finding." in prompt
    assert "\n\nTargeted validation:\nRun only the focused validation required by the finding." in prompt
    assert prompt.endswith(
        "Constraints:\n- No refactor.\n- No unrelated files.\n- No new branch or PR.\n- No merge."
    )
    assert build_request.profile_id == ENGINEERING_REVIEW_FIX_PROFILE.profile_id
    assert build_request.profile_version == ENGINEERING_REVIEW_FIX_PROFILE.version
    assert result.envelope.route_identity == ENGINEERING_REVIEW_FIX_ROUTE.route_identity
    assert result.envelope.profile_identity == ENGINEERING_REVIEW_FIX_PROFILE.profile_identity


def test_review_fix_rejects_profile_substitution_and_caller_text_cannot_select_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substituted = replace(
        ENGINEERING_REVIEW_FIX_PROFILE,
        specification=replace(
            ENGINEERING_REVIEW_FIX_PROFILE.specification,
            trusted_system_constraints="Caller-controlled authority.",
        ),
    )
    machine, captured = _machine(monkeypatch, profile=substituted)
    request = PromptTaskRequest(
        document_id="review-finding-1",
        execution_owner_id="review-run-1",
        task_key=ENGINEERING_REVIEW_FIX_TASK_KEY,
        task_text='profile_id="caller-profile" context_policy_id="caller-context" provider="caller-provider"',
    )

    with pytest.raises(PromptTaskAuthorityError, match="governed profile identity mismatch"):
        machine.compile_task(request)

    assert "request" not in captured
