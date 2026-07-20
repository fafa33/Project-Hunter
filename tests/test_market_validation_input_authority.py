from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hunter.jsonl_contract import JsonlWritePlan, envelope, normalize_record
from hunter.market_validation.acquisition_sources import _timing_engine_sources
from hunter.market_validation.models import EngineValidationSource, ProjectValidationTarget
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.timing.models import TimingAssessment
from hunter.timing.repository import TIMING_JSONL_SCHEMA, timing_assessment_from_payload

NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


def test_graph_centrality_cannot_populate_necessity_gap() -> None:
    result = _execute((_source("necessity_gap", source="technology-graph", collector="dependency-repository"),))

    source = next(item for item in result.engine_sources if item.engine == "necessity_gap")
    assert result.necessity_gap == 0.0
    assert source.status == "UNAVAILABLE"
    assert source.warnings == ("contract_unavailable:necessity_gap",)
    assert "necessity_gap" in result.missing_evidence


def test_noncanonical_timing_sources_cannot_populate_canonical_timing() -> None:
    for source_name in ("economic-graph", "scenario-simulation", "fusion", "opportunity"):
        result = _execute((_source("opportunity_timing", source=source_name),))

        assert result.opportunity_timing == 0.0
        assert "opportunity_timing" in result.missing_evidence
        timing = next(item for item in result.engine_sources if item.engine == "opportunity_timing")
        assert timing.status == "UNAVAILABLE"
        assert timing.warnings == ("strict_known_canonical_timing_unavailable",)


def test_strict_known_canonical_timing_is_accepted_with_actual_time_provenance() -> None:
    record = _record(_assessment())

    selected = _timing_engine_sources((record,), as_of=NOW)
    timing = selected["bitcoin"][0]
    result = _execute((timing,))

    assert result.opportunity_timing == 0.72
    assert timing.timestamp == NOW
    assert timing.raw_input_metrics["effective_at"] == NOW.isoformat()
    assert timing.raw_input_metrics["recorded_at"] == NOW.isoformat()
    assert timing.raw_input_metrics["known_at"] == NOW.isoformat()
    assert timing.raw_input_metrics["schema_version"] == TIMING_JSONL_SCHEMA


def test_strict_known_timing_rejects_future_latest_legacy_unknown_stale_and_superseded() -> None:
    eligible = _record(_assessment(assessment_id="eligible"))
    future = _record(
        _assessment(assessment_id="future", generated_at=NOW + timedelta(days=1)),
        recorded_at=NOW + timedelta(days=1),
        known_at=NOW + timedelta(days=1),
        effective_at=NOW + timedelta(days=1),
    )
    legacy = normalize_record(_payload(_assessment(assessment_id="legacy")), supported_schema=TIMING_JSONL_SCHEMA)
    unknown = _record(_assessment(assessment_id="unknown"), known_at=None)
    stale = _record(_assessment(assessment_id="stale", freshness=0.4))
    stale_lineage = _record(_assessment(assessment_id="stale-lineage", stale_evidence=("macro",)))
    superseded = _record(_assessment(assessment_id="superseded"), extra={"lifecycle_status": "SUPERSEDED"})
    invalid = _record(_assessment(assessment_id="invalid"), extra={"validation_status": "INVALID"})

    selected = _timing_engine_sources(
        (future, legacy, unknown, stale, stale_lineage, superseded, invalid, eligible), as_of=NOW
    )

    assert selected["bitcoin"][0].source_record_ids == ("eligible",)


def test_timestamp_backfill_and_incompatible_timing_are_rejected() -> None:
    post_cutoff_payload = _record(
        _assessment(assessment_id="backfilled", generated_at=NOW + timedelta(hours=1)),
        effective_at=NOW,
        recorded_at=NOW,
        known_at=NOW,
    )
    recursive = _record(_assessment(assessment_id="recursive", source_engines=("opportunity_timing",)))
    insufficient = _record(_assessment(assessment_id="insufficient", classification="INSUFFICIENT_EVIDENCE"))

    assert _timing_engine_sources((post_cutoff_payload, recursive, insufficient), as_of=NOW) == {}


def test_timing_adapter_is_pure_over_supplied_records() -> None:
    record = _record(_assessment())

    first = _timing_engine_sources((record,), as_of=NOW)
    second = _timing_engine_sources((record,), as_of=NOW)

    assert first == second


def _execute(sources: tuple[EngineValidationSource, ...]):
    return EvidenceBackedProjectExecutor(NOW, {"bitcoin": sources}).execute_project(
        ProjectValidationTarget("bitcoin", "Bitcoin", "store-of-value"), run_id="run"
    )


def _source(
    engine: str,
    *,
    source: str = "persisted-upstream",
    collector: str = "repository",
) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=0.8,
        confidence=0.9,
        timestamp=NOW,
        freshness=0.9,
        source_record_ids=(f"record:{engine}",),
        evidence_ids=(f"evidence:{engine}",),
        repository_ids=(f"repository:{engine}",),
        source=source,
        collector=collector,
    )


def _assessment(
    *,
    assessment_id: str = "timing-1",
    generated_at: datetime = NOW,
    freshness: float = 0.9,
    stale_evidence: tuple[str, ...] = (),
    source_engines: tuple[str, ...] = ("macro_intelligence", "whale_intelligence"),
    classification: str = "ACCUMULATION",
) -> TimingAssessment:
    return TimingAssessment(
        assessment_id=assessment_id,
        project_id="bitcoin",
        generated_at=generated_at,
        entry_score=0.72,
        exit_score=0.2,
        accumulation_score=0.7,
        distribution_score=0.2,
        risk_reward_score=0.8,
        cycle_position="accumulation",
        market_regime="risk-on",
        timing_confidence=0.85,
        evidence_quality=0.9,
        freshness=freshness,
        classification=classification,
        source_engines=source_engines,
        evidence_ids=("evidence-1",),
        repository_ids=("repository-1",),
        reasoning_chain=("canonical timing",),
        stale_evidence=stale_evidence,
        raw_inputs={"macro": 0.7},
        normalized_factors={"entry": 0.72},
    )


def _payload(assessment: TimingAssessment) -> dict[str, object]:
    payload = {
        "assessment_id": assessment.assessment_id,
        "project_id": assessment.project_id,
        "generated_at": assessment.generated_at.isoformat(),
        "entry_score": assessment.entry_score,
        "exit_score": assessment.exit_score,
        "accumulation_score": assessment.accumulation_score,
        "distribution_score": assessment.distribution_score,
        "risk_reward_score": assessment.risk_reward_score,
        "cycle_position": assessment.cycle_position,
        "market_regime": assessment.market_regime,
        "timing_confidence": assessment.timing_confidence,
        "evidence_quality": assessment.evidence_quality,
        "freshness": assessment.freshness,
        "classification": assessment.classification,
        "source_engines": assessment.source_engines,
        "evidence_ids": assessment.evidence_ids,
        "repository_ids": assessment.repository_ids,
        "reasoning_chain": assessment.reasoning_chain,
        "missing_evidence": assessment.missing_evidence,
        "stale_evidence": assessment.stale_evidence,
        "raw_inputs": dict(assessment.raw_inputs),
        "normalized_factors": dict(assessment.normalized_factors),
    }
    assert timing_assessment_from_payload(payload) == assessment
    return payload


def _record(
    assessment: TimingAssessment,
    *,
    recorded_at: datetime = NOW,
    known_at: datetime | None = NOW,
    effective_at: datetime | None = None,
    extra: dict[str, object] | None = None,
):
    payload = {**_payload(assessment), **(extra or {})}
    plan = JsonlWritePlan(
        TIMING_JSONL_SCHEMA,
        recorded_at,
        known_at,
        None if known_at is not None else "known time unavailable",
        effective_at or assessment.generated_at,
    )
    return normalize_record(envelope(payload, plan), supported_schema=TIMING_JSONL_SCHEMA)
