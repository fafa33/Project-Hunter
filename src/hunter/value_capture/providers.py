from __future__ import annotations

import hashlib
import hmac
import json
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
    signing_key_id: str
    acquired_at: datetime
    identity: EconomicClaimIdentity
    raw_payload_hash: str
    receipt_hash: str
    signature: str


@dataclass(frozen=True)
class ValueCaptureAcquisitionResult:
    receipt: AcquisitionReceipt
    payload: dict[str, Any]

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


class ValueCaptureVerificationKeyRegistry:
    def __init__(self, keys: dict[str, bytes]) -> None:
        if not keys:
            raise ValueError("verification keys must not be empty")
        normalized: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not key_id.strip():
                raise ValueError("signing_key_id must not be blank")
            if len(key) < 32:
                raise ValueError("verification key must be at least 32 bytes")
            normalized[key_id] = bytes(key)
        self.__keys = normalized

    def verify_receipt(self, receipt: AcquisitionReceipt) -> bool:
        key = self.__keys.get(receipt.signing_key_id)
        if key is None:
            return False
        recomputed_hash = receipt_hash_from_receipt(receipt)
        if not hmac.compare_digest(recomputed_hash, receipt.receipt_hash):
            return False
        expected = hmac.new(key, recomputed_hash.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.signature)


class RegisteredValueCaptureProvider:
    def __init__(
        self,
        source: ValueCaptureSourceConfig,
        *,
        signing_key_id: str,
        signing_key: bytes,
    ) -> None:
        if not signing_key_id.strip():
            raise ValueError("signing_key_id must not be blank")
        if len(signing_key) < 32:
            raise ValueError("signing_key must be at least 32 bytes")
        self._source = source
        self.__signing_key_id = signing_key_id
        self.__signing_key = bytes(signing_key)

    @property
    def source_id(self) -> str:
        return self._source.source_id

    @property
    def signing_key_id(self) -> str:
        return self.__signing_key_id

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
        payload_hash = payload_hash_for(payload_copy)
        receipt_hash = receipt_hash_for(
            acquisition_id=acquisition_id,
            kind=kind,
            capability=capability,
            source=self._source,
            signing_key_id=self.__signing_key_id,
            endpoint=endpoint,
            acquired_at=acquired,
            identity=identity,
            raw_payload_hash=payload_hash,
        )
        signature = hmac.new(self.__signing_key, receipt_hash.encode(), hashlib.sha256).hexdigest()
        receipt = AcquisitionReceipt(
            acquisition_id=acquisition_id,
            kind=kind,
            capability=capability,
            source_id=self._source.source_id,
            source_authority_tier=self._source.authority_tier,
            endpoint=endpoint,
            parser_version=self._source.parser_version,
            registry_fingerprint=self._source.fingerprint,
            signing_key_id=self.__signing_key_id,
            acquired_at=acquired,
            identity=identity,
            raw_payload_hash=payload_hash,
            receipt_hash=receipt_hash,
            signature=signature,
        )
        return ValueCaptureAcquisitionResult(receipt=receipt, payload=payload_copy)

    def verify(self, result: ValueCaptureAcquisitionResult) -> bool:
        receipt = result.receipt
        if receipt.source_id != self._source.source_id:
            return False
        if receipt.signing_key_id != self.__signing_key_id:
            return False
        if receipt.registry_fingerprint != self._source.fingerprint:
            return False
        if payload_hash_for(result.payload) != receipt.raw_payload_hash:
            return False
        expected_hash = receipt_hash_for(
            acquisition_id=receipt.acquisition_id,
            kind=receipt.kind,
            capability=receipt.capability,
            source=self._source,
            signing_key_id=receipt.signing_key_id,
            endpoint=receipt.endpoint,
            acquired_at=receipt.acquired_at,
            identity=receipt.identity,
            raw_payload_hash=receipt.raw_payload_hash,
        )
        if expected_hash != receipt.receipt_hash:
            return False
        expected_signature = hmac.new(self.__signing_key, expected_hash.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected_signature, receipt.signature)


def receipt_hash_from_receipt(receipt: AcquisitionReceipt) -> str:
    payload = {
        "acquisition_id": receipt.acquisition_id,
        "kind": receipt.kind,
        "capability": receipt.capability,
        "source_id": receipt.source_id,
        "source_authority_tier": receipt.source_authority_tier,
        "endpoint": receipt.endpoint,
        "parser_version": receipt.parser_version,
        "registry_fingerprint": receipt.registry_fingerprint,
        "signing_key_id": receipt.signing_key_id,
        "acquired_at": receipt.acquired_at.isoformat(),
        "identity": {
            "entity_id": receipt.identity.entity_id,
            "economic_claim_id": receipt.identity.economic_claim_id,
            "asset_id": receipt.identity.asset_id,
            "representation_id": receipt.identity.representation_id,
            "token_id": receipt.identity.token_id,
            "chain": receipt.identity.chain,
            "contract_address": receipt.identity.contract_address,
        },
        "raw_payload_hash": receipt.raw_payload_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def payload_hash_for(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def receipt_hash_for(
    *,
    acquisition_id: str,
    kind: AcquisitionKind,
    capability: str,
    source: ValueCaptureSourceConfig,
    signing_key_id: str,
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
        "signing_key_id": signing_key_id,
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
