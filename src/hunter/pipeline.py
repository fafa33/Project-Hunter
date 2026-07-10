from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from hunter.plugins.contracts import PipelineContext, Plugin
from hunter.plugins.manager import PluginManager


@dataclass
class PipelineOrchestrator:
    """Pipeline entry point that executes configured plugins through PluginManager."""

    plugin_manager: PluginManager = field(default_factory=PluginManager)

    def run(
        self,
        context: PipelineContext | None = None,
        *,
        config_path: Path | None = None,
        built_in_plugins: Iterable[Plugin] | None = None,
    ) -> PipelineContext:
        pipeline_context = context or PipelineContext()
        self.plugin_manager.load(config_path=config_path, built_in_plugins=built_in_plugins)
        self.plugin_manager.validate(pipeline_context)
        self.plugin_manager.initialize(pipeline_context)
        try:
            self.plugin_manager.execute(pipeline_context)
        finally:
            self.plugin_manager.shutdown(pipeline_context)
        return pipeline_context

