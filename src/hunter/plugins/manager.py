from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from hunter.plugins.contracts import PipelineContext, Plugin
from hunter.plugins.discovery import PluginDiscovery
from hunter.plugins.exceptions import PluginDependencyError
from hunter.plugins.lifecycle import PluginLifecycle
from hunter.plugins.loader import PluginConfig, PluginConfigLoader
from hunter.plugins.registry import PluginRegistry
from hunter.plugins.validator import PluginValidator


class PluginManager:
    def __init__(
        self,
        *,
        discovery: PluginDiscovery | None = None,
        loader: PluginConfigLoader | None = None,
        validator: PluginValidator | None = None,
        lifecycle: PluginLifecycle | None = None,
    ) -> None:
        self.registry = PluginRegistry()
        self._discovery = discovery or PluginDiscovery()
        self._loader = loader or PluginConfigLoader()
        self._validator = validator or PluginValidator()
        self._lifecycle = lifecycle or PluginLifecycle()
        self._config = PluginConfig()
        self._plugins: list[Plugin] = []

    def load(
        self,
        *,
        config_path: Path | None = None,
        built_in_plugins: Iterable[Plugin] | None = None,
    ) -> list[Plugin]:
        self._config = self._loader.load(config_path)
        discovered = self._discovery.discover(
            built_in_plugins=built_in_plugins,
            module_paths=self._config.module_paths,
        )
        enabled = [plugin for plugin in discovered if self._is_enabled(plugin)]
        ordered = self._order(enabled)
        self._validator.validate(ordered)
        self.registry = PluginRegistry()
        self.registry.extend(ordered)
        self._plugins = ordered
        return ordered

    def validate(self, context: PipelineContext) -> None:
        context.plugin_config = self._config.configuration
        self._lifecycle.validate(self._plugins, context)

    def initialize(self, context: PipelineContext) -> None:
        context.plugin_config = self._config.configuration
        self._lifecycle.initialize(self._plugins, context)

    def execute(self, context: PipelineContext) -> None:
        context.plugin_config = self._config.configuration
        self._lifecycle.execute(self._plugins, context)

    def shutdown(self, context: PipelineContext) -> None:
        self._lifecycle.shutdown(self._plugins, context)

    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def _is_enabled(self, plugin: Plugin) -> bool:
        configured = self._config.enabled.get(plugin.metadata.id)
        return plugin.metadata.enabled if configured is None else configured

    def _order(self, plugins: list[Plugin]) -> list[Plugin]:
        explicit = {plugin_id: index for index, plugin_id in enumerate(self._config.load_order)}
        base_order = sorted(
            plugins,
            key=lambda plugin: (
                explicit.get(plugin.metadata.id, len(explicit)),
                -self._config.priorities.get(plugin.metadata.id, 0),
                plugin.metadata.id,
            ),
        )
        return self._dependency_order(base_order)

    def _dependency_order(self, plugins: list[Plugin]) -> list[Plugin]:
        by_id = {plugin.metadata.id: plugin for plugin in plugins}
        ranked_ids = {plugin.metadata.id: index for index, plugin in enumerate(plugins)}
        ordered: list[Plugin] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in visited:
                return
            if plugin_id in visiting:
                msg = f"Plugin dependency cycle detected at {plugin_id}"
                raise PluginDependencyError(msg)
            visiting.add(plugin_id)
            plugin = by_id[plugin_id]
            dependencies = sorted(
                (_dependency_id(dependency) for dependency in plugin.metadata.dependencies),
                key=lambda dependency_id: ranked_ids.get(dependency_id, len(ranked_ids)),
            )
            for dependency_id in dependencies:
                if dependency_id in by_id:
                    visit(dependency_id)
            visiting.remove(plugin_id)
            visited.add(plugin_id)
            ordered.append(plugin)

        for plugin in plugins:
            visit(plugin.metadata.id)
        return ordered


def _dependency_id(dependency: str) -> str:
    return dependency.split(">=", 1)[0].strip()
