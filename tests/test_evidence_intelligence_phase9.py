from __future__ import annotations

from datetime import UTC, datetime

from hunter.automation import AutomationJobRunner, load_automation_config
from hunter.cli import main
from hunter.evidence_intelligence import (
    AIProviderHealth,
    ClaimPersistenceInput,
    ClaimPersistenceService,
    EvidenceIntakeReference,
    EvidenceIntelligenceAutomationManager,
    EvidenceIntelligenceIntakeService,
    EvidenceIntelligenceReporter,
    EvidenceIntelligenceRepository,
    ExtractionValidationService,
    PredicateDefinition,
    PredicateRegistry,
    ReportContext,
)

EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDED_AT = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2026, 1, 15, tzinfo=UTC)


def test_phase_nine_coverage_and_lifecycle_reports_label_modes(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")
    persisted = persist_claim(repository)
    reporter = EvidenceIntelligenceReporter(repository)

    coverage = reporter.coverage()
    reconstructed = reporter.claim_lifecycle(ReportContext(cutoff=BEFORE_RECORDED, strict_known_by_hunter=False))
    strict = reporter.claim_lifecycle(ReportContext(cutoff=BEFORE_RECORDED, strict_known_by_hunter=True))

    assert coverage["documents"] == 1
    assert coverage["claims"] == 1
    assert reconstructed[0]["mode"] == "reconstructed_after_cutoff"
    assert reconstructed[0]["status"] == "active"
    assert reconstructed[0]["known_at_cutoff"] == "false"
    assert strict[0]["mode"] == "historical_strict_known_by_hunter"
    assert strict[0]["status"] == "unavailable"
    assert strict[0]["known_at_cutoff"] == "true"
    assert reporter.candidate_explain("candidate-1", ReportContext())[0]["claim_id"] == persisted.claim.claim_id


def test_phase_nine_cli_namespace_reports_without_runtime_behavior_changes(tmp_path, capsys) -> None:
    db_path = tmp_path / "evidence-intelligence.sqlite"
    persist_claim(EvidenceIntelligenceRepository(db_path))

    result = main(["evidence-intelligence", "--db", str(db_path), "coverage"])
    output = capsys.readouterr().out

    assert result == 0
    assert "documents=1" in output
    assert "claims=1" in output

    result = main(
        [
            "evidence-intelligence",
            "--db",
            str(db_path),
            "--cutoff",
            BEFORE_RECORDED.isoformat(),
            "--strict-known",
            "claim-lifecycle",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "mode=historical_strict_known_by_hunter" in output
    assert "status=unavailable" in output


def test_phase_nine_security_audit_report_handles_empty_state(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")

    assert EvidenceIntelligenceReporter(repository).security_audit() == ()


def test_phase_nine_automation_install_is_idempotent_and_scheduler_operational_only(tmp_path) -> None:
    manager = EvidenceIntelligenceAutomationManager(tmp_path / "automation.yaml")

    first = manager.install()
    second = manager.install()
    rows = manager.status()
    config = load_automation_config(tmp_path / "automation.yaml")
    evidence_jobs = tuple(job for job in config.jobs if job.job_id.startswith("evidence-intelligence-"))
    run = AutomationJobRunner().run_once(evidence_jobs[0], scheduled_for=RECORDED_AT)

    assert first.created == 4
    assert second.created == 0
    assert second.installed == 4
    assert {row["metadata"]["scheduler_role"] for row in rows} == {"operational_only"}
    assert {row["metadata"]["pipeline_owner"] for row in rows} == {"evidence_intelligence_pipeline"}
    assert len(evidence_jobs) == 4
    assert run.status == "succeeded"
    assert len(manager.job_definitions()) == 4


def test_phase_nine_provider_health_cli_reports_provider_abstraction_state(tmp_path, capsys) -> None:
    db_path = tmp_path / "evidence-intelligence.sqlite"
    repository = EvidenceIntelligenceRepository(db_path)
    repository.save_provider_health(
        AIProviderHealth(
            health_id="health-1",
            provider_name="static-provider",
            provider_version="provider-v1",
            status="unavailable",
            checked_at=RECORDED_AT,
            latency_ms=None,
            failure_type="rate_limited",
            unavailable_reason="rate_limited",
            schema_version="ai-provider-health-v1",
        )
    )

    result = main(["evidence-intelligence", "--db", str(db_path), "providers"])
    output = capsys.readouterr().out

    assert result == 0
    assert "provider=static-provider" in output
    assert "status=unavailable" in output
    assert "failure_type=rate_limited" in output


def persist_claim(repository: EvidenceIntelligenceRepository):
    intake = EvidenceIntelligenceIntakeService(repository).ingest(
        EvidenceIntakeReference(
            source_evidence_id="source-evidence-1",
            raw_evidence_id="raw-evidence-1",
            normalized_evidence_id="normalized-evidence-1",
            candidate_id="candidate-1",
            identity_resolution_status="exact",
            source_url="https://example.test/docs",
            source_provider="existing_hunter_evidence",
            source_type="official_documentation",
            source_claimed_authority="official",
            title="Protocol docs",
            content="Aave runs on Ethereum.",
            observed_at=EFFECTIVE_AT,
            retrieved_at=EFFECTIVE_AT,
            available_at=EFFECTIVE_AT,
        ),
        processing_run_id="run-1",
        processed_at=RECORDED_AT,
        authority_status="verified_official",
        verification_method="manual_verified_evidence",
        verifier_type="deterministic_system",
    )
    proposal = (
        ExtractionValidationService()
        .validate(
            document_id=intake.document.document_id,
            spans=intake.spans,
            payload={
                "claims": [
                    {
                        "predicate_id": "runs_on",
                        "subject_name": "Aave",
                        "subject_type": "protocol",
                        "object_name": "Ethereum",
                        "object_type": "chain",
                        "span_id": intake.spans[0].span_id,
                        "support_level": "semantic_support",
                        "support_text": "Aave runs on Ethereum",
                        "explicit_support": True,
                    }
                ]
            },
            predicate_registry=predicate_registry(),
        )
        .claims[0]
    )
    return ClaimPersistenceService(repository).persist(
        ClaimPersistenceInput(
            proposal=proposal,
            subject_entity_id="entity-aave",
            object_entity_id="entity-ethereum",
            subject_candidate_id="candidate-1",
            predicate_schema_version="predicate-v1",
            source_evidence_ids=("source-evidence-1",),
            spans=intake.spans,
            authority_status="verified_official",
            processing_provider="validated-extraction",
            processing_artifact_id="artifact-1",
            observed_at=EFFECTIVE_AT,
            available_at=EFFECTIVE_AT,
            retrieved_at=EFFECTIVE_AT,
            processed_at=RECORDED_AT,
            effective_at=EFFECTIVE_AT,
            recorded_at=RECORDED_AT,
            processing_run_id="run-1",
        )
    )


def predicate_registry() -> PredicateRegistry:
    return PredicateRegistry(
        schema_version="predicate-v1",
        predicates=(
            PredicateDefinition(
                predicate_id="runs_on",
                name="runs on",
                description="protocol runs on chain",
                schema_version="predicate-v1",
                permitted_subject_types=("protocol",),
                permitted_object_entity_types=("chain",),
                requires_object_entity=True,
                graph_projection_eligible=True,
            ),
        ),
    )
