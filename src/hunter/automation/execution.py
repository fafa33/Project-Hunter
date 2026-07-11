from __future__ import annotations

from collections.abc import Callable, Mapping

from hunter.automation.models import AutomationPipelinePlan
from hunter.execution.clock import FixedClock
from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.developer.engine import DeveloperIntelligenceEngine
from hunter.intelligence.engines.macro.engine import MacroIntelligenceEngine
from hunter.intelligence.engines.narrative.engine import NarrativeIntelligenceEngine
from hunter.intelligence.engines.news.engine import NewsIntelligenceEngine
from hunter.intelligence.engines.onchain.engine import OnchainIntelligenceEngine
from hunter.intelligence.engines.protocol.engine import ProtocolIntelligenceEngine
from hunter.intelligence.engines.social.engine import SocialIntelligenceEngine
from hunter.intelligence.engines.whale.engine import WhaleIntelligenceEngine
from hunter.intelligence.fusion import CrossEngineFusionEngine
from hunter.opportunity import OpportunityTimingEngine
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PersistenceAdapter, PipelineContext

EngineBuilder = Callable[[], IntelligenceEngine]


DEFAULT_ENGINE_BUILDERS: Mapping[str, EngineBuilder] = {
    "macro-intelligence": MacroIntelligenceEngine,
    "whale-intelligence": WhaleIntelligenceEngine,
    "developer-intelligence": DeveloperIntelligenceEngine,
    "protocol-intelligence": ProtocolIntelligenceEngine,
    "news-intelligence": NewsIntelligenceEngine,
    "narrative-intelligence": NarrativeIntelligenceEngine,
    "social-intelligence": SocialIntelligenceEngine,
    "onchain-intelligence": OnchainIntelligenceEngine,
}


class AutomationPipelineExecutor:
    def __init__(
        self,
        *,
        engine_builders: Mapping[str, EngineBuilder] | None = None,
        persistence_adapter: PersistenceAdapter | None = None,
    ) -> None:
        self.engine_builders = engine_builders or DEFAULT_ENGINE_BUILDERS
        self.persistence_adapter = persistence_adapter

    def __call__(self, plan: AutomationPipelinePlan, context: PipelineContext) -> PipelineContext:
        if plan.replay.as_of is not None:
            context.clock = FixedClock(plan.replay.as_of)
        context.persistence_policy = plan.persistence_policy
        if self.persistence_adapter is not None:
            context.persistence_adapter = self.persistence_adapter
        context.ensure_run(
            run_type=plan.run_type,
            target_id=plan.target.target_id,
            target_type=plan.target.target_type,
        )
        return PipelineOrchestrator().run(
            context=context,
            intelligence_engines=_engines(plan, self.engine_builders),
            persistence_adapter=context.persistence_adapter,
            fusion_engine=CrossEngineFusionEngine() if plan.run_fusion else None,
            fusion_target=plan.target if plan.run_fusion or plan.run_opportunity_timing else None,
            opportunity_timing_engine=OpportunityTimingEngine() if plan.run_opportunity_timing else None,
        )


def _engines(plan: AutomationPipelinePlan, builders: Mapping[str, EngineBuilder]) -> tuple[IntelligenceEngine, ...] | None:
    if not plan.run_intelligence:
        return None
    selected = plan.selected_engines or tuple(sorted(builders))
    return tuple(builders[engine_id]() for engine_id in selected)
