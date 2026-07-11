from __future__ import annotations

from dataclasses import asdict

from hunter.execution.identity import fingerprint, identity
from hunter.patterns.configuration import HistoricalPatternLibrary, PatternConfig
from hunter.patterns.metrics import (
    average,
    confidence_score,
    evidence_quality,
    missing_evidence,
    numeric,
    snapshot_value,
)
from hunter.patterns.models import (
    HistoricalProjectPattern,
    PatternInputSet,
    PatternMatch,
    PatternMatchingAssessment,
    PatternSimilarityBreakdown,
)

IDENTITY_SCHEMA_VERSION = "pattern-matching-identity-v1"


class PatternMatchingEngine:
    def __init__(self, config: PatternConfig | None = None, library: HistoricalPatternLibrary | None = None) -> None:
        self.config = config or PatternConfig()
        self.library = library

    def assess(
        self,
        inputs: PatternInputSet,
        *,
        config: PatternConfig | None = None,
        library: HistoricalPatternLibrary | None = None,
    ) -> PatternMatchingAssessment:
        active_config = config or self.config
        active_library = library or self.library
        if active_library is None:
            msg = "Pattern Matching requires a historical pattern library"
            raise ValueError(msg)
        current = _current_dimensions(inputs, active_config)
        current_context = _current_context(inputs, active_config)
        missing = missing_evidence(inputs.fused_intelligence, inputs.opportunity_timing)
        confidence = _historical_confidence(inputs, current, missing, active_config)
        matches = tuple(
            sorted(
                (
                    _match(project, current, current_context, confidence, active_config)
                    for project in active_library.projects
                ),
                key=lambda item: (-item.similarity, item.project_id),
            )
        )
        top_matches = matches[: active_config.top_match_limit]
        source_ids = _source_ids(inputs)
        configuration_fingerprint = fingerprint("pattern-matching-configuration", asdict(active_config))
        library_fingerprint = fingerprint(
            "pattern-historical-library",
            tuple(
                {
                    "project_id": project.project_id,
                    "outcome": project.outcome,
                    "dimensions": project.dimensions.as_dict(),
                    "context_dimensions": project.context_dimensions.as_dict(),
                    "warnings": project.warning_patterns,
                }
                for project in active_library.projects
            ),
        )
        assessment_id = identity(
            "pattern-matching-assessment",
            {
                "target_id": inputs.target_id,
                "effective_at": inputs.effective_at,
                "source_record_ids": source_ids,
                "configuration_fingerprint": configuration_fingerprint,
                "library_fingerprint": library_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )
        if len(source_ids) < active_config.minimum_source_records:
            top_matches = ()
        return PatternMatchingAssessment(
            assessment_id=assessment_id,
            target_id=inputs.target_id,
            effective_at=inputs.effective_at,
            source_record_ids=source_ids,
            top_matches=top_matches,
            positive_matches=tuple(match for match in top_matches if not match.is_negative),
            negative_matches=tuple(match for match in top_matches if match.is_negative),
            historical_similarity=top_matches[0].historical_similarity if top_matches else 0.0,
            context_similarity=top_matches[0].context_similarity if top_matches else 0.0,
            overall_similarity=top_matches[0].similarity if top_matches else 0.0,
            historical_confidence=confidence if top_matches else 0.0,
            missing_evidence=missing,
            metadata={
                "configuration_fingerprint": configuration_fingerprint,
                "library_fingerprint": library_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "source_record_count": len(source_ids),
            },
        )


def _current_dimensions(inputs: PatternInputSet, config: PatternConfig) -> dict[str, float]:
    values: dict[str, list[float]] = {name: [] for name, _ in config.dimension_weights}
    engine_map = dict(config.engine_dimension_map)
    for record in inputs.intelligence:
        dimension = _dimension_for_engine(record.engine_id, engine_map)
        if dimension in values:
            values[dimension].append(confidence_score(record.confidence))
    for record in inputs.fused_intelligence:
        values["confidence"].append(confidence_score(record.confidence))
        for contribution in record.contributions:
            dimension = _dimension_for_engine(str(contribution.get("engine_id", "")), engine_map)
            if dimension in values:
                values[dimension].append(numeric(contribution.get("confidence")))
    for record in inputs.opportunity_timing:
        values["opportunity_timing"].append(record.timing_score / 100.0)
        values["confidence"].append(confidence_score(record.confidence))
        values["risk"].append(1.0 - numeric(record.risk_state.get("score")))
    values["evidence_quality"].append(evidence_quality(inputs.evidence, inputs.fused_intelligence))
    values["backtesting_reliability"].append(snapshot_value(inputs.snapshots, "backtesting_reliability"))
    values["probability"].append(snapshot_value(inputs.snapshots, "probability_score"))
    values["fundamentals"].append(snapshot_value(inputs.snapshots, "fundamentals"))
    values["valuation"].append(snapshot_value(inputs.snapshots, "valuation"))
    values["revenue"].append(snapshot_value(inputs.snapshots, "revenue"))
    values["tokenomics"].append(snapshot_value(inputs.snapshots, "tokenomics"))
    return {name: average(tuple(score for score in scores if score > 0.0)) for name, scores in values.items()}


def _match(
    project: HistoricalProjectPattern,
    current: dict[str, float],
    current_context: dict[str, float],
    confidence: float,
    config: PatternConfig,
) -> PatternMatch:
    weights = dict(config.dimension_weights)
    similarities = {
        dimension: round(1.0 - abs(current.get(dimension, 0.0) - project.dimensions.get(dimension, 0.0)), 4)
        for dimension in weights
    }
    context_weights = dict(config.context_weights)
    historical = round(sum(similarities[name] * weight for name, weight in weights.items()), 4)
    context_similarities = {
        dimension: round(
            1.0
            - abs(
                current_context.get(dimension, 0.0)
                - project.context_dimensions.get(dimension, project.dimensions.get(_context_fallback(dimension), 0.0))
            ),
            4,
        )
        for dimension in context_weights
    }
    context = round(sum(context_similarities[name] * weight for name, weight in context_weights.items()), 4)
    total_weight = config.historical_weight + config.context_weight
    overall = round(((historical * config.historical_weight) + (context * config.context_weight)) / total_weight, 4)
    strengths = tuple(
        sorted(
            {
                *(name for name, score in similarities.items() if score >= 0.8),
                *(name for name, score in context_similarities.items() if score >= 0.8),
            }
        )
    )
    weaknesses = tuple(
        sorted(
            {
                *(name for name, score in similarities.items() if score < 0.5),
                *(name for name, score in context_similarities.items() if score < 0.5),
            }
        )
    )
    return PatternMatch(
        project_id=project.project_id,
        project_name=project.name,
        outcome=project.outcome,
        similarity=overall,
        similarity_percent=overall * 100.0,
        historical_similarity=historical,
        context_similarity=context,
        label=_label(overall, confidence, config),
        breakdown=PatternSimilarityBreakdown(
            dimensions=similarities,
            context_dimensions=context_similarities,
            overall_similarity=overall,
            historical_similarity=historical,
            context_similarity=context,
            confidence=confidence,
        ),
        strengths=strengths,
        weaknesses=weaknesses,
        matching_factors=strengths,
        differing_factors=weaknesses,
        warning_patterns=project.warning_patterns if project.is_negative else (),
        confidence=confidence,
    )


def _current_context(inputs: PatternInputSet, config: PatternConfig) -> dict[str, float]:
    values: dict[str, list[float]] = {name: [] for name, _ in config.context_weights}
    dimensions = _current_dimensions(inputs, config)
    values["current_macro_conditions"].append(dimensions.get("macro_alignment", 0.0))
    values["current_technology_trends"].append(dimensions.get("developer_activity", 0.0))
    values["current_capital_rotation"].append(dimensions.get("valuation", 0.0))
    values["current_institutional_adoption"].append(dimensions.get("validation_health", 0.0))
    values["current_regulatory_environment"].append(dimensions.get("risk", 0.0))
    values["current_future_demand"].append(dimensions.get("future_demand", 0.0))
    values["current_sector_strength"].append(dimensions.get("fundamentals", 0.0))
    for snapshot in inputs.snapshots:
        for key in values:
            if key in snapshot.payload:
                values[key].append(numeric(snapshot.payload[key]))
    return {name: average(tuple(score for score in scores if score > 0.0)) for name, scores in values.items()}


def _context_fallback(dimension: str) -> str:
    return {
        "current_macro_conditions": "macro_alignment",
        "current_technology_trends": "developer_activity",
        "current_capital_rotation": "valuation",
        "current_institutional_adoption": "validation_health",
        "current_regulatory_environment": "risk",
        "current_future_demand": "future_demand",
        "current_sector_strength": "fundamentals",
    }.get(dimension, "fundamentals")


def _historical_confidence(
    inputs: PatternInputSet, current: dict[str, float], missing: tuple[str, ...], config: PatternConfig
) -> float:
    coverage = sum(1 for value in current.values() if value > 0.0) / max(1, len(dict(config.dimension_weights)))
    quality = evidence_quality(inputs.evidence, inputs.fused_intelligence)
    missing_penalty = len(missing) * config.missing_evidence_penalty / max(1, len(dict(config.dimension_weights)))
    return round(max(0.0, min(1.0, average((coverage, quality)) - missing_penalty)), 4)


def _label(score: float, confidence: float, config: PatternConfig) -> str:
    if confidence <= 0.05:
        return "Insufficient Evidence"
    label = "Insufficient Evidence"
    for candidate, threshold in config.label_thresholds:
        if score >= threshold:
            label = candidate
    return label


def _dimension_for_engine(engine_id: str, engine_map: dict[str, str]) -> str:
    normalized = engine_id.lower()
    for token, dimension in engine_map.items():
        if token in normalized:
            return dimension
    return "fundamentals"


def _source_ids(inputs: PatternInputSet) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *(record.id for record in inputs.intelligence),
                *(record.id for record in inputs.fused_intelligence),
                *(record.id for record in inputs.opportunity_timing),
                *(record.id for record in inputs.evidence),
                *(record.id for record in inputs.snapshots),
            }
        )
    )
