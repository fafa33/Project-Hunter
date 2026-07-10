from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from hunter.execution.clock import Clock, SystemClock
from hunter.execution.identity import fingerprint
from hunter.execution.run import PipelineRun
from hunter.intelligence.intelligence import Intelligence

DEFAULT_EFFECTIVE_AT = datetime(1970, 1, 1, tzinfo=UTC)


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
    intelligence: list[Intelligence] = field(default_factory=list)
    run: PipelineRun | None = None
    clock: Clock = field(default_factory=SystemClock)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def config_for(self, plugin_id: str) -> dict[str, Any]:
        return self.plugin_config.get(plugin_id, {})

    def record(self, event: str) -> None:
        self.events.append(event)

    def emit_intelligence(self, intelligence: Intelligence) -> None:
        self.intelligence.append(intelligence)

    def ensure_run(
        self,
        *,
        run_type: str = "manual",
        target_id: str = "global-crypto",
        target_type: str = "project",
        engine_manifest: Any | None = None,
    ) -> PipelineRun:
        if self.run is None:
            effective_at = DEFAULT_EFFECTIVE_AT if isinstance(self.clock, SystemClock) else self.clock.now()
            self.run = PipelineRun.create(
                run_type=run_type,  # type: ignore[arg-type]
                target_id=target_id,
                target_type=target_type,
                configuration_fingerprint=fingerprint("pipeline-configuration", self.plugin_config),
                input_fingerprint=fingerprint("pipeline-input", self.values),
                engine_manifest_fingerprint=fingerprint("engine-manifest", engine_manifest or {}),
                requested_at=self.clock.now(),
                effective_at=effective_at,
                clock=self.clock,
            )
        return self.run


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
