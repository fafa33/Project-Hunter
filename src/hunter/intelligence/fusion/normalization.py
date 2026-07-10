from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence.fusion.exceptions import FusionInputError
from hunter.intelligence.fusion.models import FusionInput
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
        metadata=metadata,
    )


def normalize_intelligence_record(record: IntelligenceRecord) -> FusionInput:
    metadata = dict(record.metadata)
    return FusionInput(
        intelligence_id=record.id,
        engine_id=record.engine_id,
        engine_version=_optional_text(metadata.get("engine_version")),
        plugin_id=_optional_text(metadata.get("plugin_id")),
        plugin_version=_optional_text(metadata.get("plugin_version")),
        run_id=record.pipeline_run_id,
        project=record.project,
        generated_at=record.generated_at,
        effective_at=record.effective_at,
        confidence_score=float(record.confidence.get("score", 0.0)),
        evidence_ids=record.evidence_ids,
        evidence_references=(),
        signal_ids=record.signal_ids,
        signal_categories=tuple(str(item) for item in metadata.get("signal_categories", "").split(",") if item),
        signal_strengths=(),
        signal_confidences=(),
        signal_severities=(),
        observation_ids=record.observation_ids,
        observation_descriptions=(),
        insight_ids=record.insight_ids,
        insight_titles=(),
        insight_explanations=(),
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
