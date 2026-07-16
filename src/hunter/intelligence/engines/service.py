from __future__ import annotations

from datetime import datetime
from types import MappingProxyType

from hunter.execution.identity import fingerprint
from hunter.intelligence.engines.contracts import (
    EngineContext,
    EvidenceBundle,
    Finding,
    FindingBatch,
    FoundationalIntelligenceEngine,
    IntelligenceFindingRepository,
    finding_identity,
)
from hunter.intelligence.engines.exceptions import IntelligenceEngineValidationError


class IntelligenceEngineService:
    def __init__(self, repository: IntelligenceFindingRepository) -> None:
        self._repository = repository

    def execute(
        self,
        engine: FoundationalIntelligenceEngine,
        *,
        candidate_id: str,
        as_of: datetime,
        evaluated_at: datetime,
        engine_configuration_fingerprint: str,
        execution_metadata: MappingProxyType[str, object] | dict[str, object] | None = None,
    ) -> FindingBatch:
        self._require_timestamp(as_of, "as_of")
        self._require_timestamp(evaluated_at, "evaluated_at")
        if not engine_configuration_fingerprint.strip():
            msg = "engine configuration fingerprint is required"
            raise IntelligenceEngineValidationError(msg)
        evidence = tuple(
            item for item in self._repository.load_engine_evidence(candidate_id) if item.collected_at <= as_of
        )
        bundle = EvidenceBundle(
            candidate_id=candidate_id,
            evidence=evidence,
            missing_evidence=engine.definition.evidence_contracts,
            lineage=tuple(item.reference for item in evidence if item.reference),
        )
        context = EngineContext(
            as_of=as_of,
            evaluated_at=evaluated_at,
            replay_fingerprint=self._replay_fingerprint(
                engine_id=engine.definition.metadata.id,
                engine_version=engine.definition.metadata.version,
                candidate_id=candidate_id,
                as_of=as_of,
                evidence_ids=bundle.evidence_ids,
                engine_configuration_fingerprint=engine_configuration_fingerprint,
                analysis_trace_version=engine.definition.analysis_trace_version,
            ),
            engine_configuration_fingerprint=engine_configuration_fingerprint,
            engine_version=engine.definition.metadata.version,
            execution_metadata=execution_metadata or {},
        )
        batch = engine.analyze(bundle, context)
        self._validate_batch(engine=engine, bundle=bundle, context=context, batch=batch)
        self._repository.persist_authorized_findings(batch)
        return batch

    def _validate_batch(
        self,
        *,
        engine: FoundationalIntelligenceEngine,
        bundle: EvidenceBundle,
        context: EngineContext,
        batch: FindingBatch,
    ) -> None:
        metadata = engine.definition.metadata
        if batch.engine_id != metadata.id or batch.engine_version != metadata.version:
            msg = "finding batch engine identity does not match engine definition"
            raise IntelligenceEngineValidationError(msg)
        if batch.candidate_id != bundle.candidate_id:
            msg = "finding batch candidate does not match service-loaded evidence"
            raise IntelligenceEngineValidationError(msg)
        if batch.as_of != context.as_of or batch.evaluated_at != context.evaluated_at:
            msg = "finding batch timestamps must match service-owned engine context"
            raise IntelligenceEngineValidationError(msg)
        for finding in batch.findings:
            self._validate_finding(
                finding,
                engine=engine,
                bundle=bundle,
                context=context,
            )

    def _validate_finding(
        self,
        finding: Finding,
        *,
        engine: FoundationalIntelligenceEngine,
        bundle: EvidenceBundle,
        context: EngineContext,
    ) -> None:
        definition = engine.definition
        if finding.finding_type not in definition.finding_types:
            msg = f"unsupported finding type: {finding.finding_type}"
            raise IntelligenceEngineValidationError(msg)
        if finding.analysis_trace_version != definition.analysis_trace_version:
            msg = "finding analysis trace version must match engine definition"
            raise IntelligenceEngineValidationError(msg)
        if finding.schema_version != definition.output_schema_version:
            msg = "finding schema version must match engine definition"
            raise IntelligenceEngineValidationError(msg)
        if finding.evaluated_at != context.evaluated_at or finding.as_of != context.as_of:
            msg = "finding timestamps must match service-owned engine context"
            raise IntelligenceEngineValidationError(msg)
        evidence_ids = set(bundle.evidence_ids)
        if not set(finding.supporting_evidence_ids).issubset(evidence_ids):
            msg = "finding supporting evidence must come from the service-loaded evidence bundle"
            raise IntelligenceEngineValidationError(msg)
        lineage = set(bundle.lineage)
        if not set(finding.evidence_lineage).issubset(lineage):
            msg = "finding evidence lineage must come from the service-loaded evidence bundle"
            raise IntelligenceEngineValidationError(msg)
        expected_id = finding_identity(
            candidate_id=finding.candidate_id,
            engine_id=finding.engine_id,
            engine_version=finding.engine_version,
            finding_type=finding.finding_type,
            explanation=finding.explanation,
            supporting_evidence_ids=finding.supporting_evidence_ids,
            evidence_lineage=finding.evidence_lineage,
            deterministic_confidence=finding.deterministic_confidence,
            confidence_basis=finding.confidence_basis,
            evaluated_at=finding.evaluated_at,
            as_of=finding.as_of,
            analysis_trace_version=finding.analysis_trace_version,
            missing_evidence=finding.missing_evidence,
            conflicts=finding.conflicts,
            schema_version=finding.schema_version,
        )
        if finding.finding_id != expected_id:
            msg = "finding identity does not match deterministic finding payload"
            raise IntelligenceEngineValidationError(msg)

    def _replay_fingerprint(
        self,
        *,
        engine_id: str,
        engine_version: str,
        candidate_id: str,
        as_of: datetime,
        evidence_ids: tuple[str, ...],
        engine_configuration_fingerprint: str,
        analysis_trace_version: str,
    ) -> str:
        return fingerprint(
            "intelligence-engine-replay",
            {
                "engine_id": engine_id,
                "engine_version": engine_version,
                "candidate_id": candidate_id,
                "as_of": as_of,
                "evidence_ids": evidence_ids,
                "engine_configuration_fingerprint": engine_configuration_fingerprint,
                "analysis_trace_version": analysis_trace_version,
            },
        )

    def _require_timestamp(self, value: datetime, name: str) -> None:
        if value.tzinfo is None:
            msg = f"{name} must be timezone-aware"
            raise IntelligenceEngineValidationError(msg)
