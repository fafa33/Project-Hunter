from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml


@dataclass(frozen=True)
class WhaleProviderConfig:
    name: str
    enabled: bool
    base_url: str
    metrics: dict[str, str]
    assets: dict[str, str]

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "whale provider name is required"
            raise ValueError(msg)
        object.__setattr__(self, "metrics", MappingProxyType({str(k): str(v) for k, v in self.metrics.items()}))
        object.__setattr__(self, "assets", MappingProxyType({str(k): str(v) for k, v in self.assets.items()}))


@dataclass(frozen=True)
class WhaleAcquisitionConfig:
    enabled: bool
    stale_after_hours: int
    providers: tuple[WhaleProviderConfig, ...]

    def __post_init__(self) -> None:
        if self.stale_after_hours < 1:
            msg = "stale_after_hours must be positive"
            raise ValueError(msg)
        object.__setattr__(self, "providers", tuple(self.providers))


def load_whale_config(path: str | Path = "configs/whale.yaml") -> WhaleAcquisitionConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "whale configuration must be a mapping"
        raise ValueError(msg)
    return whale_config_from_mapping(payload)


def whale_config_from_mapping(payload: dict[str, Any]) -> WhaleAcquisitionConfig:
    providers = []
    provider_payload = payload.get("providers", {})
    if not isinstance(provider_payload, dict):
        msg = "providers must be a mapping"
        raise ValueError(msg)
    for name, value in provider_payload.items():
        if not isinstance(value, dict):
            continue
        providers.append(
            WhaleProviderConfig(
                name=str(name),
                enabled=bool(value.get("enabled", False)),
                base_url=str(value.get("base_url", "")),
                metrics={str(k): str(v) for k, v in dict(value.get("metrics", {})).items()},
                assets={str(k): str(v) for k, v in dict(value.get("assets", {})).items()},
            )
        )
    return WhaleAcquisitionConfig(
        enabled=bool(payload.get("enabled", True)),
        stale_after_hours=int(payload.get("stale_after_hours", 24)),
        providers=tuple(providers),
    )
