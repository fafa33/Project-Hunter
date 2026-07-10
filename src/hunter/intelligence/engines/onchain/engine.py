from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.onchain.analyzers import OnchainAnalyzer
from hunter.intelligence.engines.onchain.collectors import ContextOnchainCollector, OnchainCollector
from hunter.intelligence.engines.onchain.confidence import OnchainConfidenceModel
from hunter.intelligence.engines.onchain.configuration import (
    OnchainEngineConfiguration,
    OnchainEngineConfigurationLoader,
)
from hunter.intelligence.engines.onchain.exceptions import OnchainCollectionError
from hunter.intelligence.engines.onchain.models import ONCHAIN_DOMAINS, OnchainAnalysis, OnchainDataset
from hunter.intelligence.engines.onchain.normalization import OnchainNormalizer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class OnchainIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[OnchainCollector, ...] | None = None,
        normalizer: OnchainNormalizer | None = None,
        analyzer: OnchainAnalyzer | None = None,
        confidence_model: OnchainConfidenceModel | None = None,
        configuration: OnchainEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or OnchainEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="On-chain Intelligence Engine",
            category="onchain",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("onchain_records", "intelligence"),
            produced_outputs=("onchain_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "onchain-intelligence"),
        )
        self._collectors = collectors or (ContextOnchainCollector(),)
        self._normalizer = normalizer or OnchainNormalizer(self.configuration)
        self._analyzer = analyzer or OnchainAnalyzer(configuration=self.configuration)
        self._confidence_model = confidence_model or OnchainConfidenceModel(self.configuration)
        self._latest_dataset = OnchainDataset(project=self.configuration.project)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise OnchainCollectionError("On-chain Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> OnchainDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records), tuple(context.intelligence))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> OnchainAnalysis:
        if not isinstance(collected, OnchainDataset):
            raise OnchainCollectionError("On-chain engine expected an OnchainDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, OnchainAnalysis):
            raise OnchainCollectionError("On-chain engine expected OnchainAnalysis")
        generated_at = datetime.now(UTC)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"onchain-observation-{indicator.name}",
                engine=self.id,
                project=self._latest_dataset.project,
                description=indicator.description,
                evidence=evidence,
                importance=indicator.value,
                metadata={"indicator": indicator.name, "direction": indicator.direction},
            )
            for indicator in analysis.indicators
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self._latest_dataset.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"onchain-signal-{indicator.name}",
                    source=self.id,
                    timestamp=generated_at,
                    category=indicator.name,
                    strength=indicator.value,
                    confidence=indicator.confidence,
                    severity=_severity(indicator.value, indicator.direction),
                    metadata={"direction": indicator.direction},
                )
                for indicator in analysis.indicators
            ),
            evidence=evidence,
            observations=observations,
            insights=self._insights(analysis, observations),
            confidence=self._confidence_model.calculate(self._latest_dataset),
            generated_at=generated_at,
            metadata={
                "health": analysis.health,
                "capital_flow_trend": analysis.capital_flow_trend,
                "address_trend": analysis.address_trend,
                "holder_trend": analysis.holder_trend,
                "concentration": analysis.concentration,
                "decentralization": analysis.decentralization,
                "contract_activity": analysis.contract_activity,
                "migration": analysis.migration,
                "anomaly_level": analysis.anomaly.level,
                "anomaly_reason": analysis.anomaly.explanation,
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "supported_domains": str(len(ONCHAIN_DOMAINS)),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=f"onchain-evidence-{record.id}",
                source=record.source,
                collected_at=record.timestamp,
                reliability=record.reliability,
                freshness=1.0,
                reference=record.reference,
                raw_data={
                    "record_type": type(record).__name__,
                    "project": record.project,
                    "asset": record.asset,
                    "chain": record.chain,
                    "block_height": record.block_height,
                    "transaction_hash": record.transaction_hash,
                    "contract_address": record.contract_address,
                    "token_denomination": record.token_denomination,
                },
                metadata={"chain": record.chain, "asset": record.asset, "record_type": type(record).__name__},
            )
            for record in self._latest_dataset.records
        )

    def _insights(self, analysis: OnchainAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="onchain-insight-health",
                title="On-chain health",
                explanation=(
                    f"On-chain health is {analysis.health}; capital flow is {analysis.capital_flow_trend}; "
                    f"address trend is {analysis.address_trend}; holder trend is {analysis.holder_trend}."
                ),
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="onchain-insight-anomaly",
                title="On-chain anomaly risk",
                explanation=f"Anomaly assessment is {analysis.anomaly.level}: {analysis.anomaly.explanation}.",
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class OnchainIntelligencePlugin:
    metadata: PluginMetadata
    engine: OnchainIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("onchain:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("onchain:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("onchain:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("onchain:intelligence:shutdown")


def create_plugin() -> OnchainIntelligencePlugin:
    return OnchainIntelligencePlugin(
        metadata=PluginMetadata(
            id="onchain-intelligence",
            name="On-chain Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized on-chain activity, capital flow, holder, contract, and anomaly intelligence.",
            category="intelligence",
            capabilities=("onchain-intelligence", "intelligence"),
        ),
        engine=OnchainIntelligenceEngine(),
    )


def _severity(value: float, direction: str) -> float:
    if direction == "negative":
        return round(value, 4)
    return round(abs(value - 0.5) * 2, 4)
