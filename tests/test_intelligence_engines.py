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
    EngineContext,
    EngineDefinition,
    EngineFactory,
    EngineMetadata,
    EngineRegistry,
    EngineRunner,
    EvidenceBundle,
    Finding,
    FindingBatch,
    HunterIntelligenceEngineBuilder,
    IntelligenceEngineService,
    finding_identity,
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


class FindingRepository:
    def __init__(self, evidence: tuple[Evidence, ...], *, fail_on_persist: bool = False) -> None:
        self.evidence = evidence
        self.fail_on_persist = fail_on_persist
        self.findings: dict[str, Finding] = {}

    def load_engine_evidence(self, candidate_id: str) -> tuple[Evidence, ...]:
        assert candidate_id == "bitcoin"
        return self.evidence

    def persist_authorized_findings(self, batch: FindingBatch) -> None:
        if self.fail_on_persist:
            raise RuntimeError("persistence failed")
        staged = dict(self.findings)
        for finding in batch.findings:
            staged[finding.finding_id] = finding
        self.findings = staged


class FoundationFindingEngine:
    def __init__(self, definition: EngineDefinition, *, forged: bool = False) -> None:
        self._definition = definition
        self.forged = forged
        self.seen_contexts: list[EngineContext] = []

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        self.seen_contexts.append(context)
        supporting = evidence.evidence_ids[:1]
        lineage = ("forged-lineage",) if self.forged else evidence.lineage[:1]
        finding_id = finding_identity(
            candidate_id=evidence.candidate_id,
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            finding_type="developer_activity",
            explanation="Evidence-backed developer activity is present.",
            supporting_evidence_ids=supporting,
            evidence_lineage=lineage,
            deterministic_confidence=0.8,
            confidence_basis="deterministic evidence coverage",
            evaluated_at=context.evaluated_at,
            as_of=context.as_of,
            analysis_trace_version=self.definition.analysis_trace_version,
            missing_evidence=evidence.missing_evidence,
            schema_version=self.definition.output_schema_version,
        )
        finding = Finding(
            finding_id=finding_id,
            candidate_id=evidence.candidate_id,
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            finding_type="developer_activity",
            explanation="Evidence-backed developer activity is present.",
            supporting_evidence_ids=supporting,
            evidence_lineage=lineage,
            deterministic_confidence=0.8,
            confidence_basis="deterministic evidence coverage",
            evaluated_at=context.evaluated_at,
            as_of=context.as_of,
            analysis_trace_version=self.definition.analysis_trace_version,
            missing_evidence=evidence.missing_evidence,
            schema_version=self.definition.output_schema_version,
        )
        return FindingBatch(
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            candidate_id=evidence.candidate_id,
            as_of=context.as_of,
            evaluated_at=context.evaluated_at,
            findings=(finding,),
        )


def engine_definition(*, analysis_trace_version: str = "analysis-trace-v1") -> EngineDefinition:
    metadata = EngineMetadata(
        id="developer-foundation",
        name="Developer Foundation",
        category="developer",
        version="1.0.0",
        priority=1,
        required_inputs=("developer-evidence",),
        produced_outputs=("developer-finding",),
        capabilities=("analyze",),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts("developer-repository")
        .with_supported_evidence_types("repository")
        .with_analysis_stages("validate", "analyze", "explain")
        .with_finding_types("developer_activity")
        .with_output_schema(schema_version="intelligence-finding-v1", analysis_trace_version=analysis_trace_version)
        .build()
    )


def foundation_evidence(*, collected_at: datetime | None = None, evidence_id: str = "evidence-1") -> Evidence:
    return Evidence(
        id=evidence_id,
        source="github",
        collected_at=collected_at or datetime(2026, 1, 1, tzinfo=UTC),
        reliability=0.9,
        freshness=0.8,
        reference=f"github:{evidence_id}",
        raw_data={"commits": 10},
    )


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


def test_hunter_intelligence_engine_builder_is_definition_only() -> None:
    definition = engine_definition()

    assert definition.metadata.id == "developer-foundation"
    assert definition.evidence_contracts == ("developer-repository",)
    assert definition.supported_evidence_types == ("repository",)
    assert definition.finding_types == ("developer_activity",)
    assert definition.analysis_trace_version == "analysis-trace-v1"
    assert not hasattr(HunterIntelligenceEngineBuilder(definition.metadata), "execute")
    assert not hasattr(HunterIntelligenceEngineBuilder(definition.metadata), "persist")
    assert not hasattr(HunterIntelligenceEngineBuilder(definition.metadata), "load_evidence")


def test_intelligence_engine_service_executes_with_context_without_repository_injection() -> None:
    repository = FindingRepository((foundation_evidence(),))
    engine = FoundationFindingEngine(engine_definition())
    service = IntelligenceEngineService(repository)
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    evaluated_at = datetime(2026, 1, 3, tzinfo=UTC)

    batch = service.execute(
        engine,
        candidate_id="bitcoin",
        as_of=as_of,
        evaluated_at=evaluated_at,
        engine_configuration_fingerprint="config:v1",
    )

    assert batch.findings == tuple(repository.findings.values())
    assert engine.seen_contexts[0].as_of == as_of
    assert engine.seen_contexts[0].evaluated_at == evaluated_at
    assert engine.seen_contexts[0].engine_version == "1.0.0"
    assert not hasattr(engine.seen_contexts[0], "repository")


def test_foundation_findings_are_deterministic_and_idempotent() -> None:
    repository = FindingRepository((foundation_evidence(),))
    engine = FoundationFindingEngine(engine_definition())
    service = IntelligenceEngineService(repository)
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    evaluated_at = datetime(2026, 1, 3, tzinfo=UTC)

    first = service.execute(
        engine,
        candidate_id="bitcoin",
        as_of=as_of,
        evaluated_at=evaluated_at,
        engine_configuration_fingerprint="config:v1",
    )
    second = service.execute(
        engine,
        candidate_id="bitcoin",
        as_of=as_of,
        evaluated_at=evaluated_at,
        engine_configuration_fingerprint="config:v1",
    )

    assert first == second
    assert len(repository.findings) == 1


def test_service_applies_explicit_replay_cutoff_and_excludes_future_evidence() -> None:
    past = foundation_evidence(collected_at=datetime(2026, 1, 1, tzinfo=UTC), evidence_id="past")
    future = foundation_evidence(collected_at=datetime(2026, 1, 5, tzinfo=UTC), evidence_id="future")
    repository = FindingRepository((future, past))
    engine = FoundationFindingEngine(engine_definition())

    batch = IntelligenceEngineService(repository).execute(
        engine,
        candidate_id="bitcoin",
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
        engine_configuration_fingerprint="config:v1",
    )

    assert batch.findings[0].supporting_evidence_ids == ("past",)
    assert "future" not in engine.seen_contexts[0].replay_fingerprint


def test_service_rejects_forged_finding_lineage() -> None:
    repository = FindingRepository((foundation_evidence(),))
    engine = FoundationFindingEngine(engine_definition(), forged=True)

    with pytest.raises(IntelligenceEngineValidationError, match="evidence lineage"):
        IntelligenceEngineService(repository).execute(
            engine,
            candidate_id="bitcoin",
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
            engine_configuration_fingerprint="config:v1",
        )


def test_missing_evidence_is_preserved_on_findings() -> None:
    repository = FindingRepository((foundation_evidence(),))
    engine = FoundationFindingEngine(engine_definition())

    batch = IntelligenceEngineService(repository).execute(
        engine,
        candidate_id="bitcoin",
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
        engine_configuration_fingerprint="config:v1",
    )

    assert batch.findings[0].missing_evidence == ("developer-repository",)


def test_analysis_trace_version_changes_finding_identity() -> None:
    repository = FindingRepository((foundation_evidence(),))
    first = IntelligenceEngineService(repository).execute(
        FoundationFindingEngine(engine_definition(analysis_trace_version="analysis-trace-v1")),
        candidate_id="bitcoin",
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
        engine_configuration_fingerprint="config:v1",
    )
    second = IntelligenceEngineService(repository).execute(
        FoundationFindingEngine(engine_definition(analysis_trace_version="analysis-trace-v2")),
        candidate_id="bitcoin",
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
        engine_configuration_fingerprint="config:v1",
    )

    assert first.findings[0].finding_id != second.findings[0].finding_id


def test_finding_persistence_rolls_back_on_repository_failure() -> None:
    repository = FindingRepository((foundation_evidence(),), fail_on_persist=True)
    engine = FoundationFindingEngine(engine_definition())

    with pytest.raises(RuntimeError, match="persistence failed"):
        IntelligenceEngineService(repository).execute(
            engine,
            candidate_id="bitcoin",
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            evaluated_at=datetime(2026, 1, 3, tzinfo=UTC),
            engine_configuration_fingerprint="config:v1",
        )

    assert repository.findings == {}


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
