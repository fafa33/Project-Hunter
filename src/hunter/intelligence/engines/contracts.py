from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from hunter.execution.identity import identity
from hunter.intelligence.evidence import Evidence
from hunter.intelligence.intelligence import Intelligence
from hunter.plugins.contracts import PipelineContext

FINDING_SCHEMA_VERSION = "intelligence-finding-v1"


@dataclass(frozen=True)
class EngineMetadata:
    id: str
    name: str
    category: str
    version: str
    priority: int
    required_inputs: tuple[str, ...]
    produced_outputs: tuple[str, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceBundle:
    candidate_id: str
    evidence: tuple[Evidence, ...]
    evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            msg = "evidence bundle requires candidate_id"
            raise ValueError(msg)
        evidence = tuple(sorted(self.evidence, key=lambda item: item.id))
        evidence_ids = tuple(sorted({*(item.id for item in evidence), *self.evidence_ids}))
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(
            self,
            "missing_evidence",
            tuple(sorted({str(item) for item in self.missing_evidence if str(item)})),
        )
        lineage = tuple(sorted({str(item) for item in self.lineage if str(item)})) or evidence_ids
        object.__setattr__(self, "lineage", lineage)


@dataclass(frozen=True)
class EngineContext:
    as_of: datetime
    evaluated_at: datetime
    replay_fingerprint: str
    engine_configuration_fingerprint: str
    engine_version: str
    execution_metadata: MappingProxyType[str, object] | dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.evaluated_at.tzinfo is None:
            msg = "engine context timestamps must be timezone-aware"
            raise ValueError(msg)
        required = (
            self.replay_fingerprint,
            self.engine_configuration_fingerprint,
            self.engine_version,
        )
        if any(not value.strip() for value in required):
            msg = "engine context requires replay, configuration, and engine version fingerprints"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "execution_metadata",
            MappingProxyType({str(key): value for key, value in self.execution_metadata.items()}),
        )


@dataclass(frozen=True)
class Finding:
    finding_id: str
    candidate_id: str
    engine_id: str
    engine_version: str
    finding_type: str
    explanation: str
    supporting_evidence_ids: tuple[str, ...]
    evidence_lineage: tuple[str, ...]
    deterministic_confidence: float
    confidence_basis: str
    evaluated_at: datetime
    as_of: datetime
    analysis_trace_version: str
    missing_evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    schema_version: str = FINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = (
            self.finding_id,
            self.candidate_id,
            self.engine_id,
            self.engine_version,
            self.finding_type,
            self.explanation,
            self.confidence_basis,
            self.analysis_trace_version,
            self.schema_version,
        )
        if any(not value.strip() for value in required):
            msg = "finding requires identity, engine, explanation, confidence basis, and schema fields"
            raise ValueError(msg)
        if self.evaluated_at.tzinfo is None or self.as_of.tzinfo is None:
            msg = "finding timestamps must be timezone-aware"
            raise ValueError(msg)
        if not 0.0 <= self.deterministic_confidence <= 1.0:
            msg = "finding confidence must be between 0.0 and 1.0"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "supporting_evidence_ids",
            tuple(sorted({str(item) for item in self.supporting_evidence_ids if str(item)})),
        )
        object.__setattr__(
            self,
            "evidence_lineage",
            tuple(sorted({str(item) for item in self.evidence_lineage if str(item)})),
        )
        object.__setattr__(
            self,
            "missing_evidence",
            tuple(sorted({str(item) for item in self.missing_evidence if str(item)})),
        )
        object.__setattr__(self, "conflicts", tuple(sorted({str(item) for item in self.conflicts if str(item)})))


@dataclass(frozen=True)
class FindingBatch:
    engine_id: str
    engine_version: str
    candidate_id: str
    as_of: datetime
    evaluated_at: datetime
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if not self.engine_id.strip() or not self.engine_version.strip() or not self.candidate_id.strip():
            msg = "finding batch requires engine_id, engine_version, and candidate_id"
            raise ValueError(msg)
        if self.as_of.tzinfo is None or self.evaluated_at.tzinfo is None:
            msg = "finding batch timestamps must be timezone-aware"
            raise ValueError(msg)
        findings = tuple(sorted(self.findings, key=lambda item: item.finding_id))
        for finding in findings:
            if finding.engine_id != self.engine_id or finding.engine_version != self.engine_version:
                msg = "finding batch contains a finding from a different engine"
                raise ValueError(msg)
            if finding.candidate_id != self.candidate_id:
                msg = "finding batch contains a finding for a different candidate"
                raise ValueError(msg)
            if finding.as_of != self.as_of or finding.evaluated_at != self.evaluated_at:
                msg = "finding batch timestamps must match every finding"
                raise ValueError(msg)
        object.__setattr__(self, "findings", findings)


@dataclass(frozen=True)
class EngineDefinition:
    metadata: EngineMetadata
    evidence_contracts: tuple[str, ...]
    supported_evidence_types: tuple[str, ...]
    analysis_stages: tuple[str, ...]
    finding_types: tuple[str, ...]
    output_schema_version: str
    analysis_trace_version: str
    deterministic_execution_contract: str

    def __post_init__(self) -> None:
        required = (
            *self.evidence_contracts,
            *self.supported_evidence_types,
            *self.analysis_stages,
            *self.finding_types,
            self.output_schema_version,
            self.analysis_trace_version,
            self.deterministic_execution_contract,
        )
        if any(not str(value).strip() for value in required):
            msg = "engine definition requires complete metadata, evidence, finding, and deterministic contracts"
            raise ValueError(msg)
        object.__setattr__(self, "evidence_contracts", tuple(sorted(set(self.evidence_contracts))))
        object.__setattr__(self, "supported_evidence_types", tuple(sorted(set(self.supported_evidence_types))))
        object.__setattr__(self, "analysis_stages", tuple(self.analysis_stages))
        object.__setattr__(self, "finding_types", tuple(sorted(set(self.finding_types))))


@runtime_checkable
class FoundationalIntelligenceEngine(Protocol):
    @property
    def definition(self) -> EngineDefinition:
        raise NotImplementedError

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        raise NotImplementedError


@runtime_checkable
class IntelligenceFindingRepository(Protocol):
    def load_engine_evidence(self, candidate_id: str) -> tuple[Evidence, ...]:
        raise NotImplementedError

    def persist_authorized_findings(self, batch: FindingBatch) -> None:
        raise NotImplementedError


def finding_identity(
    *,
    candidate_id: str,
    engine_id: str,
    engine_version: str,
    finding_type: str,
    explanation: str,
    supporting_evidence_ids: tuple[str, ...],
    evidence_lineage: tuple[str, ...],
    deterministic_confidence: float,
    confidence_basis: str,
    evaluated_at: datetime,
    as_of: datetime,
    analysis_trace_version: str,
    missing_evidence: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    schema_version: str = FINDING_SCHEMA_VERSION,
) -> str:
    return identity(
        "intelligence-finding",
        {
            "candidate_id": candidate_id,
            "engine_id": engine_id,
            "engine_version": engine_version,
            "finding_type": finding_type,
            "explanation": explanation,
            "supporting_evidence_ids": tuple(sorted(supporting_evidence_ids)),
            "evidence_lineage": tuple(sorted(evidence_lineage)),
            "deterministic_confidence": deterministic_confidence,
            "confidence_basis": confidence_basis,
            "evaluated_at": evaluated_at,
            "as_of": as_of,
            "analysis_trace_version": analysis_trace_version,
            "missing_evidence": tuple(sorted(missing_evidence)),
            "conflicts": tuple(sorted(conflicts)),
            "schema_version": schema_version,
        },
    )


@runtime_checkable
class IntelligenceEngine(Protocol):
    @property
    def metadata(self) -> EngineMetadata:
        raise NotImplementedError

    @property
    def id(self) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def category(self) -> str:
        raise NotImplementedError

    @property
    def version(self) -> str:
        raise NotImplementedError

    @property
    def priority(self) -> int:
        raise NotImplementedError

    @property
    def required_inputs(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def produced_outputs(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def capabilities(self) -> tuple[str, ...]:
        raise NotImplementedError

    def validate(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def collect(self, context: PipelineContext) -> Any:
        raise NotImplementedError

    def analyze(self, context: PipelineContext, collected: Any) -> Any:
        raise NotImplementedError

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        raise NotImplementedError

    def health_check(self) -> bool:
        raise NotImplementedError
