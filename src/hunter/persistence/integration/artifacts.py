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
            engine_version=_text_or_none(
                artifact_metadata.get("engine_version") or intelligence.metadata.as_dict().get("engine_version")
            ),
            plugin_id=_text_or_none(
                artifact_metadata.get("plugin_id") or intelligence.metadata.as_dict().get("plugin_id")
            ),
            plugin_version=_text_or_none(
                artifact_metadata.get("plugin_version") or intelligence.metadata.as_dict().get("plugin_version")
            ),
            target_refs=_target_refs(intelligence),
            evidence_references=tuple(evidence.reference for evidence in intelligence.evidence),
            evidence_lineage_keys=tuple(
                _lineage_key(evidence.metadata.as_dict()) for evidence in intelligence.evidence
            ),
            evidence_reliabilities=tuple(evidence.reliability for evidence in intelligence.evidence),
            evidence_freshness=tuple(evidence.freshness for evidence in intelligence.evidence),
            signal_categories=tuple(signal.category for signal in intelligence.signals),
            signal_strengths=tuple(signal.strength for signal in intelligence.signals),
            signal_confidences=tuple(signal.confidence for signal in intelligence.signals),
            signal_severities=tuple(signal.severity for signal in intelligence.signals),
            observation_descriptions=tuple(observation.description for observation in intelligence.observations),
            insight_titles=tuple(insight.title for insight in intelligence.insights),
            insight_explanations=tuple(insight.explanation for insight in intelligence.insights),
        )
    )
    return tuple(records)


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _lineage_key(metadata: dict[str, str | int | float | bool | None]) -> str:
    value = metadata.get("lineage_key") or metadata.get("evidence_lineage_key")
    return str(value) if value is not None else ""


def _target_refs(intelligence: Intelligence) -> tuple[tuple[str, str], ...]:
    metadata = intelligence.metadata.as_dict()
    refs: set[tuple[str, str]] = {("project", intelligence.project)}
    for target_type in ("asset", "protocol", "chain", "sector", "narrative", "ecosystem"):
        value = metadata.get(f"{target_type}_id")
        if value is not None and str(value):
            refs.add((target_type, str(value)))
    target_type = metadata.get("target_type")
    target_id = metadata.get("target_id")
    if target_type is not None and target_id is not None:
        refs.add((str(target_type), str(target_id)))
    return tuple(sorted(refs))
