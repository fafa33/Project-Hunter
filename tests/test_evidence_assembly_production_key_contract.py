from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hunter.evidence_assembly.composition import (
    ProductionEvidenceAssemblyCompositionError,
    _resolve_verification_keys,
)
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider
from hunter.value_capture.registry import ValueCaptureSourceConfig


def _source() -> ValueCaptureSourceConfig:
    return ValueCaptureSourceConfig(
        source_id="source-test",
        authority_tier="official",
        source_type="api",
        allowed_hosts=("example.com",),
        endpoint_patterns=("https://example.com/evidence",),
        parser_version="1.0.0",
        capabilities=("evidence:test",),
        enabled=True,
    )


def _identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id="entity-test",
        economic_claim_id="claim-test",
        asset_id="asset-test",
        representation_id="representation-test",
        token_id="token-test",
        chain="ethereum",
        contract_address="0x123",
    )


def test_production_verification_registry_reuses_established_signing_key_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_key = b"k" * 32
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "production-key")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", signing_key.hex())
    # A conflicting legacy value proves the established producer contract has precedence.
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_ID", "legacy-key")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", "x" * 32)

    provider = RegisteredValueCaptureProvider(
        _source(),
        signing_key_id="production-key",
        signing_key=signing_key,
    )
    result = provider.acquisition(
        kind="evidence",
        capability="evidence:test",
        endpoint="https://example.com/evidence/1",
        acquisition_id="acquisition-test",
        acquired_at=datetime(2026, 8, 23, tzinfo=UTC),
        identity=_identity(),
        payload={"value": "1"},
    )

    registry = _resolve_verification_keys()

    assert registry.verify_receipt(result.receipt) is True


def test_production_verification_registry_rejects_non_hex_established_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "production-key")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", "not-hex")
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_ID", raising=False)
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", raising=False)

    with pytest.raises(
        ProductionEvidenceAssemblyCompositionError,
        match="must be a hex-encoded byte string",
    ):
        _resolve_verification_keys()


def test_production_verification_registry_rejects_short_decoded_established_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "production-key")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", b"short".hex())
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_ID", raising=False)
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_KEY_SECRET", raising=False)

    with pytest.raises(
        ProductionEvidenceAssemblyCompositionError,
        match="must decode to at least 32 bytes",
    ):
        _resolve_verification_keys()
