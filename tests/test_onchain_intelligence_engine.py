from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Confidence, Intelligence
from hunter.intelligence.engines.onchain import (
    AddressSnapshot,
    ApplicationActivitySnapshot,
    BridgeFlowSnapshot,
    CapitalFlowSnapshot,
    ContractActivitySnapshot,
    ContractDeploymentSnapshot,
    ExchangeFlowSnapshot,
    GovernanceActivitySnapshot,
    HolderSnapshot,
    MintBurnSnapshot,
    OnchainEvent,
    OnchainIntelligenceEngine,
    StakingFlowSnapshot,
    SupplyDistributionSnapshot,
    TransactionSnapshot,
    TransferSnapshot,
    TreasuryActivitySnapshot,
    ValidatorDistributionSnapshot,
    create_plugin,
)
from hunter.intelligence.engines.onchain.analyzers import OnchainAnalyzer
from hunter.intelligence.engines.onchain.anomalies import OnchainAnomalyModel
from hunter.intelligence.engines.onchain.collectors import ContextOnchainCollector
from hunter.intelligence.engines.onchain.confidence import OnchainConfidenceModel
from hunter.intelligence.engines.onchain.contracts import OnchainContractAnalyzer
from hunter.intelligence.engines.onchain.exceptions import OnchainValidationError
from hunter.intelligence.engines.onchain.flows import OnchainFlowAnalyzer
from hunter.intelligence.engines.onchain.holders import OnchainHolderAnalyzer
from hunter.intelligence.engines.onchain.indicators import OnchainIndicatorCalculator
from hunter.intelligence.engines.onchain.normalization import OnchainNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def base(record_id: str, days_ago: int, *, chain: str = "ethereum", asset: str = "api3", reference: str | None = None):
    return {
        "id": record_id,
        "project": "api3",
        "asset": asset,
        "chain": chain,
        "source": "fixture",
        "timestamp": NOW - timedelta(days=days_ago),
        "reliability": 0.9,
        "reference": reference or f"https://example.test/onchain/{record_id}",
        "attribution_quality": 0.8,
        "entity_label_quality": 0.75,
        "token_denomination": asset.upper(),
    }


def onchain_records():
    return (
        AddressSnapshot(
            **base("addr-old", 120),
            active_addresses=1_000,
            new_addresses=120,
            returning_addresses=500,
            retained_addresses=450,
            sybil_ratio=0.05,
            bot_ratio=0.04,
        ),
        AddressSnapshot(
            **base("addr-new", 1),
            active_addresses=1_600,
            new_addresses=240,
            returning_addresses=900,
            retained_addresses=850,
            sybil_ratio=0.06,
            bot_ratio=0.05,
        ),
        TransactionSnapshot(
            **base("tx-old", 120),
            transaction_count=10_000,
            adjusted_transaction_count=7_000,
            gross_volume=1_000_000,
            adjusted_volume=700_000,
            low_value_ratio=0.08,
            repeated_pattern_ratio=0.07,
        ),
        TransactionSnapshot(
            **base("tx-new", 1),
            transaction_count=16_000,
            adjusted_transaction_count=11_000,
            gross_volume=1_800_000,
            adjusted_volume=1_300_000,
            low_value_ratio=0.09,
            repeated_pattern_ratio=0.08,
        ),
        TransferSnapshot(
            **base("transfer-new", 1),
            transfer_value=1_200_000,
            adjusted_transfer_value=1_000_000,
            internal_transfer_ratio=0.1,
            circular_transfer_ratio=0.08,
        ),
        CapitalFlowSnapshot(
            **base("capital-new", 1),
            inflow=1_000_000,
            outflow=400_000,
            retained_capital=750_000,
            circular_flow_ratio=0.07,
            internal_flow_ratio=0.08,
        ),
        ExchangeFlowSnapshot(
            **base("exchange-new", 1),
            inflow=200_000,
            outflow=500_000,
            exchange_label_quality=0.8,
            redistribution_ratio=0.1,
        ),
        BridgeFlowSnapshot(
            **base("bridge-old", 120, chain="base"),
            inflow=100_000,
            outflow=60_000,
            bridge_label_quality=0.8,
            pass_through_ratio=0.08,
            source_chain="ethereum",
            target_chain="base",
        ),
        BridgeFlowSnapshot(
            **base("bridge-new", 1, chain="base"),
            inflow=240_000,
            outflow=80_000,
            bridge_label_quality=0.8,
            pass_through_ratio=0.1,
            source_chain="ethereum",
            target_chain="base",
        ),
        StakingFlowSnapshot(**base("staking-new", 1), staked_inflow=300_000, staked_outflow=90_000, unstaked=30_000),
        HolderSnapshot(
            **base("holder-old", 120),
            holder_count=20_000,
            retained_holders=10_000,
            long_term_holders=5_000,
            top_holder_share=0.32,
            dormant_supply_ratio=0.42,
            active_supply_ratio=0.58,
            accumulation_wallets=2_000,
            distribution_wallets=900,
        ),
        HolderSnapshot(
            **base("holder-new", 1),
            holder_count=28_000,
            retained_holders=18_000,
            long_term_holders=9_000,
            top_holder_share=0.28,
            dormant_supply_ratio=0.46,
            active_supply_ratio=0.54,
            accumulation_wallets=4_500,
            distribution_wallets=1_100,
        ),
        SupplyDistributionSnapshot(
            **base("supply-new", 1),
            circulating_supply=100_000_000,
            top_10_share=0.35,
            top_100_share=0.55,
            gini=0.4,
            distribution_quality=0.68,
        ),
        ContractActivitySnapshot(
            **base("contract-old", 120),
            active_contracts=50,
            interactions=5_000,
            unique_callers=1_000,
            protocol_owned_ratio=0.3,
            spam_contract_ratio=0.08,
            generated_contract_ratio=0.06,
            contract_address="0xcore",
        ),
        ContractActivitySnapshot(
            **base("contract-new", 1),
            active_contracts=80,
            interactions=9_000,
            unique_callers=2_200,
            protocol_owned_ratio=0.28,
            spam_contract_ratio=0.07,
            generated_contract_ratio=0.06,
            contract_address="0xcore2",
        ),
        ContractDeploymentSnapshot(**base("deploy-old", 120), deployments=10, upgrades=1, proxy_changes=1),
        ContractDeploymentSnapshot(**base("deploy-new", 1), deployments=16, upgrades=2, proxy_changes=1),
        ApplicationActivitySnapshot(
            **base("app-core", 1), application_id="core", active_users=1_500, transaction_share=0.42, volume_share=0.4
        ),
        ApplicationActivitySnapshot(
            **base("app-third", 1),
            application_id="third-party",
            active_users=700,
            transaction_share=0.25,
            volume_share=0.2,
        ),
        TreasuryActivitySnapshot(**base("treasury-new", 1), inflow=50_000, outflow=20_000, treasury_label_quality=0.85),
        MintBurnSnapshot(**base("mint-new", 1), minted=10_000, burned=8_000, anomaly_ratio=0.05),
        ValidatorDistributionSnapshot(
            **base("validator-new", 1),
            validator_count=80,
            top_validator_share=0.22,
            staker_count=4_000,
            staker_concentration=0.25,
        ),
        GovernanceActivitySnapshot(**base("governance-new", 1), proposals=4, voters=1_200, participation_ratio=0.48),
        OnchainEvent(
            **base("event-new", 1), event_type="upgrade", severity=0.3, description="Routine contract upgrade."
        ),
    )


def dataset():
    return OnchainNormalizer().normalize(onchain_records())


def test_context_collector_reads_onchain_records() -> None:
    context = PipelineContext(values={"onchain_records": list(onchain_records())})

    collected = ContextOnchainCollector().collect(context)

    assert len(collected) == len(onchain_records())
    assert isinstance(collected[0], AddressSnapshot)


def test_canonical_models_reject_invalid_data() -> None:
    with pytest.raises(OnchainValidationError):
        AddressSnapshot(**base("", 1), active_addresses=-1)


def test_normalization_preserves_multi_chain_multi_asset_and_deduplicates() -> None:
    duplicate = CapitalFlowSnapshot(**base("duplicate", 1, reference="same"), inflow=1, outflow=0)
    duplicate_again = CapitalFlowSnapshot(**base("duplicate-again", 1, reference="same"), inflow=1, outflow=0)
    solana = AddressSnapshot(**base("sol-addr", 1, chain="solana", asset="sol"), active_addresses=100)

    normalized = OnchainNormalizer().normalize((*onchain_records(), duplicate, duplicate_again, solana))

    assert "duplicate-again" in normalized.duplicates
    assert {record.chain for record in normalized.records} >= {"ethereum", "base", "solana"}
    assert {record.asset for record in normalized.records} >= {"api3", "sol"}


def test_normalization_detects_overlapping_aggregation_windows() -> None:
    first = AddressSnapshot(**base("overlap-1", 1, reference="overlap-1"), active_addresses=100)
    second = AddressSnapshot(**base("overlap-2", 1, reference="overlap-2"), active_addresses=110)

    normalized = OnchainNormalizer().normalize((first, second))

    assert "overlap-2" in normalized.overlapping_windows


def test_address_transaction_and_volume_indicators() -> None:
    indicators = {item.name: item for item in OnchainIndicatorCalculator().calculate(dataset())}

    assert indicators["active_address_momentum"].value > 0.0
    assert indicators["new_address_growth"].value > 0.0
    assert indicators["address_retention"].value > 0.0
    assert indicators["adjusted_transaction_growth"].value > 0.0
    assert indicators["adjusted_volume_growth"].value > 0.0


def test_flow_analysis_covers_capital_exchange_bridge_and_staking() -> None:
    flows = OnchainFlowAnalyzer()
    normalized = dataset()

    assert flows.net_capital_flow(normalized) > 0.0
    assert flows.exchange_netflow(normalized) > 0.0
    assert flows.bridge_netflow(normalized) > 0.0
    assert flows.staking_netflow(normalized) > 0.0
    assert flows.capital_retention(normalized) > 0.0


def test_holder_analysis_covers_growth_retention_concentration_and_supply() -> None:
    holders = OnchainHolderAnalyzer()
    normalized = dataset()

    assert holders.holder_growth(normalized) > 0.0
    assert holders.holder_retention(normalized) > 0.0
    assert holders.long_term_holder_growth(normalized) > 0.0
    assert holders.concentration(normalized) > 0.0
    assert holders.supply_distribution_quality(normalized) > 0.0
    assert holders.accumulation_breadth(normalized) > holders.distribution_breadth(normalized)


def test_contract_analysis_covers_growth_diversity_breadth_and_concentration() -> None:
    contracts = OnchainContractAnalyzer()
    normalized = dataset()

    assert contracts.active_contract_growth(normalized) > 0.0
    assert contracts.contract_diversity(normalized) > 0.0
    assert contracts.deployment_growth(normalized) > 0.0
    assert contracts.interaction_breadth(normalized) > 0.0
    assert contracts.application_concentration(normalized) == 0.42


def test_token_velocity_dormancy_churn_validator_and_governance_indicators() -> None:
    indicators = {item.name: item for item in OnchainIndicatorCalculator().calculate(dataset())}

    assert indicators["token_velocity"].value > 0.0
    assert indicators["dormancy"].value > 0.0
    assert indicators["churn"].value > 0.0
    assert indicators["validator_concentration"].value > 0.0
    assert indicators["governance_participation"].value > 0.0


def test_anomaly_assessment_detects_suspected_abnormal_activity() -> None:
    noisy = OnchainNormalizer().normalize(
        (
            AddressSnapshot(**base("sybil", 1), active_addresses=100, sybil_ratio=0.8, bot_ratio=0.7),
            TransactionSnapshot(
                **base("wash", 1),
                transaction_count=1_000,
                adjusted_transaction_count=100,
                gross_volume=1000,
                adjusted_volume=100,
                low_value_ratio=0.8,
                repeated_pattern_ratio=0.7,
                gas_anomaly_ratio=0.6,
            ),
            CapitalFlowSnapshot(**base("circular", 1), inflow=100, outflow=100, circular_flow_ratio=0.75),
            BridgeFlowSnapshot(**base("bridge-loop", 1), inflow=100, outflow=100, pass_through_ratio=0.8),
        )
    )

    assessment = OnchainAnomalyModel().assess(noisy)

    assert assessment.level == "detected"
    assert assessment.sybil_risk > 0.0
    assert assessment.wash_activity_risk > 0.0
    assert assessment.circular_flow_risk > 0.0
    assert assessment.bridge_pass_through_risk > 0.0


def test_confidence_increases_with_richer_fresher_onchain_evidence() -> None:
    sparse = OnchainNormalizer().normalize((AddressSnapshot(**base("single", 1), active_addresses=100),))
    rich = dataset()

    sparse_confidence = OnchainConfidenceModel().calculate(sparse)
    rich_confidence = OnchainConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.completeness > sparse_confidence.completeness


def test_cross_engine_context_consumption_affects_alignment() -> None:
    related = Intelligence(
        id="protocol-fixture",
        project="api3",
        engine="protocol-intelligence",
        signals=(),
        evidence=(),
        observations=(),
        insights=(),
        confidence=Confidence.calculate(completeness=1.0, evidence_quality=1.0, freshness=1.0, uncertainty=0.0),
        generated_at=NOW,
    )

    normalized = OnchainNormalizer().normalize(onchain_records(), (related,))

    assert normalized.cross_engine_alignment > 0.0


def test_analyzer_detects_positive_and_negative_patterns() -> None:
    analysis = OnchainAnalyzer().analyze(dataset())

    assert analysis.indicators
    assert analysis.health in {"healthy", "mixed", "deteriorating"}
    assert analysis.capital_flow_trend in {"inflow", "outflow", "stable", "unknown"}
    assert analysis.address_trend in {"growing", "shrinking", "stable", "unknown"}
    assert analysis.strengths


def test_onchain_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"onchain_records": list(onchain_records())})
    engine = OnchainIntelligenceEngine()

    collected = engine.collect(context)
    analysis = engine.analyze(context, collected)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "onchain-intelligence"
    assert intelligence.project == "api3"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("health") in {"healthy", "mixed", "deteriorating"}


def test_onchain_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "onchain-intelligence"
    assert "onchain-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_onchain_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  onchain-intelligence: true
configuration: {}
load_order:
  - onchain-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.onchain:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["onchain-intelligence"]


def test_pipeline_executes_onchain_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  onchain-intelligence: true
configuration: {}
load_order:
  - onchain-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.onchain:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"onchain_records": list(onchain_records())})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "onchain-intelligence"
    assert "onchain:intelligence:execute" in result.events
