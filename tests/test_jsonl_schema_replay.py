from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.acquisition.models import RawEvidence
from hunter.acquisition.repositories import ACQUISITION_JSONL_SCHEMA, FileAcquisitionRepository
from hunter.jsonl_contract import JsonlContractError, JsonlWritePlan, envelope, read_records, strict_known
from hunter.macro.models import MacroProviderFailure
from hunter.macro.repository import MACRO_JSONL_SCHEMA, MacroRepository
from hunter.timing.models import TimingAssessment
from hunter.timing.repository import TIMING_JSONL_SCHEMA, TimingRepository
from hunter.whale.models import WhaleProviderFailure
from hunter.whale.repository import WHALE_JSONL_SCHEMA, WhaleRepository

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def test_each_domain_writes_versioned_record_with_service_supplied_recorded_time(tmp_path: Path) -> None:
    acquisition = FileAcquisitionRepository(tmp_path / "acquisition")
    acquisition.save_raw(
        (RawEvidence("provider", "collector", "source", "market", "price", "asset", NOW, {"value": 1}),),
        write_plan=_plan(ACQUISITION_JSONL_SCHEMA, known_at=NOW),
    )
    macro = MacroRepository(tmp_path / "macro")
    macro.save_failures(
        (MacroProviderFailure("provider", "metric", "failed", "message", "https://example.test", NOW),),
        write_plan=_plan(MACRO_JSONL_SCHEMA),
    )
    whale = WhaleRepository(tmp_path / "whale")
    whale.save_failures(
        (WhaleProviderFailure("provider", "metric", "failed", "message", "https://example.test", NOW),),
        write_plan=_plan(WHALE_JSONL_SCHEMA, known_at=NOW),
    )
    timing = TimingRepository(tmp_path / "timing")
    timing.save((_timing_assessment(),), write_plan=_plan(TIMING_JSONL_SCHEMA))

    rows = (
        acquisition.records("raw.jsonl")[0],
        macro.records(macro.failure_path)[0],
        whale.records(whale.failure_path)[0],
        timing.records(timing.assessment_path)[0],
    )
    assert [row.schema_version for row in rows] == [
        ACQUISITION_JSONL_SCHEMA,
        MACRO_JSONL_SCHEMA,
        WHALE_JSONL_SCHEMA,
        TIMING_JSONL_SCHEMA,
    ]
    assert all(row.recorded_at == NOW for row in rows)


def test_known_time_validation_and_strict_known_filtering(tmp_path: Path) -> None:
    with pytest.raises(JsonlContractError, match="later than recorded_at"):
        _plan("fixture-v1", known_at=NOW + timedelta(seconds=1))

    path = tmp_path / "records.jsonl"
    payloads = (
        envelope({"id": "eligible"}, _plan("fixture-v1", known_at=NOW)),
        envelope(
            {"id": "future-effective"},
            JsonlWritePlan("fixture-v1", NOW, NOW, None, NOW + timedelta(days=1)),
        ),
        envelope(
            {"id": "future-recorded"},
            JsonlWritePlan("fixture-v1", NOW + timedelta(days=1), NOW, None, NOW),
        ),
        envelope(
            {"id": "future-known"},
            JsonlWritePlan(
                "fixture-v1",
                NOW + timedelta(days=2),
                NOW + timedelta(days=1),
                None,
                NOW,
            ),
        ),
        envelope({"id": "unknown"}, _plan("fixture-v1")),
    )
    path.write_text("".join(json.dumps(item) + "\n" for item in payloads), encoding="utf-8")

    selected = strict_known(read_records(path, supported_schema="fixture-v1"), as_of=NOW)

    assert [record.payload["id"] for record in selected] == ["eligible"]


def test_legacy_records_are_readable_but_never_strict_known(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text('{"id":"legacy"}\n', encoding="utf-8")

    records = read_records(path, supported_schema="fixture-v1")

    assert records[0].payload == {"id": "legacy"}
    assert records[0].schema_version is None
    assert records[0].replay_limitation == "legacy unversioned record has unknown recorded/known-time provenance"
    assert strict_known(records, as_of=NOW) == ()


def test_unsupported_and_malformed_versioned_records_fail_deterministically(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.jsonl"
    unsupported.write_text(
        json.dumps(envelope({"id": "x"}, _plan("future-v2", known_at=NOW))) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(JsonlContractError, match="Unsupported JSONL schema version"):
        read_records(unsupported, supported_schema="fixture-v1")

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(
        '{"id":"x","_record_metadata":{"schema_version":"fixture-v1","recorded_at":"bad",'
        '"effective_at":"2026-07-18T12:00:00+00:00","known_at":null,'
        '"known_time_limitation":"unknown"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(JsonlContractError, match="Malformed recorded_at"):
        read_records(malformed, supported_schema="fixture-v1")


def _plan(schema: str, *, known_at: datetime | None = None) -> JsonlWritePlan:
    return JsonlWritePlan(
        schema,
        NOW,
        known_at,
        None if known_at is not None else "known time is not available from explicit provenance",
        NOW,
    )


def _timing_assessment() -> TimingAssessment:
    return TimingAssessment(
        assessment_id="timing-a",
        project_id="project-a",
        generated_at=NOW,
        entry_score=0.5,
        exit_score=0.5,
        accumulation_score=0.5,
        distribution_score=0.5,
        risk_reward_score=0.5,
        cycle_position="unknown",
        market_regime="unknown",
        timing_confidence=0.5,
        evidence_quality=0.5,
        freshness=0.5,
        classification="WAIT",
        source_engines=("fixture",),
        evidence_ids=("evidence-a",),
        repository_ids=("repository-a",),
        reasoning_chain=("fixture",),
    )
