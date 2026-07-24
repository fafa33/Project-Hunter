from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

import yaml

from hunter.execution.canonicalization import canonicalize
from hunter.market_facts.models import MARKET_FACT_TYPES, MarketFactIdentity, MarketFactType


@dataclass(frozen=True)
class MarketFactSourceConfig:
    source_id: str
    provider_id: str
    endpoint_template: str
    allowed_hosts: tuple[str, ...]
    parser_version: str
    enabled: bool
    capabilities: tuple[MarketFactType, ...]
    quote_currencies: tuple[str, ...]
    unit_by_fact: tuple[tuple[str, str], ...]
    supported_entity_scope: tuple[str, ...]
    freshness_seconds: int
    observation_confidence: str
    historical_support: str
    limitations: str
    identity_bindings: tuple[tuple[str, str, str, str, str, str], ...]

    def __post_init__(self) -> None:
        for name in ("source_id", "provider_id", "endpoint_template", "parser_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        parsed = urllib.parse.urlparse(self.endpoint_template.format(listing_id="placeholder"))
        if parsed.scheme != "https":
            raise ValueError("market fact source endpoint must use HTTPS")
        if not parsed.hostname or parsed.hostname not in self.allowed_hosts:
            raise ValueError("market fact source host must be explicitly allowed")
        for capability in self.capabilities:
            if capability not in MARKET_FACT_TYPES:
                raise ValueError(f"unsupported market fact capability: {capability}")
        if not self.quote_currencies:
            raise ValueError("quote_currencies must not be empty")
        if self.freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be positive")
        try:
            confidence = Decimal(self.observation_confidence)
        except InvalidOperation as exc:
            raise ValueError("observation_confidence must be a decimal") from exc
        if not confidence.is_finite() or confidence < 0 or confidence > 1:
            raise ValueError("observation_confidence must be between 0 and 1")
        if not self.identity_bindings:
            raise ValueError("identity_bindings must not be empty")
        if len({binding[5] for binding in self.identity_bindings}) != len(self.identity_bindings):
            raise ValueError("provider listing IDs must be unique within a market fact source")

    @property
    def units(self) -> dict[str, str]:
        return dict(self.unit_by_fact)

    @property
    def fingerprint(self) -> str:
        payload = canonicalize(
            {
                "source_id": self.source_id,
                "provider_id": self.provider_id,
                "endpoint_template": self.endpoint_template,
                "allowed_hosts": self.allowed_hosts,
                "parser_version": self.parser_version,
                "enabled": self.enabled,
                "capabilities": self.capabilities,
                "quote_currencies": self.quote_currencies,
                "unit_by_fact": self.unit_by_fact,
                "supported_entity_scope": self.supported_entity_scope,
                "freshness_seconds": self.freshness_seconds,
                "observation_confidence": self.observation_confidence,
                "historical_support": self.historical_support,
                "limitations": self.limitations,
                "identity_bindings": self.identity_bindings,
            }
        )
        return f"sha256:{sha256(payload).hexdigest()}"

    def endpoint_for(self, listing_id: str) -> str:
        if not listing_id.strip():
            raise ValueError("listing_id must not be blank")
        endpoint = self.endpoint_template.format(listing_id=urllib.parse.quote(listing_id, safe=""))
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise ValueError("resolved endpoint is not registry-approved")
        return endpoint

    def authorize_identity(self, identity: MarketFactIdentity) -> None:
        candidate = (
            identity.entity_id,
            identity.asset_id,
            identity.representation_id,
            identity.chain,
            identity.contract_address,
            identity.provider_listing_id,
        )
        if candidate not in self.identity_bindings:
            raise ValueError("market fact identity is not bound to the provider listing")


class MarketFactSourceRegistry:
    def __init__(self, sources: tuple[MarketFactSourceConfig, ...]) -> None:
        ids = [source.source_id for source in sources]
        if len(ids) != len(set(ids)):
            raise ValueError("market fact source IDs must be unique")
        self._sources = {source.source_id: source for source in sources}

    @classmethod
    def from_file(cls, path: str | Path = "configs/market_fact_sources.yaml") -> MarketFactSourceRegistry:
        config_path = Path(path)
        if not config_path.exists():
            return cls(())
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, Mapping):
            raise ValueError("market fact source registry must be a mapping")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> MarketFactSourceRegistry:
        rows = payload.get("sources", ())
        if not isinstance(rows, list):
            raise ValueError("market fact source registry sources must be a list")
        sources: list[MarketFactSourceConfig] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("market fact source entry must be a mapping")
            units = row.get("units", {})
            if not isinstance(units, Mapping):
                raise ValueError("market fact source units must be a mapping")
            capabilities = tuple(str(item) for item in _list(row, "capabilities"))
            identity_bindings = tuple(_identity_binding(item) for item in _list(row, "identity_bindings"))
            sources.append(
                MarketFactSourceConfig(
                    source_id=_text(row, "source_id"),
                    provider_id=_text(row, "provider_id"),
                    endpoint_template=_text(row, "endpoint_template"),
                    allowed_hosts=tuple(str(item) for item in _list(row, "allowed_hosts")),
                    parser_version=_text(row, "parser_version"),
                    enabled=bool(row.get("enabled", False)),
                    capabilities=capabilities,  # type: ignore[arg-type]
                    quote_currencies=tuple(str(item).lower() for item in _list(row, "quote_currencies")),
                    unit_by_fact=tuple(sorted((str(key), str(value)) for key, value in units.items())),
                    supported_entity_scope=tuple(str(item) for item in _list(row, "supported_entity_scope")),
                    freshness_seconds=int(row.get("freshness_seconds", 3600)),
                    observation_confidence=_text(row, "observation_confidence"),
                    historical_support=str(row.get("historical_support", "current-only")),
                    limitations=str(row.get("limitations", "")),
                    identity_bindings=identity_bindings,
                )
            )
        return cls(tuple(sources))

    def source(self, source_id: str) -> MarketFactSourceConfig | None:
        return self._sources.get(source_id)

    def require(self, source_id: str) -> MarketFactSourceConfig:
        source = self.source(source_id)
        if source is None:
            raise ValueError("market fact source is not registered")
        if not source.enabled:
            raise ValueError("market fact source is disabled")
        return source


def _text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _list(row: Mapping[str, object], key: str) -> list[object]:
    value = row.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _identity_binding(value: object) -> tuple[str, str, str, str, str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("identity binding must be a mapping")
    return (
        _text(value, "entity_id"),
        _text(value, "asset_id"),
        _text(value, "representation_id"),
        str(value.get("chain", "")),
        str(value.get("contract_address", "")),
        _text(value, "provider_listing_id"),
    )
