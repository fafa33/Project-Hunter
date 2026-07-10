from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from hunter.intelligence import Confidence, Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines import (
    BaseIntelligenceEngine,
    CapabilityRegistry,
    CategoryRegistry,
    EngineFactory,
    EngineMetadata,
    EngineRegistry,
    EngineRunner,
)
from hunter.intelligence.engines.exceptions import (
    IntelligenceEngineExecutionError,
    IntelligenceEngineFactoryError,
    IntelligenceEngineRegistrationError,
    IntelligenceEngineValidationError,
)
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext, PluginMetadata


def now() -> datetime:
    return datetime.now(UTC)


def intelligence(engine_id: str = "macro-engine") -> Intelligence:
    evidence = Evidence(
        id=f"{engine_id}-evidence",
        source="public-source",
        collected_at=now(),
        reliability=0.9,
        freshness=0.8,
        reference="https://example.test/source",
        raw_data={"value": 1},
    )
    observation = Observation(
        id=f"{engine_id}-observation",
        engine=engine_id,
        project="bitcoin",
        description="Observed evidence-backed intelligence.",
        evidence=(evidence,),
        importance=0.7,
    )
    insight = Insight(
        id=f"{engine_id}-insight",
        title="Framework insight",
        explanation="The engine generated canonical intelligence.",
        supporting_observations=(observation,),
        confidence=0.8,
        priority=0.6,
    )
    return Intelligence(
        id=f"{engine_id}-intelligence",
        project="bitcoin",
        engine=engine_id,
        signals=(
            Signal(
                id=f"{engine_id}-signal",
                source=engine_id,
                timestamp=now(),
                category="macro",
                strength=0.7,
                confidence=0.8,
                severity=0.2,
            ),
        ),
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
        metadata={"category": "macro"},
    )


class ExampleEngine(BaseIntelligenceEngine):
    def __init__(self, engine_id: str = "macro-engine", *, priority: int = 10, healthy: bool = True) -> None:
        self.metadata = EngineMetadata(
            id=engine_id,
            name="Macro Engine",
            category="macro",
            version="1.0.0",
            priority=priority,
            required_inputs=("market-data",),
            produced_outputs=("macro-intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence"),
        )
        self.calls: list[str] = []
        self._healthy = healthy

    def validate(self, context: PipelineContext) -> None:
        self.calls.append("validate")
        if self.required_inputs[0] not in context.values:
            context.set(self.required_inputs[0], "available")

    def collect(self, context: PipelineContext) -> dict[str, Any]:
        self.calls.append("collect")
        return {"input": context.get(self.required_inputs[0])}

    def analyze(self, context: PipelineContext, collected: Any) -> dict[str, Any]:
        self.calls.append("analyze")
        return {"collected": collected}

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        self.calls.append("generate_intelligence")
        return intelligence(self.id)

    def health_check(self) -> bool:
        self.calls.append("health_check")
        return self._healthy


def test_base_engine_exposes_required_metadata_properties() -> None:
    engine = ExampleEngine()

    assert engine.id == "macro-engine"
    assert engine.name == "Macro Engine"
    assert engine.category == "macro"
    assert engine.version == "1.0.0"
    assert engine.priority == 10
    assert engine.required_inputs == ("market-data",)
    assert engine.produced_outputs == ("macro-intelligence",)
    assert "analyze" in engine.capabilities


def test_engine_factory_registers_and_creates_engines() -> None:
    factory = EngineFactory()
    factory.register("macro-engine", ExampleEngine)

    engine = factory.create("macro-engine")

    assert isinstance(engine, ExampleEngine)
    assert factory.available() == ("macro-engine",)


def test_engine_factory_rejects_duplicates_and_unknown_ids() -> None:
    factory = EngineFactory()
    factory.register("macro-engine", ExampleEngine)

    with pytest.raises(IntelligenceEngineFactoryError):
        factory.register("macro-engine", ExampleEngine)
    with pytest.raises(IntelligenceEngineFactoryError):
        factory.create("unknown")


def test_engine_registry_supports_lookup_and_priority_ordering() -> None:
    registry = EngineRegistry()
    low = ExampleEngine("developer-engine", priority=1)
    high = ExampleEngine("macro-engine", priority=10)

    registry.register(low)
    registry.register(high)

    assert registry.get("macro-engine") == high
    assert registry.by_category("macro") == [low, high]
    assert registry.by_capability("analyze") == [low, high]
    assert registry.ordered() == [high, low]


def test_engine_registry_rejects_invalid_and_duplicate_engines() -> None:
    registry = EngineRegistry()
    engine = ExampleEngine()
    registry.register(engine)

    with pytest.raises(IntelligenceEngineRegistrationError):
        registry.register(engine)

    invalid = ExampleEngine("invalid")
    invalid.metadata = EngineMetadata(
        id="invalid",
        name="Invalid",
        category="macro",
        version="1",
        priority=1,
        required_inputs=(),
        produced_outputs=("output",),
        capabilities=("collect",),
    )
    with pytest.raises(IntelligenceEngineValidationError):
        EngineRegistry().register(invalid)


def test_category_and_capability_registries_support_future_extensions() -> None:
    categories = CategoryRegistry()
    capabilities = CapabilityRegistry()

    categories.register("sentiment")
    capabilities.register("normalize")

    assert categories.contains("sentiment")
    assert capabilities.contains("normalize")
    assert "macro" in categories.all()
    assert "collect" in capabilities.all()


def test_engine_runner_executes_lifecycle_and_emits_intelligence() -> None:
    engine = ExampleEngine()
    context = PipelineContext()

    emitted = EngineRunner().run([engine], context)

    assert len(emitted) == 1
    assert context.intelligence == emitted
    assert engine.calls == ["health_check", "validate", "collect", "analyze", "generate_intelligence"]


def test_engine_runner_rejects_unhealthy_engine() -> None:
    engine = ExampleEngine(healthy=False)

    with pytest.raises(IntelligenceEngineExecutionError):
        EngineRunner().run([engine], PipelineContext())


def test_pipeline_orchestrator_runs_intelligence_engines_before_plugins() -> None:
    engine = ExampleEngine()

    context = PipelineOrchestrator().run(intelligence_engines=[engine])

    assert len(context.intelligence) == 1
    assert context.intelligence[0].engine == "macro-engine"


@dataclass
class EngineBackedPlugin:
    metadata: PluginMetadata
    runner: EngineRunner
    engine: ExampleEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("plugin:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("plugin:validate")

    def execute(self, context: PipelineContext) -> None:
        self.runner.run([self.engine], context)
        context.record("plugin:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("plugin:shutdown")


def test_plugin_architecture_can_host_engine_runner() -> None:
    plugin = EngineBackedPlugin(
        metadata=PluginMetadata(
            id="engine-plugin",
            name="Engine Plugin",
            version="1.0.0",
            author="Project Hunter",
            description="Runs intelligence engines through the plugin lifecycle.",
            category="intelligence",
            capabilities=("intelligence-engine",),
        ),
        runner=EngineRunner(),
        engine=ExampleEngine(),
    )

    context = PipelineOrchestrator().run(built_in_plugins=[plugin])

    assert len(context.intelligence) == 1
    assert context.events == ["plugin:validate", "plugin:initialize", "plugin:execute", "plugin:shutdown"]
