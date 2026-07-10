from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime

import pytest

from hunter.intelligence import (
    Confidence,
    Evidence,
    Insight,
    Intelligence,
    IntelligenceAggregator,
    IntelligenceRegistry,
    IntelligenceValidator,
    Observation,
    Signal,
)
from hunter.intelligence.exceptions import (
    IntelligenceAggregationError,
    IntelligenceRegistryError,
    IntelligenceValidationError,
)
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext, PluginMetadata


def now() -> datetime:
    return datetime.now(UTC)


def sample_evidence(evidence_id: str = "evidence-1") -> Evidence:
    return Evidence(
        id=evidence_id,
        source="public-source",
        collected_at=now(),
        reliability=0.9,
        freshness=0.8,
        reference="https://example.test/source",
        raw_data={"value": 1},
    )


def sample_signal(signal_id: str = "signal-1", category: str = "macro") -> Signal:
    return Signal(
        id=signal_id,
        source="macro-engine",
        timestamp=now(),
        category=category,
        strength=0.7,
        confidence=0.8,
        severity=0.2,
        metadata={"region": "global"},
    )


def sample_observation(observation_id: str = "observation-1") -> Observation:
    return Observation(
        id=observation_id,
        engine="macro-engine",
        project="bitcoin",
        description="Observed macro signal.",
        evidence=(sample_evidence(),),
        importance=0.7,
    )


def sample_insight(insight_id: str = "insight-1") -> Insight:
    return Insight(
        id=insight_id,
        title="Macro tailwind",
        explanation="Evidence indicates a macro tailwind.",
        supporting_observations=(sample_observation(),),
        confidence=0.8,
        priority=0.6,
    )


def sample_intelligence(
    intelligence_id: str = "intelligence-1",
    *,
    engine: str = "macro-engine",
    project: str = "bitcoin",
    category: str = "macro",
) -> Intelligence:
    evidence = sample_evidence()
    observation = Observation(
        id="observation-1",
        engine=engine,
        project=project,
        description="Observed signal.",
        evidence=(evidence,),
        importance=0.7,
    )
    insight = Insight(
        id="insight-1",
        title="Engine insight",
        explanation="Engine produced an evidence-backed insight.",
        supporting_observations=(observation,),
        confidence=0.8,
        priority=0.6,
    )
    return Intelligence(
        id=intelligence_id,
        project=project,
        engine=engine,
        signals=(sample_signal(category=category),),
        evidence=(evidence,),
        observations=(observation,),
        insights=(insight,),
        confidence=Confidence.calculate(
            completeness=0.9,
            evidence_quality=0.8,
            freshness=0.7,
            uncertainty=0.2,
        ),
        generated_at=now(),
        metadata={"category": category},
    )


def test_intelligence_models_are_immutable_and_normalize_collections() -> None:
    intelligence = sample_intelligence()

    assert isinstance(intelligence.signals, tuple)
    assert isinstance(intelligence.evidence, tuple)
    assert isinstance(intelligence.observations, tuple)
    assert isinstance(intelligence.insights, tuple)
    assert intelligence.metadata.get("category") == "macro"
    with pytest.raises(FrozenInstanceError):
        intelligence.project = "ethereum"  # type: ignore[misc]


def test_confidence_model_is_deterministic_and_bounded() -> None:
    first = Confidence.calculate(completeness=1.2, evidence_quality=0.8, freshness=0.6, uncertainty=-1.0)
    second = Confidence.calculate(completeness=1.2, evidence_quality=0.8, freshness=0.6, uncertainty=-1.0)

    assert first == second
    assert first.completeness == 1.0
    assert first.uncertainty == 0.0
    assert 0.0 <= first.score <= 1.0


def test_validator_accepts_valid_intelligence() -> None:
    IntelligenceValidator().validate(sample_intelligence())


def test_validator_rejects_missing_evidence_duplicate_ids_and_invalid_confidence() -> None:
    validator = IntelligenceValidator()
    valid = sample_intelligence()
    missing_evidence = Intelligence(
        id="missing-evidence",
        project="bitcoin",
        engine="macro-engine",
        signals=valid.signals,
        evidence=(),
        observations=(),
        insights=(),
        confidence=valid.confidence,
        generated_at=now(),
    )
    duplicate_ids = Intelligence(
        id="duplicate",
        project="bitcoin",
        engine="macro-engine",
        signals=(sample_signal("same"),),
        evidence=(sample_evidence("same"),),
        observations=(),
        insights=(),
        confidence=valid.confidence,
        generated_at=now(),
    )
    invalid_confidence = Signal(
        id="bad-signal",
        source="source",
        timestamp=now(),
        category="macro",
        strength=1.1,
        confidence=0.8,
        severity=0.1,
    )

    with pytest.raises(IntelligenceValidationError):
        validator.validate(missing_evidence)
    with pytest.raises(IntelligenceValidationError):
        validator.validate(duplicate_ids)
    with pytest.raises(IntelligenceValidationError):
        validator.signal(invalid_confidence)


def test_registry_registers_types_and_looks_up_engine_project_and_category() -> None:
    registry = IntelligenceRegistry()
    intelligence = sample_intelligence()

    registry.register_intelligence_type("standard", Intelligence)
    registry.register_engine_output(intelligence)

    assert registry.registered_types()["standard"] is Intelligence
    assert registry.by_engine("macro-engine") == [intelligence]
    assert registry.by_project("bitcoin") == [intelligence]
    assert registry.by_category("macro") == [intelligence]


def test_registry_rejects_duplicate_outputs() -> None:
    registry = IntelligenceRegistry()
    intelligence = sample_intelligence()
    registry.register_engine_output(intelligence)

    with pytest.raises(IntelligenceRegistryError):
        registry.register_engine_output(intelligence)


def test_aggregator_combines_intelligence_without_scoring_or_ranking() -> None:
    macro = sample_intelligence("macro-1", engine="macro-engine", project="bitcoin", category="macro")
    whale = sample_intelligence("whale-1", engine="whale-engine", project="bitcoin", category="whale")

    collection = IntelligenceAggregator().aggregate([macro, whale])

    assert collection.intelligence == (macro, whale)
    assert collection.projects == ("bitcoin",)
    assert collection.engines == ("macro-engine", "whale-engine")
    assert 0.0 <= collection.confidence.score <= 1.0


def test_aggregator_rejects_empty_collections() -> None:
    with pytest.raises(IntelligenceAggregationError):
        IntelligenceAggregator().aggregate([])


@dataclass
class IntelligencePlugin:
    metadata: PluginMetadata
    intelligence: Intelligence

    def initialize(self, context: PipelineContext) -> None:
        context.record("initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("validate")

    def execute(self, context: PipelineContext) -> None:
        context.emit_intelligence(self.intelligence)
        context.record("execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("shutdown")


def test_plugin_emits_intelligence_through_pipeline_context() -> None:
    intelligence = sample_intelligence()
    context = PipelineContext()

    context.emit_intelligence(intelligence)

    assert context.intelligence == [intelligence]


def test_pipeline_collects_plugin_emitted_intelligence() -> None:
    intelligence = sample_intelligence()
    plugin = IntelligencePlugin(
        metadata=PluginMetadata(
            id="macro",
            name="Macro",
            version="1.0.0",
            author="Project Hunter",
            description="Macro intelligence plugin",
            category="intelligence",
            capabilities=("intelligence",),
        ),
        intelligence=intelligence,
    )

    context = PipelineOrchestrator().run(built_in_plugins=[plugin])

    assert context.intelligence == [intelligence]
    assert context.events == ["validate", "initialize", "execute", "shutdown"]
