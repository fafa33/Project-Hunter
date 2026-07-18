from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.opportunity import (
    CURRENT_OPPORTUNITY_FACTORS,
    OpportunityAssessmentService,
    opportunity_factor_authorities,
)
from hunter.persistence.records import MarketValidationProjectResultRecord

NOW = datetime(2026, 7, 15, tzinfo=UTC)


class FixtureSource:
    def __init__(self, records: tuple[MarketValidationProjectResultRecord, ...]) -> None:
        self.records = records

    def market_validation_project_records(self, project_id: str) -> tuple[MarketValidationProjectResultRecord, ...]:
        return tuple(record for record in self.records if record.project_id == project_id)


class FailedSource:
    def market_validation_project_records(self, project_id: str) -> tuple[MarketValidationProjectResultRecord, ...]:
        raise OSError("fixture store unavailable")


def test_every_current_factor_has_one_authority_status() -> None:
    declarations = opportunity_factor_authorities()
    assert len(declarations) == len(CURRENT_OPPORTUNITY_FACTORS) == 17
    assert len({item.factor for item in declarations}) == 17
    assert {item.status for item in declarations} == {"approved_source", "unowned"}
    assert sum(item.status == "approved_source" for item in declarations) == 5
    assert sum(item.status == "unowned" for item in declarations) == 12


def test_valid_strict_known_record_builds_deterministic_snapshot_with_provenance() -> None:
    record = _record()
    service = OpportunityAssessmentService(FixtureSource((record,)))

    result = service.assemble("project-a", effective_as_of=NOW, known_by=NOW)

    assert result.snapshot.values.as_dict() == {
        "confidence": 0.7,
        "evidence_freshness": 0.8,
        "missing_evidence": 0.1176,
        "risk": 0.3,
        "validation_health": 0.9,
    }
    assert result.snapshot.evidence_ids == ("evidence-1",)
    assert len(result.snapshot.missing_evidence) == 12
    confidence = next(item for item in result.diagnostics if item.factor == "confidence")
    assert confidence.state == "available"
    assert confidence.record_id == record.id
    assert confidence.record_version == record.schema_version
    assert confidence.source_record_ids == ("source-1",)
    assert confidence.source_versions == ("source-v1",)
    assert confidence.evidence_references == ("evidence-1",)
    assert confidence.known_at == record.known_at
    assert result.to_json() == service.assemble("project-a", effective_as_of=NOW, known_by=NOW).to_json()


def test_post_cutoff_record_cannot_affect_assembly() -> None:
    eligible = _record(record_id="eligible", confidence=0.4)
    future = _record(
        record_id="future",
        confidence=0.99,
        effective_at=NOW + timedelta(days=1),
        recorded_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
    )
    result = OpportunityAssessmentService(FixtureSource((eligible, future))).assemble(
        "project-a", effective_as_of=NOW, known_by=NOW
    )
    assert result.snapshot.values["confidence"] == 0.4
    assert all(item.record_id != "future" for item in result.diagnostics)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("legacy", "legacy_non_strict"),
        ("stale", "stale"),
        ("invalid", "invalid"),
    ),
)
def test_non_strict_stale_and_invalid_records_never_supply_supportive_values(case: str, expected: str) -> None:
    if case == "legacy":
        record = _record(known_at=None, known_time_limitation="legacy record has no trustworthy known time")
    elif case == "stale":
        record = _record(stale_evidence=("confidence",))
    else:
        record = _record(authorized_payload={"authority_classification": "experimental"})
    result = OpportunityAssessmentService(FixtureSource((record,))).assemble(
        "project-a", effective_as_of=NOW, known_by=NOW
    )
    diagnostic = next(item for item in result.diagnostics if item.factor == "confidence")
    assert diagnostic.state == expected
    assert "confidence" not in result.snapshot.values


def test_missing_and_unavailable_sources_fail_closed_without_synthetic_evidence() -> None:
    missing = OpportunityAssessmentService().assemble("project-a", effective_as_of=NOW, known_by=NOW)
    unavailable = OpportunityAssessmentService(FailedSource()).assemble("project-a", effective_as_of=NOW, known_by=NOW)

    assert missing.snapshot.values.as_dict() == {"validation_health": 0.0}
    assert missing.snapshot.evidence_ids == ()
    assert len(missing.snapshot.missing_evidence) == 17
    approved = {item.factor for item in opportunity_factor_authorities() if item.status == "approved_source"}
    assert {item.factor for item in unavailable.diagnostics if item.state == "unavailable"} == approved
    assert unavailable.snapshot.values.as_dict() == {"validation_health": 0.0}


def test_unowned_factor_cannot_use_similarly_named_canonical_field() -> None:
    result = OpportunityAssessmentService(FixtureSource((_record(valuation=0.99),))).assemble(
        "project-a", effective_as_of=NOW, known_by=NOW
    )
    valuation = next(item for item in result.diagnostics if item.factor == "valuation_discount")
    assert valuation.state == "missing"
    assert valuation.value is None
    assert "valuation_discount" not in result.snapshot.values


def test_service_has_no_production_or_operational_wiring() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "src/hunter/cli.py",
        "src/hunter/pipeline.py",
        "src/hunter/dashboard_api.py",
        "src/hunter/operational_corpus.py",
        "src/hunter/automation",
        "src/hunter/timing",
        "src/hunter/market_validation",
        "desktop/OperationalConsole",
    )
    for relative in forbidden:
        path = root / relative
        files = (path,) if path.is_file() else tuple(path.rglob("*.py")) + tuple(path.rglob("*.swift"))
        assert all("OpportunityAssessmentService" not in item.read_text() for item in files)


def _record(
    *,
    record_id: str = "market-validation-project:project-a:v1",
    confidence: float = 0.7,
    valuation: float = 0.2,
    effective_at: datetime = NOW - timedelta(days=2),
    recorded_at: datetime = NOW - timedelta(days=1),
    known_at: datetime | None = NOW - timedelta(days=1, hours=1),
    known_time_limitation: str | None = None,
    stale_evidence: tuple[str, ...] = (),
    authorized_payload: dict[str, object] | None = None,
) -> MarketValidationProjectResultRecord:
    return MarketValidationProjectResultRecord(
        id=record_id,
        schema_version="canonical-market-validation-v1",
        created_at=recorded_at,
        effective_at=effective_at,
        validation_run_id="run-1",
        project_id="project-a",
        project_name="Project A",
        sector="infrastructure",
        rank=1,
        sector_rank=1,
        hunter_score=0.5,
        risk=0.3,
        confidence=confidence,
        valuation=valuation,
        comparative_valuation=0.4,
        mispricing=0.5,
        asymmetry=0.5,
        whale_intelligence=0.5,
        macro_intelligence=0.5,
        future_demand=0.5,
        opportunity_timing=0.5,
        probability=0.5,
        pattern_matching=0.5,
        technology_necessity=0.5,
        capital_rotation=0.5,
        necessity_gap=0.5,
        committee_decision="INSUFFICIENT_EVIDENCE",
        committee_confidence=0.4,
        missing_evidence=("macro", "whale"),
        stale_evidence=stale_evidence,
        data_freshness=0.8,
        validation_health=0.9,
        strongest_positive_drivers=(),
        strongest_negative_drivers=(),
        reasons_for_ranking=(),
        validation_warnings=(),
        known_at=known_at,
        known_time_limitation=known_time_limitation,
        model_version="market-validation-v1",
        configuration_fingerprint="config-v1",
        methodology_fingerprint="method-v1",
        source_record_ids=("source-1",),
        source_versions=("source-v1",),
        evidence_references=("evidence-1",),
        authorized_payload=authorized_payload or {"authority_classification": "production"},
    )
