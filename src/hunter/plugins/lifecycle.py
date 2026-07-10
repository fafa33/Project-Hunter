from __future__ import annotations

from collections.abc import Callable, Iterable

from hunter.plugins.contracts import PipelineContext, Plugin
from hunter.plugins.exceptions import PluginLifecycleError


class PluginLifecycle:
    def initialize(self, plugins: Iterable[Plugin], context: PipelineContext) -> None:
        self._run("initialize", plugins, context, lambda plugin: plugin.initialize)

    def validate(self, plugins: Iterable[Plugin], context: PipelineContext) -> None:
        self._run("validate", plugins, context, lambda plugin: plugin.validate)

    def execute(self, plugins: Iterable[Plugin], context: PipelineContext) -> None:
        self._run("execute", plugins, context, lambda plugin: plugin.execute)

    def shutdown(self, plugins: Iterable[Plugin], context: PipelineContext) -> None:
        for plugin in reversed(list(plugins)):
            try:
                plugin.shutdown(context)
            except Exception as exc:  # noqa: BLE001
                msg = f"Plugin shutdown failed for {plugin.metadata.id}"
                raise PluginLifecycleError(msg) from exc

    def _run(
        self,
        phase: str,
        plugins: Iterable[Plugin],
        context: PipelineContext,
        method: Callable[[Plugin], Callable[[PipelineContext], None]],
    ) -> None:
        for plugin in plugins:
            try:
                method(plugin)(context)
            except Exception as exc:  # noqa: BLE001
                msg = f"Plugin {phase} failed for {plugin.metadata.id}"
                raise PluginLifecycleError(msg) from exc

