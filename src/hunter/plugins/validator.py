from __future__ import annotations

import re

from hunter.plugins.contracts import Plugin
from hunter.plugins.exceptions import PluginDependencyError, PluginValidationError


class PluginValidator:
    def validate(self, plugins: list[Plugin]) -> None:
        self._metadata(plugins)
        self._duplicates(plugins)
        self._dependencies(plugins)

    def _metadata(self, plugins: list[Plugin]) -> None:
        required = ["id", "name", "version", "author", "description", "category"]
        for plugin in plugins:
            metadata = plugin.metadata
            missing = [field for field in required if not str(getattr(metadata, field, "")).strip()]
            if missing:
                msg = f"Plugin {metadata.id or '<unknown>'} is missing metadata: {', '.join(missing)}"
                raise PluginValidationError(msg)
            if not metadata.capabilities:
                msg = f"Plugin {metadata.id} must declare at least one capability"
                raise PluginValidationError(msg)
            if not _valid_version(metadata.version):
                msg = f"Plugin {metadata.id} has invalid version: {metadata.version}"
                raise PluginValidationError(msg)

    def _duplicates(self, plugins: list[Plugin]) -> None:
        seen: set[str] = set()
        for plugin in plugins:
            plugin_id = plugin.metadata.id
            if plugin_id in seen:
                msg = f"Duplicate plugin id: {plugin_id}"
                raise PluginValidationError(msg)
            seen.add(plugin_id)

    def _dependencies(self, plugins: list[Plugin]) -> None:
        by_id = {plugin.metadata.id: plugin for plugin in plugins}
        for plugin in plugins:
            for dependency in plugin.metadata.dependencies:
                dependency_id, minimum_version = _parse_dependency(dependency)
                if dependency_id not in by_id:
                    msg = f"Plugin {plugin.metadata.id} depends on missing plugin {dependency_id}"
                    raise PluginDependencyError(msg)
                if minimum_version is not None and not _version_at_least(
                    by_id[dependency_id].metadata.version,
                    minimum_version,
                ):
                    msg = f"Plugin {plugin.metadata.id} requires {dependency_id}>={minimum_version}"
                    raise PluginDependencyError(msg)
        self._acyclic(by_id)

    def _acyclic(self, by_id: dict[str, Plugin]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in visited:
                return
            if plugin_id in visiting:
                msg = f"Plugin dependency cycle detected at {plugin_id}"
                raise PluginDependencyError(msg)
            visiting.add(plugin_id)
            for dependency in by_id[plugin_id].metadata.dependencies:
                dependency_id, _ = _parse_dependency(dependency)
                if dependency_id in by_id:
                    visit(dependency_id)
            visiting.remove(plugin_id)
            visited.add(plugin_id)

        for plugin_id in by_id:
            visit(plugin_id)


def _parse_dependency(dependency: str) -> tuple[str, str | None]:
    if ">=" not in dependency:
        return dependency, None
    plugin_id, version = dependency.split(">=", 1)
    return plugin_id.strip(), version.strip()


def _valid_version(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", version))


def _version_at_least(actual: str, minimum: str) -> bool:
    return _version_tuple(actual) >= _version_tuple(minimum)


def _version_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)

