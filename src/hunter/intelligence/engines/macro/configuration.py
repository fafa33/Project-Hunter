from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MacroEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "macro-intelligence"
    priority: int = 100
    domains: tuple[str, ...] = ()
    thresholds: dict[str, float] = field(default_factory=dict)


class MacroEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> MacroEngineConfiguration:
        config_path = path or Path("configs/macro_engine.yaml")
        if not config_path.exists():
            return MacroEngineConfiguration()
        data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return MacroEngineConfiguration(
            enabled=bool(data.get("enabled", True)),
            project=str(data.get("project", "global-crypto")),
            engine_id=str(data.get("engine_id", "macro-intelligence")),
            priority=int(data.get("priority", 100)),
            domains=tuple(str(domain) for domain in data.get("domains", [])),
            thresholds={str(key): float(value) for key, value in data.get("thresholds", {}).items()},
        )
