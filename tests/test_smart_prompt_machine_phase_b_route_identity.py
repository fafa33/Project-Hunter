from __future__ import annotations

import pytest

from hunter.evidence_intelligence.pre_model import EvidenceCapabilityConstraint, EvidencePromptSpecification
from hunter.evidence_intelligence.smart_prompt_machine import PromptMachineProfile, PromptMachineProfileRegistry
from hunter.evidence_intelligence.smart_prompt_routing import (
    PromptRouteConflict,
    PromptTaskRoute,
    PromptTaskRouteRegistry,
)


def _profile(profile_id: str, task_type: str) -> PromptMachineProfile:
    return PromptMachineProfile(
        profile_id=profile_id,
        version="1",
        task_type=task_type,
        workflow_stage="evidence-intelligence",
        output_contract_id="extraction-proposal",
        output_contract_version="1",
        context_policy_id="evidence-context",
        context_policy_version="1",
        required_span_ids=("span-a",),
        specification=EvidencePromptSpecification(
            specification_id=f"spec-{profile_id}",
            version="1",
            compiler_version="1",
            trusted_system_constraints="Return governed output only.",
            task_instruction="Perform the governed task.",
            output_contract='{"type":"object"}',
        ),
        capability=EvidenceCapabilityConstraint(
            constraint_id=f"cap-{profile_id}",
            version="1",
            maximum_input_bytes=32_000,
            reserved_completion_bytes=4_000,
        ),
    )


def test_route_registry_rejects_reused_route_id_version_with_different_payload() -> None:
    profiles = PromptMachineProfileRegistry(
        (
            _profile("profile-a", "TASK_A"),
            _profile("profile-b", "TASK_B"),
        )
    )
    first = PromptTaskRoute(
        route_id="extract",
        version="1",
        task_key="task.a",
        profile_id="profile-a",
        profile_version="1",
    )
    reused_coordinate = PromptTaskRoute(
        route_id="extract",
        version="1",
        task_key="task.b",
        profile_id="profile-b",
        profile_version="1",
    )

    with pytest.raises(PromptRouteConflict, match="conflicting governed route identity/version payload"):
        PromptTaskRouteRegistry((first, reused_coordinate), profiles=profiles)
