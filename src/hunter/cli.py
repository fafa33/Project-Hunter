from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hunter.acquisition import (
    AcquisitionConfig,
    FileAcquisitionRepository,
    InMemoryAcquisitionRepository,
    ProviderConfig,
    ProviderRegistry,
    load_acquisition_config,
)
from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.acquisition.models import AcquisitionRequest, NormalizedEvidence, RawEvidence
from hunter.acquisition.pipeline import AcquisitionPipeline
from hunter.acquisition.project_identifiers import (
    GitHubRepositoryResolution,
    ProjectIdentifierResolution,
    coingecko_sync_ids,
    coingecko_target_map,
    defillama_sync_ids,
    defillama_target_map,
    github_sync_ids,
    github_target_map,
    load_project_identifiers,
    resolve_configured_identifiers,
    resolve_defillama_identifiers,
    resolve_github_identifiers,
)
from hunter.acquisition.providers import (
    CoinGeckoEvidenceNormalizer,
    CoinGeckoProvider,
    CoinGeckoProviderConfig,
    DefiLlamaEvidenceNormalizer,
    DefiLlamaProvider,
    DefiLlamaProviderConfig,
    GitHubEvidenceNormalizer,
    GitHubProvider,
    GitHubProviderConfig,
)
from hunter.acquisition.providers.coingecko import CoinGeckoHTTPError
from hunter.acquisition.providers.defillama import DefiLlamaHTTPError
from hunter.acquisition.providers.github import GitHubHTTPError
from hunter.acquisition.validator import EvidenceAcquisitionValidator
from hunter.auth import AuthRegistry, load_auth_config
from hunter.auth.providers import provider_capabilities
from hunter.automation import AutomationJobRunner, AutomationScheduler, load_automation_config
from hunter.backtest import BacktestingCalibrationEngine, BacktestRepository, compare_backtests
from hunter.committee import (
    InvestmentCommitteeEngine,
    load_investment_committee_config,
)
from hunter.committee.ranking import rank_investment_committee
from hunter.dashboard import DashboardDataProvider, HtmlDashboardRenderer, load_dashboard_config
from hunter.dashboard.exceptions import DashboardPersistenceError
from hunter.data_ops import (
    DATA_OPS_JOB_IDS,
    data_ops_failures,
    data_ops_history,
    data_ops_status,
    install_data_ops_jobs,
    run_data_ops_now,
)
from hunter.economic import EconomicDependencyGraphEngine, EconomicGraphRepository
from hunter.economic.engine import economic_path
from hunter.explainability import DecisionAuditRenderer, DecisionExplainabilityEngine
from hunter.graph import TechnologyDependencyGraphEngine, TechnologyGraphRepository
from hunter.graph.engine import dependency_path
from hunter.historical import (
    HistoricalPointInTimeValidationEngine,
    HistoricalValidationRenderer,
    HistoricalValidationRepository,
    load_historical_validation_config,
)
from hunter.historical.snapshot_builder import HistoricalSnapshotBuilder
from hunter.historical_acquisition import (
    CoinGeckoHistoricalProvider,
    DefiLlamaHistoricalProvider,
    GitHubHistoricalActivityProvider,
    GovernanceArchiveProvider,
    HistoricalAcquisitionPipeline,
    HistoricalEvidenceRepository,
    HistoricalRSSAnnouncementsProvider,
    InternetArchiveSnapshotProvider,
    ReconstructedHistoricalEvidenceProvider,
    future_provider_metadata,
)
from hunter.macro import MacroIntelligenceEvidenceEngine, MacroProviderRegistry, MacroRepository, load_macro_config
from hunter.macro.engine import REQUIRED_MACRO_METRICS
from hunter.market_validation import (
    MarketValidationRenderer,
    MarketValidationRunner,
    compare_runs,
    load_market_validation_config,
)
from hunter.market_validation.acquisition_sources import acquisition_engine_sources, engine_coverage
from hunter.market_validation.evidence import EvidenceCoverageAnalyzer, EvidenceReportRenderer, engine_classification
from hunter.market_validation.models import EngineValidationSource, MarketValidationRun
from hunter.market_validation.repositories import InMemoryMarketValidationRunRepository
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.narrative import (
    NarrativeEvidenceNormalizer,
    NarrativeEvidenceValidator,
    NarrativeProvider,
    load_narrative_config,
    narrative_statistics,
)
from hunter.narrative.configuration import FUTURE_NARRATIVE_PROVIDERS, SUPPORTED_NARRATIVE_PROVIDERS
from hunter.narrative.discovery import (
    NarrativeSourceDiscoveryEngine,
    NarrativeSourceDiscoveryRepository,
    configured_project_ids,
    source_coverage,
)
from hunter.narrative.provider import NarrativeProviderConfig
from hunter.necessity import TechnologyNecessityEngine, TechnologyNecessityInputSet, load_technology_graph_config
from hunter.necessity.ranking import rank_necessity_assessments
from hunter.onchain import (
    CapitalFlowEngine,
    OnChainAutomationManager,
    OnChainRepository,
    SurfaceRegistry,
    load_onchain_config,
)
from hunter.onchain.automation import worker_startup_command
from hunter.opportunity.ranking import rank_opportunities
from hunter.patterns.ranking import rank_pattern_assessments
from hunter.persistence.records import EvidenceRecord, SnapshotRecord
from hunter.persistence.sql import SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.probability.ranking import rank_probability_assessments
from hunter.scenario import ScenarioRepository, ScenarioSimulationEngine, compare_scenarios
from hunter.timing import (
    OpportunityTimingEvidenceEngine,
    TimingAssessment,
    TimingRepository,
    current_timing_dependencies,
)
from hunter.weights import WeightEngine, WeightReportRenderer, load_weight_config, recommend_weight_adjustments
from hunter.whale import (
    REQUIRED_WHALE_METRICS,
    WhaleIntelligenceEvidenceEngine,
    WhaleProviderRegistry,
    WhaleRepository,
    load_whale_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter")
    parser.add_argument("--config", default="configs/automation.yaml")
    sub = parser.add_subparsers(dest="command")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("project_slug", nargs="?")
    discover = sub.add_parser("discover")
    discover.add_argument("project_slug", nargs="?")
    validate = sub.add_parser("validate")
    validate.add_argument("project_slug", nargs="?")
    whales = sub.add_parser("whales")
    whales.add_argument("project_slug", nargs="?")
    reports = sub.add_parser("reports")
    reports.add_argument("project_slug", nargs="?")
    backtesting = sub.add_parser("backtesting")
    backtesting.add_argument("project_slug", nargs="?")
    alerts = sub.add_parser("alerts")
    alerts.add_argument("project_slug", nargs="?")
    automation = sub.add_parser("automation")
    automation_sub = automation.add_subparsers(dest="automation_command")
    start = automation_sub.add_parser("start")
    start.add_argument("--max-iterations", type=int, default=1)
    automation_sub.add_parser("status")
    automation_sub.add_parser("list-jobs")
    show_job = automation_sub.add_parser("show-job")
    show_job.add_argument("job")
    run_once = automation_sub.add_parser("run-once")
    run_once.add_argument("job")
    cancel = automation_sub.add_parser("cancel")
    cancel.add_argument("run_id")
    data_ops = sub.add_parser("data-ops")
    data_ops.add_argument("--automation-config", default="configs/automation.yaml")
    data_ops_sub = data_ops.add_subparsers(dest="data_ops_command")
    data_ops_sub.add_parser("install")
    data_ops_sub.add_parser("status")
    data_ops_sub.add_parser("run-now")
    data_ops_sub.add_parser("history")
    data_ops_sub.add_parser("failures")
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--dashboard-config", default="configs/dashboard.yaml")
    dashboard_sub = dashboard.add_subparsers(dest="dashboard_command")
    build_dashboard = dashboard_sub.add_parser("build")
    build_dashboard.add_argument("--output")
    build_dashboard.add_argument("--sqlite-path")
    market_validation = sub.add_parser("market-validation")
    market_validation.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    market_validation_sub = market_validation.add_subparsers(dest="market_validation_command")
    market_validation_sub.add_parser("run")
    market_validation_sub.add_parser("report")
    market_compare = market_validation_sub.add_parser("compare")
    market_compare.add_argument("run_a")
    market_compare.add_argument("run_b")
    market_validation_sub.add_parser("history")
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    evidence_sub = evidence.add_subparsers(dest="evidence_command")
    evidence_sub.add_parser("status")
    evidence_sub.add_parser("coverage")
    evidence_sub.add_parser("validate")
    evidence_sub.add_parser("sources")
    evidence_sub.add_parser("missing")
    acquisition = sub.add_parser("acquisition")
    acquisition.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    acquisition_sub = acquisition.add_subparsers(dest="acquisition_command")
    acquisition_sub.add_parser("status")
    acquisition_sub.add_parser("providers")
    acquisition_sub.add_parser("validate")
    acquisition_sub.add_parser("sync")
    acquisition_sub.add_parser("history")
    acquisition_sub.add_parser("health")
    auth = sub.add_parser("auth")
    auth.add_argument("--providers-config", default="configs/providers.yaml")
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_sub.add_parser("status")
    auth_sub.add_parser("validate")
    auth_sub.add_parser("providers")
    auth_sub.add_parser("quota")
    auth_sub.add_parser("doctor")
    coingecko = sub.add_parser("coingecko")
    coingecko.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    coingecko.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    coingecko.add_argument("--project-identifiers-config", default="configs/project_identifiers.yaml")
    coingecko_sub = coingecko.add_subparsers(dest="coingecko_command")
    coingecko_sub.add_parser("sync")
    coingecko_sub.add_parser("resume")
    coingecko_sub.add_parser("universe")
    coingecko_sub.add_parser("unresolved")
    coingecko_sub.add_parser("resolve")
    coingecko_sub.add_parser("health")
    coingecko_sub.add_parser("statistics")
    coingecko_sub.add_parser("pending")
    coingecko_sub.add_parser("status")
    coingecko_sub.add_parser("validate")
    defillama = sub.add_parser("defillama")
    defillama.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    defillama.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    defillama.add_argument("--project-identifiers-config", default="configs/project_identifiers.yaml")
    defillama_sub = defillama.add_subparsers(dest="defillama_command")
    defillama_sub.add_parser("sync")
    defillama_sub.add_parser("status")
    defillama_sub.add_parser("validate")
    defillama_sub.add_parser("unresolved")
    defillama_sub.add_parser("resolve")
    protocol = sub.add_parser("protocol")
    protocol.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    protocol.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    protocol.add_argument("--project-identifiers-config", default="configs/project_identifiers.yaml")
    protocol_sub = protocol.add_subparsers(dest="protocol_command")
    protocol_sub.add_parser("sync")
    protocol_sub.add_parser("coverage")
    protocol_sub.add_parser("validate")
    protocol_sub.add_parser("report")
    protocol_explain = protocol_sub.add_parser("explain")
    protocol_explain.add_argument("project", nargs="?")
    github = sub.add_parser("github")
    github.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    github.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    github.add_argument("--project-identifiers-config", default="configs/project_identifiers.yaml")
    github_sub = github.add_subparsers(dest="github_command")
    github_sub.add_parser("sync")
    github_sub.add_parser("status")
    github_sub.add_parser("validate")
    github_sub.add_parser("resolve")
    github_sub.add_parser("unresolved")
    github_sub.add_parser("statistics")
    developer = sub.add_parser("developer")
    developer.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    developer.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    developer.add_argument("--project-identifiers-config", default="configs/project_identifiers.yaml")
    developer_sub = developer.add_subparsers(dest="developer_command")
    developer_sub.add_parser("sync")
    developer_sub.add_parser("coverage")
    developer_sub.add_parser("report")
    developer_explain = developer_sub.add_parser("explain")
    developer_explain.add_argument("project", nargs="?")
    engines = sub.add_parser("engines")
    engines.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    engines_sub = engines.add_subparsers(dest="engines_command")
    engines_sub.add_parser("status")
    engines_sub.add_parser("coverage")
    engines_sub.add_parser("validate")
    macro = sub.add_parser("macro")
    macro.add_argument("--macro-config", default="configs/macro.yaml")
    macro_sub = macro.add_subparsers(dest="macro_command")
    macro_sub.add_parser("status")
    macro_sub.add_parser("providers")
    macro_sub.add_parser("sync")
    macro_sub.add_parser("validate")
    macro_sub.add_parser("coverage")
    macro_sub.add_parser("missing")
    macro_sub.add_parser("failures")
    macro_sub.add_parser("report")
    macro_explain = macro_sub.add_parser("explain")
    macro_explain.add_argument("metric", nargs="?")
    macro_sub.add_parser("history")
    whale = sub.add_parser("whale")
    whale.add_argument("--whale-config", default="configs/whale.yaml")
    whale_sub = whale.add_subparsers(dest="whale_command")
    whale_sub.add_parser("status")
    whale_sub.add_parser("providers")
    whale_sub.add_parser("sync")
    whale_sub.add_parser("validate")
    whale_sub.add_parser("coverage")
    whale_sub.add_parser("report")
    whale_explain = whale_sub.add_parser("explain")
    whale_explain.add_argument("metric", nargs="?")
    whale_sub.add_parser("history")
    whale_sub.add_parser("failures")
    onchain = sub.add_parser("onchain")
    onchain.add_argument("--onchain-config", default="configs/onchain.yaml")
    onchain.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    onchain_sub = onchain.add_subparsers(dest="onchain_command")
    onchain_registry = onchain_sub.add_parser("registry")
    onchain_registry_sub = onchain_registry.add_subparsers(dest="onchain_registry_command")
    onchain_registry_sub.add_parser("validate")
    onchain_registry_sub.add_parser("coverage")
    onchain_sync = onchain_sub.add_parser("sync")
    onchain_sync.add_argument("project", nargs="?")
    onchain_sub.add_parser("coverage")
    onchain_report = onchain_sub.add_parser("report")
    onchain_report.add_argument("project", nargs="?")
    onchain_explain = onchain_sub.add_parser("explain")
    onchain_explain.add_argument("project")
    onchain_snapshots = onchain_sub.add_parser("snapshots")
    onchain_snapshots.add_argument("project")
    onchain_providers = onchain_sub.add_parser("providers")
    onchain_providers_sub = onchain_providers.add_subparsers(dest="onchain_providers_command")
    providers_check = onchain_providers_sub.add_parser("check")
    providers_check.add_argument("chain", nargs="?")
    onchain_providers_sub.add_parser("status")
    providers_reset = onchain_providers_sub.add_parser("reset-cooldown")
    providers_reset.add_argument("chain")
    onchain_automation = onchain_sub.add_parser("automation")
    onchain_automation_sub = onchain_automation.add_subparsers(dest="onchain_automation_command")
    onchain_automation_sub.add_parser("install")
    onchain_automation_sub.add_parser("status")
    onchain_automation_sub.add_parser("run-now")
    onchain_automation_sub.add_parser("pause")
    onchain_automation_sub.add_parser("resume")
    onchain_automation_sub.add_parser("failures")
    capital_flow = sub.add_parser("capital-flow")
    capital_flow.add_argument("--onchain-config", default="configs/onchain.yaml")
    capital_flow.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    capital_flow_sub = capital_flow.add_subparsers(dest="capital_flow_command")
    capital_flow_sub.add_parser("coverage")
    capital_flow_sub.add_parser("report")
    capital_flow_explain = capital_flow_sub.add_parser("explain")
    capital_flow_explain.add_argument("project")
    narrative = sub.add_parser("narrative")
    narrative.add_argument("--acquisition-config", default="configs/acquisition.yaml")
    narrative.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    narrative.add_argument("--narrative-config", default="configs/narrative_sources.yaml")
    narrative_sub = narrative.add_subparsers(dest="narrative_command")
    narrative_sub.add_parser("sync")
    narrative_sub.add_parser("resume")
    narrative_sub.add_parser("status")
    narrative_sub.add_parser("validate")
    narrative_sub.add_parser("statistics")
    narrative_sub.add_parser("coverage")
    narrative_sub.add_parser("missing")
    narrative_sub.add_parser("freshness")
    narrative_sub.add_parser("report")
    narrative_explain = narrative_sub.add_parser("explain")
    narrative_explain.add_argument("project")
    narrative_sub.add_parser("sources")
    narrative_sub.add_parser("providers")
    sources = sub.add_parser("sources")
    sources.add_argument("--market-validation-config", default="configs/market_validation.yaml")
    sources_sub = sources.add_subparsers(dest="sources_command")
    sources_sub.add_parser("discover")
    sources_sub.add_parser("validate")
    sources_sub.add_parser("status")
    sources_sub.add_parser("report")
    sources_sub.add_parser("coverage")
    sources_sub.add_parser("unresolved")
    sources_sub.add_parser("history")
    graph = sub.add_parser("graph")
    graph_sub = graph.add_subparsers(dest="graph_command")
    graph_sub.add_parser("build")
    graph_sub.add_parser("validate")
    graph_sub.add_parser("status")
    graph_sub.add_parser("report")
    graph_sub.add_parser("coverage")
    graph_explain = graph_sub.add_parser("explain")
    graph_explain.add_argument("project")
    graph_path = graph_sub.add_parser("path")
    graph_path.add_argument("project_a")
    graph_path.add_argument("project_b")
    graph_sub.add_parser("centrality")
    graph_sub.add_parser("critical")
    technology = sub.add_parser("technology")
    technology_sub = technology.add_subparsers(dest="technology_command")
    technology_sub.add_parser("coverage")
    technology_sub.add_parser("report")
    technology_sub.add_parser("build")
    technology_explain = technology_sub.add_parser("explain")
    technology_explain.add_argument("project")
    necessity = sub.add_parser("necessity")
    necessity_sub = necessity.add_subparsers(dest="necessity_command")
    necessity_sub.add_parser("coverage")
    economic = sub.add_parser("economic")
    economic_sub = economic.add_subparsers(dest="economic_command")
    economic_sub.add_parser("build")
    economic_sub.add_parser("validate")
    economic_sub.add_parser("status")
    economic_sub.add_parser("report")
    economic_sub.add_parser("coverage")
    economic_explain = economic_sub.add_parser("explain")
    economic_explain.add_argument("project")
    economic_path_parser = economic_sub.add_parser("path")
    economic_path_parser.add_argument("project_a")
    economic_path_parser.add_argument("project_b")
    economic_sub.add_parser("centrality")
    economic_sub.add_parser("moat")
    scenario = sub.add_parser("scenario")
    scenario_sub = scenario.add_subparsers(dest="scenario_command")
    scenario_sub.add_parser("run")
    scenario_sub.add_parser("status")
    scenario_sub.add_parser("report")
    scenario_sub.add_parser("explain")
    scenario_sub.add_parser("compare")
    scenario_sub.add_parser("history")
    scenario_sub.add_parser("coverage")
    backtest = sub.add_parser("backtest")
    backtest_sub = backtest.add_subparsers(dest="backtest_command")
    backtest_sub.add_parser("run")
    backtest_sub.add_parser("report")
    backtest_sub.add_parser("history")
    backtest_sub.add_parser("compare")
    calibration = sub.add_parser("calibration")
    calibration_sub = calibration.add_subparsers(dest="calibration_command")
    calibration_sub.add_parser("report")
    calibration_sub.add_parser("coverage")
    calibration_sub.add_parser("engines")
    replay = sub.add_parser("replay")
    replay.add_argument("--historical-config", default="configs/historical_validation.yaml")
    replay_sub = replay.add_subparsers(dest="replay_command")
    replay_sub.add_parser("report")
    replay_compare = replay_sub.add_parser("compare")
    replay_compare.add_argument("run_a", nargs="?")
    replay_compare.add_argument("run_b", nargs="?")
    replay_explain = replay_sub.add_parser("explain")
    replay_explain.add_argument("case_id", nargs="?")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--historical-config", default="configs/historical_validation.yaml")
    weights = sub.add_parser("weights")
    weights.add_argument("--weights-config", default="configs/weights.yaml")
    weights_sub = weights.add_subparsers(dest="weights_command")
    weights_sub.add_parser("status")
    weights_sub.add_parser("validate")
    weights_sub.add_parser("report")
    weights_sub.add_parser("recommend")
    weights_sub.add_parser("activate")
    timing = sub.add_parser("timing")
    timing_sub = timing.add_subparsers(dest="timing_command")
    timing_sub.add_parser("status")
    timing_sub.add_parser("validate")
    timing_sub.add_parser("report")
    timing_explain = timing_sub.add_parser("explain")
    timing_explain.add_argument("project")
    timing_sub.add_parser("coverage")
    timing_sub.add_parser("freshness")
    timing_sub.add_parser("rebuild-status")
    timing_sub.add_parser("dependencies")
    timing_sub.add_parser("sync")
    timing_sub.add_parser("history")
    timing_compare = timing_sub.add_parser("compare")
    timing_compare.add_argument("project_a")
    timing_compare.add_argument("project_b")
    historical = sub.add_parser("historical")
    historical.add_argument("--historical-config", default="configs/historical_validation.yaml")
    historical_sub = historical.add_subparsers(dest="historical_command")
    historical_sub.add_parser("cases")
    historical_build = historical_sub.add_parser("build")
    historical_build.add_argument("project", nargs="?")
    historical_replay = historical_sub.add_parser("replay")
    historical_replay.add_argument("case_id", nargs="?")
    historical_sub.add_parser("outcomes")
    historical_sub.add_parser("evaluate")
    historical_report = historical_sub.add_parser("report")
    historical_report.add_argument("case_id", nargs="?")
    historical_compare = historical_sub.add_parser("compare")
    historical_compare.add_argument("case_a")
    historical_compare.add_argument("case_b")
    historical_sub.add_parser("leakage-check")
    historical_sub.add_parser("survivorship-check")
    historical_sub.add_parser("coverage")
    historical_sub.add_parser("sync")
    historical_sub.add_parser("expand")
    historical_sub.add_parser("complete")
    historical_sub.add_parser("progress")
    historical_sub.add_parser("gaps")
    historical_sub.add_parser("unresolved")
    historical_sub.add_parser("summary")
    historical_sub.add_parser("status")
    historical_sub.add_parser("validate")
    historical_sub.add_parser("providers")
    historical_sub.add_parser("statistics")
    historical_sub.add_parser("calibration")
    historical_sub.add_parser("engines")
    historical_sub.add_parser("challenges")
    historical_acquisition = sub.add_parser("historical-acquisition")
    historical_acquisition.add_argument("--historical-config", default="configs/historical_validation.yaml")
    historical_acquisition_sub = historical_acquisition.add_subparsers(dest="historical_acquisition_command")
    historical_acquisition_sub.add_parser("sync")
    historical_acquisition_sub.add_parser("coverage")
    historical_acquisition_sub.add_parser("report")
    historical_acquisition_sub.add_parser("validate")
    historical_acquisition_explain = historical_acquisition_sub.add_parser("explain")
    historical_acquisition_explain.add_argument("project", nargs="?")
    explain = sub.add_parser("explain")
    explain.add_argument("explain_args", nargs="*")
    committee = sub.add_parser("committee")
    committee.add_argument("--committee-config", default="configs/investment_committee.yaml")
    committee_sub = committee.add_subparsers(dest="committee_command")
    committee_evaluate = committee_sub.add_parser("evaluate")
    committee_evaluate.add_argument("project_slug", nargs="?")
    committee_report = committee_sub.add_parser("report")
    committee_report.add_argument("project_slug", nargs="?")
    committee_sub.add_parser("ranking")
    committee_sub.add_parser("champion")
    committee_history = committee_sub.add_parser("history")
    committee_history.add_argument("project_slug", nargs="?")
    rank = sub.add_parser("rank")
    rank.add_argument(
        "--sort",
        choices=(
            "opportunity",
            "conviction",
            "probability",
            "robustness",
            "consensus",
            "similarity",
            "historical",
            "pattern",
            "necessity",
            "gap",
            "rotation",
            "dependency",
            "committee",
            "committee-confidence",
            "evidence-robustness",
            "thesis-fragility",
        ),
        default="opportunity",
    )
    args = parser.parse_args(argv)
    if args.command in {"analyze", "discover", "validate", "whales", "reports", "backtesting", "alerts"}:
        print(f"{args.command} validation command ready for {getattr(args, 'project_slug', None) or 'all projects'}")
        return 0
    if args.command == "rank":
        if args.sort in {"committee", "committee-confidence", "evidence-robustness", "thesis-fragility"}:
            rank_investment_committee((), sort=args.sort)
        elif args.sort in {"probability", "robustness", "consensus"}:
            rank_probability_assessments((), sort=args.sort)
        elif args.sort in {"similarity", "historical", "pattern"}:
            rank_pattern_assessments((), sort=args.sort)
        elif args.sort in {"necessity", "gap", "rotation", "dependency"}:
            rank_necessity_assessments((), sort=args.sort)
        else:
            rank_opportunities((), sort=args.sort)
        return 0
    if args.command == "dashboard":
        return _dashboard(args)
    if args.command == "data-ops":
        return _data_ops(args)
    if args.command == "market-validation":
        return _market_validation(args)
    if args.command == "evidence":
        return _evidence(args)
    if args.command == "acquisition":
        return _acquisition(args)
    if args.command == "auth":
        return _auth(args)
    if args.command == "coingecko":
        return _coingecko(args)
    if args.command == "defillama":
        return _defillama(args)
    if args.command == "protocol":
        return _protocol(args)
    if args.command == "github":
        return _github(args)
    if args.command == "developer":
        return _developer(args)
    if args.command == "engines":
        return _engines(args)
    if args.command == "macro":
        return _macro(args)
    if args.command == "whale":
        return _whale(args)
    if args.command == "onchain":
        return _onchain(args)
    if args.command == "capital-flow":
        return _capital_flow(args)
    if args.command == "narrative":
        return _narrative(args)
    if args.command == "sources":
        return _sources(args)
    if args.command == "graph":
        return _graph(args)
    if args.command == "technology":
        return _technology(args)
    if args.command == "necessity":
        return _necessity(args)
    if args.command == "economic":
        return _economic(args)
    if args.command == "scenario":
        return _scenario(args)
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "calibration":
        return _calibration(args)
    if args.command == "replay":
        return _replay(args)
    if args.command == "benchmark":
        return _benchmark(args)
    if args.command == "weights":
        return _weights(args)
    if args.command == "timing":
        return _timing(args)
    if args.command == "historical":
        return _historical(args)
    if args.command == "historical-acquisition":
        return _historical_acquisition(args)
    if args.command == "explain":
        return _explain(args)
    if args.command == "committee":
        return _committee(args)
    if args.command != "automation":
        parser.print_help()
        return 1
    config = load_automation_config(Path(args.config))
    runner = AutomationJobRunner()
    scheduler = AutomationScheduler(config.jobs, runner, polling_interval_seconds=config.polling_interval_seconds)
    if args.automation_command == "list-jobs":
        for job in config.jobs:
            print(f"{job.job_id}\t{job.name}\t{'enabled' if job.enabled else 'disabled'}")
        return 0
    if args.automation_command == "show-job":
        job = _job(config.jobs, args.job)
        print(job)
        return 0
    if args.automation_command == "run-once":
        run = runner.run_once(_job(config.jobs, args.job))
        print(f"{run.automation_run_id}\t{run.status}")
        return 0 if run.status in {"succeeded", "partial"} else 2
    if args.automation_command == "start":
        scheduler.run_loop(max_iterations=args.max_iterations)
        print("scheduler started")
        return 0
    if args.automation_command == "status":
        status = scheduler.status()
        print(f"jobs={len(status.jobs)} active_runs={len(status.active_runs)} events={len(status.events)}")
        return 0
    if args.automation_command == "cancel":
        try:
            run = runner.cancel_by_id(args.run_id, jobs=config.jobs)
        except LookupError as exc:
            print(str(exc))
            return 2
        print(f"{run.automation_run_id}\t{run.status}")
        return 0
    automation.print_help()
    return 1


def _dashboard(args: object) -> int:
    config = load_dashboard_config(Path(args.dashboard_config))
    sqlite_path = args.sqlite_path or config.sqlite_path
    if sqlite_path is None:
        print("Dashboard build requires --sqlite-path or dashboard sqlite_path")
        return 2
    output = Path(args.output or config.output_path)
    engine = create_sqlite_engine(sqlite_path)
    create_schema(engine)
    with UnitOfWork(SessionFactory(engine)) as uow:
        if uow.repositories is None:
            raise DashboardPersistenceError("UnitOfWork did not expose repositories")
        view = DashboardDataProvider(uow.repositories, config).build()
    output.write_text(HtmlDashboardRenderer().render(view))
    print(str(output))
    return 0


def _data_ops(args: object) -> int:
    command = getattr(args, "data_ops_command", None)
    config_path = Path(args.automation_config)
    if command == "install":
        jobs = install_data_ops_jobs(config_path)
        print(f"installed={len(jobs)} jobs={','.join(jobs)}")
        return 0
    if command == "run-now":
        before = _data_ops_coverage_line()
        runs = run_data_ops_now(config_path)
        after = _data_ops_coverage_line()
        counts = Counter(run.status for run in runs)
        print(
            f"runs={len(runs)} succeeded={counts['succeeded']} partial={counts['partial']} "
            f"failed={counts['failed']} blocked={counts['blocked']} coverage_before={before} coverage_after={after}"
        )
        return 0 if counts["failed"] == 0 and counts["blocked"] == 0 else 2
    if command == "status":
        status = data_ops_status(config_path)
        latest = status["latest_by_job"]
        print(f"jobs={status['jobs']} automation_runs={status['runs']} detail_runs={len(status['details'])}")
        for job_id in DATA_OPS_JOB_IDS:
            run = latest.get(job_id)
            print(f"{job_id}\t{run.status if run else 'never'}")
        return 0
    if command == "history":
        for run in data_ops_history():
            print(
                f"{run.finished_at.isoformat()}\t{run.job_id}\t{run.status}\tduration={run.duration_seconds}"
                f"\taccepted={run.records_accepted}\trejected={run.records_rejected}"
            )
        return 0
    if command == "failures":
        failures = data_ops_failures()
        for run in failures:
            print(f"{run.finished_at.isoformat()}\t{run.job_id}\t{run.status}\t{run.error}")
        if not failures:
            print("failures=0")
        return 0 if not failures else 2
    print("data-ops command required")
    return 1


def _data_ops_coverage_line() -> str:
    config = load_market_validation_config(Path("configs/market_validation.yaml"))
    repository = FileAcquisitionRepository()
    executor = EvidenceBackedProjectExecutor(
        config.effective_at,
        acquisition_engine_sources(repository, as_of=config.effective_at),
    )
    run = MarketValidationRunner(config, executor=executor).run()
    report = EvidenceCoverageAnalyzer().analyze(run)
    return f"{report.stats.coverage_percent:.2f}"


def _committee(args: object) -> int:
    load_investment_committee_config(Path(args.committee_config))
    command = getattr(args, "committee_command", None)
    project = getattr(args, "project_slug", None)
    if command in {"evaluate", "report", "ranking", "champion"}:
        stale = _stale_timing_message(TimingRepository())
        if stale is not None:
            print(stale)
            return 2
    if command == "evaluate":
        print(f"committee evaluation requested for {project or 'all projects'}")
        return 0
    if command == "report":
        print("No persisted committee assessment available")
        return 0
    if command == "ranking":
        InvestmentCommitteeEngine().select_champion(())
        print("No qualified candidate")
        return 0
    if command == "champion":
        snapshot, _ = InvestmentCommitteeEngine().select_champion(())
        print(snapshot.no_selection_reason or snapshot.selection_reason)
        return 0
    if command == "history":
        print(f"committee history for {project or 'all projects'}")
        return 0
    print("committee command required")
    return 1


def _market_validation(args: object) -> int:
    config = load_market_validation_config(Path(args.market_validation_config))
    repository = InMemoryMarketValidationRunRepository()
    renderer = MarketValidationRenderer()
    command = getattr(args, "market_validation_command", None)

    def run_validation() -> MarketValidationRun:
        stale = _stale_timing_message(TimingRepository())
        if stale is not None:
            raise RuntimeError(stale)
        sources = acquisition_engine_sources(FileAcquisitionRepository(), as_of=config.effective_at)
        executor = EvidenceBackedProjectExecutor(config.effective_at, sources)
        return repository.save(MarketValidationRunner(config, executor=executor).run())

    if command == "run":
        try:
            run = run_validation()
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print(f"{run.run_id}\tprojects={len(run.project_results)}")
        return 0
    if command == "report":
        try:
            run = run_validation()
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print(renderer.render_markdown(run))
        return 0
    if command == "compare":
        try:
            left = run_validation()
            right = run_validation()
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print(renderer.render_comparison_markdown(compare_runs(left, right)))
        return 0
    if command == "history":
        try:
            run = run_validation()
        except RuntimeError as exc:
            print(str(exc))
            return 2
        print(f"{run.run_id}\t{run.effective_at.isoformat()}")
        return 0
    print("market-validation command required")
    return 1


def _evidence(args: object) -> int:
    config = load_market_validation_config(Path(args.market_validation_config))
    repository = FileAcquisitionRepository()
    executor = EvidenceBackedProjectExecutor(
        config.effective_at,
        acquisition_engine_sources(repository, as_of=config.effective_at),
    )
    run = MarketValidationRunner(config, executor=executor).run()
    report = EvidenceCoverageAnalyzer().analyze(run)
    renderer = EvidenceReportRenderer()
    command = getattr(args, "evidence_command", None)
    if command == "status":
        print(renderer.render_status(report))
        return 0
    if command == "coverage":
        print(renderer.render_coverage(report))
        return 0
    if command == "validate":
        print(renderer.render_validate(report))
        return 0
    if command == "sources":
        print(renderer.render_sources(report))
        return 0
    if command == "missing":
        print(renderer.render_missing(report))
        return 0
    print("evidence command required")
    return 1


def _engines(args: object) -> int:
    config = load_market_validation_config(Path(args.market_validation_config))
    repository = FileAcquisitionRepository()
    sources = acquisition_engine_sources(repository, as_of=config.effective_at)
    executor = EvidenceBackedProjectExecutor(config.effective_at, sources)
    run = MarketValidationRunner(config, executor=executor).run()
    report = EvidenceCoverageAnalyzer().analyze(run)
    command = getattr(args, "engines_command", None)
    if command == "status":
        print(
            f"engines={report.engine_count} projects={report.project_count} "
            f"available={report.stats.available_engines} missing={report.stats.missing_engines} "
            f"coverage={report.stats.coverage_percent:.2f}"
        )
        return 0
    if command == "coverage":
        project_ids = tuple(project.project_id for project in config.project_universe)
        rows = engine_coverage(
            sources,
            project_ids=project_ids,
            engines=tuple(sorted({source.engine for values in sources.values() for source in values})),
        )
        for row in rows:
            print(
                f"{row.engine}\tavailable={row.available_projects}\tconfigured={row.configured_projects}"
                f"\tcoverage={row.coverage_percent:.2f}\tclassification={engine_classification(row.engine)}"
            )
        if not rows:
            print("no analytical acquisition evidence available")
        return 0
    if command == "validate":
        print(EvidenceReportRenderer().render_validate(report))
        return 0
    print("engines command required")
    return 1


def _macro(args: object) -> int:
    command = getattr(args, "macro_command", None)
    config = load_macro_config(Path(args.macro_config))
    repository = MacroRepository()
    snapshot = repository.latest_snapshot()
    if command == "status":
        print(
            f"enabled={config.enabled} providers={len(config.providers)} snapshots={len(repository.snapshots())} "
            f"latest={snapshot.snapshot_id if snapshot else '-'}"
        )
        statuses = _macro_metric_status(repository, config)
        for metric in REQUIRED_MACRO_METRICS:
            print(f"{metric}\t{statuses[metric]}")
        return 0
    if command == "providers":
        active = {provider.name for provider in MacroProviderRegistry(config.providers).providers()}
        for provider in config.providers:
            state = "enabled" if provider.name in active else "disabled"
            print(f"{provider.name}\t{state}\tmetrics={','.join(provider.metrics)}")
        return 0
    if command == "sync":
        snapshot = MacroIntelligenceEvidenceEngine(config=config, repository=repository).sync()
        print(
            f"{snapshot.snapshot_id}\tmetrics={len(snapshot.evidence)}\tcoverage="
            f"{round((len(snapshot.evidence) / len(REQUIRED_MACRO_METRICS)) * 100, 2):.2f}"
            f"\tconfidence={snapshot.macro_confidence:.4f}"
        )
        return 0
    if command == "validate":
        evidence = repository.evidence()
        invalid = tuple(item for item in evidence if item.validation_status != "VALID")
        print(f"evidence={len(evidence)} valid={len(evidence) - len(invalid)} invalid={len(invalid)}")
        return 0 if not invalid else 2
    if command == "coverage":
        available = len({item.metric.name for item in repository.evidence() if item.validation_status == "VALID"})
        coverage = round((available / len(REQUIRED_MACRO_METRICS)) * 100, 2)
        print(f"required={len(REQUIRED_MACRO_METRICS)} available={available} coverage={coverage:.2f}")
        return 0
    if command == "missing":
        statuses = _macro_metric_status(repository, config)
        for metric, status in statuses.items():
            if status != "AVAILABLE":
                print(f"{metric}\t{status}")
        return 0
    if command == "failures":
        for failure in repository.failures():
            print(
                f"{failure.occurred_at.isoformat()}\t{failure.provider}\t{failure.metric}\t"
                f"{_macro_failure_reason(failure.reason)}\t{failure.message}"
            )
        if not repository.failures():
            print("failures=0")
        return 0
    if command == "report":
        if snapshot is None:
            print("macro evidence unavailable")
            return 0
        print(_macro_snapshot_report(snapshot))
        return 0
    if command == "explain":
        if snapshot is None:
            print("macro evidence unavailable")
            return 0
        metric_filter = getattr(args, "metric", None)
        for evidence in snapshot.evidence:
            if metric_filter and evidence.metric.name != metric_filter:
                continue
            print(
                f"{evidence.metric.name}\tvalue={evidence.metric.value}\tnormalized={evidence.normalized_value:.4f}"
                f"\tprovider={evidence.metric.provider}\tconfidence={evidence.metric.confidence:.4f}"
                f"\tfreshness={evidence.metric.freshness:.4f}\tquality={snapshot.evidence_quality:.4f}"
                f"\tevidence_id={evidence.evidence_id}\trepository_id={evidence.repository_id}"
                f"\tvalidation={evidence.validation_status}\tsource={evidence.metric.source_url}"
            )
        return 0
    if command == "history":
        for item in repository.snapshots():
            print(
                f"{item.snapshot_id}\tgenerated={item.generated_at.isoformat()}\tmetrics={len(item.evidence)}"
                f"\tconfidence={item.macro_confidence:.4f}\tquality={item.evidence_quality:.4f}"
            )
        return 0
    print("macro command required")
    return 1


def _macro_metric_status(repository: MacroRepository, config: Any) -> dict[str, str]:
    evidence = repository.evidence()
    available = {item.metric.name for item in evidence if item.validation_status == "VALID"}
    latest_evidence: dict[str, Any] = {}
    for item in evidence:
        current = latest_evidence.get(item.metric.name)
        if current is None or item.metric.timestamp > current.metric.timestamp:
            latest_evidence[item.metric.name] = item
    failed = {failure.metric: _macro_failure_reason(failure.reason) for failure in repository.failures()}
    enabled = {metric for provider in config.providers if provider.enabled for metric in provider.metrics}
    configured = {metric for provider in config.providers for metric in provider.metrics}
    disabled = {metric for provider in config.providers if not provider.enabled for metric in provider.metrics}
    statuses = {}
    for metric in REQUIRED_MACRO_METRICS:
        if metric in available:
            statuses[metric] = "AVAILABLE"
        elif metric in latest_evidence and latest_evidence[metric].validation_status == "STALE":
            statuses[metric] = "STALE"
        elif metric in failed:
            statuses[metric] = failed[metric]
        elif metric in latest_evidence and "future_timestamp" in latest_evidence[metric].validation_errors:
            statuses[metric] = "SCHEMA_MISMATCH"
        elif metric in disabled and metric in {"bitcoin_etf_net_flows", "ethereum_etf_net_flows"}:
            statuses[metric] = "NO_PUBLIC_SOURCE"
        elif metric in disabled and metric not in enabled:
            statuses[metric] = "AUTH_REQUIRED"
        elif metric not in configured:
            statuses[metric] = "NO_PUBLIC_SOURCE"
        else:
            statuses[metric] = "MISCONFIGURED"
    return statuses


def _macro_failure_reason(reason: str) -> str:
    allowed = {
        "AVAILABLE",
        "AUTH_REQUIRED",
        "RATE_LIMITED",
        "SCHEMA_MISMATCH",
        "STALE",
        "PROVIDER_BLOCKED",
        "NO_PUBLIC_SOURCE",
        "MISCONFIGURED",
        "REQUEST_FAILED",
    }
    normalized = reason.upper()
    if normalized == "NO_PUBLIC_SOURCE":
        return "NO_DOCUMENTED_FREE_SOURCE"
    if normalized in allowed:
        return normalized
    if "ERRNO" in normalized or "NODENAME" in normalized or "NAME" in normalized:
        return "PROVIDER_BLOCKED"
    return "REQUEST_FAILED"


def _macro_snapshot_report(snapshot: Any) -> str:
    lines = [
        f"snapshot={snapshot.snapshot_id}",
        f"generated_at={snapshot.generated_at.isoformat()}",
        f"liquidity_score={snapshot.liquidity_score:.4f}",
        f"inflation_score={snapshot.inflation_score:.4f}",
        f"monetary_policy_score={snapshot.monetary_policy_score:.4f}",
        f"recession_probability={snapshot.recession_probability:.4f}",
        f"risk_on_score={snapshot.risk_on_score:.4f}",
        f"risk_off_score={snapshot.risk_off_score:.4f}",
        f"crypto_liquidity_score={snapshot.crypto_liquidity_score:.4f}",
        f"macro_confidence={snapshot.macro_confidence:.4f}",
        f"freshness={snapshot.freshness:.4f}",
        f"evidence_quality={snapshot.evidence_quality:.4f}",
    ]
    for evidence in snapshot.evidence:
        lines.append(
            f"{evidence.metric.name}\tvalue={evidence.metric.value}\tnormalized={evidence.normalized_value:.4f}"
            f"\tprovider={evidence.metric.provider}\tevidence_id={evidence.evidence_id}"
        )
    return "\n".join(lines)


def _whale(args: object) -> int:
    command = getattr(args, "whale_command", None)
    config = load_whale_config(Path(args.whale_config))
    repository = WhaleRepository()
    snapshot = repository.latest_snapshot()
    if command == "status":
        print(
            f"enabled={config.enabled} providers={len(config.providers)} snapshots={len(repository.snapshots())} "
            f"latest={snapshot.snapshot_id if snapshot else '-'}"
        )
        statuses = _whale_metric_status(repository, config)
        for metric in REQUIRED_WHALE_METRICS:
            print(f"{metric}\t{statuses[metric]}")
        return 0
    if command == "providers":
        active = {provider.name for provider in WhaleProviderRegistry(config.providers).providers()}
        for provider in config.providers:
            state = "enabled" if provider.name in active else "disabled"
            print(f"{provider.name}\t{state}\tmetrics={','.join(provider.metrics)}")
        return 0
    if command == "sync":
        snapshot = WhaleIntelligenceEvidenceEngine(config=config, repository=repository).sync()
        print(
            f"{snapshot.snapshot_id}\tmetrics={len(snapshot.evidence)}\tcoverage="
            f"{round((len({item.metric.name for item in snapshot.evidence}) / len(REQUIRED_WHALE_METRICS)) * 100, 2):.2f}"
            f"\tconfidence={snapshot.confidence:.4f}"
        )
        return 0
    if command == "validate":
        evidence = repository.evidence()
        invalid = tuple(item for item in evidence if item.validation_status != "VALID")
        print(f"evidence={len(evidence)} valid={len(evidence) - len(invalid)} invalid={len(invalid)}")
        return 0 if not invalid else 2
    if command == "coverage":
        available = len({item.metric.name for item in repository.evidence() if item.validation_status == "VALID"})
        coverage = round((available / len(REQUIRED_WHALE_METRICS)) * 100, 2)
        print(f"required={len(REQUIRED_WHALE_METRICS)} available={available} coverage={coverage:.2f}")
        return 0
    if command == "failures":
        for failure in repository.failures():
            print(
                f"{failure.occurred_at.isoformat()}\t{failure.provider}\t{failure.metric}\t"
                f"{_whale_failure_reason(failure.reason)}\t{failure.message}"
            )
        if not repository.failures():
            print("failures=0")
        return 0
    if command == "report":
        if snapshot is None:
            print("whale evidence unavailable")
            return 0
        print(_whale_snapshot_report(snapshot))
        return 0
    if command == "explain":
        if snapshot is None:
            print("whale evidence unavailable")
            return 0
        metric_filter = getattr(args, "metric", None)
        for evidence in snapshot.evidence:
            if metric_filter and evidence.metric.name != metric_filter:
                continue
            print(
                f"{evidence.metric.name}\tasset={evidence.metric.asset}\tvalue={evidence.metric.value}"
                f"\tnormalized={evidence.normalized_value:.4f}\tprovider={evidence.metric.provider}"
                f"\tconfidence={evidence.metric.confidence:.4f}\tfreshness={evidence.metric.freshness:.4f}"
                f"\tquality={snapshot.evidence_quality:.4f}\tevidence_id={evidence.evidence_id}"
                f"\trepository_id={evidence.repository_id}\tvalidation={evidence.validation_status}"
                f"\tsource={evidence.metric.source_url}"
            )
        return 0
    if command == "history":
        for item in repository.snapshots():
            print(
                f"{item.snapshot_id}\tgenerated={item.generated_at.isoformat()}\tmetrics={len(item.evidence)}"
                f"\tconfidence={item.confidence:.4f}\tquality={item.evidence_quality:.4f}"
            )
        return 0
    print("whale command required")
    return 1


def _whale_metric_status(repository: WhaleRepository, config: Any) -> dict[str, str]:
    evidence = repository.evidence()
    available = {item.metric.name for item in evidence if item.validation_status == "VALID"}
    stale = {item.metric.name for item in evidence if item.validation_status == "STALE"}
    failed = {failure.metric: _whale_failure_reason(failure.reason) for failure in repository.failures()}
    enabled = {metric for provider in config.providers if provider.enabled for metric in provider.metrics}
    configured = {metric for provider in config.providers for metric in provider.metrics}
    statuses = {}
    for metric in REQUIRED_WHALE_METRICS:
        if metric in available:
            statuses[metric] = "AVAILABLE"
        elif metric in stale:
            statuses[metric] = "STALE"
        elif metric in failed:
            statuses[metric] = failed[metric]
        elif metric not in configured:
            statuses[metric] = "NO_DOCUMENTED_FREE_SOURCE"
        elif metric not in enabled:
            statuses[metric] = "NO_DOCUMENTED_FREE_SOURCE"
        else:
            statuses[metric] = "MISCONFIGURED"
    return statuses


def _whale_failure_reason(reason: str) -> str:
    allowed = {
        "AUTH_REQUIRED",
        "RATE_LIMITED",
        "SCHEMA_MISMATCH",
        "STALE",
        "PROVIDER_BLOCKED",
        "NO_DOCUMENTED_FREE_SOURCE",
        "MISCONFIGURED",
        "REQUEST_FAILED",
        "ASSET_UNSUPPORTED",
    }
    normalized = reason.upper()
    if normalized in allowed:
        return normalized
    if "ERRNO" in normalized or "NODENAME" in normalized or "NAME" in normalized:
        return "PROVIDER_BLOCKED"
    return "REQUEST_FAILED"


def _whale_snapshot_report(snapshot: Any) -> str:
    lines = [
        f"snapshot={snapshot.snapshot_id}",
        f"generated_at={snapshot.generated_at.isoformat()}",
        f"whale_score={snapshot.whale_score:.4f}",
        f"accumulation_score={snapshot.accumulation_score:.4f}",
        f"distribution_score={snapshot.distribution_score:.4f}",
        f"exchange_pressure={snapshot.exchange_pressure:.4f}",
        f"smart_money_score={snapshot.smart_money_score:.4f}",
        f"stablecoin_pressure={snapshot.stablecoin_pressure:.4f}",
        f"institutional_score={snapshot.institutional_score:.4f}",
        f"market_participation={snapshot.market_participation:.4f}",
        f"confidence={snapshot.confidence:.4f}",
        f"freshness={snapshot.freshness:.4f}",
        f"evidence_quality={snapshot.evidence_quality:.4f}",
    ]
    for evidence in snapshot.evidence:
        lines.append(
            f"{evidence.metric.name}\tasset={evidence.metric.asset}\tvalue={evidence.metric.value}"
            f"\tnormalized={evidence.normalized_value:.4f}\tprovider={evidence.metric.provider}"
            f"\tevidence_id={evidence.evidence_id}"
        )
    return "\n".join(lines)


def _narrative(args: object) -> int:
    acquisition_config = load_acquisition_config(Path(args.acquisition_config))
    narrative_config = load_narrative_config(Path(args.narrative_config))
    repository = FileAcquisitionRepository()
    stats = narrative_statistics(repository)
    command = getattr(args, "narrative_command", None)
    if command == "status":
        state = "enabled" if narrative_config.enabled else "disabled"
        print(
            f"narrative={state} sources={len(narrative_config.sources)} raw={stats.raw} "
            f"normalized={stats.normalized} valid={stats.valid}"
        )
        return 0
    if command == "providers":
        for provider in SUPPORTED_NARRATIVE_PROVIDERS:
            configured = sum(1 for source in narrative_config.sources if source.provider == provider and source.enabled)
            print(f"{provider}\tsupported\tconfigured={configured}")
        for provider in FUTURE_NARRATIVE_PROVIDERS:
            print(f"{provider}\tfuture\tconfigured=0")
        return 0
    if command == "validate":
        unknown = tuple(
            source
            for source in narrative_config.sources
            if source.enabled and source.provider not in SUPPORTED_NARRATIVE_PROVIDERS
        )
        missing = tuple(source for source in narrative_config.sources if source.enabled and not source.project_id)
        malformed = tuple(source for source in narrative_config.sources if source.enabled and not source.url)
        print(
            f"sources={len(narrative_config.sources)} unknown_providers={len(unknown)} "
            f"missing_project={len(missing)} malformed_sources={len(malformed)}"
        )
        return 2 if unknown or missing or malformed else 0
    if command == "statistics":
        print(
            f"raw={stats.raw} normalized={stats.normalized} valid={stats.valid} duplicate={stats.duplicate} "
            f"stale={stats.stale} invalid={stats.invalid} projects={stats.projects} "
            f"providers={','.join(stats.providers) or 'none'}"
        )
        return 0
    if command == "coverage":
        project_ids = _market_project_ids(args)
        covered = _narrative_projects(repository)
        coverage = round((len(covered) / len(project_ids)) * 100, 2) if project_ids else 0.0
        print(f"projects={len(project_ids)} narrative_projects={len(covered)} coverage={coverage:.2f}")
        return 0
    if command == "missing":
        covered = _narrative_projects(repository)
        for project_id in _market_project_ids(args):
            if project_id not in covered:
                print(f"{project_id}\tMISSING_NARRATIVE")
        return 0
    if command == "freshness":
        latest = _latest_narrative_evidence(repository)
        for project_id in _market_project_ids(args):
            item = latest.get(project_id)
            if item is None:
                print(f"{project_id}\tmissing")
            else:
                validation = repository.validations[item.evidence_id]
                print(
                    f"{project_id}\t{item.retrieved_at.isoformat()}\tfreshness={validation.freshness:.4f}"
                    f"\tevidence_id={item.evidence_id}"
                )
        return 0
    if command == "report":
        latest = _latest_narrative_evidence(repository)
        for project_id in _market_project_ids(args):
            item = latest.get(project_id)
            if item is None:
                print(f"{project_id}\tMISSING_NARRATIVE")
                continue
            categories = ",".join(item.raw_metrics.get("evidence_categories", ())) or "-"
            print(
                f"{project_id}\tAVAILABLE\tprovider={item.raw_metrics.get('provider', '-')}"
                f"\tsource={item.raw_metrics.get('source', '-')}\tcategories={categories}"
                f"\ttimestamp={item.raw_metrics.get('timestamp', '-')}\tevidence_id={item.evidence_id}"
            )
        return 0
    if command == "explain":
        project_id = str(args.project)
        rows = _valid_narrative_evidence(repository).get(project_id, ())
        if not rows:
            print(f"{project_id}\tMISSING_NARRATIVE")
            return 2
        for item in rows:
            categories = ",".join(item.raw_metrics.get("evidence_categories", ())) or "-"
            print(
                f"{project_id}\ttitle={item.raw_metrics.get('title', '-')}\tprovider={item.raw_metrics.get('provider', '-')}"
                f"\tcategories={categories}\tconfidence={item.confidence:.4f}\tfreshness={item.freshness:.4f}"
                f"\tevidence_id={item.evidence_id}\trepository_id={item.repository_id}\turl={item.source_url}"
            )
        return 0
    if command == "sources":
        for source in sorted(
            narrative_config.sources, key=lambda item: (item.project_id, item.provider, item.source_id)
        ):
            print(
                f"{source.project_id}\t{source.provider}\t{source.source_id}\t{source.url}"
                f"\tenabled={source.enabled}\tcategories={','.join(source.categories) or '-'}"
            )
        return 0
    if command in {"sync", "resume"}:
        if not narrative_config.enabled:
            print("narrative provider not enabled")
            return 0
        provider = NarrativeProvider(NarrativeProviderConfig(sources=narrative_config.sources))
        pipeline = AcquisitionPipeline(
            normalizer=NarrativeEvidenceNormalizer(),
            validator=NarrativeEvidenceValidator(expired_after_days=narrative_config.expired_after_days),
            repository=repository,
            config=acquisition_config,
        )
        run = pipeline.sync(
            provider,
            AcquisitionRequest(
                domain="narrative",
                metric="narrative_item",
                target_id="configured-projects",
                requested_at=datetime.now(tz=UTC),
                mode="resume" if command == "resume" else "incremental",
                parameters={
                    "source_ids": tuple(source.source_id for source in narrative_config.sources if source.enabled)
                },
            ),
        )
        print(
            f"{run.run_id}\tsources={len(narrative_config.sources)} raw={run.raw_count} "
            f"normalized={run.normalized_count} valid={run.valid_count} duplicate={run.duplicate_count} "
            f"stale={run.stale_count} invalid={run.invalid_count}"
        )
        return 0
    print("narrative command required")
    return 1


def _sources(args: object) -> int:
    project_ids = configured_project_ids(Path(args.market_validation_config))
    repository = NarrativeSourceDiscoveryRepository()
    sources = repository.sources()
    command = getattr(args, "sources_command", None)
    if command == "discover":
        run = NarrativeSourceDiscoveryEngine().discover(project_ids)
        print(
            f"{run.run_id}\tprojects={run.configured_projects}\tdiscovered={run.discovered_sources} "
            f"verified={run.verified_sources}\tresolved={run.projects_resolved} "
            f"partial={run.projects_partially_resolved}\tunresolved={run.projects_unresolved} "
            f"rejected={run.rejected_sources}\tduplicates={run.duplicate_sources}"
        )
        return 0
    if command == "validate":
        valid = sum(1 for source in sources if source.validation_status == "VALID")
        invalid = sum(1 for source in sources if source.validation_status != "VALID")
        print(f"sources={len(sources)} valid={valid} invalid={invalid}")
        return 0 if invalid == 0 else 2
    if command == "status":
        runs = repository.runs()
        latest = runs[-1].run_id if runs else "-"
        print(f"projects={len(project_ids)} sources={len(sources)} runs={len(runs)} latest_run={latest}")
        return 0
    if command == "coverage":
        coverage = source_coverage(sources, project_ids=project_ids)
        runs = repository.runs()
        latest = runs[-1] if runs else None
        print(
            f"projects={coverage.configured_projects} resolved={coverage.projects_resolved} "
            f"partial={coverage.projects_partially_resolved} unresolved={coverage.projects_unresolved} "
            f"coverage={coverage.coverage_percent:.2f} "
            f"verified={latest.verified_sources if latest else sum(1 for source in sources if source.verified)} "
            f"rejected={latest.rejected_sources if latest else sum(1 for source in sources if not source.verified)} "
            f"duplicates={latest.duplicate_sources if latest else 0}"
        )
        return 0
    if command == "unresolved":
        coverage = source_coverage(sources, project_ids=project_ids)
        for project_id, missing in coverage.missing_by_project.items():
            if len(missing) == 16:
                print(f"{project_id}\tUNRESOLVED\tmissing={','.join(missing)}")
        return 0
    if command == "report":
        coverage = source_coverage(sources, project_ids=project_ids)
        by_project: dict[str, set[str]] = {}
        for source in sources:
            by_project.setdefault(source.project_id, set()).add(source.source_type)
        for project_id in project_ids:
            found = by_project.get(project_id, set())
            missing = coverage.missing_by_project[project_id]
            status = "RESOLVED" if not missing else "PARTIAL" if found else "UNRESOLVED"
            project_sources = tuple(source for source in sources if source.project_id == project_id)
            trust = (
                round(sum(source.trust_score for source in project_sources) / len(project_sources), 4)
                if project_sources
                else 0.0
            )
            validation = (
                "VALID"
                if project_sources and all(source.validation_status == "VALID" for source in project_sources)
                else "MISSING"
            )
            print(
                f"{project_id}\t{status}\tofficial_website={'yes' if 'official_website' in found else 'no'}"
                f"\trss={'yes' if 'rss_feed' in found else 'no'}\tblog={'yes' if 'official_blog' in found else 'no'}"
                f"\tdocs={'yes' if 'documentation' in found or 'developer_docs' in found else 'no'}"
                f"\tgithub={'yes' if 'github_repository' in found else 'no'}"
                f"\tmedium={'yes' if 'medium' in found else 'no'}"
                f"\tmirror={'yes' if 'mirror' in found else 'no'}"
                f"\tgovernance={'yes' if 'governance_forum' in found else 'no'}"
                f"\trelease_notes={'yes' if 'github_releases' in found else 'no'}"
                f"\tengineering_blog={'yes' if 'developer_blog' in found else 'no'}"
                f"\tcoverage={round(((16 - len(missing)) / 16) * 100, 2):.2f}"
                f"\tmissing={','.join(missing) or 'none'}"
                f"\tvalidation={validation}\ttrust={trust:.4f}"
            )
        return 0
    if command == "history":
        for run in repository.runs():
            print(
                f"{run.run_id}\tstarted={run.started_at.isoformat()}\tprojects={run.configured_projects} "
                f"discovered={run.discovered_sources}\tverified={run.verified_sources} "
                f"rejected={run.rejected_sources}\tduplicates={run.duplicate_sources} "
                f"resolved={run.projects_resolved}\tpartial={run.projects_partially_resolved} "
                f"unresolved={run.projects_unresolved}"
            )
        return 0
    print("sources command required")
    return 1


def _graph(args: object) -> int:
    command = getattr(args, "graph_command", None)
    repository = TechnologyGraphRepository()
    if command == "build":
        graph, run = TechnologyDependencyGraphEngine(graph_repository=repository).build()
        print(
            f"{run.run_id}\tprojects={run.projects_analyzed}\tnodes={run.nodes}\tedges={run.edges} "
            f"validated={run.validated_dependencies}\trejected={run.rejected_dependencies} "
            f"graph_coverage={run.graph_coverage:.2f}\ttechnology_coverage={run.technology_coverage:.2f}"
        )
        return 0
    graph = repository.graph()
    runs = repository.runs()
    latest = runs[-1] if runs else None
    metrics_by_project = {item.project_id: item for item in graph.metrics}
    if command == "status":
        print(
            f"nodes={len(graph.nodes)} edges={len(graph.edges)} metrics={len(graph.metrics)} runs={len(runs)} "
            f"latest_run={latest.run_id if latest else '-'}"
        )
        return 0
    if command == "validate":
        valid = sum(1 for edge in graph.edges if edge.validation_status == "VALID")
        invalid = len(graph.edges) - valid
        duplicate_pairs = len(graph.edges) - len({(edge.source_project, edge.target_project) for edge in graph.edges})
        print(f"edges={len(graph.edges)} valid={valid} invalid={invalid} duplicate_pairs={duplicate_pairs}")
        return 0 if invalid == 0 and duplicate_pairs == 0 else 2
    if command == "coverage":
        if latest is None:
            print("projects=0 nodes=0 graph_coverage=0.00 technology_coverage=0.00")
        else:
            print(
                f"projects={latest.projects_analyzed} nodes={latest.nodes} edges={latest.edges} "
                f"graph_coverage={latest.graph_coverage:.2f} technology_coverage={latest.technology_coverage:.2f}"
            )
        return 0
    if command == "report":
        for node in graph.nodes:
            metric = metrics_by_project[node.project_id]
            outgoing = tuple(edge.target_project for edge in graph.edges if edge.source_project == node.project_id)
            incoming = tuple(edge.source_project for edge in graph.edges if edge.target_project == node.project_id)
            print(
                f"{node.project_id}\tdirect_dependencies={','.join(outgoing) or '-'}"
                f"\tdependent_projects={','.join(incoming) or '-'}\tdepth={metric.dependency_depth}"
                f"\tcentrality={metric.dependency_centrality:.4f}"
                f"\tinfrastructure={metric.infrastructure_centrality:.4f}"
                f"\treplacement_availability={metric.replacement_availability:.4f}"
                f"\tspof_risk={metric.single_point_of_failure_risk:.4f}"
            )
        return 0
    if command == "explain":
        project = str(args.project)
        node_edges = tuple(
            edge for edge in graph.edges if edge.source_project == project or edge.target_project == project
        )
        if project not in metrics_by_project or not node_edges:
            print(f"{project}\tUNAVAILABLE\tno validated dependency graph evidence")
            return 0
        metric = metrics_by_project[project]
        print(
            f"{project}\tcentrality={metric.dependency_centrality:.4f}\tinfrastructure="
            f"{metric.infrastructure_centrality:.4f}\tcritical_path={','.join(metric.critical_path)}"
        )
        for edge in node_edges:
            print(
                f"edge={edge.source_project}->{edge.target_project}\ttype={edge.dependency_type}"
                f"\tevidence={','.join(edge.evidence_ids)}\trepository={','.join(edge.repository_ids)}"
                f"\tconfidence={edge.dependency_confidence:.4f}\tfreshness={edge.freshness:.4f}"
                f"\tvalidation={edge.validation_status}"
            )
        return 0
    if command == "path":
        path = dependency_path(graph, str(args.project_a), str(args.project_b))
        print("->".join(path) if path else "UNAVAILABLE")
        return 0
    if command == "centrality":
        for metric in sorted(graph.metrics, key=lambda item: item.dependency_centrality, reverse=True):
            print(
                f"{metric.project_id}\tcentrality={metric.dependency_centrality:.4f}\tfan_in={metric.fan_in}\tfan_out={metric.fan_out}"
            )
        return 0
    if command == "critical":
        for metric in sorted(graph.metrics, key=lambda item: item.single_point_of_failure_risk, reverse=True):
            print(
                f"{metric.project_id}\tspof_risk={metric.single_point_of_failure_risk:.4f}"
                f"\tinfrastructure={metric.infrastructure_centrality:.4f}"
            )
        return 0
    print("graph command required")
    return 1


def _economic(args: object) -> int:
    command = getattr(args, "economic_command", None)
    repository = EconomicGraphRepository()
    if command == "build":
        graph, run = EconomicDependencyGraphEngine(graph_repository=repository).build()
        print(
            f"{run.run_id}\tprojects={run.projects_analyzed}\tnodes={run.nodes}\tedges={run.edges} "
            f"validated={run.validated_relationships}\trejected={run.rejected_relationships} "
            f"graph_coverage={run.graph_coverage:.2f}\teconomic_coverage={run.economic_coverage:.2f}"
        )
        return 0
    graph = repository.graph()
    runs = repository.runs()
    latest = runs[-1] if runs else None
    metrics_by_project = {item.project_id: item for item in graph.metrics}
    if command == "status":
        print(
            f"nodes={len(graph.nodes)} edges={len(graph.edges)} metrics={len(graph.metrics)} runs={len(runs)} "
            f"latest_run={latest.run_id if latest else '-'}"
        )
        return 0
    if command == "validate":
        valid = sum(1 for edge in graph.edges if edge.validation_status == "VALID")
        invalid = len(graph.edges) - valid
        duplicate_pairs = len(graph.edges) - len({(edge.source_project, edge.target_project) for edge in graph.edges})
        print(f"edges={len(graph.edges)} valid={valid} invalid={invalid} duplicate_pairs={duplicate_pairs}")
        return 0 if invalid == 0 and duplicate_pairs == 0 else 2
    if command == "coverage":
        if latest is None:
            print("projects=0 nodes=0 graph_coverage=0.00 economic_coverage=0.00")
        else:
            print(
                f"projects={latest.projects_analyzed} nodes={latest.nodes} edges={latest.edges} "
                f"graph_coverage={latest.graph_coverage:.2f} economic_coverage={latest.economic_coverage:.2f}"
            )
        return 0
    if command == "report":
        for node in graph.nodes:
            metric = metrics_by_project[node.project_id]
            revenue = tuple(
                edge.target_project
                for edge in graph.edges
                if edge.source_project == node.project_id
                and edge.relationship_type in {"revenue_dependency", "fee_dependency"}
            )
            capital = tuple(
                edge.target_project
                for edge in graph.edges
                if edge.source_project == node.project_id
                and edge.relationship_type in {"capital_dependency", "liquidity_dependency", "treasury_dependency"}
            )
            print(
                f"{node.project_id}\trevenue_dependencies={','.join(revenue) or '-'}"
                f"\tcapital_dependencies={','.join(capital) or '-'}"
                f"\tvalue_capture={metric.value_capture:.4f}\teconomic_moat={metric.economic_moat:.4f}"
                f"\tswitching_cost={metric.switching_cost:.4f}"
                f"\trevenue_concentration={metric.revenue_concentration:.4f}"
                f"\tcapital_concentration={metric.capital_concentration:.4f}"
                f"\tcritical_counterparties={','.join(metric.critical_counterparties) or '-'}"
                f"\tresilience={metric.economic_resilience:.4f}"
            )
        return 0
    if command == "explain":
        project = str(args.project)
        node_edges = tuple(
            edge for edge in graph.edges if edge.source_project == project or edge.target_project == project
        )
        if project not in metrics_by_project or not node_edges:
            print(f"{project}\tUNAVAILABLE\tno validated economic graph evidence")
            return 0
        metric = metrics_by_project[project]
        print(
            f"{project}\tmoat={metric.economic_moat:.4f}\tvalue_capture={metric.value_capture:.4f}"
            f"\tcapital_centrality={metric.capital_centrality:.4f}"
            f"\trevenue_centrality={metric.revenue_centrality:.4f}"
        )
        for edge in node_edges:
            print(
                f"edge={edge.source_project}->{edge.target_project}\ttype={edge.relationship_type}"
                f"\tevidence={','.join(edge.evidence_ids)}\trepository={','.join(edge.repository_ids)}"
                f"\tconfidence={edge.dependency_confidence:.4f}\tfreshness={edge.freshness:.4f}"
                f"\tvalidation={edge.validation_status}"
            )
        return 0
    if command == "path":
        path = economic_path(graph, str(args.project_a), str(args.project_b))
        print("->".join(path) if path else "UNAVAILABLE")
        return 0
    if command == "centrality":
        for metric in sorted(
            graph.metrics, key=lambda item: item.capital_centrality + item.revenue_centrality, reverse=True
        ):
            print(
                f"{metric.project_id}\tcapital={metric.capital_centrality:.4f}"
                f"\trevenue={metric.revenue_centrality:.4f}\tmoat={metric.economic_moat:.4f}"
            )
        return 0
    if command == "moat":
        for metric in sorted(graph.metrics, key=lambda item: item.economic_moat, reverse=True):
            print(
                f"{metric.project_id}\tmoat={metric.economic_moat:.4f}"
                f"\tvalue_capture={metric.value_capture:.4f}\tswitching_cost={metric.switching_cost:.4f}"
            )
        return 0
    print("economic command required")
    return 1


def _scenario(args: object) -> int:
    command = getattr(args, "scenario_command", None)
    repository = ScenarioRepository()
    if command == "run":
        results, run = ScenarioSimulationEngine(scenario_repository=repository).run()
        print(
            f"{run.run_id}\tprojects={run.projects_analyzed}\tscenarios={run.scenarios}"
            f"\tprojects_simulated={run.projects_simulated}\taffected_nodes={run.affected_nodes}"
            f"\taffected_edges={run.affected_edges}\tpropagation_depth={run.propagation_depth}"
            f"\tscenario_coverage={run.scenario_coverage:.2f}"
        )
        return 0 if results else 2
    results = repository.results()
    runs = repository.runs()
    latest = runs[-1] if runs else None
    if command == "status":
        print(
            f"scenarios={len(results)} impacts={len(repository.impacts())} runs={len(runs)} "
            f"latest_run={latest.run_id if latest else '-'}"
        )
        return 0
    if command == "coverage":
        if latest is None:
            print("projects=0 scenarios=0 projects_simulated=0 scenario_coverage=0.00")
        else:
            print(
                f"projects={latest.projects_analyzed} scenarios={latest.scenarios}"
                f"\tprojects_simulated={latest.projects_simulated}"
                f"\tscenario_coverage={latest.scenario_coverage:.2f}"
            )
        return 0
    if command == "history":
        for run in runs:
            print(
                f"{run.run_id}\tgenerated={run.generated_at.isoformat()}\tprojects={run.projects_analyzed}"
                f"\tscenarios={run.scenarios}\tprojects_simulated={run.projects_simulated}"
                f"\tcoverage={run.scenario_coverage:.2f}"
            )
        return 0
    if command == "report":
        for result in results:
            direct = sum(1 for impact in result.impacts if impact.direct_impact > 0)
            indirect = sum(1 for impact in result.impacts if impact.indirect_impact > 0)
            critical = sorted(
                {node for impact in result.impacts if impact.system_fragility >= 0.5 for node in impact.affected_nodes}
            )
            economic = round(sum(impact.economic_propagation for impact in result.impacts), 4)
            recovery = _mean_cli(tuple(impact.recovery_difficulty for impact in result.impacts))
            replacement = _mean_cli(tuple(impact.replacement_availability for impact in result.impacts))
            infrastructure = _mean_cli(tuple(impact.infrastructure_resilience for impact in result.impacts))
            economic_resilience = _mean_cli(tuple(impact.economic_resilience for impact in result.impacts))
            print(
                f"{result.scenario.scenario_id}\ttype={result.scenario.scenario_type}"
                f"\ttarget={result.scenario.target_project}\tprojects={len(result.affected_projects)}"
                f"\tdirect={direct}\tindirect={indirect}"
                f"\tcritical_dependencies={','.join(critical) or '-'}"
                f"\teconomic_impact={economic:.4f}\trecovery_difficulty={recovery:.4f}"
                f"\treplacement_options={replacement:.4f}"
                f"\tinfrastructure_resilience={infrastructure:.4f}"
                f"\teconomic_resilience={economic_resilience:.4f}"
                f"\tconfidence={result.confidence:.4f}"
            )
        return 0
    if command == "explain":
        for result in results[:5]:
            print(
                f"{result.scenario.scenario_id}\ttype={result.scenario.scenario_type}"
                f"\ttarget={result.scenario.target_project}\tevidence={','.join(result.scenario.evidence_ids)}"
                f"\trepository={','.join(result.scenario.repository_ids)}"
            )
            for impact in result.impacts:
                print(
                    f"project={impact.project_id}\tdirect={impact.direct_impact:.4f}"
                    f"\tindirect={impact.indirect_impact:.4f}"
                    f"\tdependency_paths={';'.join('->'.join(path) for path in impact.dependency_paths) or '-'}"
                    f"\teconomic_paths={';'.join('->'.join(path) for path in impact.economic_paths) or '-'}"
                    f"\tconfidence={impact.confidence:.4f}\tfreshness={impact.freshness:.4f}"
                    f"\tvalidation={impact.validation_status}"
                )
        return 0
    if command == "compare":
        if len(results) < 2:
            print("scenario comparison requires at least two persisted scenarios")
            return 2
        comparison = compare_scenarios(results[0], results[1])
        print(
            f"left={comparison['left']}\tright={comparison['right']}"
            f"\tshared={len(comparison['shared'])}\tleft_only={len(comparison['left_only'])}"
            f"\tright_only={len(comparison['right_only'])}\taffected_delta={comparison['affected_delta']}"
        )
        return 0
    print("scenario command required")
    return 1


def _backtest(args: object) -> int:
    command = getattr(args, "backtest_command", None)
    repository = BacktestRepository()
    if command == "run":
        run = BacktestingCalibrationEngine(backtest_repository=repository).run()
        print(
            f"{run.run_id}\thistorical_runs={run.historical_runs}\tprojects={run.projects_evaluated}"
            f"\tengines={run.engines_evaluated}\tcoverage={run.coverage:.2f}"
            f"\thistorical_consistency={run.historical_consistency:.4f}"
            f"\tcalibration_completeness={run.calibration_completeness:.2f}"
        )
        return 0
    runs = repository.runs()
    latest = runs[-1] if runs else None
    if command == "report":
        if latest is None:
            print("no persisted backtest run")
            return 0
        for metric in latest.engine_metrics:
            print(
                f"{metric.engine}\tcoverage={metric.historical_coverage:.2f}\thit_rate={metric.hit_rate:.2f}"
                f"\tfalse_positives={metric.false_positives}\tfalse_negatives={metric.false_negatives}"
                f"\tconfidence_calibration={metric.confidence_calibration:.2f}"
                f"\treliability={metric.prediction_reliability:.2f}"
                f"\tweaknesses={'coverage' if metric.engine in latest.calibration.coverage_gaps else '-'}"
                f"\tstrengths={'reliable' if metric.engine in latest.calibration.strong_engines else '-'}"
            )
        return 0
    if command == "history":
        for run in runs:
            print(
                f"{run.run_id}\tgenerated={run.generated_at.isoformat()}\thistorical_runs={run.historical_runs}"
                f"\tprojects={run.projects_evaluated}\tengines={run.engines_evaluated}"
                f"\tcoverage={run.coverage:.2f}\tconsistency={run.historical_consistency:.4f}"
            )
        return 0
    if command == "compare":
        if len(runs) < 2:
            print("backtest comparison requires at least two persisted runs")
            return 2
        comparison = compare_backtests(runs[-2], runs[-1])
        print(
            f"left={comparison['left']}\tright={comparison['right']}"
            f"\tcoverage_delta={comparison['coverage_delta']}"
            f"\tconsistency_delta={comparison['consistency_delta']}"
            f"\tcalibration_delta={comparison['calibration_delta']}"
        )
        return 0
    print("backtest command required")
    return 1


def _calibration(args: object) -> int:
    command = getattr(args, "calibration_command", None)
    if command is None:
        try:
            run = _run_historical_replay(append_snapshots=False)
        except ValueError:
            run = _run_historical_replay(append_snapshots=False)
        print(HistoricalValidationRenderer().render_calibration(run))
        return 0
    runs = BacktestRepository().runs()
    latest = runs[-1] if runs else None
    if latest is None:
        print("no persisted calibration report")
        return 0
    calibration = latest.calibration
    if command == "report":
        print(
            f"{calibration.calibration_id}\tconfidence_calibration={calibration.confidence_calibration:.2f}"
            f"\tevidence_quality={calibration.evidence_quality:.2f}"
            f"\thistorical_drift={calibration.historical_drift:.4f}"
            f"\tweak_engines={','.join(calibration.weak_engines) or '-'}"
            f"\tstrong_engines={','.join(calibration.strong_engines) or '-'}"
        )
        return 0
    if command == "coverage":
        print(
            f"coverage={latest.coverage:.2f}\tcalibration_completeness={latest.calibration_completeness:.2f}"
            f"\tcoverage_gaps={','.join(calibration.coverage_gaps) or '-'}"
        )
        return 0
    if command == "engines":
        for metric in latest.engine_metrics:
            adjustment = calibration.recommended_weight_adjustments.get(metric.engine, 0.0)
            print(
                f"{metric.engine}\treliability={metric.prediction_reliability:.2f}"
                f"\tcoverage={metric.historical_coverage:.2f}"
                f"\trecommended_adjustment={adjustment:.4f}"
            )
        return 0
    print("calibration command required")
    return 1


def _replay(args: object) -> int:
    command = getattr(args, "replay_command", None)
    renderer = HistoricalValidationRenderer()
    if command in {None, "report", "explain"}:
        try:
            run = _run_historical_replay(
                config_path=Path(getattr(args, "historical_config", "configs/historical_validation.yaml")),
                append_snapshots=command is None,
            )
        except ValueError as error:
            if "immutable historical snapshots already exist" not in str(error):
                raise
            run = _run_historical_replay(
                config_path=Path(getattr(args, "historical_config", "configs/historical_validation.yaml")),
                append_snapshots=False,
            )
        if command is None:
            HistoricalValidationRenderer().write_reports(run)
            print(
                f"{run.run_id}\tcases={len(run.cases)}\tcoverage={run.historical_coverage:.2f}"
                f"\tleakage={'PASS' if run.leakage_passed else 'FAIL'}"
                f"\tsurvivorship={'PASS' if run.survivorship_passed else 'FAIL'}"
                f"\tsample_size={run.sample_size_status}"
            )
            return 0
        if command == "report":
            print(renderer.render_markdown(run))
            return 0
        print(renderer.render_explanation(run, getattr(args, "case_id", None)))
        return 0
    if command == "compare":
        left = _run_historical_replay(
            config_path=Path(getattr(args, "historical_config", "configs/historical_validation.yaml")),
            append_snapshots=False,
        )
        right = _run_historical_replay(
            config_path=Path(getattr(args, "historical_config", "configs/historical_validation.yaml")),
            append_snapshots=False,
        )
        print(renderer.render_comparison(left, right))
        return 0
    print("replay command required")
    return 1


def _benchmark(args: object) -> int:
    run = _run_historical_replay(
        config_path=Path(getattr(args, "historical_config", "configs/historical_validation.yaml")),
        append_snapshots=False,
    )
    print(HistoricalValidationRenderer().render_benchmarks(run))
    return 0


def _run_historical_replay(
    *,
    config_path: Path = Path("configs/historical_validation.yaml"),
    append_snapshots: bool,
):
    config = load_historical_validation_config(config_path)
    historical_repository = HistoricalEvidenceRepository()
    return HistoricalPointInTimeValidationEngine(
        config=config,
        repository=HistoricalValidationRepository(),
        snapshot_builder=HistoricalSnapshotBuilder(
            historical_repository=historical_repository,
            include_live_acquisition=False,
        ),
        append_snapshots=append_snapshots,
    ).run()


def _weights(args: object) -> int:
    command = getattr(args, "weights_command", None)
    config = load_weight_config(Path(args.weights_config))
    renderer = WeightReportRenderer()
    if command == "status":
        print(renderer.render_status(config))
        return 0
    if command == "validate":
        print(renderer.render_validate(config))
        return 0
    if command == "report":
        market_config = load_market_validation_config(Path("configs/market_validation.yaml"))
        sources = acquisition_engine_sources(FileAcquisitionRepository(), as_of=market_config.effective_at)
        run = MarketValidationRunner(
            market_config,
            executor=EvidenceBackedProjectExecutor(market_config.effective_at, sources),
        ).run()
        sample = run.project_results[0].engine_sources if run.project_results else ()
        print(renderer.render_report(config, WeightEngine(config).score(sample)))
        return 0
    if command == "recommend":
        runs = BacktestRepository().runs()
        recommendation = recommend_weight_adjustments(config, runs[-1] if runs else None)
        print(renderer.render_recommendation(recommendation))
        return 0
    if command == "activate":
        print(f"active_version={config.version} policy={config.calibration_policy} recommended_weights_activated=false")
        return 0
    print("weights command required")
    return 1


def _timing(args: object) -> int:
    command = getattr(args, "timing_command", None)
    repository = TimingRepository()
    if command == "status":
        assessments = repository.assessments()
        latest = repository.latest_by_project()
        available = tuple(item for item in latest.values() if item.classification != "INSUFFICIENT_EVIDENCE")
        status = _timing_rebuild_status(repository)
        print(
            f"assessments={len(assessments)} projects={len(latest)} available={len(available)} "
            f"insufficient={len(latest) - len(available)} latest={_timing_latest_timestamp(latest)} "
            f"rebuild_status={status.status} stale_dependencies={','.join(status.stale_dependencies) or '-'}"
        )
        return 0 if not status.is_stale else 2
    if command == "validate":
        assessments = repository.assessments()
        invalid = tuple(
            item for item in assessments if item.classification == "INSUFFICIENT_EVIDENCE" and not item.missing_evidence
        )
        status = _timing_rebuild_status(repository)
        print(
            f"assessments={len(assessments)} structurally_valid={len(assessments) - len(invalid)} invalid={len(invalid)}"
            f" rebuild_status={status.status} stale_dependencies={','.join(status.stale_dependencies) or '-'}"
        )
        return 0 if not invalid and not status.is_stale else 2
    if command == "report":
        OpportunityTimingEvidenceEngine(repository=repository).sync()
        latest = repository.latest_by_project()
        for item in sorted(latest.values(), key=lambda row: row.project_id):
            print(_timing_report_line(item))
        return 0
    if command == "coverage":
        cached = repository.latest_by_project()
        project_count = len(load_market_validation_config().project_universe)
        cached_available = sum(1 for item in cached.values() if item.classification != "INSUFFICIENT_EVIDENCE")
        cached_coverage = round((cached_available / project_count) * 100.0, 2) if project_count else 0.0
        status = _timing_rebuild_status(repository)
        latest = _timing_latest_or_sync(repository)
        available = sum(1 for item in latest.values() if item.classification != "INSUFFICIENT_EVIDENCE")
        coverage = round((available / project_count) * 100.0, 2) if project_count else 0.0
        print(
            f"cached projects={project_count} available={cached_available} "
            f"insufficient={project_count - cached_available} coverage={cached_coverage:.2f} status={status.status}"
        )
        print(
            f"rebuilt projects={project_count} available={available} "
            f"insufficient={project_count - available} coverage={coverage:.2f} status=CURRENT"
        )
        return 0
    if command == "sync":
        assessments = OpportunityTimingEvidenceEngine(repository=repository).sync()
        available = sum(1 for item in assessments if item.classification != "INSUFFICIENT_EVIDENCE")
        print(
            f"synced assessments={len(assessments)} available={available} "
            f"insufficient={len(assessments) - available}"
        )
        return 0
    if command == "freshness":
        print(_timing_freshness_line(repository))
        return 0
    if command == "rebuild-status":
        status = _timing_rebuild_status(repository)
        print(
            f"status={status.status} stale_dependencies={','.join(status.stale_dependencies) or '-'} "
            f"saved={status.saved_generation_timestamp.isoformat() if status.saved_generation_timestamp else '-'} "
            f"current={status.current_generation_timestamp.isoformat() if status.current_generation_timestamp else '-'}"
        )
        return 0 if not status.is_stale else 2
    if command == "dependencies":
        print(_timing_dependencies_report(repository))
        return 0
    if command == "history":
        for row in repository.history():
            print(
                f"{row.get('generated_at')}\tassessments={row.get('assessments', 0)}"
                f"\tavailable={row.get('available', 0)}\tinsufficient={row.get('insufficient', 0)}"
            )
        if not repository.history():
            print("history=0")
        return 0
    if command == "explain":
        latest = _timing_latest_or_sync(repository)
        project = args.project
        item = latest.get(project)
        if item is None:
            print(f"{project}\tINSUFFICIENT_EVIDENCE\tmissing=project_not_assessed")
            return 2
        print(_timing_explain_line(item))
        return 0
    if command == "compare":
        latest = _timing_latest_or_sync(repository)
        first = latest.get(args.project_a)
        second = latest.get(args.project_b)
        if first is None or second is None:
            print("comparison unavailable")
            return 2
        print(
            f"{first.project_id}\tclassification={first.classification}\tentry={first.entry_score:.4f}"
            f"\texit={first.exit_score:.4f}\tconfidence={first.timing_confidence:.4f}"
        )
        print(
            f"{second.project_id}\tclassification={second.classification}\tentry={second.entry_score:.4f}"
            f"\texit={second.exit_score:.4f}\tconfidence={second.timing_confidence:.4f}"
        )
        return 0
    print("timing command required")
    return 1


def _timing_latest_or_sync(repository: TimingRepository) -> dict[str, TimingAssessment]:
    latest = repository.latest_by_project()
    status = _timing_rebuild_status(repository)
    if latest and not status.is_stale:
        return latest
    OpportunityTimingEvidenceEngine(repository=repository).sync()
    return repository.latest_by_project()


def _timing_rebuild_status(repository: TimingRepository):
    return repository.rebuild_status(current_timing_dependencies())


def _stale_timing_message(repository: TimingRepository) -> str | None:
    status = _timing_rebuild_status(repository)
    if not status.is_stale:
        return None
    return f"STALE_TIMING_REBUILD_REQUIRED stale_dependencies={','.join(status.stale_dependencies) or '-'}"


def _timing_freshness_line(repository: TimingRepository) -> str:
    current = current_timing_dependencies()
    status = repository.rebuild_status(current)
    saved = repository.latest_dependencies()
    return (
        f"status={status.status} stale_dependencies={','.join(status.stale_dependencies) or '-'} "
        f"saved_generation={saved.generation_timestamp.isoformat() if saved else '-'} "
        f"current_generation={current.generation_timestamp.isoformat()} "
        f"protocol={_optional_iso(current.protocol_evidence_timestamp)} "
        f"narrative={_optional_iso(current.narrative_evidence_timestamp)} "
        f"developer={_optional_iso(current.developer_evidence_timestamp)} "
        f"graph={_optional_iso(current.graph_timestamp)} "
        f"macro={_optional_iso(current.macro_timestamp)} "
        f"whale={_optional_iso(current.whale_timestamp)}"
    )


def _timing_dependencies_report(repository: TimingRepository) -> str:
    current = current_timing_dependencies()
    saved = repository.latest_dependencies()
    lines = [_timing_freshness_line(repository)]
    for dependency in sorted(current.dependency_fingerprints):
        saved_fingerprint = saved.dependency_fingerprints.get(dependency) if saved is not None else None
        lines.append(
            f"{dependency}\ttimestamp={_optional_iso(current.dependency_timestamps.get(dependency))}"
            f"\tfingerprint={current.dependency_fingerprints[dependency]}"
            f"\tsaved_fingerprint={saved_fingerprint or '-'}"
        )
    return "\n".join(lines)


def _optional_iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else "-"


def _timing_latest_timestamp(latest: dict[str, TimingAssessment]) -> str:
    if not latest:
        return "-"
    return max(item.generated_at for item in latest.values()).isoformat()


def _timing_report_line(item: TimingAssessment) -> str:
    return (
        f"{item.project_id}\tclassification={item.classification}\tentry={item.entry_score:.4f}"
        f"\texit={item.exit_score:.4f}\taccumulation={item.accumulation_score:.4f}"
        f"\tdistribution={item.distribution_score:.4f}\trisk_reward={item.risk_reward_score:.4f}"
        f"\tconfidence={item.timing_confidence:.4f}\tquality={item.evidence_quality:.4f}"
        f"\tfreshness={item.freshness:.4f}"
    )


def _timing_explain_line(item: TimingAssessment) -> str:
    return (
        _timing_report_line(item)
        + f"\tcycle={item.cycle_position}\tregime={item.market_regime}"
        + f"\tsources={','.join(item.source_engines) or '-'}"
        + f"\tevidence_ids={','.join(item.evidence_ids) or '-'}"
        + f"\trepository_ids={','.join(item.repository_ids) or '-'}"
        + f"\tmissing={','.join(item.missing_evidence) or '-'}"
        + f"\tstale={','.join(item.stale_evidence) or '-'}"
        + f"\treasoning={';'.join(item.reasoning_chain) or '-'}"
    )


def _historical(args: object) -> int:
    command = getattr(args, "historical_command", None)
    config = load_historical_validation_config(Path(args.historical_config))
    repository = HistoricalValidationRepository()
    historical_repository = HistoricalEvidenceRepository()
    if command == "cases":
        for case in config.challenge_cases:
            print(
                f"{case.case_id}\t{case.project_id}\t{case.case_type}\tcutoff="
                f"{case.historical_cutoff_timestamp.isoformat()}\tlifecycle={case.project_lifecycle_state}"
            )
        return 0
    if command == "sync":
        coverage_before = repository.runs()[-1]["historical_coverage"] if repository.runs() else 0.0
        identifiers = load_project_identifiers()
        acquisition_runs = []
        pipeline = HistoricalAcquisitionPipeline(historical_repository)
        providers = (
            CoinGeckoHistoricalProvider(id_map=_historical_coingecko_map(identifiers)),
            DefiLlamaHistoricalProvider(slug_map=_historical_defillama_map(identifiers)),
            GitHubHistoricalActivityProvider(repository_map=_historical_github_map(identifiers)),
            HistoricalRSSAnnouncementsProvider(),
            GovernanceArchiveProvider(space_map=_historical_governance_map()),
            InternetArchiveSnapshotProvider(domain_map=_historical_domain_map()),
        )
        for provider in providers:
            acquisition_runs.append(pipeline.sync(provider, tuple(config.challenge_cases)))
        new_valid = sum(run.valid_count for run in acquisition_runs)
        if new_valid:
            replay = HistoricalPointInTimeValidationEngine(
                config=config,
                repository=repository,
                snapshot_builder=HistoricalSnapshotBuilder(historical_repository=historical_repository),
                allow_snapshot_corrections=True,
            ).run()
            snapshots_created = len(replay.snapshots)
            coverage_after = replay.historical_coverage
            completed, incomplete = _historical_challenge_counts(replay.challenge_results)
        else:
            latest = repository.runs()[-1] if repository.runs() else {}
            snapshots_created = 0
            coverage_after = float(latest.get("historical_coverage", 0.0))
            completed, incomplete = _historical_case_progress()
        print(
            f"historical_records_downloaded={sum(run.raw_count for run in acquisition_runs)}"
            f"\tnormalized={sum(run.normalized_count for run in acquisition_runs)}"
            f"\tvalid={new_valid}"
            f"\tinvalid={sum(run.invalid_count for run in acquisition_runs)}"
            f"\tduplicates={sum(run.duplicate_count for run in acquisition_runs)}"
            f"\tsnapshots_created={snapshots_created}"
            f"\tcoverage_before={coverage_before:.2f}"
            f"\tcoverage_after={coverage_after:.2f}"
            f"\tcompleted={completed}\tincomplete={incomplete}"
        )
        return 0
    if command == "expand":
        coverage_before = float(repository.runs()[-1]["historical_coverage"]) if repository.runs() else 0.0
        identifiers = load_project_identifiers()
        expansion_cases = _historical_expansion_cases(config)
        pipeline = HistoricalAcquisitionPipeline(historical_repository)
        providers = (
            CoinGeckoHistoricalProvider(
                id_map=_historical_coingecko_map(identifiers), months_before=12, months_after=24
            ),
            DefiLlamaHistoricalProvider(
                slug_map=_historical_defillama_map(identifiers), months_before=12, months_after=24
            ),
            GitHubHistoricalActivityProvider(
                repository_map=_historical_github_map(identifiers), months_before=6, months_after=0
            ),
            HistoricalRSSAnnouncementsProvider(),
            GovernanceArchiveProvider(space_map=_historical_governance_map()),
            InternetArchiveSnapshotProvider(domain_map=_historical_domain_map(), months_before=12, months_after=24),
        )
        acquisition_runs = [pipeline.sync(provider, expansion_cases) for provider in providers]
        new_valid = sum(run.valid_count for run in acquisition_runs)
        if new_valid:
            replay = HistoricalPointInTimeValidationEngine(
                config=config,
                repository=repository,
                snapshot_builder=HistoricalSnapshotBuilder(historical_repository=historical_repository),
                allow_snapshot_corrections=True,
            ).run()
            snapshots_created = len(replay.snapshots)
            coverage_after = replay.historical_coverage
            completed, incomplete = _historical_challenge_counts(replay.challenge_results)
        else:
            latest = repository.runs()[-1] if repository.runs() else {}
            snapshots_created = 0
            coverage_after = float(latest.get("historical_coverage", 0.0))
            completed, incomplete = _historical_case_progress()
        print(
            f"historical_records_downloaded={sum(run.raw_count for run in acquisition_runs)}"
            f"\tnormalized={sum(run.normalized_count for run in acquisition_runs)}"
            f"\tvalid={new_valid}"
            f"\tinvalid={sum(run.invalid_count for run in acquisition_runs)}"
            f"\tduplicates={sum(run.duplicate_count for run in acquisition_runs)}"
            f"\tsnapshots_created={snapshots_created}"
            f"\tcoverage_before={coverage_before:.2f}"
            f"\tcoverage_after={coverage_after:.2f}"
            f"\tcompleted={completed}\tincomplete={incomplete}"
        )
        return 0
    if command == "complete":
        coverage_before = float(repository.runs()[-1]["historical_coverage"]) if repository.runs() else 0.0
        identifiers = load_project_identifiers()
        completion_cases = tuple(config.challenge_cases) + _historical_benchmark_cases(config)
        outcome_offsets = tuple(int(days) for days in config.evaluation_windows)
        pipeline = HistoricalAcquisitionPipeline(historical_repository)
        providers = (
            CoinGeckoHistoricalProvider(
                id_map=_historical_coingecko_map(identifiers),
                months_before=1,
                months_after=24,
                extra_offsets_days=outcome_offsets,
            ),
            DefiLlamaHistoricalProvider(
                slug_map=_historical_defillama_map(identifiers),
                months_before=1,
                months_after=24,
                extra_offsets_days=outcome_offsets,
            ),
            GitHubHistoricalActivityProvider(
                repository_map=_historical_github_map(identifiers),
                months_before=6,
                months_after=0,
                extra_offsets_days=outcome_offsets,
            ),
            HistoricalRSSAnnouncementsProvider(),
            GovernanceArchiveProvider(space_map=_historical_governance_map()),
            InternetArchiveSnapshotProvider(
                domain_map=_historical_domain_map(),
                months_before=1,
                months_after=24,
                extra_offsets_days=outcome_offsets,
            ),
        )
        acquisition_runs = [pipeline.sync(provider, completion_cases) for provider in providers]
        new_valid = sum(run.valid_count for run in acquisition_runs)
        if new_valid:
            replay = HistoricalPointInTimeValidationEngine(
                config=config,
                repository=repository,
                snapshot_builder=HistoricalSnapshotBuilder(historical_repository=historical_repository),
                allow_snapshot_corrections=True,
            ).run()
            snapshots_created = len(replay.snapshots)
            coverage_after = replay.historical_coverage
            completed, incomplete = _historical_challenge_counts(replay.challenge_results)
        else:
            replay = HistoricalPointInTimeValidationEngine(
                config=config,
                repository=repository,
                snapshot_builder=HistoricalSnapshotBuilder(historical_repository=historical_repository),
                append_snapshots=False,
            ).run()
            snapshots_created = 0
            coverage_after = replay.historical_coverage
            completed, incomplete = _historical_challenge_counts(replay.challenge_results)
        completed, incomplete = _historical_completion_state_counts(config)
        outcome_coverage, benchmark_coverage = _historical_outcome_benchmark_coverage()
        print(
            f"historical_records_downloaded={sum(run.raw_count for run in acquisition_runs)}"
            f"\tnormalized={sum(run.normalized_count for run in acquisition_runs)}"
            f"\tvalid={new_valid}"
            f"\tinvalid={sum(run.invalid_count for run in acquisition_runs)}"
            f"\tduplicates={sum(run.duplicate_count for run in acquisition_runs)}"
            f"\tsnapshots_created={snapshots_created}"
            f"\tcoverage_before={coverage_before:.2f}"
            f"\tcoverage_after={coverage_after:.2f}"
            f"\tcompleted={completed}\tblocked={incomplete}"
            f"\toutcome_coverage={outcome_coverage:.2f}\tbenchmark_coverage={benchmark_coverage:.2f}"
        )
        return 0
    if command == "status":
        runs = historical_repository.runs()
        snapshots = repository.snapshots()
        latest = runs[-1].run_id if runs else "-"
        print(
            f"raw={len(historical_repository.raw())}\tnormalized={len(historical_repository.normalized())}"
            f"\tvalidations={len(historical_repository.validations())}\truns={len(runs)}"
            f"\tsnapshots={len(snapshots)}\tlatest_run={latest}"
        )
        return 0
    if command == "validate":
        counts = Counter(item.status for item in historical_repository.validations())
        invalid = counts["invalid"] + counts["future"] + counts["corrupted"]
        print(
            f"valid={counts['valid']}\tinvalid={counts['invalid']}\tfuture={counts['future']}"
            f"\tcorrupted={counts['corrupted']}\tduplicates={counts['duplicate']}"
        )
        return 0 if invalid == 0 else 2
    if command == "providers":
        providers = (
            CoinGeckoHistoricalProvider(id_map={}),
            DefiLlamaHistoricalProvider(slug_map={}),
            GitHubHistoricalActivityProvider(repository_map={}),
            HistoricalRSSAnnouncementsProvider(),
            GovernanceArchiveProvider(space_map={}),
            InternetArchiveSnapshotProvider(domain_map={}),
        )
        for provider in providers:
            metadata = provider.metadata
            print(f"{metadata.name}\timplemented\tmetrics={','.join(metadata.supported_metrics) or '-'}")
        for metadata in future_provider_metadata():
            print(f"{metadata.name}\tfuture\tmetrics={','.join(metadata.supported_metrics) or '-'}")
        return 0
    if command == "statistics":
        validations = historical_repository.validations()
        counts = Counter(item.status for item in validations)
        provider_runs = Counter(run.provider for run in historical_repository.runs())
        projects = {item.project_id for item in historical_repository.normalized()}
        print(
            f"raw={len(historical_repository.raw())}\tnormalized={len(historical_repository.normalized())}"
            f"\tvalid={counts['valid']}\tinvalid={counts['invalid']}\tfuture={counts['future']}"
            f"\tcorrupted={counts['corrupted']}\tduplicates={counts['duplicate']}"
            f"\tprojects={len(projects)}"
            f"\tprovider_runs={','.join(f'{key}:{value}' for key, value in sorted(provider_runs.items())) or '-'}"
        )
        return 0
    if command == "progress":
        latest = repository.runs()[-1] if repository.runs() else {}
        completed, incomplete = _historical_case_progress()
        outcome_coverage, benchmark_coverage = _historical_outcome_benchmark_coverage()
        print(
            f"records={len(historical_repository.normalized())}\tsnapshots={len(repository.snapshots())}"
            f"\tprojects={len({item.project_id for item in historical_repository.normalized()})}"
            f"\tcoverage={float(latest.get('historical_coverage', 0.0)):.2f}"
            f"\tcompleted={completed}\tincomplete={incomplete}"
            f"\toutcome_coverage={outcome_coverage:.2f}\tbenchmark_coverage={benchmark_coverage:.2f}"
        )
        return 0
    if command == "gaps":
        for row in _historical_gap_rows(config):
            print(
                f"{row['case_id']}\tproject={row['project_id']}\tmissing_evidence={row['missing_evidence']}"
                f"\tmissing_providers={row['missing_providers']}\tmissing_timestamps={row['missing_timestamps']}"
                f"\tmissing_outcome_windows={row['missing_outcome_windows']}"
                f"\tmissing_benchmarks={row['missing_benchmarks']}\treplay_blocked_by={row['replay_blocked_by']}"
                f"\tcoverage={row['coverage']}"
            )
        return 0
    if command == "unresolved":
        for row in _historical_gap_rows(config):
            state = "COMPLETE" if row["replay_blocked_by"] == "none" else "BLOCKED_BY_UNAVAILABLE_DATA"
            print(f"{row['case_id']}\t{state}\tblocked_by={row['replay_blocked_by']}")
        return 0
    if command == "summary":
        latest = repository.runs()[-1] if repository.runs() else {}
        completed, incomplete = _historical_completion_state_counts(config)
        outcome_coverage, benchmark_coverage = _historical_outcome_benchmark_coverage()
        calibration = repository.calibration_metrics()
        sample_size = calibration[-1].sample_size_status if calibration else "INSUFFICIENT_SAMPLE_SIZE"
        print(
            f"records={len(historical_repository.normalized())}\tsnapshots={len(repository.snapshots())}"
            f"\tprojects={len({item.project_id for item in historical_repository.normalized()})}"
            f"\thistorical_coverage={float(latest.get('historical_coverage', 0.0)):.2f}"
            f"\tcompleted={completed}\tblocked={incomplete}"
            f"\toutcome_coverage={outcome_coverage:.2f}\tbenchmark_coverage={benchmark_coverage:.2f}"
            f"\tcalibration_readiness={sample_size}"
        )
        return 0
    if command in {"build", "replay", "evaluate"}:
        selected = getattr(args, "project", None) if command == "build" else getattr(args, "case_id", None)
        try:
            run = HistoricalPointInTimeValidationEngine(config=config, repository=repository).run(case_id=selected)
        except ValueError as error:
            if "immutable historical snapshots already exist" not in str(error):
                raise
            run = HistoricalPointInTimeValidationEngine(
                config=config, repository=repository, append_snapshots=False
            ).run(case_id=selected)
            print(f"snapshots already up to date, replayed from existing evidence: {error}")
        HistoricalValidationRenderer().write_reports(run)
        print(
            f"{run.run_id}\tcases={len(run.cases)}\tsnapshots={len(run.snapshots)}"
            f"\tengine_outputs={len(run.engine_outputs)}\toutcomes={len(run.outcomes)}"
            f"\tcoverage={run.historical_coverage:.2f}\tleakage={'PASS' if run.leakage_passed else 'FAIL'}"
            f"\tsurvivorship={'PASS' if run.survivorship_passed else 'FAIL'}"
            f"\tsample_size={run.sample_size_status}"
        )
        return 0
    historical_runs: tuple[dict[str, Any], ...] = repository.runs()
    latest_run = historical_runs[-1] if historical_runs else None
    if command == "coverage":
        coverage = float(latest_run.get("historical_coverage", 0.0)) if latest_run else 0.0
        case_count = int(latest_run.get("case_count", 0)) if latest_run else 0
        print(f"runs={len(historical_runs)}\tcoverage={coverage:.2f}" f"\tcases={case_count}")
        return 0
    if command == "leakage-check":
        validations = repository.bias_validations()
        failed = tuple(item for item in validations if not item.leakage_passed)
        print(f"cases={len(validations)} leakage_failures={len(failed)} result={'PASS' if not failed else 'FAIL'}")
        return 0 if not failed else 2
    if command == "survivorship-check":
        validations = repository.bias_validations()
        failed = tuple(item for item in validations if not item.survivorship_passed)
        print(f"cases={len(validations)} survivorship_failures={len(failed)} result={'PASS' if not failed else 'FAIL'}")
        return 0 if not failed else 2
    if command == "calibration":
        metrics = repository.calibration_metrics()
        latest_metric = metrics[-1] if metrics else None
        print(
            f"metrics={len(metrics)} sample_size="
            f"{latest_metric.sample_size_status if latest_metric else 'INSUFFICIENT_SAMPLE_SIZE'}"
        )
        return 0
    if command == "engines":
        rows = _read_jsonl_cli(Path("data/historical_validation/engine_metrics.jsonl"))
        for row in rows:
            print(
                f"{row['engine']}\tavailability={row['historical_availability']}"
                f"\tsample_count={row['sample_count']}\tevidence_quality={row['evidence_quality']}"
            )
        return 0
    if command == "outcomes":
        for row in _read_jsonl_cli(Path("data/historical_validation/outcomes.jsonl")):
            print(f"{row['case_id']}\t{row['project_id']}\t{row['final_success_label']}")
        return 0
    if command == "challenges":
        for row in _read_jsonl_cli(Path("data/historical_validation/challenge_results.jsonl")):
            print(
                f"{row['case_id']}\t{row['project_id']}\tdecision={row['committee_decision']}"
                f"\toutcome={row['realized_outcome']}\tcorrect={row['was_hunter_correct']}"
            )
        return 0
    if command == "report":
        rows = _read_jsonl_cli(Path("data/historical_validation/challenge_results.jsonl"))
        case_id = getattr(args, "case_id", None)
        for row in rows:
            if case_id and row["case_id"] != case_id:
                continue
            print(
                f"{row['case_id']}\tproject={row['project_id']}\tcutoff={row['historical_cutoff_timestamp']}"
                f"\tdecision={row['committee_decision']}\tprobability={row['probability']}"
                f"\topportunity={row['opportunity']}\trisk={row['risk']}\toutcome={row['realized_outcome']}"
                f"\tbenchmark={row['benchmark_outcome']}\texcess_return={row['excess_return']}"
                f"\tmax_drawdown={row['maximum_drawdown']}\tcorrect={row['was_hunter_correct']}"
                f"\tleakage={row['leakage_validation']}\tsurvivorship={row['survivorship_validation']}"
            )
        return 0
    if command == "compare":
        rows = {
            row["case_id"]: row for row in _read_jsonl_cli(Path("data/historical_validation/challenge_results.jsonl"))
        }
        left = rows.get(args.case_a)
        right = rows.get(args.case_b)
        if left is None or right is None:
            print("historical comparison requires two persisted cases")
            return 2
        print(
            f"left={left['case_id']}\tright={right['case_id']}\tleft_outcome={left['realized_outcome']}"
            f"\tright_outcome={right['realized_outcome']}\tleft_decision={left['committee_decision']}"
            f"\tright_decision={right['committee_decision']}"
        )
        return 0
    print("historical command required")
    return 1


def _historical_acquisition(args: object) -> int:
    command = getattr(args, "historical_acquisition_command", None)
    config = load_historical_validation_config(Path(args.historical_config))
    repository = HistoricalEvidenceRepository()
    if command == "sync":
        before = _historical_acquisition_coverage(repository, config)
        runs = _historical_acquisition_sync(config, repository)
        after = _historical_acquisition_coverage(repository, config)
        print(
            f"runs={len(runs)} raw={sum(run.raw_count for run in runs)}"
            f"\tnormalized={sum(run.normalized_count for run in runs)}"
            f"\tvalid={sum(run.valid_count for run in runs)}"
            f"\tsnapshots={len(repository.snapshots())}"
            f"\tcoverage_before={before:.2f}\tcoverage_after={after:.2f}"
        )
        return 0
    if command == "coverage":
        print(_historical_acquisition_coverage_report(repository, config))
        return 0
    if command == "report":
        print(_historical_acquisition_report(repository, config))
        return 0
    if command == "explain":
        print(_historical_acquisition_explain(repository, config, getattr(args, "project", None)))
        return 0
    if command == "validate":
        violations = _historical_acquisition_violations(repository)
        print(
            f"snapshots={len(repository.snapshots())}\tviolations={len(violations)}"
            f"\tresult={'PASS' if not violations else 'FAIL'}"
        )
        for violation in violations:
            print(violation)
        return 0 if not violations else 2
    print("historical-acquisition command required")
    return 1


def _historical_acquisition_sync(config: Any, repository: HistoricalEvidenceRepository):
    identifiers = load_project_identifiers()
    pipeline = HistoricalAcquisitionPipeline(repository)
    providers = (
        CoinGeckoHistoricalProvider(id_map=_historical_coingecko_map(identifiers)),
        DefiLlamaHistoricalProvider(slug_map=_historical_defillama_map(identifiers)),
        GitHubHistoricalActivityProvider(repository_map=_historical_github_map(identifiers)),
        HistoricalRSSAnnouncementsProvider(),
        GovernanceArchiveProvider(space_map=_historical_governance_map()),
        InternetArchiveSnapshotProvider(domain_map=_historical_domain_map()),
        ReconstructedHistoricalEvidenceProvider(),
    )
    return tuple(pipeline.sync(provider, tuple(config.challenge_cases)) for provider in providers)


def _historical_acquisition_coverage(repository: HistoricalEvidenceRepository, config: Any) -> float:
    total = len(config.challenge_cases) * max(len(HISTORICAL_ACQUISITION_ENGINES), 1)
    available = {
        (snapshot.case_id, snapshot.engine) for snapshot in repository.snapshots() if snapshot.status == "AVAILABLE"
    }
    return round((len(available) / max(total, 1)) * 100.0, 2)


HISTORICAL_ACQUISITION_ENGINES: tuple[str, ...] = (
    "protocol",
    "developer",
    "news",
    "narrative",
    "future_demand",
    "macro_intelligence",
    "whale_intelligence",
    "technology_graph",
    "economic_graph",
    "scenario",
    "market",
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
)


def _historical_acquisition_coverage_report(repository: HistoricalEvidenceRepository, config: Any) -> str:
    snapshots = repository.snapshots()
    available = tuple(item for item in snapshots if item.status == "AVAILABLE")
    missing = len(config.challenge_cases) * len(HISTORICAL_ACQUISITION_ENGINES) - len(
        {(item.case_id, item.engine) for item in available}
    )
    confidence_distribution = Counter(_confidence_bucket(item.confidence) for item in available)
    return (
        f"projects={len(config.challenge_cases)}\tengines={len(HISTORICAL_ACQUISITION_ENGINES)}"
        f"\tdates={len({case.historical_cutoff_timestamp.date().isoformat() for case in config.challenge_cases})}"
        f"\tavailable_snapshots={len(available)}\tmissing_snapshots={max(missing, 0)}"
        f"\tcoverage={_historical_acquisition_coverage(repository, config):.2f}"
        f"\tconfidence_distribution={','.join(f'{key}:{value}' for key, value in sorted(confidence_distribution.items())) or '-'}"
    )


def _historical_acquisition_report(repository: HistoricalEvidenceRepository, config: Any) -> str:
    lines = [_historical_acquisition_coverage_report(repository, config)]
    latest_by_case_engine = {(item.case_id, item.engine): item for item in repository.snapshots()}
    for case in config.challenge_cases:
        for engine in HISTORICAL_ACQUISITION_ENGINES:
            snapshot = latest_by_case_engine.get((case.case_id, engine))
            if snapshot is None:
                lines.append(f"{case.case_id}\t{case.project_id}\t{engine}\tMISSING\tprovider=-\tconfidence=0.0000")
            else:
                lines.append(
                    f"{case.case_id}\t{case.project_id}\t{engine}\t{snapshot.status}"
                    f"\tprovider={snapshot.provider}\tconfidence={snapshot.confidence:.4f}"
                    f"\tfreshness={snapshot.freshness:.4f}\tmissing={','.join(snapshot.missing_fields) or '-'}"
                )
    return "\n".join(lines)


def _historical_acquisition_explain(
    repository: HistoricalEvidenceRepository,
    config: Any,
    project: str | None,
) -> str:
    target = project or "-"
    cases = tuple(case for case in config.challenge_cases if project in {None, case.project_id, case.project_slug})
    lines = ["case_id\tproject\tengine\tstatus\tsnapshot_id\tacquisition_id\tprovider\teffective\tmissing"]
    for case in cases:
        for snapshot in repository.snapshots(case_id=case.case_id):
            lines.append(
                f"{case.case_id}\t{case.project_id}\t{snapshot.engine}\t{snapshot.status}"
                f"\t{snapshot.snapshot_id}\t{snapshot.acquisition_id}\t{snapshot.provider}"
                f"\t{snapshot.effective_timestamp.isoformat()}\t{','.join(snapshot.missing_fields) or '-'}"
            )
    if len(lines) == 1:
        lines.append(f"-\t{target}\t-\tMISSING\t-\t-\t-\t-\tno historical acquisition snapshots")
    return "\n".join(lines)


def _historical_acquisition_violations(repository: HistoricalEvidenceRepository) -> tuple[str, ...]:
    violations = []
    seen: set[str] = set()
    for snapshot in repository.snapshots():
        if snapshot.snapshot_id in seen:
            violations.append(f"{snapshot.snapshot_id}:duplicate_snapshot")
        seen.add(snapshot.snapshot_id)
        if snapshot.observation_timestamp > snapshot.effective_timestamp:
            violations.append(f"{snapshot.snapshot_id}:observation_after_effective")
        if snapshot.effective_timestamp > snapshot.acquisition_timestamp:
            violations.append(f"{snapshot.snapshot_id}:effective_after_acquisition")
        if snapshot.status == "AVAILABLE" and not snapshot.historical_snapshot:
            violations.append(f"{snapshot.snapshot_id}:missing_payload")
        if snapshot.status == "UNAVAILABLE" and not snapshot.missing_fields:
            violations.append(f"{snapshot.snapshot_id}:missing_unavailable_reason")
    return tuple(sorted(violations))


def _confidence_bucket(value: float) -> str:
    if value < 0.25:
        return "0.00-0.25"
    if value < 0.5:
        return "0.25-0.50"
    if value < 0.75:
        return "0.50-0.75"
    return "0.75-1.00"


def _historical_coingecko_map(identifiers: dict[str, Any]) -> dict[str, str]:
    return {
        project_id: identifier.coingecko_id
        for project_id, identifier in identifiers.items()
        if identifier.coingecko_id and not identifier.unsupported and not identifier.ambiguous
    }


def _historical_defillama_map(identifiers: dict[str, Any]) -> dict[str, str]:
    return {
        project_id: identifier.defillama_slug
        for project_id, identifier in identifiers.items()
        if identifier.defillama_slug and not identifier.defillama_unsupported and not identifier.defillama_ambiguous
    }


def _historical_github_map(identifiers: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        project_id: identifier.github_repositories
        for project_id, identifier in identifiers.items()
        if identifier.github_repositories and not identifier.github_unsupported and not identifier.github_ambiguous
    }


def _historical_governance_map(path: str | Path = "configs/governance_spaces.yaml") -> dict[str, str]:
    import yaml

    location = Path(path)
    if not location.exists():
        return {}
    data = yaml.safe_load(location.read_text(encoding="utf-8")) or {}
    return {str(project_id): str(space) for project_id, space in data.items() if space}


def _historical_domain_map(path: str | Path = "configs/project_domains.yaml") -> dict[str, str]:
    import yaml

    location = Path(path)
    if not location.exists():
        return {}
    data = yaml.safe_load(location.read_text(encoding="utf-8")) or {}
    return {str(project_id): str(domain) for project_id, domain in data.items() if domain}


def _historical_challenge_counts(challenges: object) -> tuple[int, int]:
    rows = tuple(challenges) if isinstance(challenges, tuple | list) else ()
    completed = sum(1 for row in rows if getattr(row, "realized_outcome", "") != "INSUFFICIENT_OUTCOME_DATA")
    return completed, len(rows) - completed


def _historical_expansion_cases(config: Any) -> tuple[Any, ...]:
    challenge_cases = tuple(config.challenge_cases)
    existing = {case.project_id for case in challenge_cases}
    market_config = load_market_validation_config()
    universe_cases = []
    for project in market_config.project_universe:
        if project.project_id in existing:
            continue
        universe_cases.append(
            _historical_universe_case(
                project_id=project.project_id,
                name=project.name,
                sector=project.sector,
                timestamp=market_config.effective_at,
            )
        )
    return challenge_cases + tuple(universe_cases)


def _historical_benchmark_cases(config: Any) -> tuple[Any, ...]:
    benchmark_ids = tuple(item for item in config.benchmarks if item in {"bitcoin", "ethereum"})
    cases = []
    for case in config.challenge_cases:
        for benchmark_id in benchmark_ids:
            cases.append(
                _historical_universe_case(
                    project_id=benchmark_id,
                    name=benchmark_id.title(),
                    sector="benchmark",
                    timestamp=case.evaluation_timestamp,
                    case_id=f"benchmark-{benchmark_id}-for-{case.case_id}",
                )
            )
    return tuple(cases)


def _historical_universe_case(
    *,
    project_id: str,
    name: str,
    sector: str,
    timestamp: datetime,
    case_id: str | None = None,
) -> Any:
    from hunter.historical.models import HistoricalValidationCase

    return HistoricalValidationCase(
        case_id=case_id or f"universe-{project_id}-{timestamp.date().isoformat()}",
        project_id=project_id,
        project_slug=project_id,
        project_name=name,
        symbol=project_id[:8].upper(),
        sector=sector,
        case_type="NEUTRAL_CONTROL",
        evaluation_timestamp=timestamp,
        historical_cutoff_timestamp=timestamp,
        project_lifecycle_state="active",
        token_lifecycle_state="active",
    )


def _historical_case_progress() -> tuple[int, int]:
    rows = _read_jsonl_cli(Path("data/historical_validation/challenge_results.jsonl"))
    completed = sum(1 for row in rows if row.get("realized_outcome") != "INSUFFICIENT_OUTCOME_DATA")
    return completed, len(rows) - completed


def _historical_completion_state_counts(config: Any) -> tuple[int, int]:
    rows = _historical_gap_rows(config)
    completed = sum(1 for row in rows if row["replay_blocked_by"] == "none")
    return completed, len(rows) - completed


def _historical_outcome_benchmark_coverage() -> tuple[float, float]:
    outcomes = _read_jsonl_cli(Path("data/historical_validation/outcomes.jsonl"))
    benchmarks = _read_jsonl_cli(Path("data/historical_validation/benchmark_outcomes.jsonl"))
    outcome_coverage = (
        round(
            (
                sum(1 for row in outcomes if row.get("final_success_label") != "INSUFFICIENT_OUTCOME_DATA")
                / len(outcomes)
            )
            * 100.0,
            2,
        )
        if outcomes
        else 0.0
    )
    benchmark_coverage = (
        round((sum(1 for row in benchmarks if row.get("excess_return") is not None) / len(benchmarks)) * 100.0, 2)
        if benchmarks
        else 0.0
    )
    return outcome_coverage, benchmark_coverage


def _historical_gap_rows(config: Any) -> tuple[dict[str, str], ...]:
    validation_repository = HistoricalValidationRepository()
    acquisition_repository = HistoricalEvidenceRepository()
    latest_snapshots = _latest_snapshots_by_case(validation_repository)
    outcomes = {row["case_id"]: row for row in _read_jsonl_cli(Path("data/historical_validation/outcomes.jsonl"))}
    benchmark_rows = _read_jsonl_cli(Path("data/historical_validation/benchmark_outcomes.jsonl"))
    valid_historical = {item.evidence_id for item in acquisition_repository.validations() if item.status == "valid"}
    normalized = tuple(item for item in acquisition_repository.normalized() if item.evidence_id in valid_historical)
    rows = []
    for case in config.challenge_cases:
        snapshot = latest_snapshots.get(case.case_id)
        missing_evidence = tuple(snapshot.missing_evidence if snapshot else config.required_evidence)
        case_records = tuple(item for item in normalized if item.case_id == case.case_id)
        missing_providers = tuple(
            provider
            for provider, expected in {
                "coingecko-historical": "historical_market",
                "defillama-historical": "historical_protocol",
                "github-historical": "historical_developer",
                "historical-rss-announcements": "historical_narrative",
            }.items()
            if not any(item.provider == provider and item.metric == expected for item in case_records)
        )
        outcome = outcomes.get(case.case_id, {})
        missing_windows = tuple(
            str(window.get("window_days"))
            for window in outcome.get("windows", ())
            if isinstance(window, dict) and window.get("simple_return") is None
        )
        missing_benchmarks = tuple(
            str(row.get("window_days"))
            for row in benchmark_rows
            if row.get("case_id") == case.case_id and row.get("excess_return") is None
        )
        missing_timestamps = _missing_window_timestamps(case, config.evaluation_windows, normalized)
        coverage = _case_snapshot_coverage(snapshot)
        blocked = _blocked_reason(missing_evidence, missing_windows, missing_benchmarks)
        rows.append(
            {
                "case_id": case.case_id,
                "project_id": case.project_id,
                "missing_evidence": ",".join(missing_evidence) or "none",
                "missing_providers": ",".join(missing_providers) or "none",
                "missing_timestamps": ",".join(missing_timestamps) or "none",
                "missing_outcome_windows": ",".join(missing_windows) or "none",
                "missing_benchmarks": ",".join(missing_benchmarks) or "none",
                "replay_blocked_by": blocked,
                "coverage": f"{coverage:.2f}",
            }
        )
    return tuple(rows)


def _latest_snapshots_by_case(repository: Any) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    for snapshot in repository.snapshots():
        current = snapshots.get(snapshot.case_id)
        if current is None or snapshot.version > current.version:
            snapshots[snapshot.case_id] = snapshot
    return snapshots


def _missing_window_timestamps(case: Any, windows: tuple[int, ...], normalized: tuple[Any, ...]) -> tuple[str, ...]:
    project_rows = tuple(
        item for item in normalized if item.project_id == case.project_id and item.metric == "historical_market"
    )
    missing = []
    for days in windows:
        end = case.evaluation_timestamp + timedelta(days=int(days))
        has_start = any(item.event_timestamp <= case.evaluation_timestamp for item in project_rows)
        has_end = any(case.evaluation_timestamp < item.event_timestamp <= end for item in project_rows)
        if not has_start or not has_end:
            missing.append(f"{days}d")
    return tuple(missing)


def _case_snapshot_coverage(snapshot: Any | None) -> float:
    if snapshot is None:
        return 0.0
    available = len(snapshot.evidence)
    total = available + len(snapshot.missing_evidence)
    return round((available / max(total, 1)) * 100.0, 2)


def _blocked_reason(
    missing_evidence: tuple[str, ...],
    missing_windows: tuple[str, ...],
    missing_benchmarks: tuple[str, ...],
) -> str:
    reasons = []
    if missing_evidence:
        reasons.append("missing_evidence")
    if missing_windows:
        reasons.append("missing_outcome_windows")
    if missing_benchmarks:
        reasons.append("missing_benchmarks")
    return ",".join(reasons) or "none"


def _acquisition(args: object) -> int:
    config = load_acquisition_config(Path(args.acquisition_config))
    registry = ProviderRegistry()
    repository = InMemoryAcquisitionRepository()
    command = getattr(args, "acquisition_command", None)
    if command == "status":
        enabled = "enabled" if config.enabled else "disabled"
        print(f"acquisition={enabled} providers={len(config.providers)} cache={config.cache.enabled}")
        return 0
    if command == "providers":
        if not registry.providers():
            print("no providers registered")
            return 0
        for provider in registry.providers():
            metadata = provider.metadata
            print(f"{metadata.name}\t{metadata.availability}\t{','.join(metadata.supported_metrics)}")
        return 0
    if command == "validate":
        print(f"configuration valid providers={len(config.providers)}")
        return 0
    if command == "sync":
        print("no enabled providers registered")
        return 0
    if command == "history":
        print(f"runs={len(repository.history())}")
        return 0
    if command == "health":
        print(f"checked_at={datetime.now(tz=UTC).isoformat()} providers={len(registry.providers())}")
        return 0
    print("acquisition command required")
    return 1


def _auth(args: object) -> int:
    config = load_auth_config(Path(args.providers_config))
    registry = AuthRegistry(config)
    command = getattr(args, "auth_command", None)
    if command == "status":
        for provider in registry.providers():
            state = registry.state(provider.name)
            print(f"{provider.name}\t{state.mode}\t{state.credential_status}\t{state.credential_source or '-'}")
        return 0
    if command == "validate":
        invalid = 0
        for provider in registry.providers():
            state = registry.state(provider.name)
            if state.mode == "disabled" or state.credential_status == "invalid":
                invalid += 1
            print(f"{provider.name}\t{state.credential_status}\t{state.message}")
        return 0 if invalid == 0 else 2
    if command == "providers":
        for provider in registry.providers():
            print(f"{provider.name}\t{','.join(provider_capabilities(provider))}")
        return 0
    if command == "quota":
        for provider in registry.providers():
            quota = registry.quota(provider.name)
            limit = "-" if quota.limit is None else str(quota.limit)
            remaining = "-" if quota.remaining is None else str(quota.remaining)
            mode = "authenticated" if quota.authenticated else "anonymous"
            print(f"{provider.name}\t{mode}\tlimit={limit}\tremaining={remaining}\tsource={quota.source}")
        return 0
    if command == "doctor":
        for provider in registry.providers():
            state = registry.state(provider.name)
            quota = registry.quota(provider.name)
            print(
                f"{provider.name}\tmode={state.mode}\tcredential={state.credential_status}"
                f"\tquota={quota.remaining if quota.remaining is not None else '-'}"
                f"\tmessage={state.message}"
            )
        return 0
    print("auth command required")
    return 1


def _coingecko(args: object) -> int:
    config = load_acquisition_config(Path(args.acquisition_config))
    provider_config = next((item for item in config.providers if item.name == "coingecko"), None)
    command = getattr(args, "coingecko_command", None)
    if command == "status":
        state = "enabled" if provider_config and provider_config.enabled else "disabled"
        repository = FileAcquisitionRepository()
        valid_ids = {evidence_id for evidence_id, item in repository.validations.items() if item.status == "valid"}
        coingecko_normalized = tuple(item for item in repository.normalized.values() if item.provider == "coingecko")
        print(
            f"coingecko={state} raw={sum(1 for item in repository.raw.values() if item.provider == 'coingecko')} "
            f"normalized={len(coingecko_normalized)} valid={sum(1 for item in coingecko_normalized if item.evidence_id in valid_ids)}"
        )
        return 0
    if command == "health":
        state = "enabled" if provider_config and provider_config.enabled else "disabled"
        stats = _coingecko_persistent_statistics(
            FileAcquisitionRepository(),
            universe_targets=_coingecko_universe_targets(args),
        )
        print(
            f"coingecko={state} success_rate={stats['success_rate']:.4f} retries={stats['retry_count']} "
            f"rate_limits={stats['rate_limit_count']} accepted={stats['accepted']} rejected={stats['rejected']}"
        )
        return 0
    if command == "statistics":
        stats = _coingecko_persistent_statistics(
            FileAcquisitionRepository(),
            universe_targets=_coingecko_universe_targets(args),
        )
        print(
            f"raw={stats['raw']} normalized={stats['normalized']} valid={stats['valid']} "
            f"duplicate={stats['duplicate']} stale={stats['stale']} invalid={stats['invalid']} "
            f"market_coverage={stats['market_coverage']:.2f} detail_coverage={stats['detail_coverage']:.2f} "
            f"accepted={stats['accepted']} rejected={stats['rejected']} pending={stats['pending']} "
            f"rejection_reasons={stats['rejection_reasons']}"
        )
        return 0
    if command == "pending":
        stats = _coingecko_persistent_statistics(
            FileAcquisitionRepository(),
            universe_targets=_coingecko_universe_targets(args),
        )
        print(f"pending_detail_enrichment={stats['pending']} targets={stats['pending_targets']}")
        return 0
    if command in {"universe", "unresolved", "resolve"}:
        if provider_config is None:
            print("coingecko provider not configured")
            return 0
        if not provider_config.enabled:
            print("coingecko provider not enabled")
            return 0
        resolutions = _coingecko_identifier_resolutions(args, provider_config, config)
        counts = Counter(row.status for row in resolutions)
        accepted = _coingecko_persistent_statistics(
            FileAcquisitionRepository(),
            universe_targets=_coingecko_universe_targets(args),
        )
        print(
            f"configured={len(resolutions)} resolved={counts['RESOLVED']} unresolved="
            f"{len(resolutions) - counts['RESOLVED'] - counts['UNSUPPORTED']} unsupported={counts['UNSUPPORTED']} "
            f"invalid_mappings={counts['INVALID_ID'] + counts['AMBIGUOUS']} "
            f"accepted_market_records={accepted['market_records']} market_coverage={accepted['market_coverage']:.2f}"
        )
        rows = resolutions if command in {"unresolved", "resolve"} else ()
        for row in rows:
            if command == "unresolved" and row.status in {"RESOLVED", "UNSUPPORTED"}:
                continue
            print(f"{row.project_id}\t{row.coingecko_id or '-'}\t{row.status}\t{row.reason}")
        return 0
    if command == "validate":
        if provider_config is None:
            print("coingecko provider not configured")
            return 0
        print("coingecko configuration valid")
        return 0
    if command in {"sync", "resume"}:
        if provider_config is None or not provider_config.enabled:
            print("coingecko provider not enabled")
            return 0
        settings = provider_config.settings or {}
        provider = CoinGeckoProvider(_coingecko_provider_config(provider_config, config))
        resolutions = _coingecko_identifier_resolutions(args, provider_config, config)
        project_ids = coingecko_sync_ids(resolutions)
        target_map = coingecko_target_map(resolutions)
        repository = FileAcquisitionRepository()
        detail_cache = _coingecko_detail_cache(
            repository,
            ttl_seconds=int(settings.get("detail_metadata_ttl_seconds", 604_800)),
            as_of=datetime.now(tz=UTC),
        )
        requested_at = datetime.now(tz=UTC)
        pipeline = AcquisitionPipeline(
            normalizer=CoinGeckoEvidenceNormalizer(),
            validator=EvidenceAcquisitionValidator(
                stale_after_seconds=config.stale_after_seconds,
                minimum_confidence=1.0,
            ),
            repository=repository,
            config=config,
        )
        run = pipeline.sync(
            provider,
            AcquisitionRequest(
                domain="market",
                metric="coingecko_market_profile",
                target_id="configured-projects",
                requested_at=requested_at,
                mode="resume" if command == "resume" else "incremental",
                parameters={"project_ids": project_ids, "target_map": target_map, "detail_cache": detail_cache},
            ),
        )
        print(
            f"{run.run_id}\trequested={len(resolutions)}\tresolved={len(project_ids)}"
            f"\traw={run.raw_count}\tnormalized={run.normalized_count}"
            f"\tvalid={run.valid_count}\tinvalid={run.invalid_count}"
            f"\tmarket_accepted={provider.statistics.accepted_record_count}"
            f"\tdetail_accepted={provider.statistics.accepted_detail_count}"
            f"\treused_from_cache={provider.statistics.cached_detail_count}"
            f"\tdeferred={provider.statistics.deferred_detail_count}"
            f"\tpending_enrichment={provider.statistics.deferred_detail_count}"
            f"\trejected={provider.statistics.rejected_record_count}"
            f"\tretried={provider.statistics.retry_count}"
            f"\trate_limited={provider.statistics.rate_limit_count}"
            f"\tsucceeded={provider.statistics.success_count}"
            f"\tsuccess_rate={provider.statistics.success_rate:.4f}"
        )
        return 0
    print("coingecko command required")
    return 1


def _defillama(args: object) -> int:
    config = load_acquisition_config(Path(args.acquisition_config))
    provider_config = next((item for item in config.providers if item.name == "defillama"), None)
    command = getattr(args, "defillama_command", None)
    repository = FileAcquisitionRepository()
    stats = _defillama_persistent_statistics(repository, universe_targets=_defillama_universe_targets(args))
    if command == "status":
        state = "enabled" if provider_config and provider_config.enabled else "disabled"
        print(
            f"defillama={state} raw={stats['raw']} normalized={stats['normalized']} valid={stats['valid']} "
            f"tvl_coverage={stats['tvl_coverage']:.2f} revenue_coverage={stats['revenue_coverage']:.2f} "
            f"fee_coverage={stats['fee_coverage']:.2f}"
        )
        return 0
    if command == "validate":
        if provider_config is None:
            print("defillama provider not configured")
            return 0
        print("defillama configuration valid")
        return 0
    if command in {"unresolved", "resolve"}:
        if provider_config is None:
            print("defillama provider not configured")
            return 0
        if not provider_config.enabled:
            print("defillama provider not enabled")
            return 0
        resolutions = _defillama_identifier_resolutions(args, provider_config, config)
        counts = Counter(row.status for row in resolutions)
        print(
            f"configured={len(resolutions)} resolved={counts['RESOLVED']} "
            f"unresolved={len(resolutions) - counts['RESOLVED'] - counts['UNSUPPORTED']} "
            f"unsupported={counts['UNSUPPORTED']} invalid_mappings={counts['INVALID_ID'] + counts['AMBIGUOUS']} "
            f"accepted_records={stats['protocol_records']} tvl_coverage={stats['tvl_coverage']:.2f} "
            f"revenue_coverage={stats['revenue_coverage']:.2f} fee_coverage={stats['fee_coverage']:.2f}"
        )
        for row in resolutions:
            if command == "unresolved" and row.status in {"RESOLVED", "UNSUPPORTED"}:
                continue
            print(f"{row.project_id}\t{row.coingecko_id or '-'}\t{row.status}\t{row.reason}")
        return 0
    if command == "sync":
        if provider_config is None or not provider_config.enabled:
            print("defillama provider not enabled")
            return 0
        provider = DefiLlamaProvider(_defillama_provider_config(provider_config, config))
        resolutions = _defillama_identifier_resolutions(args, provider_config, config)
        protocol_slugs = defillama_sync_ids(resolutions)
        target_map = defillama_target_map(resolutions)
        pipeline = AcquisitionPipeline(
            normalizer=DefiLlamaEvidenceNormalizer(),
            validator=EvidenceAcquisitionValidator(
                stale_after_seconds=config.stale_after_seconds,
                minimum_confidence=1.0,
            ),
            repository=repository,
            config=config,
        )
        run = pipeline.sync(
            provider,
            AcquisitionRequest(
                domain="protocol",
                metric="defillama_protocol_profile",
                target_id="configured-projects",
                requested_at=datetime.now(tz=UTC),
                mode="incremental",
                parameters={"project_ids": protocol_slugs, "target_map": target_map},
            ),
        )
        print(
            f"{run.run_id}\tconfigured={len(resolutions)}\tresolved={len(protocol_slugs)}"
            f"\traw={run.raw_count}\tnormalized={run.normalized_count}\tvalid={run.valid_count}"
            f"\tinvalid={run.invalid_count}\taccepted={provider.statistics.accepted_record_count}"
            f"\trejected={provider.statistics.rejected_record_count}\ttvl={provider.statistics.tvl_record_count}"
            f"\trevenue={provider.statistics.revenue_record_count}\tfees={provider.statistics.fee_record_count}"
            f"\trate_limits={provider.statistics.rate_limit_count}\tretries={provider.statistics.retry_count}"
            f"\tsuccess_rate={provider.statistics.success_rate:.4f}"
        )
        return 0
    print("defillama command required")
    return 1


def _protocol(args: object) -> int:
    command = getattr(args, "protocol_command", None)
    if command == "sync":
        args.defillama_command = "sync"
        return _defillama(args)
    if command == "validate":
        args.defillama_command = "validate"
        result = _defillama(args)
        repository = FileAcquisitionRepository()
        latest = _latest_protocol_evidence(repository)
        invalid = tuple(
            validation
            for validation in repository.validations.values()
            if validation.evidence_id in {item.evidence_id for item in latest.values()}
            and validation.status not in {"valid", "duplicate"}
        )
        print(f"protocol_records={len(latest)} invalid={len(invalid)}")
        return result if result != 0 else 0 if not invalid else 2
    if command in {"coverage", "report", "explain"}:
        config = load_acquisition_config(Path(args.acquisition_config))
        provider_config = next((item for item in config.providers if item.name == "defillama"), None)
        repository = FileAcquisitionRepository()
        latest = _latest_protocol_evidence(repository)
        targets = _defillama_universe_targets(args)
        coverage = round((len(latest) / max(len(targets), 1)) * 100.0, 2)
        if command == "coverage":
            print(
                f"projects={len(targets)} available={len(latest)} missing={len(targets) - len(latest)} coverage={coverage:.2f}"
            )
            return 0
        if provider_config is None:
            print("defillama provider not configured")
            return 0
        resolutions = _defillama_identifier_resolutions(args, provider_config, config)
        project_filter = getattr(args, "project", None)
        for row in _protocol_audit_rows(resolutions, latest):
            if command == "explain" and project_filter and row["project"] != project_filter:
                continue
            print(
                f"{row['project']}\t{row['provider']}\t{row['slug']}\t{row['status']}\t{row['reason']}"
                f"\ttvl={row['tvl_available']}\trevenue={row['revenue_available']}\tfees={row['fees_available']}"
                f"\tfreshness={row['freshness']}\tvalidation={row['validation']}"
            )
        return 0
    print("protocol command required")
    return 1


def _latest_protocol_evidence(repository: FileAcquisitionRepository) -> dict[str, NormalizedEvidence]:
    valid_ids = {item.evidence_id for item in repository.validations.values() if item.status == "valid"}
    latest: dict[str, NormalizedEvidence] = {}
    for evidence in repository.normalized.values():
        if (
            evidence.provider != "defillama"
            or evidence.metric != "defillama_protocol_profile"
            or evidence.evidence_id not in valid_ids
        ):
            continue
        current = latest.get(evidence.target_id)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            latest[evidence.target_id] = evidence
    return latest


def _protocol_audit_rows(
    resolutions: tuple[ProjectIdentifierResolution, ...],
    latest: dict[str, NormalizedEvidence],
) -> tuple[dict[str, str], ...]:
    rows = []
    for resolution in resolutions:
        evidence = latest.get(resolution.project_id)
        status = _protocol_status(resolution, evidence)
        payload = evidence.raw_metrics if evidence is not None else {}
        rows.append(
            {
                "project": resolution.project_id,
                "provider": "defillama",
                "slug": resolution.coingecko_id or "-",
                "status": status,
                "reason": _protocol_reason(resolution, evidence),
                "tvl_available": str(payload.get("tvl") is not None).lower(),
                "revenue_available": str(
                    payload.get("revenue") is not None or payload.get("daily_revenue") is not None
                ).lower(),
                "fees_available": str(payload.get("fees") is not None or payload.get("daily_fees") is not None).lower(),
                "freshness": f"{evidence.freshness:.4f}" if evidence is not None else "0.0000",
                "validation": "valid" if evidence is not None else resolution.status.lower(),
            }
        )
    return tuple(rows)


def _protocol_status(resolution: ProjectIdentifierResolution, evidence: NormalizedEvidence | None) -> str:
    if evidence is not None:
        return "AVAILABLE"
    if resolution.status == "INVALID_ID":
        return "INVALID_IDENTIFIER"
    if resolution.status == "UNSUPPORTED":
        return "NO_PUBLIC_PROTOCOL"
    if resolution.status in {"NOT_FOUND", "REQUEST_FAILED", "RATE_LIMITED"}:
        return "SUPPORTED_WITH_PROVIDER_FIX" if resolution.coingecko_id else "UNSUPPORTED_BY_PROVIDER"
    return "SUPPORTED_WITH_PROVIDER_FIX"


def _protocol_reason(resolution: ProjectIdentifierResolution, evidence: NormalizedEvidence | None) -> str:
    if evidence is not None:
        return "validated persisted protocol evidence"
    if resolution.status == "UNSUPPORTED":
        if resolution.coingecko_id:
            return "DefiLlama exposes a canonical identifier but no usable public TVL protocol endpoint"
        return "no verified public protocol TVL endpoint configured"
    return resolution.reason or "protocol evidence unavailable"


def _github(args: object) -> int:
    config = load_acquisition_config(Path(args.acquisition_config))
    provider_config = next((item for item in config.providers if item.name == "github"), None)
    command = getattr(args, "github_command", None)
    repository = FileAcquisitionRepository()
    stats = _github_persistent_statistics(repository, universe_targets=_github_universe_targets(args))
    if command == "status":
        state = "enabled" if provider_config and provider_config.enabled else "disabled"
        print(
            f"github={state} raw={stats['raw']} normalized={stats['normalized']} valid={stats['valid']} "
            f"commit_coverage={stats['commit_coverage']:.2f} "
            f"contributor_coverage={stats['contributor_coverage']:.2f} "
            f"release_coverage={stats['release_coverage']:.2f}"
        )
        return 0
    if command == "validate":
        if provider_config is None:
            print("github provider not configured")
            return 0
        print("github configuration valid")
        return 0
    if command == "statistics":
        print(
            f"raw={stats['raw']} normalized={stats['normalized']} valid={stats['valid']} "
            f"accepted_records={stats['repository_records']} "
            f"commit_coverage={stats['commit_coverage']:.2f} "
            f"contributor_coverage={stats['contributor_coverage']:.2f} "
            f"release_coverage={stats['release_coverage']:.2f}"
        )
        return 0
    if command in {"resolve", "unresolved"}:
        if provider_config is None:
            print("github provider not configured")
            return 0
        if not provider_config.enabled:
            print("github provider not enabled")
            return 0
        resolutions = _github_identifier_resolutions(args, provider_config, config)
        counts = Counter(row.status for row in resolutions)
        print(
            f"configured={len(_github_universe_targets(args))} resolved={counts['RESOLVED']} "
            f"unresolved={len(resolutions) - counts['RESOLVED'] - counts['UNSUPPORTED']} "
            f"unsupported={counts['UNSUPPORTED']} invalid_mappings={counts['INVALID_ID'] + counts['AMBIGUOUS']} "
            f"accepted_records={stats['repository_records']} commit_coverage={stats['commit_coverage']:.2f} "
            f"contributor_coverage={stats['contributor_coverage']:.2f} "
            f"release_coverage={stats['release_coverage']:.2f}"
        )
        for row in resolutions:
            if command == "unresolved" and row.status in {"RESOLVED", "UNSUPPORTED"}:
                continue
            print(f"{row.project_id}\t{row.repository or '-'}\t{row.status}\t{row.reason}")
        return 0
    if command == "sync":
        if provider_config is None or not provider_config.enabled:
            print("github provider not enabled")
            return 0
        provider = GitHubProvider(_github_provider_config(provider_config, config))
        resolutions = _github_configured_resolutions(args)
        repositories = github_sync_ids(resolutions)
        target_map = github_target_map(resolutions)
        pipeline = AcquisitionPipeline(
            normalizer=GitHubEvidenceNormalizer(),
            validator=EvidenceAcquisitionValidator(
                stale_after_seconds=config.stale_after_seconds,
                minimum_confidence=1.0,
            ),
            repository=repository,
            config=config,
        )
        run = pipeline.sync(
            provider,
            AcquisitionRequest(
                domain="github",
                metric="github_repository_profile",
                target_id="configured-projects",
                requested_at=datetime.now(tz=UTC),
                mode="incremental",
                parameters={
                    "project_ids": repositories,
                    "target_map": target_map,
                    "response_cache": _github_response_cache(repository),
                },
            ),
        )
        print(
            f"{run.run_id}\tconfigured={len(_github_universe_targets(args))}\tresolved={len(repositories)}"
            f"\traw={run.raw_count}\tnormalized={run.normalized_count}\tvalid={run.valid_count}"
            f"\tinvalid={run.invalid_count}\taccepted={provider.statistics.accepted_record_count}"
            f"\trejected={provider.statistics.rejected_record_count}\tcommits={provider.statistics.commit_record_count}"
            f"\tcontributors={provider.statistics.contributor_record_count}"
            f"\treleases={provider.statistics.release_record_count}"
            f"\tetag_reused={provider.statistics.etag_reused_count}"
            f"\trate_limits={provider.statistics.rate_limit_count}\tretries={provider.statistics.retry_count}"
            f"\tsuccess_rate={provider.statistics.success_rate:.4f}"
        )
        return 0
    print("github command required")
    return 1


def _developer(args: object) -> int:
    command = getattr(args, "developer_command", None)
    if command == "sync":
        args.github_command = "sync"
        return _github(args)
    repository = FileAcquisitionRepository()
    targets = _github_universe_targets(args)
    latest = _latest_github_evidence(repository)
    identifiers = load_project_identifiers(Path(args.project_identifiers_config))
    stats = _developer_coverage_stats(targets, latest, identifiers)
    if command == "coverage":
        print(
            f"projects={len(targets)} covered={stats['covered']} live_repositories={stats['live_repositories']} "
            f"missing={stats['missing']} coverage={stats['coverage']:.2f} "
            f"live_repository_coverage={stats['live_repository_coverage']:.2f} "
            f"commit_coverage={stats['commit_coverage']:.2f} "
            f"contributor_coverage={stats['contributor_coverage']:.2f} release_coverage={stats['release_coverage']:.2f}"
        )
        return 0
    if command in {"report", "explain"}:
        project_filter = getattr(args, "project", None)
        for project_id in targets:
            if command == "explain" and project_filter and project_id != project_filter:
                continue
            evidence = latest.get(project_id)
            identifier = identifiers.get(project_id)
            configured = tuple(identifier.github_repositories) if identifier is not None else ()
            status = (
                "AVAILABLE"
                if evidence is not None
                else "NO_PUBLIC_REPOSITORY" if not configured else "NO_LIVE_EVIDENCE"
            )
            payload = evidence.raw_metrics if evidence is not None else {}
            repositories = (
                (str(payload.get("full_name") or payload.get("repository_name")),) if evidence else configured
            )
            print(
                f"{project_id}\t{status}\trepository={','.join(repo for repo in repositories if repo) or 'NO_PUBLIC_REPOSITORY'}"
                f"\torganization={payload.get('owner') or _repository_owner(configured)}"
                f"\tcontributors={payload.get('contributors_count', 'unavailable')}"
                f"\tcommits={payload.get('commit_count', 'unavailable')}"
                f"\tcommits_30d={payload.get('commits_30d', 'unavailable')}"
                f"\tcommits_90d={payload.get('commits_90d', 'unavailable')}"
                f"\treleases={payload.get('releases', 'unavailable')}"
                f"\tissues_open={payload.get('open_issues', 'unavailable')}"
                f"\tissues_closed={payload.get('closed_issues', 'unavailable')}"
                f"\tconfidence={evidence.confidence if evidence is not None else 0.0:.4f}"
                f"\tfreshness={evidence.freshness if evidence is not None else 0.0:.4f}"
                f"\tevidence={evidence.evidence_id if evidence is not None else '-'}"
            )
        return 0
    print("developer command required")
    return 1


def _onchain(args: object) -> int:
    config = load_onchain_config(Path(args.onchain_config))
    registry = SurfaceRegistry(config)
    command = getattr(args, "onchain_command", None)
    if command == "registry":
        subcommand = getattr(args, "onchain_registry_command", None)
        validation = registry.validate()
        if subcommand == "validate":
            print(
                f"valid={str(validation.valid).lower()} surfaces={validation.surfaces} "
                f"projects={validation.projects_with_surface} issues={','.join(validation.issues) or '-'}"
            )
            return 0 if validation.valid else 2
        if subcommand == "coverage":
            total = _configured_project_count(args)
            coverage = round((validation.projects_with_surface / max(total, 1)) * 100, 2)
            print(
                f"projects={total} verified_projects={validation.projects_with_surface} "
                f"surfaces={validation.surfaces} coverage={coverage:.2f}"
            )
            return 0
    if command == "sync":
        snapshots = CapitalFlowEngine(config).sync(getattr(args, "project", None))
        live = sum(1 for item in snapshots if item.status == "live")
        unavailable = len(snapshots) - live
        print(f"synced={len(snapshots)} live={live} unavailable={unavailable}")
        return 0
    if command == "coverage":
        return _capital_flow_coverage(config)
    if command == "report":
        return _capital_flow_report(config, getattr(args, "project", None))
    if command == "explain":
        return _capital_flow_explain(config, str(args.project))
    if command == "snapshots":
        return _capital_flow_snapshots(config, str(args.project))
    if command == "providers":
        return _onchain_providers(args, config)
    if command == "automation":
        return _onchain_automation(args, config)
    print("onchain command required")
    return 1


def _capital_flow(args: object) -> int:
    config = load_onchain_config(Path(args.onchain_config))
    command = getattr(args, "capital_flow_command", None)
    if command == "coverage":
        return _capital_flow_coverage(config)
    if command == "report":
        return _capital_flow_report(config, None)
    if command == "explain":
        return _capital_flow_explain(config, str(args.project))
    print("capital-flow command required")
    return 1


def _onchain_providers(args: object, config: object) -> int:
    repository = OnChainRepository(str(config.retention.get("runtime_root", "data/onchain/runtime")))  # type: ignore[attr-defined]
    engine = CapitalFlowEngine(config, repository=repository)  # type: ignore[arg-type]
    command = getattr(args, "onchain_providers_command", None)
    if command == "check":
        states = engine.check_providers(getattr(args, "chain", None))
        for state in states:
            print(
                f"{state.network}\tchain_id={state.chain_id}\tendpoint={state.endpoint_identity}"
                f"\tstatus={state.status}\tlatest={state.latest_block}\tfailure={state.failure_type or '-'}"
            )
        return 0
    if command == "status" or command is None:
        rows = repository.provider_states()
        if not rows:
            print("provider_status=unavailable")
            return 0
        for row in rows:
            print(
                f"{row.get('network')}\tchain_id={row.get('chain_id')}\tendpoint={row.get('endpoint_identity')}"
                f"\tstatus={row.get('status')}\tlatest={row.get('latest_block')}"
                f"\tcooldown_until={row.get('cooldown_until')}"
            )
        return 0
    if command == "reset-cooldown":
        engine.reset_provider_cooldown(str(args.chain))
        print(f"cooldown_reset={args.chain}")
        return 0
    print("onchain providers command required")
    return 1


def _onchain_automation(args: object, config: object) -> int:
    manager = OnChainAutomationManager(config)  # type: ignore[arg-type]
    command = getattr(args, "onchain_automation_command", None)
    if command == "install":
        result = manager.install()
        print(f"installed={result.installed} created={result.created} jobs={','.join(result.jobs)}")
        return 0
    if command == "status":
        rows = manager.status()
        print(f"jobs={len(rows)} worker_startup='{worker_startup_command()}'")
        for row in rows:
            print(
                f"{row.get('job_id')}\tenabled={row.get('enabled')}\tlast_attempted={row.get('last_attempted_run')}"
                f"\tlast_success={row.get('last_successful_run')}\tlast_failure={row.get('last_failure')}"
                f"\tnext={row.get('next_scheduled_run')}\tactive_provider={row.get('active_provider')}"
                f"\tcheckpoint={row.get('checkpoint')}\tfreshness={row.get('project_freshness')}"
                f"\tmissed_windows={row.get('missed_windows')}"
            )
        return 0
    if command == "run-now":
        runs = manager.run_now()
        for run in runs:
            print(f"{run['job_id']}\t{run['status']}")
        return 0
    if command == "pause":
        manager.set_enabled(False)
        print("onchain_automation=paused")
        return 0
    if command == "resume":
        manager.set_enabled(True)
        print("onchain_automation=resumed")
        return 0
    if command == "failures":
        failures = manager.failures()
        if not failures:
            print("failures=0")
            return 0
        for failure in failures:
            print(f"{failure.get('job_id')}\t{failure.get('last_failure')}")
        return 2
    print("onchain automation command required")
    return 1


def _capital_flow_coverage(config: object) -> int:
    engine = CapitalFlowEngine(config)  # type: ignore[arg-type]
    coverage = engine.coverage()
    coverage_value = float(coverage["coverage"])
    print(
        f"projects={coverage['projects']} verified_surfaces={coverage['verified_surfaces']} "
        f"projects_with_surface={coverage['projects_with_surface']} live_projects={coverage['live_projects']} "
        f"adapter_supported_chains={coverage['adapter_supported_chains']} "
        f"provider_reachable_chains={coverage['provider_reachable_chains']} "
        f"raw_observation_projects={coverage['raw_observation_projects']} coverage={coverage_value:.2f}"
    )
    return 0


def _capital_flow_report(config: object, project: str | None) -> int:
    snapshots = OnChainRepository(str(config.retention.get("runtime_root", "data/onchain/runtime"))).snapshots()  # type: ignore[attr-defined]
    surfaces = {surface.project: surface for surface in config.surfaces}  # type: ignore[attr-defined]
    projects = _market_project_ids(_ArgsWithMarketConfig())
    for project_id in projects:
        if project and project_id != project:
            continue
        if project_id not in surfaces:
            print(f"{project_id}\tNO_VERIFIED_ONCHAIN_SURFACE")
            continue
        project_snapshots = [row for row in snapshots if row.get("project") == project_id]
        if not project_snapshots:
            print(f"{project_id}\tverified_surface=true\tstatus=cached_empty\tlive=false")
            continue
        latest = sorted(project_snapshots, key=lambda row: str(row.get("generated_at", "")))[-1]
        print(
            f"{project_id}\tstatus={latest.get('status')}\tchain_id={latest.get('chain_id')}"
            f"\twindow={latest.get('window')}\tnet_external_flow={latest.get('net_external_flow')}"
            f"\tevidence={','.join(latest.get('evidence_ids', ())) or '-'}"
        )
    return 0


def _capital_flow_explain(config: object, project: str) -> int:
    surfaces = tuple(surface for surface in config.surfaces if surface.project == project)  # type: ignore[attr-defined]
    if not surfaces:
        print(f"{project}\tNO_VERIFIED_ONCHAIN_SURFACE")
        return 0
    print(
        f"{project}\tverified_surfaces={len(surfaces)}"
        f"\taddresses={','.join(surface.address for surface in surfaces)}"
        f"\tevidence={','.join(surface.evidence_id for surface in surfaces)}"
    )
    return _capital_flow_report(config, project)


def _capital_flow_snapshots(config: object, project: str) -> int:
    snapshots = OnChainRepository(str(config.retention.get("runtime_root", "data/onchain/runtime"))).snapshots()  # type: ignore[attr-defined]
    found = False
    for snapshot in snapshots:
        if snapshot.get("project") != project:
            continue
        found = True
        print(
            f"{project}\tchain_id={snapshot.get('chain_id')}\twindow={snapshot.get('window')}"
            f"\tstatus={snapshot.get('status')}\tstart={snapshot.get('start_block')}"
            f"\tend={snapshot.get('end_block')}\tunavailable={','.join(snapshot.get('unavailable_fields', ())) or '-'}"
        )
    if not found:
        print(f"{project}\tNO_SNAPSHOT")
    return 0


class _ArgsWithMarketConfig:
    market_validation_config = "configs/market_validation.yaml"


def _technology(args: object) -> int:
    command = getattr(args, "technology_command", None)
    if command == "build":
        args.graph_command = "build"
        return _graph(args)
    if command in {"coverage", "report", "explain"}:
        args.graph_command = command
        return _graph(args)
    print("technology command required")
    return 1


def _necessity(args: object) -> int:
    command = getattr(args, "necessity_command", None)
    if command != "coverage":
        print("necessity command required")
        return 1
    assessments = _persisted_necessity_assessments()
    project_count = len(load_market_validation_config().project_universe)
    available = sum(1 for item in assessments if item.source_record_ids)
    coverage = round((available / max(project_count, 1)) * 100.0, 2)
    missing = tuple(item.technology_id for item in assessments if not item.source_record_ids)
    print(
        f"projects={project_count} available={available} insufficient={project_count - available} "
        f"coverage={coverage:.2f} missing={','.join(missing) or '-'}"
    )
    return 0


def _latest_github_evidence(repository: FileAcquisitionRepository) -> dict[str, NormalizedEvidence]:
    valid_ids = {evidence_id for evidence_id, item in repository.validations.items() if item.status == "valid"}
    latest: dict[str, NormalizedEvidence] = {}
    for evidence in repository.normalized.values():
        if (
            evidence.provider != "github"
            or evidence.metric != "github_repository_profile"
            or evidence.evidence_id not in valid_ids
        ):
            continue
        current = latest.get(evidence.target_id)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            latest[evidence.target_id] = evidence
    return latest


def _developer_coverage_stats(
    targets: tuple[str, ...],
    latest: dict[str, NormalizedEvidence],
    identifiers: dict[str, Any],
) -> dict[str, float | int]:
    universe = max(len(targets), 1)
    covered = {
        project
        for project in targets
        if project in latest
        or (
            (identifier := identifiers.get(project)) is not None
            and (identifier.github_repositories or identifier.github_unsupported)
        )
    }
    commit_targets = {project for project, item in latest.items() if item.raw_metrics.get("commit_count")}
    contributor_targets = {project for project, item in latest.items() if item.raw_metrics.get("contributors_count")}
    release_targets = {project for project, item in latest.items() if item.raw_metrics.get("releases")}
    return {
        "covered": len(covered),
        "live_repositories": len(set(targets) & set(latest)),
        "missing": len(set(targets) - covered),
        "coverage": round((len(covered) / universe) * 100, 2),
        "live_repository_coverage": round((len(set(targets) & set(latest)) / universe) * 100, 2),
        "commit_coverage": round((len(commit_targets & set(targets)) / universe) * 100, 2),
        "contributor_coverage": round((len(contributor_targets & set(targets)) / universe) * 100, 2),
        "release_coverage": round((len(release_targets & set(targets)) / universe) * 100, 2),
    }


def _repository_owner(repositories: tuple[str, ...]) -> str:
    if not repositories:
        return "unavailable"
    return repositories[0].split("/", 1)[0]


def _persisted_necessity_assessments():
    graph = TechnologyGraphRepository().graph()
    graph_metrics = {item.project_id: item for item in graph.metrics}
    acquisition_repository = FileAcquisitionRepository()
    latest: dict[str, NormalizedEvidence] = {}
    valid_ids = {
        evidence_id for evidence_id, item in acquisition_repository.validations.items() if item.status == "valid"
    }
    for evidence in acquisition_repository.normalized.values():
        if evidence.evidence_id not in valid_ids:
            continue
        current = latest.get(evidence.target_id)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            latest[evidence.target_id] = evidence
    config = load_market_validation_config()
    graph_config = load_technology_graph_config("configs/technology_graph.yaml")
    engine = TechnologyNecessityEngine(graph_config=graph_config)
    assessments = []
    for target in config.project_universe:
        evidence = latest.get(target.project_id)
        metric = graph_metrics.get(target.project_id)
        if evidence is None:
            inputs = TechnologyNecessityInputSet(technology_id=target.project_id, effective_at=config.effective_at)
        else:
            records = (_evidence_record(evidence),)
            snapshots = (_necessity_snapshot(target.project_id, evidence, metric),) if metric is not None else ()
            inputs = TechnologyNecessityInputSet(
                technology_id=target.project_id,
                effective_at=config.effective_at,
                evidence=records,
                snapshots=snapshots,
            )
        assessments.append(engine.assess(inputs))
    return tuple(assessments)


def _evidence_record(evidence: NormalizedEvidence) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence.evidence_id,
        created_at=evidence.normalized_at,
        effective_at=evidence.retrieved_at,
        pipeline_run_id="acquisition",
        source=evidence.provider,
        reference=evidence.source_url,
        collected_at=evidence.retrieved_at,
        reliability=evidence.confidence,
        freshness=evidence.freshness,
        raw_data=dict(evidence.raw_metrics),
    )


def _necessity_snapshot(project_id: str, evidence: NormalizedEvidence, metric: Any) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"technology-necessity-snapshot-{project_id}-{evidence.evidence_id}",
        created_at=evidence.normalized_at,
        effective_at=evidence.retrieved_at,
        snapshot_type="technology-necessity-input",
        target_id=project_id,
        record_ids=(evidence.evidence_id,),
        payload={
            "infrastructure_criticality": metric.infrastructure_centrality,
            "dependency_strength": metric.dependency_centrality,
            "replacement_difficulty": metric.replacement_availability,
            "technology_maturity": evidence.confidence,
            "market_awareness": evidence.freshness,
        },
    )


def _coingecko_sources(repository: FileAcquisitionRepository) -> dict[str, tuple[EngineValidationSource, ...]]:
    sources: dict[str, list[EngineValidationSource]] = {}
    valid_ids = {evidence_id for evidence_id, item in repository.validations.items() if item.status == "valid"}
    latest: dict[str, NormalizedEvidence] = {}
    for candidate in repository.normalized.values():
        if (
            candidate.provider != "coingecko"
            or candidate.metric != "coingecko_market_profile"
            or candidate.evidence_id not in valid_ids
        ):
            continue
        current = latest.get(candidate.target_id)
        if current is None or candidate.retrieved_at > current.retrieved_at:
            latest[candidate.target_id] = candidate
    for evidence in latest.values():
        sources.setdefault(evidence.target_id, []).append(
            EngineValidationSource(
                engine="valuation",
                score=0.0,
                confidence=evidence.confidence,
                timestamp=evidence.retrieved_at,
                freshness=evidence.freshness,
                source_record_ids=(evidence.raw_evidence_id,),
                evidence_ids=(evidence.evidence_id,),
                source="coingecko",
                collector=evidence.collector,
                repository_ids=(evidence.repository_id,),
                validation_status="VALID",
                status="AVAILABLE",
                raw_input_metrics={
                    key: value
                    for key, value in evidence.raw_metrics.items()
                    if isinstance(value, str | int | float | bool) or value is None
                },
                normalized_inputs=dict(evidence.normalized_metrics),
                applied_weight=0.0,
                weighted_contribution=0.0,
            )
        )
    return {project: tuple(items) for project, items in sources.items()}


def _mean_cli(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _read_jsonl_cli(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _coingecko_persistent_statistics(
    repository: FileAcquisitionRepository,
    *,
    universe_targets: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    raw = tuple(item for item in repository.raw.values() if item.provider == "coingecko")
    normalized = tuple(item for item in repository.normalized.values() if item.provider == "coingecko")
    normalized_ids = {item.evidence_id for item in normalized}
    validations = tuple(item for item in repository.validations.values() if item.evidence_id in normalized_ids)
    status_counts = Counter(item.status for item in validations)
    rejection_reasons: Counter[str] = Counter()
    for validation in validations:
        if validation.status == "valid":
            continue
        if validation.issues:
            for issue in validation.issues:
                rejection_reasons[f"{issue.code}:{issue.field}"] += 1
        else:
            rejection_reasons[validation.status] += 1
    request_count = len(validations)
    valid_count = status_counts["valid"]
    invalid_count = status_counts["invalid"]
    market_targets = {
        evidence.target_id
        for validation in repository.validations.values()
        if validation.status == "valid"
        for evidence in normalized
        if evidence.evidence_id == validation.evidence_id and evidence.metric == "coingecko_market_profile"
    }
    detail_targets = {item.target_id for item in raw if item.metric == "coingecko_detail_metadata"}
    pending_targets = sorted(
        {item.target_id for item in raw if item.metric == "coingecko_pending_detail_enrichment"} - detail_targets
    )
    universe = max(len(tuple(universe_targets or ())) or len(market_targets | detail_targets | set(pending_targets)), 1)
    return {
        "raw": len(raw),
        "normalized": len(normalized),
        "valid": valid_count,
        "market_records": len(market_targets),
        "duplicate": status_counts["duplicate"],
        "stale": status_counts["stale"],
        "invalid": invalid_count,
        "accepted": valid_count,
        "rejected": invalid_count + status_counts["stale"],
        "retry_count": 0,
        "rate_limit_count": 0,
        "success_rate": round(valid_count / request_count, 4) if request_count else 0.0,
        "market_coverage": round((len(market_targets) / universe) * 100, 2),
        "detail_coverage": round((len(detail_targets) / universe) * 100, 2),
        "pending": len(pending_targets),
        "pending_targets": ",".join(pending_targets) or "none",
        "rejection_reasons": ",".join(f"{key}:{value}" for key, value in sorted(rejection_reasons.items())) or "none",
    }


def _coingecko_provider_config(provider_config: ProviderConfig, config: AcquisitionConfig) -> CoinGeckoProviderConfig:
    settings = provider_config.settings or {}
    credential = AuthRegistry(load_auth_config()).credential("coingecko", "api_key")
    return CoinGeckoProviderConfig(
        base_url=str(settings.get("base_url", "https://api.coingecko.com/api/v3")),
        api_key=str(settings["api_key"]) if settings.get("api_key") else (credential.value if credential else None),
        per_page=int(settings.get("per_page", 250)),
        max_pages=int(settings.get("max_pages", 20)),
        max_attempts=int(settings.get("max_attempts", config.retry.max_attempts)),
        detail_max_attempts=int(settings.get("detail_max_attempts", 1)),
        detail_metadata_ttl_seconds=int(settings.get("detail_metadata_ttl_seconds", 604_800)),
        detail_rate_limit_threshold=int(settings.get("detail_rate_limit_threshold", 3)),
        backoff_seconds=float(settings.get("backoff_seconds", config.retry.backoff_seconds)),
        jitter_seconds=float(settings.get("jitter_seconds", 0.25)),
        min_interval_seconds=float(settings.get("min_interval_seconds", 0.0)),
        vs_currency=str(settings.get("vs_currency", "usd")),
    )


def _coingecko_identifier_resolutions(
    args: object,
    provider_config: ProviderConfig,
    config: AcquisitionConfig,
) -> tuple[ProjectIdentifierResolution, ...]:
    project_ids = _coingecko_universe_targets(args)
    identifiers = load_project_identifiers(Path(args.project_identifiers_config))
    configured_ids = tuple(
        identifier.coingecko_id
        for identifier in identifiers.values()
        if identifier.coingecko_id and not identifier.unsupported and not identifier.ambiguous
    )
    if not configured_ids:
        return resolve_configured_identifiers(project_ids, identifiers, set())
    try:
        provider = CoinGeckoProvider(_coingecko_provider_config(provider_config, config))
        rows = provider._markets(ids=tuple(dict.fromkeys(configured_ids)), page=1)  # noqa: SLF001
    except CoinGeckoHTTPError as exc:
        return resolve_configured_identifiers(project_ids, identifiers, set(), rate_limited=exc.status_code == 429)
    except ProviderUnavailableError:
        return resolve_configured_identifiers(project_ids, identifiers, set(), failed=True)
    available = {str(row.get("id")) for row in rows if row.get("id")}
    return resolve_configured_identifiers(project_ids, identifiers, available)


def _coingecko_universe_targets(args: object) -> tuple[str, ...]:
    try:
        market_config = load_market_validation_config(Path(args.market_validation_config))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(project.project_id for project in market_config.project_universe)


def _configured_project_count(args: object) -> int:
    try:
        market_config = load_market_validation_config(Path(args.market_validation_config))
    except Exception:  # noqa: BLE001
        return 0
    return len(market_config.project_universe)


def _market_project_ids(args: object) -> tuple[str, ...]:
    try:
        market_config = load_market_validation_config(Path(args.market_validation_config))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(project.project_id for project in market_config.project_universe)


def _valid_narrative_evidence(repository: FileAcquisitionRepository) -> dict[str, tuple[NormalizedEvidence, ...]]:
    rows: dict[str, list[NormalizedEvidence]] = {}
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if validation is None or validation.status != "valid":
            continue
        if evidence.provider != "narrative" or evidence.metric != "narrative_item":
            continue
        rows.setdefault(evidence.target_id, []).append(evidence)
    return {
        project_id: tuple(sorted(items, key=lambda item: (item.retrieved_at, item.evidence_id), reverse=True))
        for project_id, items in rows.items()
    }


def _latest_narrative_evidence(repository: FileAcquisitionRepository) -> dict[str, NormalizedEvidence]:
    return {project_id: items[0] for project_id, items in _valid_narrative_evidence(repository).items() if items}


def _narrative_projects(repository: FileAcquisitionRepository) -> set[str]:
    return set(_valid_narrative_evidence(repository))


def _coingecko_detail_cache(
    repository: FileAcquisitionRepository,
    *,
    ttl_seconds: int,
    as_of: datetime,
) -> dict[str, dict[str, object]]:
    latest: dict[str, RawEvidence] = {}
    for item in repository.raw.values():
        if item.provider != "coingecko" or item.metric != "coingecko_detail_metadata":
            continue
        current = latest.get(item.target_id)
        if current is None or item.retrieved_at > current.retrieved_at:
            latest[item.target_id] = item
    cache: dict[str, dict[str, object]] = {}
    for target_id, item in latest.items():
        if as_of - item.retrieved_at <= timedelta(seconds=ttl_seconds):
            cache[target_id] = {
                "retrieved_at": item.retrieved_at.isoformat(),
                "payload": dict(item.payload),
            }
    return cache


def _defillama_provider_config(provider_config: ProviderConfig, config: AcquisitionConfig) -> DefiLlamaProviderConfig:
    settings = provider_config.settings or {}
    return DefiLlamaProviderConfig(
        base_url=str(settings.get("base_url", "https://api.llama.fi")),
        max_attempts=int(settings.get("max_attempts", config.retry.max_attempts)),
        backoff_seconds=float(settings.get("backoff_seconds", config.retry.backoff_seconds)),
        jitter_seconds=float(settings.get("jitter_seconds", 0.25)),
        min_interval_seconds=float(settings.get("min_interval_seconds", 0.0)),
    )


def _defillama_identifier_resolutions(
    args: object,
    provider_config: ProviderConfig,
    config: AcquisitionConfig,
) -> tuple[ProjectIdentifierResolution, ...]:
    project_ids = _defillama_universe_targets(args)
    identifiers = load_project_identifiers(Path(args.project_identifiers_config))
    configured_slugs = tuple(
        identifier.defillama_slug
        for identifier in identifiers.values()
        if identifier.defillama_slug and not identifier.defillama_unsupported and not identifier.defillama_ambiguous
    )
    if not configured_slugs:
        return resolve_defillama_identifiers(project_ids, identifiers, set())
    try:
        provider = DefiLlamaProvider(_defillama_provider_config(provider_config, config))
        rows = provider.protocols()
    except DefiLlamaHTTPError as exc:
        return resolve_defillama_identifiers(project_ids, identifiers, set(), rate_limited=exc.status_code == 429)
    except ProviderUnavailableError:
        return resolve_defillama_identifiers(project_ids, identifiers, set(), failed=True)
    available = {str(row.get("slug")) for row in rows if row.get("slug")}
    return resolve_defillama_identifiers(project_ids, identifiers, available)


def _defillama_universe_targets(args: object) -> tuple[str, ...]:
    try:
        market_config = load_market_validation_config(Path(args.market_validation_config))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(project.project_id for project in market_config.project_universe)


def _defillama_persistent_statistics(
    repository: FileAcquisitionRepository,
    *,
    universe_targets: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    raw = tuple(item for item in repository.raw.values() if item.provider == "defillama")
    normalized = tuple(item for item in repository.normalized.values() if item.provider == "defillama")
    normalized_ids = {item.evidence_id for item in normalized}
    valid_ids = {
        evidence_id
        for evidence_id, item in repository.validations.items()
        if item.status == "valid" and evidence_id in normalized_ids
    }
    valid = tuple(item for item in normalized if item.evidence_id in valid_ids)
    protocol_targets = {item.target_id for item in valid if item.metric == "defillama_protocol_profile"}
    tvl_targets = {item.target_id for item in valid if item.raw_metrics.get("tvl") is not None}
    revenue_targets = {
        item.target_id
        for item in valid
        if item.raw_metrics.get("revenue") is not None or item.raw_metrics.get("daily_revenue") is not None
    }
    fee_targets = {
        item.target_id
        for item in valid
        if item.raw_metrics.get("fees") is not None or item.raw_metrics.get("daily_fees") is not None
    }
    universe = max(len(tuple(universe_targets or ())) or len(protocol_targets), 1)
    return {
        "raw": len(raw),
        "normalized": len(normalized),
        "valid": len(valid),
        "protocol_records": len(protocol_targets),
        "tvl_coverage": round((len(tvl_targets) / universe) * 100, 2),
        "revenue_coverage": round((len(revenue_targets) / universe) * 100, 2),
        "fee_coverage": round((len(fee_targets) / universe) * 100, 2),
    }


def _github_provider_config(provider_config: ProviderConfig, config: AcquisitionConfig) -> GitHubProviderConfig:
    settings = provider_config.settings or {}
    credential = AuthRegistry(load_auth_config()).credential("github", "token")
    return GitHubProviderConfig(
        base_url=str(settings.get("base_url", "https://api.github.com")),
        token=str(settings["token"]) if settings.get("token") else (credential.value if credential else None),
        per_page=int(settings.get("per_page", 100)),
        max_pages=int(settings.get("max_pages", 3)),
        max_attempts=int(settings.get("max_attempts", config.retry.max_attempts)),
        commit_period_days=int(settings.get("commit_period_days", 365)),
        backoff_seconds=float(settings.get("backoff_seconds", config.retry.backoff_seconds)),
        jitter_seconds=float(settings.get("jitter_seconds", 0.25)),
        min_interval_seconds=float(settings.get("min_interval_seconds", 0.0)),
    )


def _github_identifier_resolutions(
    args: object,
    provider_config: ProviderConfig,
    config: AcquisitionConfig,
) -> tuple[GitHubRepositoryResolution, ...]:
    project_ids = _github_universe_targets(args)
    identifiers = load_project_identifiers(Path(args.project_identifiers_config))
    configured_repositories = tuple(
        repository
        for identifier in identifiers.values()
        if not identifier.github_unsupported and not identifier.github_ambiguous
        for repository in identifier.github_repositories
    )
    if not configured_repositories:
        return resolve_github_identifiers(project_ids, identifiers, set())
    provider = GitHubProvider(_github_provider_config(provider_config, config))
    available = set()
    try:
        for repository in tuple(dict.fromkeys(configured_repositories)):
            if provider.repository_exists(repository):
                available.add(repository.lower())
    except GitHubHTTPError as exc:
        return resolve_github_identifiers(project_ids, identifiers, set(), rate_limited=exc.status_code in {403, 429})
    except ProviderUnavailableError:
        return resolve_github_identifiers(project_ids, identifiers, set(), failed=True)
    return resolve_github_identifiers(project_ids, identifiers, available)


def _github_configured_resolutions(args: object) -> tuple[GitHubRepositoryResolution, ...]:
    project_ids = _github_universe_targets(args)
    identifiers = load_project_identifiers(Path(args.project_identifiers_config))
    available = {
        repository.lower()
        for project_id in project_ids
        if (identifier := identifiers.get(project_id)) is not None
        if not identifier.github_unsupported and not identifier.github_ambiguous
        for repository in identifier.github_repositories
    }
    return resolve_github_identifiers(project_ids, identifiers, available)


def _github_universe_targets(args: object) -> tuple[str, ...]:
    try:
        market_config = load_market_validation_config(Path(args.market_validation_config))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(project.project_id for project in market_config.project_universe)


def _github_response_cache(repository: FileAcquisitionRepository) -> dict[str, dict[str, object]]:
    latest: dict[str, RawEvidence] = {}
    for item in repository.raw.values():
        if item.provider != "github" or item.metric != "github_repository_profile":
            continue
        current = latest.get(item.raw_source_id)
        if current is None or item.retrieved_at > current.retrieved_at:
            latest[item.raw_source_id] = item
    return {
        key: {
            "payload": dict(item.payload),
            "etags": {"repo": item.payload.get("etag")},
        }
        for key, item in latest.items()
        if item.payload.get("etag")
    }


def _github_persistent_statistics(
    repository: FileAcquisitionRepository,
    *,
    universe_targets: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    raw = tuple(item for item in repository.raw.values() if item.provider == "github")
    normalized = tuple(item for item in repository.normalized.values() if item.provider == "github")
    normalized_ids = {item.evidence_id for item in normalized}
    valid_ids = {
        evidence_id
        for evidence_id, item in repository.validations.items()
        if item.status == "valid" and evidence_id in normalized_ids
    }
    valid = tuple(item for item in normalized if item.evidence_id in valid_ids)
    repository_targets = {item.target_id for item in valid if item.metric == "github_repository_profile"}
    commit_targets = {item.target_id for item in valid if item.raw_metrics.get("commit_count")}
    contributor_targets = {item.target_id for item in valid if item.raw_metrics.get("contributors_count")}
    release_targets = {item.target_id for item in valid if item.raw_metrics.get("releases")}
    universe = max(len(tuple(universe_targets or ())) or len(repository_targets), 1)
    return {
        "raw": len(raw),
        "normalized": len(normalized),
        "valid": len(valid),
        "repository_records": len(repository_targets),
        "commit_coverage": round((len(commit_targets) / universe) * 100, 2),
        "contributor_coverage": round((len(contributor_targets) / universe) * 100, 2),
        "release_coverage": round((len(release_targets) / universe) * 100, 2),
    }


def _explain(args: object) -> int:
    stale = _stale_timing_message(TimingRepository())
    if stale is not None:
        print(stale)
        return 2
    config = load_market_validation_config()
    sources = acquisition_engine_sources(FileAcquisitionRepository(), as_of=config.effective_at)
    executor = EvidenceBackedProjectExecutor(config.effective_at, sources)
    run = MarketValidationRunner(config, executor=executor).run()
    engine = DecisionExplainabilityEngine()
    renderer = DecisionAuditRenderer()
    explain_args = tuple(str(item) for item in getattr(args, "explain_args", ()))
    if len(explain_args) == 1 and explain_args[0] != "ranking":
        target = explain_args[0]
        print(renderer.render_project(engine.explain_project(run, target)))
        return 0
    if len(explain_args) == 3 and explain_args[0] == "compare":
        print(renderer.render_comparison(engine.compare_projects(run, explain_args[1], explain_args[2])))
        return 0
    if explain_args == ("ranking",):
        print(renderer.render_ranking(engine.explain_ranking(run)))
        return 0
    print("explain command required")
    return 1


def _job(jobs: tuple[object, ...], job_id: str):
    for job in jobs:
        if job.job_id == job_id:
            return job
    raise SystemExit(f"Unknown automation job: {job_id}")


if __name__ == "__main__":
    raise SystemExit(main())
