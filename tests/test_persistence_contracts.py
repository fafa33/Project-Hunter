from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from hunter.persistence import (
    PERSISTENCE_SCHEMA_VERSION,
    ConfigurationRecord,
    EngineManifestRecord,
    EvidenceRecord,
    FusedIntelligenceRecord,
    HistorySpec,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    PipelineRunRecord,
    QueryFilter,
    QuerySpec,
    SchemaVersion,
    SignalRecord,
    SnapshotRecord,
    SnapshotSpec,
    canonical_record_bytes,
    record_from_dict,
    record_from_json,
    record_to_dict,
    record_to_json,
)
from hunter.persistence.exceptions import PersistenceValidationError
from hunter.persistence.repositories import (
    ConfigurationRepository,
    EngineManifestRepository,
    EvidenceRepository,
    FusedIntelligenceRepository,
    InsightRepository,
    IntelligenceRepository,
    ObservationRepository,
    PipelineRunRepository,
    SignalRepository,
    SnapshotRepository,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_pipeline_run_record_validation_schema_version_and_immutability() -> None:
    record = pipeline_run_record()

    assert record.id == "pipeline-run:identity-v1:abc"
    assert record.schema_version == PERSISTENCE_SCHEMA_VERSION
    assert record.created_at == NOW
    assert record.effective_at == NOW
    with pytest.raises(FrozenInstanceError):
        record.status = "failed"  # type: ignore[misc]


def test_record_validation_rejects_missing_identity_naive_datetime_and_bad_ranges() -> None:
    with pytest.raises(PersistenceValidationError):
        pipeline_run_record(record_id="")
    with pytest.raises(PersistenceValidationError):
        pipeline_run_record(created_at=datetime(2026, 1, 2))
    with pytest.raises(PersistenceValidationError):
        evidence_record(reliability=1.5)


def test_all_canonical_records_can_be_constructed_and_validate() -> None:
    records = (
        pipeline_run_record(),
        evidence_record(),
        signal_record(),
        observation_record(),
        insight_record(),
        intelligence_record(),
        fused_intelligence_record(),
        snapshot_record(),
        configuration_record(),
        engine_manifest_record(),
    )

    assert [record.record_type for record in records] == [
        "pipeline-run",
        "evidence",
        "signal",
        "observation",
        "insight",
        "intelligence",
        "fused-intelligence",
        "snapshot",
        "configuration",
        "engine-manifest",
    ]
    for record in records:
        record.validate()


def test_serialization_round_trip_preserves_identity_and_schema_version() -> None:
    record = intelligence_record()

    restored = record_from_dict(record_to_dict(record))
    restored_json = record_from_json(record_to_json(record))

    assert restored == record
    assert restored_json == record
    assert restored.id == record.id
    assert restored.schema_version == PERSISTENCE_SCHEMA_VERSION


def test_serialization_is_deterministic_and_canonically_ordered() -> None:
    first = configuration_record(payload={"b": 2, "a": 1})
    second = configuration_record(payload={"a": 1, "b": 2})

    assert record_to_json(first) == record_to_json(second)
    assert canonical_record_bytes(first) == canonical_record_bytes(second)


def test_identity_preservation_does_not_generate_new_analytical_ids() -> None:
    record = evidence_record(record_id="evidence:analytical-identity-v1:source")
    restored = record_from_json(record_to_json(record))

    assert restored.id == "evidence:analytical-identity-v1:source"


def test_repository_contracts_expose_required_operations() -> None:
    required = {"save", "save_many", "load", "load_many", "exists", "delete", "query", "latest", "history", "snapshot"}
    repositories = (
        PipelineRunRepository,
        EvidenceRepository,
        SignalRepository,
        ObservationRepository,
        InsightRepository,
        IntelligenceRepository,
        FusedIntelligenceRepository,
        SnapshotRepository,
        ConfigurationRepository,
        EngineManifestRepository,
    )

    for repository in repositories:
        assert required.issubset(set(dir(repository)))


def test_query_history_and_snapshot_semantics_are_explicit_and_validated() -> None:
    query = QuerySpec(
        record_kind="intelligence",
        filters=(QueryFilter(field="project", value="bitcoin"),),
        limit=10,
    )
    history = HistorySpec(identity="intelligence:identity-v1:abc", limit=5)
    snapshot = SnapshotSpec(target_id="bitcoin", snapshot_type="daily", effective_at=NOW)

    assert query.filters[0].field == "project"
    assert history.identity == "intelligence:identity-v1:abc"
    assert snapshot.target_id == "bitcoin"
    with pytest.raises(ValueError):
        QuerySpec(limit=0)
    with pytest.raises(ValueError):
        HistorySpec(identity="")
    with pytest.raises(ValueError):
        SnapshotSpec(target_id="", snapshot_type="daily", effective_at=NOW)


def test_snapshot_record_preserves_record_membership_order() -> None:
    record = snapshot_record(record_ids=("record-b", "record-a"))
    restored = record_from_json(record_to_json(record))

    assert isinstance(restored, SnapshotRecord)
    assert restored.record_ids == ("record-b", "record-a")


def test_schema_version_metadata_supports_future_migrations_without_migrating() -> None:
    version = SchemaVersion(PERSISTENCE_SCHEMA_VERSION)

    assert version.name == PERSISTENCE_SCHEMA_VERSION
    with pytest.raises(ValueError):
        SchemaVersion("")


def pipeline_run_record(
    *,
    record_id: str = "pipeline-run:identity-v1:abc",
    created_at: datetime = NOW,
) -> PipelineRunRecord:
    return PipelineRunRecord(
        id=record_id,
        created_at=created_at,
        effective_at=NOW,
        metadata={"source": "test"},
        run_type="test",
        target_id="bitcoin",
        target_type="project",
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        input_fingerprint="input:fingerprint-v1:abc",
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        status="succeeded",
        requested_at=NOW,
    )


def evidence_record(
    *,
    record_id: str = "evidence:identity-v1:abc",
    reliability: float = 0.9,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=record_id,
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        source="fixture",
        reference="fixture://evidence",
        collected_at=NOW,
        reliability=reliability,
        freshness=0.8,
        raw_data={"value": 1},
    )


def signal_record() -> SignalRecord:
    return SignalRecord(
        id="signal:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        engine_id="macro-intelligence",
        project="bitcoin",
        timestamp=NOW,
        category="macro",
        strength=0.7,
        confidence=0.8,
        severity=0.2,
    )


def observation_record() -> ObservationRecord:
    return ObservationRecord(
        id="observation:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        engine_id="macro-intelligence",
        project="bitcoin",
        description="Observed evidence.",
        evidence_ids=("evidence:identity-v1:abc",),
        importance=0.7,
    )


def insight_record() -> InsightRecord:
    return InsightRecord(
        id="insight:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        title="Insight",
        explanation="Evidence supports the insight.",
        observation_ids=("observation:identity-v1:abc",),
        confidence=0.8,
        priority=0.6,
    )


def intelligence_record() -> IntelligenceRecord:
    return IntelligenceRecord(
        id="intelligence:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        project="bitcoin",
        engine_id="macro-intelligence",
        generated_at=NOW,
        signal_ids=("signal:identity-v1:abc",),
        evidence_ids=("evidence:identity-v1:abc",),
        observation_ids=("observation:identity-v1:abc",),
        insight_ids=("insight:identity-v1:abc",),
        confidence={"score": 0.8},
    )


def fused_intelligence_record() -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id="fused-intelligence:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        target_id="bitcoin",
        fusion_strategy="weighted-evidence",
        source_intelligence_ids=("intelligence:identity-v1:abc",),
        confidence={"score": 0.8},
    )


def snapshot_record(record_ids: tuple[str, ...] = ("intelligence:identity-v1:abc",)) -> SnapshotRecord:
    return SnapshotRecord(
        id="snapshot:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="daily",
        target_id="bitcoin",
        record_ids=record_ids,
        payload={"count": len(record_ids)},
    )


def configuration_record(payload: dict[str, int] | None = None) -> ConfigurationRecord:
    return ConfigurationRecord(
        id="configuration:fingerprint-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        configuration_type="engine",
        payload=payload or {"threshold": 1},
    )


def engine_manifest_record() -> EngineManifestRecord:
    return EngineManifestRecord(
        id="engine-manifest:fingerprint-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        engines=({"id": "macro-intelligence", "version": "1.0.0"},),
    )
