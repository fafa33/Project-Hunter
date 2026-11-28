"""Issue #575: overlay-bound ECA must survive into governed execution dispatch."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_issue_agent_execution as harness  # noqa: E402

from hunter.automation.issue_agent_execution import (  # noqa: E402
    GovernedIssueAgentExecutionService,
    IssueAgentConfigurationError,
    IssueAgentExecutionError,
    SignedIssueAgentAuthorization,
    build_production_source_handling_resolver,
    issue_agent_intake_reference,
    issue_agent_task_request,
)
from hunter.evidence_intelligence.engineering_context_authority import (  # noqa: E402
    ENGINEERING_IMPLEMENT_TASK_KEY,
    EngineeringContextAuthority,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository  # noqa: E402


@pytest.fixture(autouse=True)
def _automation_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", harness.AUTOMATION_SIGNING_KEY_HEX)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", harness.AUTOMATION_VERIFYING_KEY_HEX)


def _service(deployment: Any, *, expected_lineage: dict[str, str] | None = None) -> GovernedIssueAgentExecutionService:
    return GovernedIssueAgentExecutionService(
        configuration=deployment.configuration,
        repository=EvidenceIntelligenceRepository(deployment.database),
        source_handling_resolver=build_production_source_handling_resolver(
            deployment.configuration, provenance_resolver=harness._provenance
        ),
        ledger=harness.IssueAgentExecutionLedger(deployment.database),
        fallback=deployment.fallback,
        verifier=deployment.verifier,
        issuer_verifier=deployment.issuer_verifier,
        clock=deployment.clock,
        engineering_context_authority=EngineeringContextAuthority(
            registry_path=ROOT / "docs" / "DEFECT_REGISTRY.json",
        ),
        expected_lineage=expected_lineage,
    )


def _signed_document() -> str:
    event = harness._event()
    event["issue"]["updated_at"] = "2099-01-01T00:00:00Z"
    document = harness.trigger.authorize_event(
        event,
        expected_repository=harness.REPOSITORY,
        owner_login=harness.OWNER,
        authorization_label=harness.ISSUE_AGENT_AUTHORIZATION_LABEL,
    )
    return harness.trigger.sign_authorization(document, signing_key=harness.ISSUER_SIGNING_KEY).to_json()


def _exact_lineage(deployment: Any, document: str) -> dict[str, str]:
    signed = SignedIssueAgentAuthorization.from_json(document)
    service = _service(deployment)
    authorization = signed.authorization
    reference = issue_agent_intake_reference(authorization)
    processed_at = service._clock.now()
    service._boundary.preflight(reference, processing_run_id=authorization.authorization_id, processed_at=processed_at)
    service._boundary.ingest(reference, processing_run_id=authorization.authorization_id, processed_at=processed_at)
    compiled = service._ingress.compile(
        issue_agent_task_request(authorization), implementation_scope=signed.implementation_scope
    )
    artifact = compiled.compilation.orchestration.build_result.prompt_artifact
    assert artifact is not None
    return {
        "prompt_sha256": hashlib.sha256(artifact.content.encode("utf-8")).hexdigest(),
        "dpm_context_sha256": hashlib.sha256(
            service._dpm.canonical_json(ENGINEERING_IMPLEMENT_TASK_KEY, scope=signed.implementation_scope).encode(
                "utf-8"
            )
        ).hexdigest(),
    }


def test_composition_root_passes_explicit_eca_into_the_smart_prompt_machine(tmp_path: Path) -> None:
    deployment = harness._deployment(tmp_path)
    service = _service(deployment)
    assert service._machine._engineering_context_authority is service._dpm
    assert isinstance(service._dpm, EngineeringContextAuthority)


def test_prompt_digest_mismatch_blocks_before_provider_dispatch(tmp_path: Path) -> None:
    deployment = harness._deployment(tmp_path)
    event = harness._event()
    event["issue"]["updated_at"] = "2099-01-01T00:00:00Z"
    document = harness.trigger.authorize_event(
        event,
        expected_repository=harness.REPOSITORY,
        owner_login=harness.OWNER,
        authorization_label=harness.ISSUE_AGENT_AUTHORIZATION_LABEL,
    )
    document = harness.trigger.sign_authorization(document, signing_key=harness.ISSUER_SIGNING_KEY).to_json()
    service = _service(
        deployment,
        expected_lineage={"prompt_sha256": "0" * 64, "dpm_context_sha256": "1" * 64},
    )
    with pytest.raises(IssueAgentExecutionError, match="prompt digest differs"):
        service.execute(document)
    assert deployment.fallback.documents == []


def test_expected_lineage_requires_both_prompt_and_dpm_digests(tmp_path: Path) -> None:
    deployment = harness._deployment(tmp_path)
    with pytest.raises(IssueAgentConfigurationError, match="prompt and DPM context digests"):
        _service(deployment, expected_lineage={"prompt_sha256": "0" * 64})


def test_dpm_context_digest_mismatch_blocks_before_provider_dispatch(tmp_path: Path) -> None:
    deployment = harness._deployment(tmp_path)
    document = _signed_document()
    lineage = _exact_lineage(deployment, document)
    lineage["dpm_context_sha256"] = "0" * 64
    service = _service(deployment, expected_lineage=lineage)
    with pytest.raises(IssueAgentExecutionError, match="DPM context digest differs"):
        service.execute(document)
    assert deployment.fallback.documents == []


def test_matching_lineage_allows_one_exact_dispatch(tmp_path: Path) -> None:
    deployment = harness._deployment(tmp_path)
    document = _signed_document()
    service = _service(deployment, expected_lineage=_exact_lineage(deployment, document))
    receipt = service.execute(document)
    assert receipt.authorization_id
    assert len(deployment.fallback.documents) == 1


def test_production_composition_refuses_unbound_execution_lineage() -> None:
    with pytest.raises(IssueAgentConfigurationError, match="bound at authorization"):
        GovernedIssueAgentExecutionService.from_environment(
            provenance_resolver=harness._provenance,
            environ={},
        )
