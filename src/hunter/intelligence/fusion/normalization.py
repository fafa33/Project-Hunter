from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence.fusion.exceptions import FusionInputError
from hunter.intelligence.fusion.models import FusionInput, FusionTargetType
from hunter.intelligence.intelligence import Intelligence
from hunter.persistence.records import IntelligenceRecord


def normalize_fusion_inputs(items: Iterable[Intelligence | IntelligenceRecord]) -> tuple[FusionInput, ...]:
    inputs = tuple(_normalize_item(item) for item in items)
    return tuple(sorted(inputs, key=lambda item: (item.engine_id, item.intelligence_id)))


def _normalize_item(item: Intelligence | IntelligenceRecord) -> FusionInput:
    if isinstance(item, Intelligence):
        return normalize_intelligence(item)
    if isinstance(item, IntelligenceRecord):
        return normalize_intelligence_record(item)
    msg = f"Unsupported fusion input type: {item.__class__.__name__}"
    raise FusionInputError(msg)


def normalize_intelligence(intelligence: Intelligence) -> FusionInput:
    metadata = intelligence.metadata.as_dict()
    return FusionInput(
        intelligence_id=intelligence.id,
        engine_id=intelligence.engine,
        engine_version=_optional_text(metadata.get("engine_version")),
        plugin_id=_optional_text(metadata.get("plugin_id")),
        plugin_version=_optional_text(metadata.get("plugin_version")),
        run_id=_optional_text(metadata.get("pipeline_run_id")),
        project=intelligence.project,
        generated_at=_aware(intelligence.generated_at),
        effective_at=_aware(intelligence.generated_at),
        confidence_score=intelligence.confidence.score,
        evidence_ids=tuple(evidence.id for evidence in intelligence.evidence),
        evidence_references=tuple(evidence.reference for evidence in intelligence.evidence),
        evidence_lineage_keys=tuple(_lineage_key(evidence.metadata.as_dict()) for evidence in intelligence.evidence),
        evidence_reliabilities=tuple(evidence.reliability for evidence in intelligence.evidence),
        evidence_freshness=tuple(evidence.freshness for evidence in intelligence.evidence),
        signal_ids=tuple(signal.id for signal in intelligence.signals),
        signal_categories=tuple(signal.category for signal in intelligence.signals),
        signal_strengths=tuple(signal.strength for signal in intelligence.signals),
        signal_confidences=tuple(signal.confidence for signal in intelligence.signals),
        signal_severities=tuple(signal.severity for signal in intelligence.signals),
        observation_ids=tuple(observation.id for observation in intelligence.observations),
        observation_descriptions=tuple(observation.description for observation in intelligence.observations),
        insight_ids=tuple(insight.id for insight in intelligence.insights),
        insight_titles=tuple(insight.title for insight in intelligence.insights),
        insight_explanations=tuple(insight.explanation for insight in intelligence.insights),
        target_refs=_target_refs(intelligence.project, metadata),
        metadata=metadata,
    )


def normalize_intelligence_record(record: IntelligenceRecord) -> FusionInput:
    metadata = dict(record.metadata)
    return FusionInput(
        intelligence_id=record.id,
        engine_id=record.engine_id,
        engine_version=record.engine_version or _optional_text(metadata.get("engine_version")),
        plugin_id=record.plugin_id or _optional_text(metadata.get("plugin_id")),
        plugin_version=record.plugin_version or _optional_text(metadata.get("plugin_version")),
        run_id=record.pipeline_run_id,
        project=record.project,
        generated_at=record.generated_at,
        effective_at=record.effective_at,
        confidence_score=float(record.confidence.get("score", 0.0)),
        evidence_ids=record.evidence_ids,
        evidence_references=record.evidence_references,
        evidence_lineage_keys=record.evidence_lineage_keys,
        evidence_reliabilities=record.evidence_reliabilities,
        evidence_freshness=record.evidence_freshness,
        signal_ids=record.signal_ids,
        signal_categories=record.signal_categories or tuple(str(item) for item in metadata.get("signal_categories", "").split(",") if item),
        signal_strengths=record.signal_strengths,
        signal_confidences=record.signal_confidences,
        signal_severities=record.signal_severities,
        observation_ids=record.observation_ids,
        observation_descriptions=record.observation_descriptions,
        insight_ids=record.insight_ids,
        insight_titles=record.insight_titles,
        insight_explanations=record.insight_explanations,
        target_refs=record.target_refs or _target_refs(record.project, metadata),
        metadata=metadata,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "Fusion inputs require timezone-aware timestamps"
        raise FusionInputError(msg)
    return value.astimezone(UTC)


def _lineage_key(metadata: dict[str, str | int | float | bool | None]) -> str:
    value = metadata.get("lineage_key") or metadata.get("evidence_lineage_key")
    return str(value) if value is not None else ""


def _target_refs(project: str, metadata: dict[str, str | int | float | bool | None]) -> tuple[tuple[FusionTargetType, str], ...]:
    refs: set[tuple[FusionTargetType, str]] = {("project", project)}
    for target_type in ("asset", "protocol", "chain", "sector", "narrative", "ecosystem"):
        value = metadata.get(f"{target_type}_id")
        if value is not None and str(value):
            refs.add((target_type, str(value)))  # type: ignore[arg-type]
    target_type = metadata.get("target_type")
    target_id = metadata.get("target_id")
    supported = {"project", "asset", "protocol", "chain", "sector", "narrative", "ecosystem"}
    if target_type in supported and target_id is not None and str(target_id):
        refs.add((str(target_type), str(target_id)))  # type: ignore[arg-type]
    return tuple(sorted(refs))
