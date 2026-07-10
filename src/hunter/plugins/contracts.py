from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class PluginMetadata:
    id: str
    name: str
    version: str
    author: str
    description: str
    category: str
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    configuration_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class PipelineContext:
    values: dict[str, Any] = field(default_factory=dict)
    plugin_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def config_for(self, plugin_id: str) -> dict[str, Any]:
        return self.plugin_config.get(plugin_id, {})

    def record(self, event: str) -> None:
        self.events.append(event)


@runtime_checkable
class Plugin(Protocol):
    @property
    def metadata(self) -> PluginMetadata:
        raise NotImplementedError

    def initialize(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def validate(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def execute(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def shutdown(self, context: PipelineContext) -> None:
        raise NotImplementedError

