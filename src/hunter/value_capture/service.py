from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

from hunter.value_capture.models import (
    VALUE_CAPTURE_SCHEMA_VERSION,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureAcquisitionResult
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, open_authoritative_value_capture_store


class SupplyAndValueCaptureAuthorityError(ValueError):
    pass


class SupplyAndValueCaptureService:
    def __init__(self, *, registry: ValueCaptureSourceRegistry, repository: SupplyAndValueCaptureRepository) -> None:
        self.registry = registry
        self.repository, self.__commit = open_authoritative_value_capture_store(repository.path)

    def ingest_evidence(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> FundamentalEvidenceRecord:
        self._authorize_result(provider, result, expected_kind="evidence")
        payload = result.payload
        record = FundamentalEvidenceRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
            semantic_version=str(payload.get("semantic_version", "1.0.0")),
            identity=result.identity,
            evidence_type=str(payload["evidence_type"]),  # type: ignore[arg-type]
            source_id=result.source_id,
            source_authority_tier=result.source_authority_tier,
            source_reference=str(payload["source_reference"]),
            parser_version=result.parser_version,
            extracted_claim=str(payload["extracted_claim"]),
            amount=_optional_text(payload.get("amount")),
            unit=_optional_text(payload.get("unit")),
            effective_at=_datetime(payload["effective_at"]),
            recorded_at=result.acquired_at,
            known_at=result.acquired_at,
            raw_content_hash=result.raw_payload_hash,
            quality_state=str(payload.get("quality_state", "accepted")),  # type: ignore[arg-type]
            conflict_state=str(payload.get("conflict_state", "none")),  # type: ignore[arg-type]
            supersedes_record_id=_optional_text(payload.get("supersedes_record_id")),
            correction_reason=str(payload.get("correction_reason", "")),
            acquisition_id=result.acquisition_id,
        )
        self._authorize_correction(record)
        normalized = self._normalize(record)
        self.__commit(result.receipt, normalized)
        return normalized

    def ingest_supply(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> SupplyBasisSnapshot:
        self._authorize_result(provider, result, expected_kind="supply")
        payload = result.payload
        record = SupplyBasisSnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
            semantic_version=str(payload.get("semantic_version", "1.0.0")),
            identity=result.identity,
            supply_basis_type=str(payload["supply_basis_type"]),  # type: ignore[arg-type]
            quantity=str(payload["quantity"]),
            unit=str(payload["unit"]),
            denominator_meaning=str(payload["denominator_meaning"]),
            effective_at=_datetime(payload["effective_at"]),
            recorded_at=result.acquired_at,
            known_at=result.acquired_at,
            source_id=result.source_id,
            parser_version=result.parser_version,
            evidence_record_ids=tuple(str(value) for value in payload["evidence_record_ids"]),
            raw_payload_hash=result.raw_payload_hash,
            quality_state=str(payload.get("quality_state", "accepted")),  # type: ignore[arg-type]
            conflict_state=str(payload.get("conflict_state", "none")),  # type: ignore[arg-type]
            supersedes_record_id=_optional_text(payload.get("supersedes_record_id")),
            correction_reason=str(payload.get("correction_reason", "")),
            acquisition_id=result.acquisition_id,
        )
        self._require_evidence(record)
        self._authorize_correction(record)
        normalized = self._normalize(record)
        self.__commit(result.receipt, normalized)
        return normalized

    def ingest_rule(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> ValueCaptureRuleSnapshot:
        self._authorize_result(provider, result, expected_kind="rule")
        payload = result.payload
        record = ValueCaptureRuleSnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUE_CAPTURE_SCHEMA_VERSION,
            semantic_version=str(payload.get("semantic_version", "1.0.0")),
            identity=result.identity,
            rule_type=str(payload["rule_type"]),  # type: ignore[arg-type]
            entitlement_scope=str(payload["entitlement_scope"]),
            beneficiary_scope=str(payload["beneficiary_scope"]),
            source_economic_flow=str(payload["source_economic_flow"]),
            destination_economic_flow=str(payload["destination_economic_flow"]),
            trigger_condition=str(payload["trigger_condition"]),
            distribution_formula=str(payload.get("distribution_formula", "")),
            rate_or_proportion=_optional_text(payload.get("rate_or_proportion")),
            governance_or_contract_authority=str(payload["governance_or_contract_authority"]),
            effective_at=_datetime(payload["effective_at"]),
            recorded_at=result.acquired_at,
            known_at=result.acquired_at,
            source_id=result.source_id,
            parser_version=result.parser_version,
            evidence_record_ids=tuple(str(value) for value in payload["evidence_record_ids"]),
            raw_payload_hash=result.raw_payload_hash,
            quality_state=str(payload.get("quality_state", "accepted")),  # type: ignore[arg-type]
            conflict_state=str(payload.get("conflict_state", "none")),  # type: ignore[arg-type]
            supersedes_record_id=_optional_text(payload.get("supersedes_record_id")),
            correction_reason=str(payload.get("correction_reason", "")),
            acquisition_id=result.acquisition_id,
        )
        self._require_evidence(record)
        self._authorize_correction(record)
        normalized = self._normalize(record)
        self.__commit(result.receipt, normalized)
        return normalized

    def strict_known_supply(self, **kwargs: Any) -> SupplyBasisSnapshot | None:
        return self.repository.strict_known_supply(**kwargs)

    def strict_known_rule(self, **kwargs: Any) -> ValueCaptureRuleSnapshot | None:
        return self.repository.strict_known_rule(**kwargs)

    def _authorize_result(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
        *,
        expected_kind: str,
    ) -> None:
        if not provider.verify(result):
            raise SupplyAndValueCaptureAuthorityError("provider signature or acquisition receipt is invalid")
        if provider.source_id != result.source_id:
            raise SupplyAndValueCaptureAuthorityError("provider source does not match acquisition receipt")
        if result.kind != expected_kind:
            raise SupplyAndValueCaptureAuthorityError("provider acquisition kind mismatch")
        source = self.registry.require(result.source_id)
        source.authorize(
            endpoint=result.endpoint,
            parser_version=result.parser_version,
            capability=result.capability,
        )
        if result.registry_fingerprint != source.fingerprint:
            raise SupplyAndValueCaptureAuthorityError("registry fingerprint mismatch")

    def _require_evidence(self, record: SupplyBasisSnapshot | ValueCaptureRuleSnapshot) -> None:
        for evidence_id in record.evidence_record_ids:
            evidence = self.repository.evidence(evidence_id)
            if evidence is None:
                raise SupplyAndValueCaptureAuthorityError(f"authoritative evidence does not exist: {evidence_id}")
            if evidence.identity != record.identity:
                raise SupplyAndValueCaptureAuthorityError("evidence identity does not match snapshot identity")
            if evidence.source_id != record.source_id or evidence.parser_version != record.parser_version:
                raise SupplyAndValueCaptureAuthorityError("evidence source/parser provenance mismatch")
            if evidence.recorded_at > record.recorded_at or evidence.known_at > record.known_at:
                raise SupplyAndValueCaptureAuthorityError("future-known evidence cannot support snapshot")
            if evidence.quality_state != "accepted" or evidence.conflict_state not in {"none", "resolved"}:
                raise SupplyAndValueCaptureAuthorityError("non-authoritative evidence cannot support snapshot")

    def _authorize_correction(
        self,
        record: FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot,
    ) -> None:
        predecessor_id = record.supersedes_record_id
        if predecessor_id is None:
            return
        predecessor: FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot | None
        if isinstance(record, FundamentalEvidenceRecord):
            predecessor = self.repository.evidence(predecessor_id)
        elif isinstance(record, SupplyBasisSnapshot):
            predecessor = self.repository.supply(predecessor_id)
        else:
            predecessor = self.repository.rule(predecessor_id)
        if predecessor is None:
            raise SupplyAndValueCaptureAuthorityError("correction predecessor does not exist")
        self.registry.authorize_correction_transition(
            predecessor_source_id=predecessor.source_id,
            successor_source_id=record.source_id,
        )

    def _normalize(self, record: Any) -> Any:
        logical_id = _logical_id(record)
        content_hash = _content_hash(record, logical_id=logical_id)
        record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
        return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _logical_id(record: Any) -> str:
    identity = record.identity
    if isinstance(record, FundamentalEvidenceRecord):
        category = f"evidence:{record.evidence_type}:{record.source_reference}"
    elif isinstance(record, SupplyBasisSnapshot):
        category = f"supply:{record.supply_basis_type}"
    else:
        category = f"rule:{record.rule_type}"
    raw = "|".join(
        (
            identity.entity_id,
            identity.economic_claim_id,
            identity.asset_id,
            identity.representation_id,
            identity.token_id,
            identity.chain,
            identity.contract_address,
            category,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _content_hash(record: Any, *, logical_id: str) -> str:
    payload = asdict(record)
    payload["record_id"] = ""
    payload["logical_id"] = logical_id
    payload["content_hash"] = ""
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
