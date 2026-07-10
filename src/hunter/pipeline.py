from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, Plugin
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
    ) -> PipelineContext:
        pipeline_context = context or PipelineContext()
        engine_list = list(intelligence_engines or [])
        self.plugin_manager.load(config_path=config_path, built_in_plugins=built_in_plugins)
        pipeline_context.plugin_config = self.plugin_manager.config().configuration
        pipeline_context.ensure_run(engine_manifest=_manifest(engine_list, self.plugin_manager.plugins()))
        if intelligence_engines is not None:
            self.engine_runner.run(engine_list, pipeline_context)
        self.plugin_manager.validate(pipeline_context)
        self.plugin_manager.initialize(pipeline_context)
        try:
            self.plugin_manager.execute(pipeline_context)
        finally:
            self.plugin_manager.shutdown(pipeline_context)
        return pipeline_context


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
