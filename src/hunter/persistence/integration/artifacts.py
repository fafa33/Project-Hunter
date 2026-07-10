from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from hunter.intelligence import Intelligence
from hunter.intelligence.validator import IntelligenceValidator
from hunter.persistence.records import (
    EvidenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    SignalRecord,
)


def records_for_intelligence(
    intelligence: Intelligence,
    *,
    pipeline_run_id: str,
    created_at: datetime,
    effective_at: datetime,
    declaration_metadata: dict[str, str | int | float | bool | None] | None = None,
) -> tuple[EvidenceRecord | SignalRecord | ObservationRecord | InsightRecord | IntelligenceRecord, ...]:
    IntelligenceValidator().validate(intelligence)
    artifact_metadata = dict(declaration_metadata or {})
    records: list[EvidenceRecord | SignalRecord | ObservationRecord | InsightRecord | IntelligenceRecord] = []
    for evidence in intelligence.evidence:
        metadata = {**artifact_metadata, **evidence.metadata.as_dict()}
        metadata["intelligence_id"] = intelligence.id
        records.append(
            EvidenceRecord(
                id=evidence.id,
                created_at=created_at,
                effective_at=effective_at,
                pipeline_run_id=pipeline_run_id,
                source=evidence.source,
                reference=evidence.reference,
                collected_at=evidence.collected_at,
                reliability=evidence.reliability,
                freshness=evidence.freshness,
                raw_data=evidence.raw_data,
                metadata=metadata,
            )
        )
    for signal in intelligence.signals:
        records.append(
            SignalRecord(
                id=signal.id,
                created_at=created_at,
                effective_at=effective_at,
                pipeline_run_id=pipeline_run_id,
                intelligence_id=intelligence.id,
                engine_id=intelligence.engine,
                project=intelligence.project,
                timestamp=signal.timestamp,
                category=signal.category,
                strength=signal.strength,
                confidence=signal.confidence,
                severity=signal.severity,
                metadata={**artifact_metadata, **signal.metadata.as_dict()},
            )
        )
    for observation in intelligence.observations:
        records.append(
            ObservationRecord(
                id=observation.id,
                created_at=created_at,
                effective_at=effective_at,
                pipeline_run_id=pipeline_run_id,
                intelligence_id=intelligence.id,
                engine_id=observation.engine,
                project=observation.project,
                description=observation.description,
                evidence_ids=tuple(evidence.id for evidence in observation.evidence),
                importance=observation.importance,
                metadata={**artifact_metadata, **observation.metadata.as_dict()},
            )
        )
    for insight in intelligence.insights:
        records.append(
            InsightRecord(
                id=insight.id,
                created_at=created_at,
                effective_at=effective_at,
                pipeline_run_id=pipeline_run_id,
                intelligence_id=intelligence.id,
                title=insight.title,
                explanation=insight.explanation,
                observation_ids=tuple(observation.id for observation in insight.supporting_observations),
                confidence=insight.confidence,
                priority=insight.priority,
                metadata=artifact_metadata,
            )
        )
    records.append(
        IntelligenceRecord(
            id=intelligence.id,
            created_at=created_at,
            effective_at=effective_at,
            pipeline_run_id=pipeline_run_id,
            project=intelligence.project,
            engine_id=intelligence.engine,
            generated_at=intelligence.generated_at,
            signal_ids=tuple(signal.id for signal in intelligence.signals),
            evidence_ids=tuple(evidence.id for evidence in intelligence.evidence),
            observation_ids=tuple(observation.id for observation in intelligence.observations),
            insight_ids=tuple(insight.id for insight in intelligence.insights),
            confidence=asdict(intelligence.confidence),
            metadata={**artifact_metadata, **intelligence.metadata.as_dict()},
        )
    )
    return tuple(records)
