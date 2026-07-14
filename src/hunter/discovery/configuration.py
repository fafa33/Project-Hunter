from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DiscoveryProviderConfig:
    enabled: bool = True
    limit: int = 250
    base_url: str | None = None
    timeout_seconds: int = 30
    max_attempts: int = 3
    backoff_seconds: float = 0.5


@dataclass(frozen=True)
class DiscoveryConfig:
    registry_path: str = "data/discovery/runtime/candidates.sqlite"
    market_validation_config: str = "configs/market_validation.yaml"
    project_identifiers_config: str = "configs/project_identifiers.yaml"
    seed_market_validation_universe: bool = True
    minimum_screening_identifiers: int = 1
    providers: dict[str, DiscoveryProviderConfig] | None = None

    def provider(self, name: str) -> DiscoveryProviderConfig:
        return (self.providers or {}).get(name, DiscoveryProviderConfig(enabled=False))


def load_discovery_config(path: str | Path = "configs/discovery.yaml") -> DiscoveryConfig:
    config_path = Path(path)
    if not config_path.exists():
        return DiscoveryConfig(providers={})
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "discovery configuration must be a mapping"
        raise ValueError(msg)
    providers = {
        str(name): _provider_config(str(name), raw)
        for name, raw in (payload.get("providers") or {}).items()
        if isinstance(raw, dict)
    }
    return DiscoveryConfig(
        registry_path=str(payload.get("registry_path", "data/discovery/runtime/candidates.sqlite")),
        market_validation_config=str(payload.get("market_validation_config", "configs/market_validation.yaml")),
        project_identifiers_config=str(payload.get("project_identifiers_config", "configs/project_identifiers.yaml")),
        seed_market_validation_universe=bool(payload.get("seed_market_validation_universe", True)),
        minimum_screening_identifiers=int(payload.get("minimum_screening_identifiers", 1)),
        providers=providers,
    )


def discovery_config_from_mapping(payload: dict[str, Any]) -> DiscoveryConfig:
    providers = {
        str(name): _provider_config(str(name), raw)
        for name, raw in (payload.get("providers") or {}).items()
        if isinstance(raw, dict)
    }
    return DiscoveryConfig(
        registry_path=str(payload.get("registry_path", "data/discovery/runtime/candidates.sqlite")),
        market_validation_config=str(payload.get("market_validation_config", "configs/market_validation.yaml")),
        project_identifiers_config=str(payload.get("project_identifiers_config", "configs/project_identifiers.yaml")),
        seed_market_validation_universe=bool(payload.get("seed_market_validation_universe", True)),
        minimum_screening_identifiers=int(payload.get("minimum_screening_identifiers", 1)),
        providers=providers,
    )


def _provider_config(name: str, raw: dict[str, Any]) -> DiscoveryProviderConfig:
    env_prefix = f"HUNTER_DISCOVERY_{name.upper().replace('-', '_')}"
    return DiscoveryProviderConfig(
        enabled=_env_bool(f"{env_prefix}_ENABLED", bool(raw.get("enabled", True))),
        limit=int(os.getenv(f"{env_prefix}_LIMIT", str(raw.get("limit", 250)))),
        base_url=os.getenv(f"{env_prefix}_BASE_URL") or (str(raw["base_url"]) if raw.get("base_url") else None),
        timeout_seconds=int(os.getenv(f"{env_prefix}_TIMEOUT_SECONDS", str(raw.get("timeout_seconds", 30)))),
        max_attempts=int(os.getenv(f"{env_prefix}_MAX_ATTEMPTS", str(raw.get("max_attempts", 3)))),
        backoff_seconds=float(os.getenv(f"{env_prefix}_BACKOFF_SECONDS", str(raw.get("backoff_seconds", 0.5)))),
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
