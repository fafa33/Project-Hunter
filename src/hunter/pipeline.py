"""Experimental pipeline orchestration path for v2.1.x.

The canonical production runtime is the evidence-backed Market Validation path
documented in docs/CANONICAL_RUNTIME_ARCHITECTURE.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.runner import EngineRunner
from hunter.intelligence.fusion.contracts import FusionEngine
from hunter.intelligence.fusion.models import FusionTarget
from hunter.opportunity.contracts import OpportunityTimingEngine
from hunter.plugins.contracts import PersistenceAdapter, PipelineContext, Plugin
from hunter.plugins.manager import PluginManager


@dataclass
class PipelineOrchestrator:
    """Pipeline entry point that executes configured plugins through PluginManager."""

    plugin_manager: PluginManager = field(default_factory=PluginManager)
    engine_runner: EngineRunner = field(default_factory=EngineRunner)

    def run(
        self,
        context: PipelineContext | None = None,
        *,
        config_path: Path | None = None,
        built_in_plugins: Iterable[Plugin] | None = None,
        intelligence_engines: Iterable[IntelligenceEngine] | None = None,
        persistence_adapter: PersistenceAdapter | None = None,
        fusion_engine: FusionEngine | None = None,
        fusion_target: FusionTarget | None = None,
        opportunity_timing_engine: OpportunityTimingEngine | None = None,
    ) -> PipelineContext:
        pipeline_context = context or PipelineContext()
        engine_list = list(intelligence_engines or [])
        self.plugin_manager.load(config_path=config_path, built_in_plugins=built_in_plugins)
        pipeline_context.plugin_config = self.plugin_manager.config().configuration
        engine_manifest = _manifest(engine_list, self.plugin_manager.plugins())
        adapter = persistence_adapter or pipeline_context.persistence_adapter
        if opportunity_timing_engine is not None:
            pipeline_context.opportunity_timing_engine = opportunity_timing_engine
            pipeline_context.opportunity_timing_target = fusion_target
        if adapter is None:
            pipeline_context.ensure_run(engine_manifest=engine_manifest)
            self._execute(
                pipeline_context,
                engine_list=engine_list,
                intelligence_engines=intelligence_engines,
                fusion_engine=fusion_engine,
                fusion_target=fusion_target,
                opportunity_timing_engine=opportunity_timing_engine,
                run_opportunity_from_context=True,
            )
        else:
            pipeline_context.persistence_adapter = adapter
            adapter.run(
                pipeline_context,
                lambda: self._execute(
                    pipeline_context,
                    engine_list=engine_list,
                    intelligence_engines=intelligence_engines,
                    fusion_engine=fusion_engine,
                    fusion_target=fusion_target,
                    opportunity_timing_engine=opportunity_timing_engine,
                    run_opportunity_from_context=False,
                ),
                engine_manifest=engine_manifest,
            )
        return pipeline_context

    def _execute(
        self,
        pipeline_context: PipelineContext,
        *,
        engine_list: list[IntelligenceEngine],
        intelligence_engines: Iterable[IntelligenceEngine] | None,
        fusion_engine: FusionEngine | None = None,
        fusion_target: FusionTarget | None = None,
        opportunity_timing_engine: OpportunityTimingEngine | None = None,
        run_opportunity_from_context: bool = True,
    ) -> None:
        if intelligence_engines is not None:
            self.engine_runner.run(engine_list, pipeline_context)
        self.plugin_manager.validate(pipeline_context)
        self.plugin_manager.initialize(pipeline_context)
        try:
            self.plugin_manager.execute(pipeline_context)
        finally:
            self.plugin_manager.shutdown(pipeline_context)
        if fusion_engine is not None and fusion_target is not None:
            pipeline_context.fused_intelligence.append(fusion_engine.fuse(pipeline_context.intelligence, fusion_target))
        if run_opportunity_from_context and opportunity_timing_engine is not None and fusion_target is not None:
            fused_records = pipeline_context.get("persisted_fused_intelligence", ())
            if fused_records:
                pipeline_context.opportunity_timing.append(
                    opportunity_timing_engine.assess(fused_records, fusion_target)
                )


def _manifest(engines: list[IntelligenceEngine], plugins: list[Plugin]) -> dict[str, object]:
    return {
        "engines": [
            {
                "id": engine.id,
                "version": engine.version,
                "category": engine.category,
                "priority": engine.priority,
                "required_inputs": engine.required_inputs,
                "produced_outputs": engine.produced_outputs,
                "capabilities": engine.capabilities,
            }
            for engine in engines
        ],
        "plugins": [
            {
                "id": plugin.metadata.id,
                "version": plugin.metadata.version,
                "category": plugin.metadata.category,
                "dependencies": plugin.metadata.dependencies,
                "capabilities": plugin.metadata.capabilities,
            }
            for plugin in plugins
        ],
    }
