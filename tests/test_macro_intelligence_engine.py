from __future__ import annotations

from datetime import UTC, datetime

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.macro import MacroDataPoint, MacroIntelligenceEngine, create_plugin
from hunter.intelligence.engines.macro.analyzers import MacroAnalyzer
from hunter.intelligence.engines.macro.collectors import ContextMacroCollector
from hunter.intelligence.engines.macro.confidence import MacroConfidenceModel
from hunter.intelligence.engines.macro.indicators import MacroIndicatorCalculator
from hunter.intelligence.engines.macro.normalization import MacroNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager


def point(
    domain: str,
    value: float,
    previous: float,
    *,
    source: str = "fixture",
    reliability: float = 0.9,
) -> MacroDataPoint:
    return MacroDataPoint(
        domain=domain,
        value=value,
        previous_value=previous,
        source=source,
        timestamp=datetime.now(UTC),
        reliability=reliability,
        reference=f"https://example.test/{domain}",
        raw_data={"domain": domain, "value": value},
    )


def macro_points() -> tuple[MacroDataPoint, ...]:
    return (
        point("global_liquidity", 0.8, 0.6),
        point("interest_rates", 0.2, 0.4),
        point("inflation", 0.3, 0.5),
        point("stablecoin_supply", 0.75, 0.6),
        point("eth_btc_ratio", 0.7, 0.5),
        point("institutional_adoption", 0.8, 0.6),
        point("ai_sector", 0.9, 0.7),
        point("defi_sector", 0.4, 0.6),
        point("oracle_sector", 0.7, 0.6),
    )


def test_context_collector_reads_replaceable_macro_inputs() -> None:
    context = PipelineContext(values={"macro_data": list(macro_points())})

    collected = ContextMacroCollector().collect(context)

    assert len(collected) == len(macro_points())
    assert collected[0].domain == "global_liquidity"


def test_normalization_filters_unknown_domains_and_clamps_values() -> None:
    unknown = point("unknown", 1.0, 0.5)
    high = point("global_liquidity", 2.0, -1.0)

    dataset = MacroNormalizer().normalize((unknown, high))

    assert len(dataset.points) == 1
    assert dataset.points[0].value == 1.0
    assert dataset.points[0].previous_value == 0.0


def test_indicators_detect_liquidity_risk_appetite_and_sector_rotation() -> None:
    dataset = MacroNormalizer().normalize(macro_points())

    indicators = {indicator.name: indicator for indicator in MacroIndicatorCalculator().calculate(dataset.by_domain())}

    assert indicators["liquidity_expansion"].direction == "strengthening"
    assert indicators["risk_appetite"].direction == "strengthening"
    assert indicators["sector_rotation"].value > 0.5
    assert indicators["market_cycle"].direction == "risk_on"


def test_analyzer_produces_macro_analysis_without_recommendations() -> None:
    dataset = MacroNormalizer().normalize(macro_points())

    analysis = MacroAnalyzer().analyze(dataset)

    assert analysis.risk_regime == "risk_on"
    assert "global_liquidity" in analysis.strengthening_domains
    assert all("buy" not in event.lower() for event in analysis.notable_events)


def test_confidence_uses_completeness_quality_freshness_and_agreement() -> None:
    sparse = MacroNormalizer().normalize((point("global_liquidity", 0.8, 0.6),))
    richer = MacroNormalizer().normalize(macro_points())

    sparse_confidence = MacroConfidenceModel().calculate(sparse)
    richer_confidence = MacroConfidenceModel().calculate(richer)

    assert richer_confidence.score > sparse_confidence.score
    assert richer_confidence.evidence_quality > 0.0
    assert richer_confidence.freshness == 1.0


def test_macro_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"macro_data": macro_points()})
    engine = MacroIntelligenceEngine()

    dataset = engine.collect(context)
    analysis = engine.analyze(context, dataset)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "macro-intelligence"
    assert intelligence.project == "global-crypto"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("risk_regime") == "risk_on"


def test_macro_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "macro-intelligence"
    assert "macro-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_macro_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  macro-intelligence: true
configuration: {}
load_order:
  - macro-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.macro:create_plugin
""",
        encoding="utf-8",
    )
    manager = PluginManager()

    loaded = manager.load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["macro-intelligence"]


def test_pipeline_executes_macro_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  macro-intelligence: true
configuration: {}
load_order:
  - macro-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.macro:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"macro_data": macro_points()})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "macro-intelligence"
    assert "macro:intelligence:execute" in result.events
