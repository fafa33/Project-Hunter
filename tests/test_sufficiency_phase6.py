from __future__ import annotations

import json
from datetime import UTC, datetime

from hunter.automation import load_automation_config
from hunter.cli import main
from hunter.sufficiency import (
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessment,
    DataSufficiencyAutomationManager,
    DataSufficiencyRepository,
    SourceDisagreement,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def test_phase_six_cli_reports_sufficiency_separately_from_scoring_outputs(tmp_path, capsys) -> None:
    db = tmp_path / "sufficiency.sqlite"
    seed_repository(db)

    assert main(["sufficiency", "--db", str(db), "coverage"]) == 0
    coverage = json.loads(capsys.readouterr().out)
    assert coverage["data_sufficiency_only"] is True
    assert coverage["availability_by_state"]["unavailable"] == 1
    assert coverage["direct_observation_coverage"] == 0.5
    assert coverage["proxy_signal_coverage"] == 0.0
    assert "opportunity_score" not in coverage

    assert main(["sufficiency", "--db", str(db), "report"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "Scoring, ranking, valuation" in report["limitations"][1]
    assert report["coverage"]["confidence_limits"]


def test_phase_six_cli_exposes_requirements_assessment_missing_and_disagreements(tmp_path, capsys) -> None:
    db = tmp_path / "sufficiency.sqlite"
    seed_repository(db)

    assert main(["sufficiency", "--db", str(db), "requirements"]) == 0
    requirements = json.loads(capsys.readouterr().out)
    assert {row["output_field"] for row in requirements} == {"market_cap", "treasury"}

    assert main(["sufficiency", "--db", str(db), "assess", "candidate-1"]) == 0
    assessment = json.loads(capsys.readouterr().out)
    assert assessment["required_data"][1]["availability_state"] == "unavailable"
    assert assessment["degraded_mode_limitations"] == ["treasury missing"]

    assert main(["sufficiency", "--db", str(db), "missing", "candidate-1"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["material_missing_count"] == 1
    assert missing["missing_or_degraded"][0]["missing_reason"] == "provider_unavailable:market_data"

    assert main(["sufficiency", "--db", str(db), "disagreements"]) == 0
    disagreements = json.loads(capsys.readouterr().out)
    assert disagreements["replay_mode"] == "current"
    assert disagreements["disagreements"][0]["reason"] == "data_quality_state:compatible_sources_disagree"


def test_phase_six_historical_reports_are_cutoff_distinct(tmp_path, capsys) -> None:
    db = tmp_path / "sufficiency.sqlite"
    seed_repository(db)

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--strict-known",
                "coverage",
            ]
        )
        == 0
    )
    strict = json.loads(capsys.readouterr().out)
    assert strict["replay_mode"] == "historical_strict_known_by_hunter"
    assert strict["cutoff_at"] == NOW.isoformat()

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--reconstructed",
                "coverage",
            ]
        )
        == 0
    )
    reconstructed = json.loads(capsys.readouterr().out)
    assert reconstructed["replay_mode"] == "reconstructed_after_cutoff"


def test_phase_six_historical_disagreements_requirements_and_proxy_policy_are_cutoff_safe(tmp_path, capsys) -> None:
    db = tmp_path / "sufficiency.sqlite"
    repository = DataSufficiencyRepository(db)
    repository.save_requirement(requirement("market_cap", proxy_allowed=False, recorded_at=NOW))
    repository.save_requirement(requirement("market_cap", proxy_allowed=True, recorded_at=LATER))
    repository.save_disagreement(disagreement(reason="known-at-cutoff", recorded_at=NOW))
    repository.save_disagreement(disagreement(reason="later-recorded", recorded_at=LATER))

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--strict-known",
                "requirements",
            ]
        )
        == 0
    )
    strict_requirements = json.loads(capsys.readouterr().out)
    assert strict_requirements[0]["proxy_allowed"] == 0
    assert strict_requirements[0]["accepted_proxy_types"] == []

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--reconstructed",
                "requirements",
            ]
        )
        == 0
    )
    reconstructed_requirements = json.loads(capsys.readouterr().out)
    assert reconstructed_requirements[0]["proxy_allowed"] == 1
    assert reconstructed_requirements[0]["accepted_proxy_types"] == ["market_proxy"]

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--strict-known",
                "disagreements",
            ]
        )
        == 0
    )
    strict_disagreements = json.loads(capsys.readouterr().out)
    assert strict_disagreements["replay_mode"] == "historical_strict_known_by_hunter"
    assert strict_disagreements["disagreements"][0]["reason"] == "known-at-cutoff"

    assert (
        main(
            [
                "sufficiency",
                "--db",
                str(db),
                "--cutoff",
                NOW.isoformat(),
                "--reconstructed",
                "disagreements",
            ]
        )
        == 0
    )
    reconstructed_disagreements = json.loads(capsys.readouterr().out)
    assert reconstructed_disagreements["replay_mode"] == "reconstructed_after_cutoff"
    assert reconstructed_disagreements["disagreements"][0]["reason"] == "later-recorded"


def test_phase_six_automation_install_is_idempotent_and_scheduler_valid(tmp_path) -> None:
    config = tmp_path / "automation.yaml"
    manager = DataSufficiencyAutomationManager(config)

    first = manager.install()
    second = manager.install()
    status = manager.status()
    automation_config = load_automation_config(config)
    sufficiency_jobs = tuple(job for job in automation_config.jobs if job.job_id.startswith("sufficiency-"))

    assert first == second
    assert status["installed_jobs"] == 6
    assert len(sufficiency_jobs) == 6
    assert {job.metadata["scheduler_role"] for job in sufficiency_jobs} == {"operational_only"}
    assert {job.metadata["provider_dependency"] for job in sufficiency_jobs} == {False}
    assert all(not job.pipeline_options.run_opportunity_timing for job in sufficiency_jobs)
    assert all(not job.pipeline_options.run_investment_committee for job in sufficiency_jobs)


def test_phase_six_cli_automation_status_uses_existing_scheduler_jobs(tmp_path, capsys) -> None:
    config = tmp_path / "automation.yaml"

    assert main(["sufficiency", "--automation-config", str(config), "automation", "install"]) == 0
    capsys.readouterr()
    assert main(["sufficiency", "--automation-config", str(config), "automation", "install"]) == 0
    capsys.readouterr()
    assert main(["sufficiency", "--automation-config", str(config), "automation", "status"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert status["installed_jobs"] == 6
    assert status["expected_jobs"] == 6


def seed_repository(path) -> None:
    repository = DataSufficiencyRepository(path)
    repository.save_requirement(requirement("market_cap"))
    repository.save_requirement(requirement("treasury"))
    repository.save_availability(availability("market_cap", "available", missing_reason=""))
    repository.save_availability(
        availability("treasury", "unavailable", missing_reason="provider_unavailable:market_data")
    )
    repository.save_assessment(assessment())
    repository.save_disagreement(disagreement())


def requirement(
    output_field: str,
    *,
    proxy_allowed: bool = False,
    recorded_at: datetime = NOW,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=f"requirement-{output_field}",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        output_field=output_field,
        requirement_kind="direct_observation",
        evidence_domain="market",
        required_entity_type="candidate",
        required_source_types=("market_data",),
        direct_observation_required=True,
        proxy_allowed=proxy_allowed,
        accepted_proxy_types=("market_proxy",) if proxy_allowed else (),
        minimum_freshness_seconds=86_400,
        minimum_source_authority="medium",
        minimum_lineage_depth=1,
        minimum_confidence=0.0,
        historical_required=True,
        blocking_level="required_for_output",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=recorded_at,
        schema_version="data-sufficiency-v1",
    )


def availability(output_field: str, state: str, *, missing_reason: str) -> DataAvailability:
    return DataAvailability(
        availability_id=f"availability-{output_field}",
        requirement_id=f"requirement-{output_field}",
        candidate_id="candidate-1",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        availability_state=state,
        directness="direct_observation" if state != "unavailable" else "unavailable",
        proxy_type=None,
        freshness_seconds=60 if state != "unavailable" else None,
        source_quality="high" if state != "unavailable" else "unavailable",
        lineage_complete=state != "unavailable",
        conflict_state="none",
        evidence_count=1 if state != "unavailable" else 0,
        missing_reason=missing_reason,
        effective_at=NOW,
        recorded_at=NOW,
        cutoff_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def assessment() -> DataSufficiencyAssessment:
    return DataSufficiencyAssessment(
        assessment_id="assessment-candidate-1",
        candidate_id="candidate-1",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        assessment_scope="candidate_report",
        sufficiency_state="insufficient",
        degraded_mode="blocked_insufficient_evidence",
        coverage_score=0.5,
        freshness_state="unavailable",
        source_quality_state="unavailable",
        lineage_state="missing",
        conflict_state="disputed",
        direct_observation_coverage=0.5,
        proxy_signal_coverage=0.0,
        material_missing_count=1,
        limitations_summary="treasury missing",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=NOW,
        cutoff_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def disagreement(
    *,
    reason: str = "data_quality_state:compatible_sources_disagree",
    recorded_at: datetime = NOW,
) -> SourceDisagreement:
    return SourceDisagreement(
        disagreement_id="disagreement-1",
        candidate_id="candidate-1",
        requirement_id="requirement-market_cap",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        disagreement_state="disagreement",
        compared_source_count=2,
        compatible_scope=True,
        reason=reason,
        effective_at=NOW,
        recorded_at=recorded_at,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )
