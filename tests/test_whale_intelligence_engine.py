from __future__ import annotations

from datetime import UTC, datetime

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.whale import WhaleEvent, WhaleIntelligenceEngine, create_plugin
from hunter.intelligence.engines.whale.analyzers import WhaleAnalyzer
from hunter.intelligence.engines.whale.collectors import ContextWhaleCollector
from hunter.intelligence.engines.whale.confidence import WhaleConfidenceModel
from hunter.intelligence.engines.whale.normalization import WhaleNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager


def event(
    event_id: str,
    event_type: str,
    amount: float,
    direction: str,
    *,
    asset: str = "BTC",
    source: str = "fixture",
    reliability: float = 0.9,
    attribution: float = 0.8,
    confirmation: float = 0.85,
) -> WhaleEvent:
    return WhaleEvent(
        id=event_id,
        asset=asset,
        event_type=event_type,
        amount=amount,
        direction=direction,
        source=source,
        timestamp=datetime.now(UTC),
        reliability=reliability,
        wallet_attribution_quality=attribution,
        confirmation=confirmation,
        reference=f"https://example.test/{event_id}",
        raw_data={"id": event_id, "type": event_type},
    )


def whale_events() -> tuple[WhaleEvent, ...]:
    return (
        event("e1", "accumulation", 0.8, "accumulation", asset="BTC"),
        event("e2", "distribution", 0.3, "distribution", asset="ETH"),
        event("e3", "exchange_flow", 0.7, "outflow", asset="BTC"),
        event("e4", "smart_money", 0.75, "accumulation", asset="SOL"),
        event("e5", "treasury_movement", 0.6, "allocation", asset="ETH"),
        event("e6", "cross_chain_flow", 0.65, "migration", asset="USDC"),
    )


def test_context_collector_reads_replaceable_whale_inputs() -> None:
    context = PipelineContext(values={"whale_events": list(whale_events())})

    collected = ContextWhaleCollector().collect(context)

    assert len(collected) == len(whale_events())
    assert collected[0].event_type == "accumulation"


def test_normalization_filters_unknown_types_deduplicates_and_clamps() -> None:
    duplicate = event("same", "accumulation", 2.0, "large accumulation", reliability=2.0)
    duplicate_again = event("same", "accumulation", 0.2, "distribution")
    unknown = event("unknown", "unsupported", 0.7, "unknown")

    dataset = WhaleNormalizer().normalize((duplicate, duplicate_again, unknown))

    assert len(dataset.events) == 1
    assert dataset.events[0].amount == 1.0
    assert dataset.events[0].reliability == 1.0
    assert dataset.events[0].direction == "large_accumulation"


def test_analyzer_detects_accumulation_exchange_flow_and_smart_money() -> None:
    dataset = WhaleNormalizer().normalize(whale_events())

    analysis = WhaleAnalyzer().analyze(dataset)

    assert "btc" in analysis.accumulating_assets
    assert "eth" in analysis.distributing_assets
    assert analysis.exchange_flow == "net_outflow"
    assert analysis.smart_money_activity == "elevated"
    assert any(signal.event_type == "accumulation" for signal in analysis.signals)


def test_confidence_uses_source_attribution_confirmation_completeness_and_agreement() -> None:
    sparse = WhaleNormalizer().normalize((event("e1", "accumulation", 0.8, "accumulation"),))
    richer = WhaleNormalizer().normalize(whale_events())

    sparse_confidence = WhaleConfidenceModel().calculate(sparse)
    richer_confidence = WhaleConfidenceModel().calculate(richer)

    assert richer_confidence.score > sparse_confidence.score
    assert richer_confidence.evidence_quality > 0.0
    assert richer_confidence.freshness == 1.0


def test_whale_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"whale_events": whale_events()})
    engine = WhaleIntelligenceEngine()

    dataset = engine.collect(context)
    analysis = engine.analyze(context, dataset)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "whale-intelligence"
    assert intelligence.project == "global-crypto"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("exchange_flow") == "net_outflow"


def test_whale_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "whale-intelligence"
    assert "whale-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_whale_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  whale-intelligence: true
configuration: {}
load_order:
  - whale-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.whale:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["whale-intelligence"]


def test_pipeline_executes_whale_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  whale-intelligence: true
configuration: {}
load_order:
  - whale-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.whale:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"whale_events": whale_events()})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "whale-intelligence"
    assert "whale:intelligence:execute" in result.events

