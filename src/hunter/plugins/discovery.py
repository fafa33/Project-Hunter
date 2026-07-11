from __future__ import annotations

import importlib
from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any

from hunter.plugins.contracts import Plugin
from hunter.plugins.exceptions import PluginDiscoveryError


class PluginDiscovery:
    def discover(
        self,
        *,
        built_in_plugins: Iterable[Plugin] | None = None,
        module_paths: Iterable[str] | None = None,
        entry_point_group: str = "hunter.plugins",
        include_entry_points: bool = False,
    ) -> list[Plugin]:
        plugins = list(built_in_plugins or [])
        configured_module_paths = tuple(module_paths or ())
        plugins.extend(self._from_modules(configured_module_paths))
        if include_entry_points:
            plugins.extend(self._from_entry_points(entry_point_group))
        return plugins

    def _from_modules(self, module_paths: Iterable[str]) -> list[Plugin]:
        plugins: list[Plugin] = []
        for module_path in module_paths:
            try:
                module_name, attribute = module_path.split(":", 1)
                plugin_factory = getattr(importlib.import_module(module_name), attribute)
                plugin = plugin_factory() if callable(plugin_factory) else plugin_factory
            except Exception as exc:  # noqa: BLE001
                msg = f"Unable to discover plugin from module path: {module_path}"
                raise PluginDiscoveryError(msg) from exc
            plugins.append(_ensure_plugin(plugin, module_path))
        return plugins

    def _from_entry_points(self, group: str) -> list[Plugin]:
        plugins: list[Plugin] = []
        for entry_point in entry_points(group=group):
            try:
                loaded = entry_point.load()
                plugin = loaded() if callable(loaded) else loaded
            except Exception as exc:  # noqa: BLE001
                msg = f"Unable to discover plugin from entry point: {entry_point.name}"
                raise PluginDiscoveryError(msg) from exc
            plugins.append(_ensure_plugin(plugin, entry_point.name))
        return plugins


def _ensure_plugin(value: Any, source: str) -> Plugin:
    if not hasattr(value, "metadata"):
        msg = f"Discovered object is not a plugin: {source}"
        raise PluginDiscoveryError(msg)
    return value
