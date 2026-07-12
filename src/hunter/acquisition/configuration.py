from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hunter.acquisition.exceptions import AcquisitionConfigurationError


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 1
    backoff_seconds: int = 0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise AcquisitionConfigurationError("max_attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise AcquisitionConfigurationError("backoff_seconds must be non-negative")


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.ttl_seconds < 0:
            raise AcquisitionConfigurationError("ttl_seconds must be non-negative")


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    enabled: bool = False
    capabilities: tuple[str, ...] = ()
    supported_metrics: tuple[str, ...] = ()
    settings: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise AcquisitionConfigurationError("provider name is required")
        object.__setattr__(self, "capabilities", tuple(sorted(str(item) for item in self.capabilities)))
        object.__setattr__(self, "supported_metrics", tuple(sorted(str(item) for item in self.supported_metrics)))
        object.__setattr__(self, "settings", dict(self.settings or {}))


@dataclass(frozen=True)
class AcquisitionConfig:
    enabled: bool = False
    retry: RetryConfig = RetryConfig()
    cache: CacheConfig = CacheConfig()
    stale_after_seconds: int = 86_400
    providers: tuple[ProviderConfig, ...] = ()

    def __post_init__(self) -> None:
        if self.stale_after_seconds < 0:
            raise AcquisitionConfigurationError("stale_after_seconds must be non-negative")
        object.__setattr__(self, "providers", tuple(self.providers))


def load_acquisition_config(path: str | Path = "configs/acquisition.yaml") -> AcquisitionConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AcquisitionConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise AcquisitionConfigurationError("acquisition configuration must be a mapping")
    return acquisition_config_from_mapping(payload)


def acquisition_config_from_mapping(payload: dict[str, Any]) -> AcquisitionConfig:
    retry_payload = payload.get("retry", {})
    cache_payload = payload.get("cache", {})
    providers = tuple(
        ProviderConfig(
            name=str(item["name"]),
            enabled=bool(item.get("enabled", False)),
            capabilities=tuple(str(value) for value in item.get("capabilities", ())),
            supported_metrics=tuple(str(value) for value in item.get("supported_metrics", ())),
            settings=dict(item.get("settings", {})),
        )
        for item in payload.get("providers", ())
        if isinstance(item, dict) and "name" in item
    )
    return AcquisitionConfig(
        enabled=bool(payload.get("enabled", False)),
        retry=RetryConfig(
            max_attempts=int(retry_payload.get("max_attempts", 1)) if isinstance(retry_payload, dict) else 1,
            backoff_seconds=int(retry_payload.get("backoff_seconds", 0)) if isinstance(retry_payload, dict) else 0,
        ),
        cache=CacheConfig(
            enabled=bool(cache_payload.get("enabled", True)) if isinstance(cache_payload, dict) else True,
            ttl_seconds=int(cache_payload.get("ttl_seconds", 300)) if isinstance(cache_payload, dict) else 300,
        ),
        stale_after_seconds=int(payload.get("stale_after_seconds", 86_400)),
        providers=providers,
    )
