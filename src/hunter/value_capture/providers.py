from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.registry import ValueCaptureSourceConfig

AcquisitionKind = Literal["evidence", "supply", "rule"]
_PROVIDER_SEAL = object()


@dataclass(frozen=True, init=False)
class ValueCaptureAcquisitionResult:
    kind: AcquisitionKind
    capability: str
    source_id: str
    source_authority_tier: str
    endpoint: str
    parser_version: str
    registry_fingerprint: str
    acquisition_id: str
    acquired_at: datetime
    identity: EconomicClaimIdentity
    payload: dict[str, Any]
    raw_payload_hash: str
    _seal: object

    def __init__(
        self,
        *,
        kind: AcquisitionKind,
        capability: str,
        source: ValueCaptureSourceConfig,
        endpoint: str,
        acquisition_id: str,
        acquired_at: datetime,
        identity: EconomicClaimIdentity,
        payload: dict[str, Any],
        seal: object,
    ) -> None:
        if seal is not _PROVIDER_SEAL:
            raise ValueError("provider acquisition results cannot be caller-constructed")
        source.authorize(endpoint=endpoint, parser_version=source.parser_version, capability=capability)
        acquired = _utc(acquired_at)
        if not acquisition_id.strip():
            raise ValueError("acquisition_id must not be blank")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "source_id", source.source_id)
        object.__setattr__(self, "source_authority_tier", source.authority_tier)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "parser_version", source.parser_version)
        object.__setattr__(self, "registry_fingerprint", source.fingerprint)
        object.__setattr__(self, "acquisition_id", acquisition_id)
        object.__setattr__(self, "acquired_at", acquired)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "payload", dict(payload))
        object.__setattr__(self, "raw_payload_hash", hashlib.sha256(canonical.encode()).hexdigest())
        object.__setattr__(self, "_seal", seal)

    @property
    def is_provider_sealed(self) -> bool:
        return self._seal is _PROVIDER_SEAL


class RegisteredValueCaptureProvider:
    def __init__(self, source: ValueCaptureSourceConfig) -> None:
        self._source = source

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
        return ValueCaptureAcquisitionResult(
            kind=kind,
            capability=capability,
            source=self._source,
            endpoint=endpoint,
            acquisition_id=acquisition_id,
            acquired_at=acquired_at,
            identity=identity,
            payload=payload,
            seal=_PROVIDER_SEAL,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("acquired_at must be timezone-aware")
    return value.astimezone(UTC)
