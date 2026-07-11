from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PluginConfig:
    enabled: dict[str, bool] = field(default_factory=dict)
    configuration: dict[str, dict[str, Any]] = field(default_factory=dict)
    load_order: tuple[str, ...] = ()
    priorities: dict[str, int] = field(default_factory=dict)
    module_paths: tuple[str, ...] = ()


class PluginConfigLoader:
    def load(self, path: Path | None = None) -> PluginConfig:
        config_path = path or Path("configs/plugins.yaml")
        if not config_path.exists():
            return PluginConfig()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return PluginConfig(
            enabled={str(key): bool(value) for key, value in data.get("enabled", {}).items()},
            configuration={str(key): dict(value or {}) for key, value in data.get("configuration", {}).items()},
            load_order=tuple(str(item) for item in data.get("load_order", [])),
            priorities={str(key): int(value) for key, value in data.get("priorities", {}).items()},
            module_paths=tuple(str(item) for item in data.get("module_paths", [])),
        )
