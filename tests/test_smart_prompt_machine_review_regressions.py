from __future__ import annotations

from dataclasses import replace

import pytest

from hunter.evidence_intelligence.pre_model import (
    EvidenceCapabilityConstraint,
    EvidencePromptSpecification,
)
from hunter.evidence_intelligence.smart_prompt_machine import (
    PromptBuildAuthorityError,
    PromptBuildManifest,
    PromptMachineProfile,
    PromptProfileConflict,
)


def _profile() -> PromptMachineProfile:
    return PromptMachineProfile(
        profile_id="hunter-evidence-extraction",
        version="1",
        task_type="EVIDENCE_EXTRACTION",
        workflow_stage="evidence-intelligence",
        output_contract_id="extraction-proposal",
        output_contract_version="1",
        context_policy_id="evidence-context",
        context_policy_version="1",
        required_span_ids=("span-a",),
        specification=EvidencePromptSpecification(
            specification_id="evidence-extraction",
            version="1",
            compiler_version="1",
            trusted_system_constraints="Return governed output only.",
            task_instruction="Extract evidence.",
            output_contract='{"type":"object"}',
        ),
        capability=EvidenceCapabilityConstraint(
            constraint_id="phase-a-bytes",
            version="1",
            maximum_input_bytes=4096,
            reserved_completion_bytes=128,
        ),
    )


def test_profile_rejects_non_string_governed_coordinate() -> None:
    profile = _profile()
    with pytest.raises(ValueError, match="task_type must be non-empty"):
        replace(profile, task_type=None)  # type: ignore[arg-type]


def test_profile_rejects_non_tuple_or_non_string_required_span_ids() -> None:
    profile = _profile()
    with pytest.raises(PromptProfileConflict, match="tuple of non-empty strings"):
        replace(profile, required_span_ids="span-a")  # type: ignore[arg-type]

    with pytest.raises(PromptProfileConflict, match="non-empty strings"):
        replace(profile, required_span_ids=("span-a", 7))  # type: ignore[arg-type]


def test_manifest_rejects_unknown_schema_version() -> None:
    with pytest.raises(PromptBuildAuthorityError, match="manifest schema version"):
        PromptBuildManifest(
            request_id="request-1",
            registry_identity="registry-1",
            profile_identity="profile-1",
            build_record_id="build-1",
            intent_id="intent-1",
            ledger_id="ledger-1",
            allocation_id="allocation-1",
            package_id="package-1",
            prompt_plan_id="plan-1",
            prompt_artifact_id="artifact-1",
            schema_version="unsupported",
        )
