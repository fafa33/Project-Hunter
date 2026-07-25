from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.market_facts.models import MarketFactIdentity, MarketFactRequest
from hunter.market_facts.providers import CoinGeckoObservedMarketFactProvider, JsonTransport
from hunter.market_facts.registry import MarketFactSourceRegistry
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_MARKET_FACT_SOURCES_CONFIG = Path("configs/market_fact_sources.yaml")
_VALUE_CAPTURE_SOURCES_CONFIG = Path("configs/value_capture_sources.yaml")
_SIGNING_KEY_ID_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID"
_SIGNING_KEY_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY"

# Maps a market-fact source's registered provider_id to the provider class that can
# service it. Adding a future provider is a registration here, not a change to the
# acquisition orchestration below. No entity identity appears in this module: every
# entity, source, and identity value is supplied entirely by the manifest and by the
# YAML source registries it references.
_MARKET_FACT_PROVIDERS: dict[str, type[CoinGeckoObservedMarketFactProvider]] = {
    "coingecko": CoinGeckoObservedMarketFactProvider,
}

_VALUE_CAPTURE_KINDS = ("evidence", "supply", "rule")


def main(argv: list[str], *, transport: JsonTransport | None = None) -> int:
    if len(argv) != 2 or argv[0] != "acquire":
        print("usage: hunter valuation-evidence acquire MANIFEST.json")
        return 1
    manifest_path = Path(argv[1]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("valuation-evidence manifest must be a JSON object")
    application_root = _application_root()
    operation = manifest.get("operation")
    if operation == "market_fact":
        output = _acquire_market_fact(manifest, application_root, transport=transport)
    elif operation == "value_capture":
        output = _acquire_value_capture(manifest, application_root)
    else:
        raise ValueError("valuation-evidence manifest requires operation: market_fact or value_capture")
    print(json.dumps(output, sort_keys=True))
    return 0


def _acquire_market_fact(
    manifest: dict[str, Any],
    application_root: Path,
    *,
    transport: JsonTransport | None,
) -> dict[str, Any]:
    registry_path = _canonical_path(application_root, _MARKET_FACT_SOURCES_CONFIG)
    registry = MarketFactSourceRegistry.from_file(registry_path)
    identity = _market_fact_identity(manifest["identity"])
    request = MarketFactRequest(
        source_id=str(manifest["source_id"]),
        provider_id=str(manifest["provider_id"]),
        identity=identity,
        quote_currency=str(manifest["quote_currency"]),
        requested_fact_types=tuple(str(item) for item in manifest["requested_fact_types"]),  # type: ignore[misc]
        requested_at=_datetime(manifest["requested_at"]),
    )
    provider_cls = _MARKET_FACT_PROVIDERS.get(request.provider_id)
    if provider_cls is None:
        raise ValueError(f"unsupported market fact provider_id: {request.provider_id}")
    provider = provider_cls(registry, transport=transport) if transport is not None else provider_cls(registry)
    result = provider.collect(request)
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    repository = ObservedMarketFactRepository(persistence_path)
    service = ObservedMarketFactService(repository, registry)
    recorded_at = _datetime(manifest["recorded_at"]) if "recorded_at" in manifest else result.known_at
    records = service.ingest(request, result, recorded_at=recorded_at)
    if not records:
        raise ValueError(
            f"market fact acquisition produced no accepted records: "
            f"status={result.status!r} failure_reason={result.failure_reason!r}"
        )
    return {
        "operation": "market_fact",
        "source_id": request.source_id,
        "persistence_database": str(persistence_path),
        "records": [
            {
                "record_id": record.record_id,
                "logical_id": record.logical_id,
                "semantic_version": record.semantic_version,
                "fact_type": record.fact_type,
                "quality_state": record.quality_state,
                "conflict_state": record.conflict_state,
            }
            for record in records
        ],
    }


def _acquire_value_capture(manifest: dict[str, Any], application_root: Path) -> dict[str, Any]:
    kind = str(manifest["kind"])
    if kind not in _VALUE_CAPTURE_KINDS:
        raise ValueError(f"unsupported value_capture kind: {kind}")
    sources_path = _canonical_path(application_root, _VALUE_CAPTURE_SOURCES_CONFIG)
    source_registry = ValueCaptureSourceRegistry.from_yaml(sources_path)
    source = source_registry.require(str(manifest["source_id"]))
    signing_key_id, signing_key = _signing_key()
    verification_keys = ValueCaptureVerificationKeyRegistry({signing_key_id: signing_key})
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    repository = SupplyAndValueCaptureRepository(persistence_path)
    market_fact_repository = ObservedMarketFactRepository(persistence_path)
    service = SupplyAndValueCaptureService(
        registry=source_registry,
        repository=repository,
        verification_keys=verification_keys,
        market_fact_repository=market_fact_repository,
    )
    provider = RegisteredValueCaptureProvider(source, signing_key_id=signing_key_id, signing_key=signing_key)
    identity = _value_capture_identity(manifest["identity"])
    payload = manifest["payload"]
    if not isinstance(payload, dict):
        raise ValueError("valuation-evidence value_capture manifest requires a payload object")
    result = provider.acquisition(
        kind=kind,  # type: ignore[arg-type]
        capability=str(manifest["capability"]),
        endpoint=str(manifest["endpoint"]),
        acquisition_id=str(manifest["acquisition_id"]),
        acquired_at=_datetime(manifest["acquired_at"]),
        identity=identity,
        payload=payload,
    )
    if kind == "evidence":
        record = service.ingest_evidence(provider, result)
    elif kind == "supply":
        record = service.ingest_supply(provider, result)
    else:
        record = service.ingest_rule(provider, result)
    return {
        "operation": "value_capture",
        "kind": kind,
        "source_id": source.source_id,
        "persistence_database": str(persistence_path),
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
    }


def _market_fact_identity(payload: Any) -> MarketFactIdentity:
    if not isinstance(payload, dict):
        raise ValueError("valuation-evidence market_fact manifest requires an identity object")
    return MarketFactIdentity(
        entity_id=str(payload["entity_id"]),
        asset_id=str(payload["asset_id"]),
        representation_id=str(payload["representation_id"]),
        chain=str(payload.get("chain", "")),
        contract_address=str(payload.get("contract_address", "")),
        provider_listing_id=str(payload["provider_listing_id"]),
    )


def _value_capture_identity(payload: Any) -> EconomicClaimIdentity:
    if not isinstance(payload, dict):
        raise ValueError("valuation-evidence value_capture manifest requires an identity object")
    return EconomicClaimIdentity(
        entity_id=str(payload["entity_id"]),
        economic_claim_id=str(payload["economic_claim_id"]),
        asset_id=str(payload["asset_id"]),
        representation_id=str(payload["representation_id"]),
        token_id=str(payload["token_id"]),
        chain=str(payload.get("chain", "")),
        contract_address=str(payload.get("contract_address", "")),
    )


def _signing_key() -> tuple[str, bytes]:
    key_id = os.environ.get(_SIGNING_KEY_ID_ENV, "").strip()
    key_hex = os.environ.get(_SIGNING_KEY_ENV, "").strip()
    if not key_id:
        raise ValueError(f"{_SIGNING_KEY_ID_ENV} must identify the value-capture provider signing key")
    if not key_hex:
        raise ValueError(f"{_SIGNING_KEY_ENV} must supply the value-capture provider signing key as hex")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError(f"{_SIGNING_KEY_ENV} must be a hex-encoded byte string") from exc
    return key_id, key


def _application_root() -> Path:
    configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
    if not configured:
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root")
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return root.resolve()


def _canonical_path(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("canonical valuation-evidence runtime path escaped application root")
    return candidate


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("valuation-evidence manifest timestamps must be timezone-aware ISO-8601 values")
    return parsed
