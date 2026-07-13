from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from hunter.execution.identity import identity
from hunter.onchain.models import AssetConfig, ChainConfig, OnChainSurface


@dataclass(frozen=True)
class OnChainConfig:
    enabled: bool
    version: str
    chains: tuple[ChainConfig, ...]
    tracked_assets: tuple[AssetConfig, ...]
    surfaces: tuple[OnChainSurface, ...]
    snapshot_windows: tuple[str, ...]
    retention: dict[str, Any]


def load_onchain_config(path: str | Path = "configs/onchain.yaml") -> OnChainConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    raw = raw or {}
    chains = tuple(_chain(item) for item in raw.get("chains", ()))
    assets = tuple(_asset(item) for item in raw.get("tracked_assets", ()))
    surfaces = tuple(
        _surface(item, version=str(raw.get("version", "onchain-surface-registry-v1")))
        for item in raw.get("surface_registry", ())
    )
    return OnChainConfig(
        enabled=bool(raw.get("enabled", True)),
        version=str(raw.get("version", "onchain-surface-registry-v1")),
        chains=chains,
        tracked_assets=assets,
        surfaces=surfaces,
        snapshot_windows=tuple(str(item) for item in raw.get("snapshot_windows", ("24h",))),
        retention=dict(raw.get("retention", {})),
    )


def _chain(raw: dict[str, Any]) -> ChainConfig:
    endpoint = str(raw.get("rpc_endpoint", ""))
    env_name = str(raw.get("rpc_env", "")) or None
    if env_name and os.environ.get(env_name):
        endpoint = str(os.environ[env_name])
    return ChainConfig(
        chain_id=int(raw["chain_id"]),
        network=str(raw["network"]),
        family=str(raw.get("family", "evm")),
        enabled=bool(raw.get("enabled", True)),
        rpc_endpoint=endpoint,
        rpc_env=env_name,
        explorer_url=str(raw.get("explorer_url", "")),
        finality_depth=int(raw.get("finality_depth", 64)),
        max_block_range=int(raw.get("max_block_range", 5000)),
        polling_interval_seconds=int(raw.get("polling_interval_seconds", 300)),
        retry_limit=int(raw.get("retry_limit", 2)),
        timeout_seconds=int(raw.get("timeout_seconds", 20)),
    )


def _asset(raw: dict[str, Any]) -> AssetConfig:
    address = raw.get("contract_address")
    return AssetConfig(
        chain_id=int(raw["chain_id"]),
        symbol=str(raw["symbol"]),
        asset_type=str(raw["asset_type"]),
        decimals=int(raw["decimals"]),
        contract_address=str(address).lower() if address else None,
    )


def _surface(raw: dict[str, Any], *, version: str) -> OnChainSurface:
    payload = {
        "version": version,
        "project": raw["project"],
        "chain_id": raw["chain_id"],
        "address": str(raw["address"]).lower(),
        "source_url": raw["source_url"],
        "valid_from": raw["valid_from"],
    }
    valid_to = raw.get("valid_to")
    return OnChainSurface(
        project=str(raw["project"]),
        chain_id=int(raw["chain_id"]),
        network=str(raw["network"]),
        address=str(raw["address"]),
        address_type=str(raw["address_type"]),  # type: ignore[arg-type]
        protocol_role=str(raw["protocol_role"]),
        asset_scope=str(raw["asset_scope"]),
        source_url=str(raw["source_url"]),
        source_type=str(raw["source_type"]),
        verification_timestamp=datetime.fromisoformat(str(raw["verification_timestamp"])),
        confidence=float(raw["confidence"]),
        active=bool(raw.get("active", True)),
        valid_from=datetime.fromisoformat(str(raw["valid_from"])),
        valid_to=datetime.fromisoformat(str(valid_to)) if valid_to else None,
        evidence_id=identity("onchain-surface", payload),
    )
