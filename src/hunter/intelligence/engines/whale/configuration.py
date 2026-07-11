from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class WhaleEngineConfiguration:
    enabled: bool = True
    project: str = "global-crypto"
    engine_id: str = "whale-intelligence"
    priority: int = 90
    signal_types: tuple[str, ...] = ()
    thresholds: dict[str, float] = field(default_factory=dict)


class WhaleEngineConfigurationLoader:
    def load(self, path: Path | None = None) -> WhaleEngineConfiguration:
        config_path = path or Path("configs/whale_engine.yaml")
        if not config_path.exists():
            return WhaleEngineConfiguration()
        data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return WhaleEngineConfiguration(
            enabled=bool(data.get("enabled", True)),
            project=str(data.get("project", "global-crypto")),
            engine_id=str(data.get("engine_id", "whale-intelligence")),
            priority=int(data.get("priority", 90)),
            signal_types=tuple(str(signal_type) for signal_type in data.get("signal_types", [])),
            thresholds={str(key): float(value) for key, value in data.get("thresholds", {}).items()},
        )
