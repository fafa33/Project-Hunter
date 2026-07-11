from __future__ import annotations

from collections.abc import Iterable

from hunter.plugins.contracts import Plugin
from hunter.plugins.exceptions import PluginValidationError


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        plugin_id = plugin.metadata.id
        if plugin_id in self._plugins:
            msg = f"Duplicate plugin id: {plugin_id}"
            raise PluginValidationError(msg)
        self._plugins[plugin_id] = plugin

    def all(self) -> list[Plugin]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> Plugin | None:
        return self._plugins.get(plugin_id)

    def by_capability(self, capability: str) -> list[Plugin]:
        return [plugin for plugin in self._plugins.values() if capability in plugin.metadata.capabilities]

    def by_category(self, category: str) -> list[Plugin]:
        return [plugin for plugin in self._plugins.values() if plugin.metadata.category == category]

    def version(self, plugin_id: str) -> str | None:
        plugin = self.get(plugin_id)
        return plugin.metadata.version if plugin else None

    def dependencies(self, plugin_id: str) -> tuple[str, ...]:
        plugin = self.get(plugin_id)
        return plugin.metadata.dependencies if plugin else ()

    def extend(self, plugins: Iterable[Plugin]) -> None:
        for plugin in plugins:
            self.register(plugin)
