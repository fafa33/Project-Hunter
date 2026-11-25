from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from hunter.execution.identity import identity
from hunter.onchain.adapters import endpoint_identity
from hunter.onchain.configuration import OnChainConfig, load_onchain_config
from hunter.onchain.models import RawOnChainObservation

EVIDENCE_SIGNING_DOMAIN = "hunter-capital-flow-evidence-v1"
EVIDENCE_SCHEMA_VERSION = "hunter-capital-flow-evidence-record-v1"
TRUST_SCHEMA_VERSION = "hunter-capital-flow-evidence-trust-v1"
TRUST_FILE_ENV = "HUNTER_CAPITAL_FLOW_EVIDENCE_TRUST_FILE"
SIGNING_KEY_FILE_ENV = "HUNTER_CAPITAL_FLOW_EVIDENCE_SIGNING_KEY_FILE"
CORROBORATION_POLICY = "evm-receipt-corroboration-v1"
MAX_EVIDENCE_AGE = timedelta(hours=24)
FUTURE_TOLERANCE = timedelta(minutes=5)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_ADDRESS = re.compile(r"0x[0-9a-f]{40}")
_HASH = re.compile(r"0x[0-9a-f]{64}")
_HEX = re.compile(r"[0-9a-f]{64}")


class CapitalFlowEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceTrust:
    keys: Mapping[str, Ed25519PublicKey]
    source: Path

    def __post_init__(self) -> None:
        if not self.keys:
            raise CapitalFlowEvidenceError("capital-flow evidence trust has no public keys")


def load_signing_key(path: str | Path | None = None) -> Ed25519PrivateKey:
    source = Path(path) if path is not None else Path(os.environ.get(SIGNING_KEY_FILE_ENV, ""))
    if not str(source) or not source.exists():
        raise CapitalFlowEvidenceError("capital-flow evidence signing key is not configured")
    raw = source.read_text(encoding="utf-8").strip()
    if _HEX.fullmatch(raw) is None:
        raise CapitalFlowEvidenceError("capital-flow evidence signing key is malformed")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw))


def configured_signing_key() -> Ed25519PrivateKey | None:
    if not os.environ.get(SIGNING_KEY_FILE_ENV):
        return None
    return load_signing_key()


def load_trust(path: str | Path | None = None) -> EvidenceTrust:
    source = Path(path) if path is not None else Path(os.environ.get(TRUST_FILE_ENV, ""))
    if not str(source) or not source.exists():
        raise CapitalFlowEvidenceError("capital-flow evidence trust configuration is missing")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CapitalFlowEvidenceError("capital-flow evidence trust configuration is malformed") from None
    if not isinstance(document, dict) or set(document) != {"schema_version", "domain", "public_keys"}:
        raise CapitalFlowEvidenceError("capital-flow evidence trust document has an invalid schema")
    if document["schema_version"] != TRUST_SCHEMA_VERSION or document["domain"] != EVIDENCE_SIGNING_DOMAIN:
        raise CapitalFlowEvidenceError("capital-flow evidence trust document uses the wrong domain")
    entries = document["public_keys"]
    if not isinstance(entries, dict) or not entries:
        raise CapitalFlowEvidenceError("capital-flow evidence trust pins no public keys")
    from hunter.automation.issue_agent_state import public_key_id

    keys: dict[str, Ed25519PublicKey] = {}
    for declared_id, value in entries.items():
        if not isinstance(declared_id, str) or _HEX.fullmatch(declared_id) is None:
            raise CapitalFlowEvidenceError("capital-flow evidence key id is malformed")
        if not isinstance(value, str) or _HEX.fullmatch(value) is None:
            raise CapitalFlowEvidenceError("capital-flow evidence public key is malformed")
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))
        if public_key_id(public) != declared_id:
            raise CapitalFlowEvidenceError("capital-flow evidence key id does not match its public key")
        keys[declared_id] = public
    return EvidenceTrust(keys=keys, source=source)


def write_trust_document(path: str | Path, private_key: Ed25519PrivateKey) -> None:
    from hunter.automation.issue_agent_state import public_key_id

    public = private_key.public_key()
    key_id = public_key_id(public)
    raw_key = public.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    document = {
        "schema_version": TRUST_SCHEMA_VERSION,
        "domain": EVIDENCE_SIGNING_DOMAIN,
        "public_keys": {key_id: raw_key.hex()},
    }
    Path(path).write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")


def write_signing_key(path: str | Path, private_key: Ed25519PrivateKey) -> None:
    raw = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    Path(path).write_text(raw.hex() + "\n", encoding="utf-8")


def signature_id(row: RawOnChainObservation | Mapping[str, Any]) -> str:
    return f"{_field(row, 'project')}:{_field(row, 'chain_id')}:{_field(row, 'evidence_id')}"


def receipt_log_digest(row: RawOnChainObservation | Mapping[str, Any]) -> str:
    log_index = _field(row, "log_index")
    block_number = _field(row, "block_number")
    return identity(
        "evm-receipt-log",
        {
            "address": str(_field(row, "asset_contract")).lower(),
            "topics": [str(item).lower() for item in (_field(row, "log_topics") or ())],
            "data": str(_field(row, "log_data")).lower(),
            "transactionHash": str(_field(row, "transaction_hash")).lower(),
            "logIndex": hex(_as_int(log_index)),
            "blockNumber": hex(_as_int(block_number)),
            "blockHash": str(_field(row, "block_hash")).lower(),
        },
    )


def observation_payload(row: RawOnChainObservation, surface: Any, verification_timestamp: datetime) -> dict[str, Any]:
    if verification_timestamp.tzinfo is None:
        raise CapitalFlowEvidenceError("evidence verification timestamp has no timezone")
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "domain": EVIDENCE_SIGNING_DOMAIN,
        "signature_id": signature_id(row),
        "project": row.project,
        "surface": {
            "evidence_id": surface.evidence_id,
            "project": surface.project,
            "chain_id": surface.chain_id,
            "network": surface.network,
            "address": surface.address.lower(),
            "address_type": surface.address_type,
            "protocol_role": surface.protocol_role,
            "asset_scope": surface.asset_scope,
            "source_url": surface.source_url,
            "source_type": surface.source_type,
            "verification_timestamp": _instant(surface.verification_timestamp),
            "valid_from": _instant(surface.valid_from),
            "valid_to": _instant(surface.valid_to) if surface.valid_to is not None else None,
            "confidence": surface.confidence,
            "project_state": surface.project_state,
        },
        "chain_id": row.chain_id,
        "evidence_id": row.evidence_id,
        "event_type": row.event_type,
        "transaction_hash": _lower(row.transaction_hash),
        "log_index": row.log_index,
        "log_topics": list(row.log_topics),
        "log_data": row.log_data,
        "block_number": row.block_number,
        "block_hash": _lower(row.block_hash),
        "block_timestamp": _instant(row.block_timestamp),
        "observed_address": row.observed_address.lower(),
        "token_contract": _lower(row.asset_contract),
        "raw_amount": row.raw_amount,
        "amount": str(row.normalized_amount),
        "decimals": row.decimals,
        "direction": row.direction,
        "counterparty": _lower(row.counterparty_address),
        "acquisition_timestamp": _instant(row.acquisition_timestamp),
        "log_endpoint": row.log_endpoint,
        "receipt_endpoint": row.receipt_endpoint,
        "corroborating_endpoints": sorted(row.corroborating_endpoints),
        "validation_policy": row.receipt_validation_policy,
        "receipt_log_digest": row.receipt_log_digest,
        "verification_timestamp": _instant(verification_timestamp),
        "provider": row.provider,
        "source_reference": row.source_reference,
        "finality_status": row.finality_status,
    }


def sign_observation(
    row: RawOnChainObservation,
    surface: Any,
    private_key: Ed25519PrivateKey,
    *,
    verification_timestamp: datetime | None = None,
) -> dict[str, Any]:
    from hunter.automation.issue_agent_state import sign_record

    verified_at = verification_timestamp or row.receipt_verified_at
    if verified_at is None:
        raise CapitalFlowEvidenceError("evidence has no verification timestamp")
    payload = observation_payload(row, surface, verified_at)
    signed = sign_record(payload, private_key, domain=EVIDENCE_SIGNING_DOMAIN)
    return {
        "signature_id": signature_id(row),
        "project": row.project,
        "chain_id": row.chain_id,
        "evidence_id": row.evidence_id,
        "surface_evidence_id": surface.evidence_id,
        "key_id": signed["signature"]["key_id"],
        "domain": signed["signature"]["domain"],
        "signature": signed["signature"],
    }


def verify_observation(
    row: RawOnChainObservation | Mapping[str, Any],
    signatures: Sequence[Mapping[str, Any]],
    surface: Any,
    trust: EvidenceTrust | Mapping[str, Ed25519PublicKey],
    *,
    as_of: datetime | None = None,
) -> RawOnChainObservation:
    from hunter.automation.issue_agent_state import LedgerError, canonical_json, verify_record_signature

    observation = as_observation(row)
    candidates = [
        item
        for item in signatures
        if item.get("signature_id") == signature_id(observation)
        and item.get("evidence_id") == observation.evidence_id
        and item.get("project") == observation.project
    ]
    if not candidates:
        raise CapitalFlowEvidenceError("evidence has no signature in the trusted repository")
    distinct = {canonical_json(item) for item in candidates}
    if len(distinct) != 1:
        raise CapitalFlowEvidenceError("evidence has conflicting signature records")
    record = candidates[0]
    if record.get("surface_evidence_id") != surface.evidence_id:
        raise CapitalFlowEvidenceError("evidence signature binds another registered surface")
    if any(
        other.get("evidence_id") == observation.evidence_id and other.get("signature_id") != record.get("signature_id")
        for other in signatures
    ):
        raise CapitalFlowEvidenceError("evidence id is attributed to another project or surface")
    if observation.receipt_verified_at is None:
        raise CapitalFlowEvidenceError("evidence has no verification timestamp")
    payload = observation_payload(observation, surface, observation.receipt_verified_at)
    payload["signature"] = dict(record.get("signature") or {})
    keys = trust.keys if isinstance(trust, EvidenceTrust) else trust
    try:
        verify_record_signature(payload, keys, domain=EVIDENCE_SIGNING_DOMAIN)
    except (LedgerError, ValueError, TypeError) as exc:
        raise CapitalFlowEvidenceError("capital-flow evidence signature does not verify") from exc
    _validate_observation(observation, surface, as_of or datetime.now(UTC))
    return observation


def verified_capital_flow_rows(
    project: str,
    rows: Iterable[RawOnChainObservation | Mapping[str, Any]],
    *,
    config: OnChainConfig | None = None,
    repository: Any | None = None,
    trust: EvidenceTrust | Mapping[str, Ed25519PublicKey] | None = None,
    as_of: datetime | None = None,
) -> tuple[RawOnChainObservation, ...]:
    from hunter.automation.issue_agent_state import canonical_json
    from hunter.onchain.repository import OnChainRepository

    cutoff = as_of or datetime.now(UTC)
    try:
        resolved_trust = trust if trust is not None else load_trust()
        resolved_config = config or load_onchain_config()
        repo = repository or OnChainRepository()
        signatures = repo.evidence_signatures()
    except (CapitalFlowEvidenceError, OSError, ValueError):
        return ()
    relevant = [
        row
        for row in rows
        if _field(row, "project") == project
        and _field(row, "event_type") == "erc20_transfer"
        and _field(row, "finality_status") == "finalized"
    ]
    accepted: dict[str, RawOnChainObservation] = {}
    rejected: set[str] = set()
    for row in relevant:
        candidate = as_observation(row)
        surface = next(
            (
                item
                for item in resolved_config.surfaces
                if item.active
                and item.project == project
                and item.chain_id == candidate.chain_id
                and item.address.lower() == candidate.observed_address.lower()
            ),
            None,
        )
        if surface is None:
            rejected.add(candidate.evidence_id)
            continue
        try:
            verified = verify_observation(candidate, signatures, surface, resolved_trust, as_of=cutoff)
        except (CapitalFlowEvidenceError, ValueError, TypeError):
            rejected.add(candidate.evidence_id)
            continue
        if candidate.evidence_id in accepted and canonical_json(
            _comparison_value(accepted[candidate.evidence_id])
        ) != canonical_json(_comparison_value(verified)):
            rejected.add(candidate.evidence_id)
            continue
        accepted[candidate.evidence_id] = verified
    for evidence_id in rejected:
        accepted.pop(evidence_id, None)
    return tuple(
        sorted(accepted.values(), key=lambda item: (item.block_number, item.log_index or -1, item.evidence_id))
    )


def as_observation(value: RawOnChainObservation | Mapping[str, Any]) -> RawOnChainObservation:
    if isinstance(value, RawOnChainObservation):
        return value
    get = value.get
    return RawOnChainObservation(
        project=str(get("project")),
        chain_id=int(get("chain_id")),
        provider=str(get("provider")),
        endpoint_identity=str(get("endpoint_identity")),
        block_number=int(get("block_number")),
        block_hash=str(get("block_hash")),
        block_timestamp=_datetime(get("block_timestamp")),
        observed_address=str(get("observed_address")),
        acquisition_timestamp=_datetime(get("acquisition_timestamp")),
        finality_status=str(get("finality_status")),
        source_reference=str(get("source_reference")),
        evidence_id=str(get("evidence_id")),
        transaction_hash=_optional_str(get("transaction_hash")),
        log_index=_optional_int(get("log_index")),
        counterparty_address=_optional_str(get("counterparty_address")),
        asset_contract=_optional_str(get("asset_contract")),
        asset_symbol=_optional_str(get("asset_symbol")),
        raw_amount=_optional_str(get("raw_amount")),
        decimals=_optional_int(get("decimals")),
        normalized_amount=Decimal(str(get("normalized_amount"))) if get("normalized_amount") is not None else None,
        direction=str(get("direction")),
        event_type=str(get("event_type")),
        log_endpoint=_optional_str(get("log_endpoint")),
        receipt_endpoint=_optional_str(get("receipt_endpoint")),
        corroborating_endpoints=tuple(str(item) for item in (get("corroborating_endpoints") or ())),
        receipt_validation_policy=_optional_str(get("receipt_validation_policy")),
        receipt_verified_at=_datetime(get("receipt_verified_at")) if get("receipt_verified_at") else None,
        receipt_log_digest=_optional_str(get("receipt_log_digest")),
        log_topics=tuple(str(item) for item in (get("log_topics") or ())),
        log_data=str(get("log_data") or ""),
    )


def _validate_observation(row: RawOnChainObservation, surface: Any, as_of: datetime) -> None:
    if row.project != surface.project or row.chain_id != surface.chain_id:
        raise CapitalFlowEvidenceError("evidence project or chain attribution does not match the surface")
    if row.observed_address.lower() != surface.address.lower():
        raise CapitalFlowEvidenceError("evidence observed address does not match the registered surface")
    if row.event_type != "erc20_transfer" or row.finality_status != "finalized":
        raise CapitalFlowEvidenceError("evidence is not a finalized ERC20 transfer")
    if row.transaction_hash is None or _HASH.fullmatch(row.transaction_hash.lower()) is None:
        raise CapitalFlowEvidenceError("evidence transaction hash is malformed")
    if row.block_hash is None or _HASH.fullmatch(row.block_hash.lower()) is None:
        raise CapitalFlowEvidenceError("evidence block hash is malformed")
    if row.log_index is None or row.log_index < 0:
        raise CapitalFlowEvidenceError("evidence log index is malformed")
    if row.block_number <= 0:
        raise CapitalFlowEvidenceError("evidence block number is malformed")
    if (
        identity(
            "onchain-evm-log",
            {"chain_id": row.chain_id, "transaction_hash": row.transaction_hash.lower(), "log_index": row.log_index},
        )
        != row.evidence_id
    ):
        raise CapitalFlowEvidenceError("evidence id does not bind the transaction and log")
    if row.asset_contract is None or _ADDRESS.fullmatch(row.asset_contract.lower()) is None:
        raise CapitalFlowEvidenceError("evidence token contract is malformed")
    if row.counterparty_address is None or _ADDRESS.fullmatch(row.counterparty_address.lower()) is None:
        raise CapitalFlowEvidenceError("evidence counterparty is malformed")
    if row.direction not in {"in", "out"}:
        raise CapitalFlowEvidenceError("evidence direction is malformed")
    if row.raw_amount is None or not row.raw_amount.isdigit() or int(row.raw_amount) <= 0:
        raise CapitalFlowEvidenceError("evidence raw amount is malformed")
    if row.decimals is None or not 0 <= row.decimals <= 36:
        raise CapitalFlowEvidenceError("evidence decimals are malformed")
    if row.normalized_amount is None or not row.normalized_amount.is_finite() or row.normalized_amount <= 0:
        raise CapitalFlowEvidenceError("evidence normalized amount is malformed")
    expected_amount = Decimal(int(row.raw_amount)) / (Decimal(10) ** Decimal(row.decimals))
    if row.normalized_amount != expected_amount:
        raise CapitalFlowEvidenceError("evidence normalized amount does not match its raw amount")
    if len(row.log_topics) != 3 or row.log_topics[0].lower() != TRANSFER_TOPIC:
        raise CapitalFlowEvidenceError("evidence log topics are malformed")
    expected_topics = [
        TRANSFER_TOPIC,
        (
            "0x" + row.counterparty_address.lower()[2:].zfill(64)
            if row.direction == "in"
            else "0x" + row.observed_address.lower()[2:].zfill(64)
        ),
        (
            "0x" + row.observed_address.lower()[2:].zfill(64)
            if row.direction == "in"
            else "0x" + row.counterparty_address.lower()[2:].zfill(64)
        ),
    ]
    if [topic.lower() for topic in row.log_topics] != expected_topics:
        raise CapitalFlowEvidenceError("evidence log topics do not bind direction and counterparty")
    if not _HASH.fullmatch(row.log_data.lower()):
        raise CapitalFlowEvidenceError("evidence log data is malformed")
    if int(row.log_data, 16) != int(row.raw_amount):
        raise CapitalFlowEvidenceError("evidence log data does not match its raw amount")
    if row.receipt_log_digest is None or row.receipt_log_digest != receipt_log_digest(row):
        raise CapitalFlowEvidenceError("evidence receipt log digest does not match the signed log")
    if row.receipt_validation_policy != CORROBORATION_POLICY:
        raise CapitalFlowEvidenceError("evidence did not use the independent corroboration policy")
    if not row.log_endpoint or not row.receipt_endpoint:
        raise CapitalFlowEvidenceError("evidence is missing log or receipt endpoint provenance")
    corroborating = {endpoint_identity(item) for item in row.corroborating_endpoints}
    if (
        len(corroborating) < 2
        or endpoint_identity(row.log_endpoint) in corroborating
        or endpoint_identity(row.receipt_endpoint) in corroborating
        or len(corroborating) != len(row.corroborating_endpoints)
    ):
        raise CapitalFlowEvidenceError("evidence lacks independent RPC corroboration")
    if row.receipt_verified_at is None:
        raise CapitalFlowEvidenceError("evidence has no verification timestamp")
    latest = max(row.acquisition_timestamp, row.receipt_verified_at, as_of)
    if (
        row.block_timestamp > as_of + FUTURE_TOLERANCE
        or row.acquisition_timestamp > as_of + FUTURE_TOLERANCE
        or row.receipt_verified_at > as_of + FUTURE_TOLERANCE
        or row.receipt_verified_at < row.block_timestamp - FUTURE_TOLERANCE
        or row.acquisition_timestamp < row.block_timestamp - FUTURE_TOLERANCE
        or latest - row.block_timestamp > MAX_EVIDENCE_AGE
    ):
        raise CapitalFlowEvidenceError("evidence is stale, future-dated, or internally inconsistent in time")


def _comparison_value(row: RawOnChainObservation) -> dict[str, Any]:
    return {
        "project": row.project,
        "chain_id": row.chain_id,
        "evidence_id": row.evidence_id,
        "transaction_hash": row.transaction_hash,
        "log_index": row.log_index,
        "log_topics": list(row.log_topics),
        "log_data": row.log_data,
        "block_number": row.block_number,
        "block_hash": row.block_hash,
        "block_timestamp": _instant(row.block_timestamp),
        "observed_address": row.observed_address,
        "asset_contract": row.asset_contract,
        "raw_amount": row.raw_amount,
        "decimals": row.decimals,
        "normalized_amount": str(row.normalized_amount),
        "direction": row.direction,
        "counterparty": row.counterparty_address,
        "acquisition_timestamp": _instant(row.acquisition_timestamp),
        "log_endpoint": row.log_endpoint,
        "receipt_endpoint": row.receipt_endpoint,
        "corroborating_endpoints": sorted(row.corroborating_endpoints),
        "validation_policy": row.receipt_validation_policy,
        "receipt_log_digest": row.receipt_log_digest,
        "receipt_verified_at": _instant(row.receipt_verified_at) if row.receipt_verified_at else None,
    }


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CapitalFlowEvidenceError("timestamp has no timezone")
    return parsed.astimezone(UTC)


def _instant(value: object) -> str:
    return _datetime(value).isoformat()


def _lower(value: str | None) -> str:
    if value is None:
        raise CapitalFlowEvidenceError("evidence field is missing")
    return value.lower()


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else _as_int(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        raise CapitalFlowEvidenceError("evidence integer field is malformed")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise CapitalFlowEvidenceError("evidence integer field is malformed")


def _field(value: RawOnChainObservation | Mapping[str, Any], name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
