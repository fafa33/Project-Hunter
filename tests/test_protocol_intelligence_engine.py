from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.protocol import (
    ApplicationSnapshot,
    FeeSnapshot,
    GovernanceSnapshot,
    IncentiveSnapshot,
    IncidentSnapshot,
    LiquiditySnapshot,
    ProtocolIntelligenceEngine,
    ProtocolSnapshot,
    RevenueSnapshot,
    TransactionSnapshot,
    TreasurySnapshot,
    TVLSnapshot,
    UsageSnapshot,
    ValidatorSnapshot,
    create_plugin,
)
from hunter.intelligence.engines.protocol.analyzers import ProtocolAnalyzer
from hunter.intelligence.engines.protocol.collectors import ContextProtocolCollector
from hunter.intelligence.engines.protocol.confidence import ProtocolConfidenceModel
from hunter.intelligence.engines.protocol.exceptions import ProtocolValidationError
from hunter.intelligence.engines.protocol.indicators import ProtocolIndicatorCalculator
from hunter.intelligence.engines.protocol.normalization import ProtocolNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def base(
    record_id: str,
    days_ago: int = 0,
    *,
    source: str = "fixture",
    chain: str = "ethereum",
    deployment: str = "main",
    reliability: float = 0.9,
):
    return {
        "id": record_id,
        "project": "aave",
        "protocol": "aave-v3",
        "source": source,
        "timestamp": NOW - timedelta(days=days_ago),
        "reliability": reliability,
        "reference": f"https://example.test/{record_id}",
        "chain": chain,
        "deployment": deployment,
    }


def protocol_records():
    return (
        UsageSnapshot(**base("usage-old", 60), active_users=100, new_users=20, returning_users=70, retained_users=50),
        UsageSnapshot(**base("usage-new", 5, source="defillama"), active_users=150, new_users=30, returning_users=110, retained_users=90),
        TransactionSnapshot(**base("tx-old", 60), transaction_count=1000, economically_meaningful_count=700, duplicate_ratio=0.05),
        TransactionSnapshot(**base("tx-new", 5), transaction_count=1500, economically_meaningful_count=1200, duplicate_ratio=0.05, bridge_pass_through_ratio=0.05),
        FeeSnapshot(**base("fee-old", 60), fees_usd=1000),
        FeeSnapshot(**base("fee-new", 5), fees_usd=1600),
        RevenueSnapshot(**base("rev-old", 60), revenue_usd=300, protocol_income_usd=150),
        RevenueSnapshot(**base("rev-new", 5), revenue_usd=700, protocol_income_usd=300),
        TVLSnapshot(**base("tvl-old", 60), tvl_usd=100_000, organic_tvl_usd=65_000, incentive_tvl_usd=35_000),
        TVLSnapshot(**base("tvl-new", 5), tvl_usd=140_000, organic_tvl_usd=100_000, incentive_tvl_usd=40_000),
        LiquiditySnapshot(**base("liq-new", 5), liquidity_usd=50_000, depth_usd=30_000, stable_liquidity_ratio=0.8, utilization_ratio=0.7),
        ApplicationSnapshot(**base("app-1", 5), application_id="lend", volume_share=0.45),
        ApplicationSnapshot(**base("app-2", 5, chain="polygon"), application_id="borrow", volume_share=0.30),
        ApplicationSnapshot(**base("app-3", 5, chain="arbitrum"), application_id="stable", volume_share=0.25),
        ValidatorSnapshot(**base("val-new", 5), active_validators=120, online_ratio=0.95, concentration_ratio=0.25),
        IncidentSnapshot(**base("incident-old", 70), severity=0.2, resolved=True, duration_minutes=10),
        GovernanceSnapshot(**base("gov-new", 5), proposals=4, voter_count=1200, participation_ratio=0.55),
        TreasurySnapshot(**base("treasury-new", 5), treasury_usd=1_000_000, monthly_expense_usd=50_000, runway_months=20),
        IncentiveSnapshot(**base("incentive-new", 5), incentives_usd=250, emissions_usd=200, revenue_usd=700),
    )


def dataset():
    return ProtocolNormalizer().normalize(protocol_records())


def test_context_collector_reads_replaceable_protocol_inputs() -> None:
    context = PipelineContext(values={"protocol_records": list(protocol_records())})

    collected = ContextProtocolCollector().collect(context)

    assert len(collected) == len(protocol_records())
    assert isinstance(collected[0], ProtocolSnapshot)


def test_canonical_models_reject_invalid_records() -> None:
    with pytest.raises(ProtocolValidationError):
        UsageSnapshot(**base("", 0), active_users=1)
    with pytest.raises(ProtocolValidationError):
        TVLSnapshot(**base("bad-tvl", 0), tvl_usd=-1)


def test_normalization_handles_multi_chain_multi_deployment_duplicates_and_missing_data() -> None:
    duplicate_low_quality = TVLSnapshot(
        **base("duplicate-low", 5, source="weak", reliability=0.1),
        tvl_usd=1,
        organic_tvl_usd=1,
    )
    duplicate_high_quality = TVLSnapshot(
        **base("duplicate-high", 5, source="strong", reliability=0.95),
        tvl_usd=2,
        organic_tvl_usd=2,
    )

    normalized = ProtocolNormalizer().normalize((*protocol_records(), duplicate_low_quality, duplicate_high_quality))

    assert normalized.project == "aave"
    assert normalized.protocol == "aave-v3"
    assert normalized.chains() == ("arbitrum", "ethereum", "polygon")
    assert normalized.deployments() == ("main",)
    assert any(item.id == "duplicate-high" for item in normalized.tvl)
    assert not any(item.id == "duplicate-low" for item in normalized.tvl)
    assert "users" in normalized.missing_fields


def test_user_retention_transaction_fee_revenue_and_tvl_indicators() -> None:
    indicators = {indicator.name: indicator for indicator in ProtocolIndicatorCalculator().calculate(dataset())}

    assert indicators["user_growth"].value > 0.5
    assert indicators["returning_user_ratio"].value > 0.7
    assert indicators["retention_trend"].value > 0.5
    assert indicators["transaction_quality"].value > 0.7
    assert indicators["fee_growth"].value > 0.5
    assert indicators["revenue_growth"].value > 0.5
    assert indicators["tvl_growth"].value > 0.5
    assert indicators["organic_tvl_ratio"].value > 0.6


def test_liquidity_capital_efficiency_applications_and_reliability_indicators() -> None:
    indicators = {indicator.name: indicator for indicator in ProtocolIndicatorCalculator().calculate(dataset())}

    assert indicators["liquidity_depth"].value == 0.6
    assert indicators["capital_efficiency"].value > 0.0
    assert indicators["application_breadth"].value > 0.5
    assert indicators["application_concentration"].value > 0.5
    assert indicators["network_reliability"].value > 0.7


def test_incident_treasury_incentive_emissions_and_resilience_indicators() -> None:
    indicators = {indicator.name: indicator for indicator in ProtocolIndicatorCalculator().calculate(dataset())}

    assert indicators["incident_frequency"].value > 0.0
    assert indicators["treasury_runway"].value == 1.0
    assert indicators["incentive_dependence"].value > 0.5
    assert indicators["emissions_dependence"].value > 0.5
    assert indicators["protocol_resilience"].value > 0.6


def test_analyzer_detects_protocol_health_and_risks() -> None:
    analysis = ProtocolAnalyzer().analyze(dataset())

    assert analysis.health in {"strong", "stable"}
    assert analysis.operational_trend in {"improving", "stable"}
    assert analysis.economic_trend in {"improving", "stable"}
    assert "application_breadth" in analysis.strengths


def test_confidence_uses_completeness_freshness_coverage_and_provider_agreement() -> None:
    sparse = ProtocolNormalizer().normalize((UsageSnapshot(**base("only", 5), active_users=10),))
    rich = dataset()

    sparse_confidence = ProtocolConfidenceModel().calculate(sparse)
    rich_confidence = ProtocolConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.completeness > sparse_confidence.completeness


def test_protocol_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"protocol_records": list(protocol_records())})
    engine = ProtocolIntelligenceEngine()

    collected = engine.collect(context)
    analysis = engine.analyze(context, collected)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "protocol-intelligence"
    assert intelligence.project == "aave"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("protocol_health") in {"strong", "stable"}


def test_protocol_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "protocol-intelligence"
    assert "protocol-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_protocol_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  protocol-intelligence: true
configuration: {}
load_order:
  - protocol-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.protocol:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["protocol-intelligence"]


def test_pipeline_executes_protocol_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  protocol-intelligence: true
configuration: {}
load_order:
  - protocol-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.protocol:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"protocol_records": list(protocol_records())})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "protocol-intelligence"
    assert "protocol:intelligence:execute" in result.events
