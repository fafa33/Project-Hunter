from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

_AUTHORITY_RANK = {
    "unverified": 0,
    "aggregator": 1,
    "onchain": 2,
    "audited": 3,
    "official": 4,
}


@dataclass(frozen=True)
class ValueCaptureSourceConfig:
    source_id: str
    authority_tier: str
    source_type: str
    allowed_hosts: tuple[str, ...]
    endpoint_patterns: tuple[str, ...]
    parser_version: str
    capabilities: tuple[str, ...]
    enabled: bool
    correction_predecessor_tiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("source_id", "authority_tier", "source_type", "parser_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.authority_tier not in _AUTHORITY_RANK:
            raise ValueError(f"unsupported authority_tier: {self.authority_tier}")
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")
        if not self.endpoint_patterns:
            raise ValueError("endpoint_patterns must not be empty")
        if not self.capabilities:
            raise ValueError("capabilities must not be empty")
        unknown = set(self.correction_predecessor_tiers) - set(_AUTHORITY_RANK)
        if unknown:
            raise ValueError(f"unsupported correction predecessor tiers: {sorted(unknown)}")

    @property
    def fingerprint(self) -> str:
        payload = {
            "source_id": self.source_id,
            "authority_tier": self.authority_tier,
            "source_type": self.source_type,
            "allowed_hosts": self.allowed_hosts,
            "endpoint_patterns": self.endpoint_patterns,
            "parser_version": self.parser_version,
            "capabilities": self.capabilities,
            "enabled": self.enabled,
            "correction_predecessor_tiers": self.correction_predecessor_tiers,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def authorize(self, *, endpoint: str, parser_version: str, capability: str) -> None:
        if not self.enabled:
            raise ValueError(f"source is disabled: {self.source_id}")
        if parser_version != self.parser_version:
            raise ValueError("parser version is not registry-authorized")
        if capability not in self.capabilities:
            raise ValueError(f"capability is not registry-authorized: {capability}")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise ValueError("endpoint host is not registry-authorized")
        if not any(endpoint.startswith(pattern) for pattern in self.endpoint_patterns):
            raise ValueError("endpoint pattern is not registry-authorized")


class ValueCaptureSourceRegistry:
    def __init__(self, sources: tuple[ValueCaptureSourceConfig, ...]) -> None:
        by_id = {source.source_id: source for source in sources}
        if len(by_id) != len(sources):
            raise ValueError("duplicate value-capture source_id")
        self._sources = by_id

    @classmethod
    def from_yaml(cls, path: str | Path) -> ValueCaptureSourceRegistry:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        sources = tuple(
            ValueCaptureSourceConfig(
                source_id=str(item["source_id"]),
                authority_tier=str(item["authority_tier"]),
                source_type=str(item["source_type"]),
                allowed_hosts=tuple(str(value) for value in item["allowed_hosts"]),
                endpoint_patterns=tuple(str(value) for value in item["endpoint_patterns"]),
                parser_version=str(item["parser_version"]),
                capabilities=tuple(str(value) for value in item["capabilities"]),
                enabled=bool(item.get("enabled", False)),
                correction_predecessor_tiers=tuple(
                    str(value) for value in item.get("correction_predecessor_tiers", ())
                ),
            )
            for item in payload.get("sources", ())
        )
        return cls(sources)

    def require(self, source_id: str) -> ValueCaptureSourceConfig:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise ValueError(f"unregistered value-capture source: {source_id}") from exc

    def authorize_correction_transition(self, *, predecessor_source_id: str, successor_source_id: str) -> None:
        predecessor = self.require(predecessor_source_id)
        successor = self.require(successor_source_id)
        if predecessor.source_id == successor.source_id:
            return
        if predecessor.authority_tier not in successor.correction_predecessor_tiers:
            raise ValueError("correction source transition is not registry-authorized")
        if _AUTHORITY_RANK[successor.authority_tier] < _AUTHORITY_RANK[predecessor.authority_tier]:
            raise ValueError("correction cannot downgrade source authority")
