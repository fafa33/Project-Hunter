from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext, PluginMetadata
from hunter.plugins.discovery import PluginDiscovery
from hunter.plugins.exceptions import PluginDependencyError, PluginValidationError
from hunter.plugins.manager import PluginManager
from hunter.plugins.registry import PluginRegistry
from hunter.plugins.validator import PluginValidator


@dataclass
class ExamplePlugin:
    metadata: PluginMetadata
    calls: list[str] = field(default_factory=list)

    def initialize(self, context: PipelineContext) -> None:
        self.calls.append("initialize")
        context.record(f"{self.metadata.id}:initialize")

    def validate(self, context: PipelineContext) -> None:
        self.calls.append("validate")
        context.record(f"{self.metadata.id}:validate")

    def execute(self, context: PipelineContext) -> None:
        self.calls.append("execute")
        context.record(f"{self.metadata.id}:execute")
        context.set(self.metadata.id, context.config_for(self.metadata.id).get("value", True))

    def shutdown(self, context: PipelineContext) -> None:
        self.calls.append("shutdown")
        context.record(f"{self.metadata.id}:shutdown")


def plugin(
    plugin_id: str,
    *,
    category: str = "intelligence",
    version: str = "1.0.0",
    dependencies: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = ("analyze",),
    enabled: bool = True,
) -> ExamplePlugin:
    return ExamplePlugin(
        PluginMetadata(
            id=plugin_id,
            name=plugin_id.title(),
            version=version,
            author="Project Hunter",
            description=f"{plugin_id} plugin",
            category=category,
            dependencies=dependencies,
            capabilities=capabilities,
            configuration_schema={},
            enabled=enabled,
        )
    )


def test_plugin_discovery_accepts_built_in_plugins() -> None:
    alpha = plugin("alpha")

    discovered = PluginDiscovery().discover(built_in_plugins=[alpha])

    assert discovered == [alpha]


def test_plugin_loading_registers_enabled_plugins(tmp_path: Path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  alpha: true
configuration:
  alpha:
    value: configured
load_order:
  - alpha
priorities:
  alpha: 10
module_paths: []
""",
        encoding="utf-8",
    )
    manager = PluginManager()

    loaded = manager.load(config_path=config, built_in_plugins=[plugin("alpha")])
    context = PipelineContext()
    manager.validate(context)
    manager.initialize(context)
    manager.execute(context)
    manager.shutdown(context)

    assert [item.metadata.id for item in loaded] == ["alpha"]
    assert manager.registry.get("alpha") is not None
    assert context.get("alpha") == "configured"
    assert context.events == ["alpha:validate", "alpha:initialize", "alpha:execute", "alpha:shutdown"]


def test_registry_lookup_by_id_capability_category_version_and_dependencies() -> None:
    alpha = plugin("alpha", category="macro", capabilities=("macro",))
    beta = plugin("beta", dependencies=("alpha",))
    registry = PluginRegistry()
    registry.extend([alpha, beta])

    assert registry.get("alpha") == alpha
    assert registry.by_capability("macro") == [alpha]
    assert registry.by_category("macro") == [alpha]
    assert registry.version("alpha") == "1.0.0"
    assert registry.dependencies("beta") == ("alpha",)


def test_dependency_resolution_and_configured_order_are_deterministic(tmp_path: Path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled: {}
configuration: {}
load_order:
  - beta
  - alpha
priorities: {}
module_paths: []
""",
        encoding="utf-8",
    )
    manager = PluginManager()

    loaded = manager.load(
        config_path=config,
        built_in_plugins=[plugin("alpha"), plugin("beta", dependencies=("alpha>=1.0.0",))],
    )

    assert [item.metadata.id for item in loaded] == ["alpha", "beta"]


def test_lifecycle_executes_plugins_through_pipeline_orchestrator() -> None:
    alpha = plugin("alpha")
    context = PipelineOrchestrator().run(built_in_plugins=[alpha])

    assert alpha.calls == ["validate", "initialize", "execute", "shutdown"]
    assert context.get("alpha") is True


def test_duplicate_detection_rejects_plugins() -> None:
    with pytest.raises(PluginValidationError):
        PluginValidator().validate([plugin("alpha"), plugin("alpha")])


def test_disabled_plugins_do_not_load(tmp_path: Path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  alpha: false
configuration: {}
load_order: []
priorities: {}
module_paths: []
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config, built_in_plugins=[plugin("alpha")])

    assert loaded == []


def test_invalid_plugins_do_not_load() -> None:
    invalid = plugin("invalid", capabilities=())

    with pytest.raises(PluginValidationError):
        PluginManager().load(built_in_plugins=[invalid])


def test_missing_dependency_rejects_plugin() -> None:
    with pytest.raises(PluginDependencyError):
        PluginManager().load(built_in_plugins=[plugin("beta", dependencies=("alpha",))])


def test_version_compatibility_rejects_old_dependencies() -> None:
    with pytest.raises(PluginDependencyError):
        PluginManager().load(
            built_in_plugins=[
                plugin("alpha", version="1.0.0"),
                plugin("beta", dependencies=("alpha>=2.0.0",)),
            ]
        )
