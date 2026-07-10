from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, ClassVar

from hunter.execution.canonicalization import normalize
from hunter.persistence.exceptions import PersistenceValidationError
from hunter.persistence.identity import preserve_identity
from hunter.persistence.versioning import PERSISTENCE_SCHEMA_VERSION

MetadataValue = str | int | float | bool | None


@dataclass(frozen=True, kw_only=True)
class BasePersistenceRecord:
    record_type: ClassVar[str]

    id: str
    schema_version: str = PERSISTENCE_SCHEMA_VERSION
    created_at: datetime
    effective_at: datetime
    metadata: dict[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", preserve_identity(self.id))
        _require_text("schema_version", self.schema_version)
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("effective_at", self.effective_at)
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def validate(self) -> None:
        self.__post_init__()

    def identity_payload(self) -> dict[str, object]:
        return {
            "record_type": self.record_type,
            "id": self.id,
            "schema_version": self.schema_version,
            "effective_at": self.effective_at,
        }

    def serializable_fields(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, kw_only=True)
class PipelineRunRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "pipeline-run"

    run_type: str
    target_id: str
    target_type: str
    configuration_fingerprint: str
    input_fingerprint: str
    engine_manifest_fingerprint: str
    status: str
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_run_id: str | None = None
    replay_of_run_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("run_type", "target_id", "target_type", "configuration_fingerprint", "input_fingerprint", "engine_manifest_fingerprint", "status"):
            _require_text(name, getattr(self, name))
        for name in ("requested_at", "started_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                _require_aware_datetime(name, value)
                object.__setattr__(self, name, value.astimezone(UTC))


@dataclass(frozen=True, kw_only=True)
class OperationalAttemptRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "operational-attempt"

    attempt_id: str
    run_id: str
    attempt_number: int
    requested_at: datetime
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    warning_summary: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("attempt_id", "run_id", "status"):
            _require_text(name, getattr(self, name))
        if self.attempt_number < 1:
            raise PersistenceValidationError("attempt_number must be positive")
        _require_aware_datetime("requested_at", self.requested_at)
        object.__setattr__(self, "requested_at", self.requested_at.astimezone(UTC))
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                _require_aware_datetime(name, value)
                object.__setattr__(self, name, value.astimezone(UTC))


@dataclass(frozen=True, kw_only=True)
class EvidenceRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "evidence"

    pipeline_run_id: str
    source: str
    reference: str
    collected_at: datetime
    reliability: float
    freshness: float
    raw_data: Any

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "source", "reference"):
            _require_text(name, getattr(self, name))
        _require_aware_datetime("collected_at", self.collected_at)
        object.__setattr__(self, "collected_at", self.collected_at.astimezone(UTC))
        _range("reliability", self.reliability)
        _range("freshness", self.freshness)
        normalize(self.raw_data)


@dataclass(frozen=True, kw_only=True)
class SignalRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "signal"

    pipeline_run_id: str
    intelligence_id: str
    engine_id: str
    project: str
    timestamp: datetime
    category: str
    strength: float
    confidence: float
    severity: float

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "intelligence_id", "engine_id", "project", "category"):
            _require_text(name, getattr(self, name))
        _require_aware_datetime("timestamp", self.timestamp)
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        for name in ("strength", "confidence", "severity"):
            _range(name, getattr(self, name))


@dataclass(frozen=True, kw_only=True)
class ObservationRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "observation"

    pipeline_run_id: str
    intelligence_id: str
    engine_id: str
    project: str
    description: str
    evidence_ids: tuple[str, ...]
    importance: float

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "intelligence_id", "engine_id", "project", "description"):
            _require_text(name, getattr(self, name))
        object.__setattr__(self, "evidence_ids", _identity_tuple("evidence_ids", self.evidence_ids))
        _range("importance", self.importance)


@dataclass(frozen=True, kw_only=True)
class InsightRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "insight"

    pipeline_run_id: str
    intelligence_id: str
    title: str
    explanation: str
    observation_ids: tuple[str, ...]
    confidence: float
    priority: float

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "intelligence_id", "title", "explanation"):
            _require_text(name, getattr(self, name))
        object.__setattr__(self, "observation_ids", _identity_tuple("observation_ids", self.observation_ids))
        _range("confidence", self.confidence)
        _range("priority", self.priority)


@dataclass(frozen=True, kw_only=True)
class IntelligenceRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "intelligence"

    pipeline_run_id: str
    project: str
    engine_id: str
    generated_at: datetime
    signal_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    insight_ids: tuple[str, ...]
    confidence: dict[str, Any]
    engine_version: str | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    target_refs: tuple[tuple[str, str], ...] = ()
    evidence_references: tuple[str, ...] = ()
    evidence_lineage_keys: tuple[str, ...] = ()
    evidence_reliabilities: tuple[float, ...] = ()
    evidence_freshness: tuple[float, ...] = ()
    signal_categories: tuple[str, ...] = ()
    signal_strengths: tuple[float, ...] = ()
    signal_confidences: tuple[float, ...] = ()
    signal_severities: tuple[float, ...] = ()
    observation_descriptions: tuple[str, ...] = ()
    insight_titles: tuple[str, ...] = ()
    insight_explanations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "project", "engine_id"):
            _require_text(name, getattr(self, name))
        _require_aware_datetime("generated_at", self.generated_at)
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
        for name in ("signal_ids", "evidence_ids", "observation_ids", "insight_ids"):
            object.__setattr__(self, name, _identity_tuple(name, getattr(self, name)))
        for name in (
            "evidence_references",
            "evidence_lineage_keys",
            "evidence_reliabilities",
            "evidence_freshness",
            "signal_categories",
            "signal_strengths",
            "signal_confidences",
            "signal_severities",
            "observation_descriptions",
            "insight_titles",
            "insight_explanations",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "target_refs", tuple((str(kind), str(value)) for kind, value in self.target_refs))
        normalize(self.confidence)
        object.__setattr__(self, "confidence", dict(self.confidence))


@dataclass(frozen=True, kw_only=True)
class FusedIntelligenceRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "fused-intelligence"

    pipeline_run_id: str
    target_id: str
    fusion_strategy: str
    source_intelligence_ids: tuple[str, ...]
    confidence: dict[str, Any]
    target_type: str = "project"
    configuration_fingerprint: str = ""
    contribution_model_fingerprint: str = ""
    source_run_ids: tuple[str, ...] = ()
    effective_window: tuple[str, ...] = ()
    contributions: tuple[dict[str, Any], ...] = ()
    corroboration: dict[str, Any] = field(default_factory=dict)
    contradictions: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, Any] = field(default_factory=dict)
    missing_evidence: dict[str, Any] = field(default_factory=dict)
    unified_signals: tuple[dict[str, Any], ...] = ()
    unified_observations: tuple[dict[str, Any], ...] = ()
    unified_insights: tuple[dict[str, Any], ...] = ()
    unified_narrative: dict[str, Any] = field(default_factory=dict)
    graph_nodes: tuple[dict[str, Any], ...] = ()
    graph_edges: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("pipeline_run_id", "target_id", "fusion_strategy"):
            _require_text(name, getattr(self, name))
        object.__setattr__(self, "source_intelligence_ids", _identity_tuple("source_intelligence_ids", self.source_intelligence_ids))
        for name in ("source_run_ids", "effective_window"):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        for name in ("contributions", "unified_signals", "unified_observations", "unified_insights", "graph_nodes", "graph_edges"):
            object.__setattr__(self, name, tuple(_freeze_payload(item) for item in getattr(self, name)))
        for name in ("corroboration", "contradictions", "dependencies", "missing_evidence", "unified_narrative"):
            object.__setattr__(self, name, _freeze_payload(getattr(self, name)))
        normalize(self.confidence)
        object.__setattr__(self, "confidence", dict(self.confidence))
        for name in ("configuration_fingerprint", "contribution_model_fingerprint"):
            if getattr(self, name):
                _require_text(name, getattr(self, name))


@dataclass(frozen=True, kw_only=True)
class SnapshotRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "snapshot"

    snapshot_type: str
    target_id: str
    record_ids: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text("snapshot_type", self.snapshot_type)
        _require_text("target_id", self.target_id)
        object.__setattr__(self, "record_ids", _identity_tuple("record_ids", self.record_ids))
        normalize(self.payload)
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, kw_only=True)
class ConfigurationRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "configuration"

    configuration_fingerprint: str
    configuration_type: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text("configuration_fingerprint", self.configuration_fingerprint)
        _require_text("configuration_type", self.configuration_type)
        normalize(self.payload)
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, kw_only=True)
class EngineManifestRecord(BasePersistenceRecord):
    record_type: ClassVar[str] = "engine-manifest"

    engine_manifest_fingerprint: str
    engines: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text("engine_manifest_fingerprint", self.engine_manifest_fingerprint)
        engines = tuple(dict(engine) for engine in self.engines)
        normalize(engines)
        object.__setattr__(self, "engines", engines)


PersistenceRecord = (
    PipelineRunRecord
    | OperationalAttemptRecord
    | EvidenceRecord
    | SignalRecord
    | ObservationRecord
    | InsightRecord
    | IntelligenceRecord
    | FusedIntelligenceRecord
    | SnapshotRecord
    | ConfigurationRecord
    | EngineManifestRecord
)

RECORD_TYPES: dict[str, type[PersistenceRecord]] = {
    record.record_type: record
    for record in (
        PipelineRunRecord,
        OperationalAttemptRecord,
        EvidenceRecord,
        SignalRecord,
        ObservationRecord,
        InsightRecord,
        IntelligenceRecord,
        FusedIntelligenceRecord,
        SnapshotRecord,
        ConfigurationRecord,
        EngineManifestRecord,
    )
}


def _require_text(name: str, value: str) -> None:
    if not str(value).strip():
        raise PersistenceValidationError(f"{name} is required")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PersistenceValidationError(f"{name} must be a timezone-aware datetime")


def _range(name: str, value: float) -> None:
    if value < 0.0 or value > 1.0:
        raise PersistenceValidationError(f"{name} must be between 0.0 and 1.0")


def _metadata(metadata: dict[str, Any]) -> dict[str, MetadataValue]:
    normalized: dict[str, MetadataValue] = {}
    for key, value in metadata.items():
        if not isinstance(value, str | int | float | bool) and value is not None:
            raise PersistenceValidationError("metadata values must be JSON scalar values")
        normalized[str(key)] = value
    return normalized


def _identity_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    identities = tuple(preserve_identity(value) for value in values)
    if not identities:
        raise PersistenceValidationError(f"{name} must include at least one identity")
    return identities


def _freeze_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _freeze_payload(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, list | tuple):
        return tuple(_freeze_payload(item) for item in value)
    normalize(value)
    return value
