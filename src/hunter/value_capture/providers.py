from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.registry import ValueCaptureSourceConfig

AcquisitionKind = Literal["evidence", "supply", "rule"]


@dataclass(frozen=True)
class AcquisitionReceipt:
    acquisition_id: str
    kind: AcquisitionKind
    capability: str
    source_id: str
    source_authority_tier: str
    endpoint: str
    parser_version: str
    registry_fingerprint: str
    acquired_at: datetime
    identity: EconomicClaimIdentity
    raw_payload_hash: str
    receipt_hash: str


@dataclass(frozen=True)
class ValueCaptureAcquisitionResult:
    receipt: AcquisitionReceipt
    payload: dict[str, Any]
    signature: str

    @property
    def kind(self) -> AcquisitionKind:
        return self.receipt.kind

    @property
    def capability(self) -> str:
        return self.receipt.capability

    @property
    def source_id(self) -> str:
        return self.receipt.source_id

    @property
    def source_authority_tier(self) -> str:
        return self.receipt.source_authority_tier

    @property
    def endpoint(self) -> str:
        return self.receipt.endpoint

    @property
    def parser_version(self) -> str:
        return self.receipt.parser_version

    @property
    def registry_fingerprint(self) -> str:
        return self.receipt.registry_fingerprint

    @property
    def acquisition_id(self) -> str:
        return self.receipt.acquisition_id

    @property
    def acquired_at(self) -> datetime:
        return self.receipt.acquired_at

    @property
    def identity(self) -> EconomicClaimIdentity:
        return self.receipt.identity

    @property
    def raw_payload_hash(self) -> str:
        return self.receipt.raw_payload_hash


class RegisteredValueCaptureProvider:
    def __init__(self, source: ValueCaptureSourceConfig) -> None:
        self._source = source
        self.__signing_key = secrets.token_bytes(32)

    @property
    def source_id(self) -> str:
        return self._source.source_id

    def acquisition(
        self,
        *,
        kind: AcquisitionKind,
        capability: str,
        endpoint: str,
        acquisition_id: str,
        acquired_at: datetime,
        identity: EconomicClaimIdentity,
        payload: dict[str, Any],
    ) -> ValueCaptureAcquisitionResult:
        expected_prefix = {"evidence": "evidence:", "supply": "supply:", "rule": "rule:"}[kind]
        if not capability.startswith(expected_prefix):
            raise ValueError("acquisition kind does not match capability")
        self._source.authorize(
            endpoint=endpoint,
            parser_version=self._source.parser_version,
            capability=capability,
        )
        acquired = _utc(acquired_at)
        if not acquisition_id.strip():
            raise ValueError("acquisition_id must not be blank")
        payload_copy = dict(payload)
        payload_hash = _payload_hash(payload_copy)
        receipt_hash = _receipt_hash(
            acquisition_id=acquisition_id,
            kind=kind,
            capability=capability,
            source=self._source,
            endpoint=endpoint,
            acquired_at=acquired,
            identity=identity,
            raw_payload_hash=payload_hash,
        )
        receipt = AcquisitionReceipt(
            acquisition_id=acquisition_id,
            kind=kind,
            capability=capability,
            source_id=self._source.source_id,
            source_authority_tier=self._source.authority_tier,
            endpoint=endpoint,
            parser_version=self._source.parser_version,
            registry_fingerprint=self._source.fingerprint,
            acquired_at=acquired,
            identity=identity,
            raw_payload_hash=payload_hash,
            receipt_hash=receipt_hash,
        )
        signature = hmac.new(self.__signing_key, receipt_hash.encode(), hashlib.sha256).hexdigest()
        return ValueCaptureAcquisitionResult(receipt=receipt, payload=payload_copy, signature=signature)

    def verify(self, result: ValueCaptureAcquisitionResult) -> bool:
        receipt = result.receipt
        if receipt.source_id != self._source.source_id:
            return False
        if receipt.registry_fingerprint != self._source.fingerprint:
            return False
        if _payload_hash(result.payload) != receipt.raw_payload_hash:
            return False
        expected_hash = _receipt_hash(
            acquisition_id=receipt.acquisition_id,
            kind=receipt.kind,
            capability=receipt.capability,
            source=self._source,
            endpoint=receipt.endpoint,
            acquired_at=receipt.acquired_at,
            identity=receipt.identity,
            raw_payload_hash=receipt.raw_payload_hash,
        )
        if expected_hash != receipt.receipt_hash:
            return False
        expected_signature = hmac.new(self.__signing_key, expected_hash.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_signature, result.signature)


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _receipt_hash(
    *,
    acquisition_id: str,
    kind: AcquisitionKind,
    capability: str,
    source: ValueCaptureSourceConfig,
    endpoint: str,
    acquired_at: datetime,
    identity: EconomicClaimIdentity,
    raw_payload_hash: str,
) -> str:
    payload = {
        "acquisition_id": acquisition_id,
        "kind": kind,
        "capability": capability,
        "source_id": source.source_id,
        "source_authority_tier": source.authority_tier,
        "endpoint": endpoint,
        "parser_version": source.parser_version,
        "registry_fingerprint": source.fingerprint,
        "acquired_at": acquired_at.isoformat(),
        "identity": {
            "entity_id": identity.entity_id,
            "economic_claim_id": identity.economic_claim_id,
            "asset_id": identity.asset_id,
            "representation_id": identity.representation_id,
            "token_id": identity.token_id,
            "chain": identity.chain,
            "contract_address": identity.contract_address,
        },
        "raw_payload_hash": raw_payload_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("acquired_at must be timezone-aware")
    return value.astimezone(UTC)
