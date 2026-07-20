from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

from hunter.value_capture.models import (
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository, ValueCaptureWritePlan


class SupplyAndValueCaptureAuthorityError(ValueError):
    pass


class SupplyAndValueCaptureService:
    def __init__(
        self,
        *,
        registry: ValueCaptureSourceRegistry,
        repository: SupplyAndValueCaptureRepository,
    ) -> None:
        self.registry = registry
        self.repository = repository

    def persist_evidence(
        self,
        record: FundamentalEvidenceRecord,
        *,
        endpoint: str,
        registry_fingerprint: str,
    ) -> FundamentalEvidenceRecord:
        source = self.registry.require(record.source_id)
        source.authorize(
            endpoint=endpoint,
            parser_version=record.parser_version,
            capability=f"evidence:{record.evidence_type}",
        )
        if registry_fingerprint != source.fingerprint:
            raise SupplyAndValueCaptureAuthorityError("registry fingerprint mismatch")
        normalized = self._normalize(record)
        self.repository.apply(
            ValueCaptureWritePlan(evidence=(normalized,), authority=self.repository._authority)
        )
        return normalized

    def persist_supply(
        self,
        record: SupplyBasisSnapshot,
        *,
        endpoint: str,
        registry_fingerprint: str,
    ) -> SupplyBasisSnapshot:
        source = self.registry.require(record.source_id)
        source.authorize(
            endpoint=endpoint,
            parser_version=record.parser_version,
            capability=f"supply:{record.supply_basis_type}",
        )
        if registry_fingerprint != source.fingerprint:
            raise SupplyAndValueCaptureAuthorityError("registry fingerprint mismatch")
        self._require_evidence(record.evidence_record_ids, record.identity)
        normalized = self._normalize(record)
        self.repository.apply(
            ValueCaptureWritePlan(supply=(normalized,), authority=self.repository._authority)
        )
        return normalized

    def persist_rule(
        self,
        record: ValueCaptureRuleSnapshot,
        *,
        endpoint: str,
        registry_fingerprint: str,
    ) -> ValueCaptureRuleSnapshot:
        source = self.registry.require(record.source_id)
        source.authorize(
            endpoint=endpoint,
            parser_version=record.parser_version,
            capability=f"rule:{record.rule_type}",
        )
        if registry_fingerprint != source.fingerprint:
            raise SupplyAndValueCaptureAuthorityError("registry fingerprint mismatch")
        self._require_evidence(record.evidence_record_ids, record.identity)
        normalized = self._normalize(record)
        self.repository.apply(
            ValueCaptureWritePlan(rules=(normalized,), authority=self.repository._authority)
        )
        return normalized

    def strict_known_supply(
        self,
        *,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        supply_basis_type: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> SupplyBasisSnapshot | None:
        return self.repository.strict_known_supply(
            entity_id=entity_id,
            economic_claim_id=economic_claim_id,
            representation_id=representation_id,
            supply_basis_type=supply_basis_type,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_rule(
        self,
        *,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        rule_type: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> ValueCaptureRuleSnapshot | None:
        return self.repository.strict_known_rule(
            entity_id=entity_id,
            economic_claim_id=economic_claim_id,
            representation_id=representation_id,
            rule_type=rule_type,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def _require_evidence(self, evidence_ids: tuple[str, ...], identity: object) -> None:
        for evidence_id in evidence_ids:
            evidence = self.repository.evidence(evidence_id)
            if evidence is None:
                raise SupplyAndValueCaptureAuthorityError(
                    f"authoritative evidence does not exist: {evidence_id}"
                )
            if evidence.identity != identity:
                raise SupplyAndValueCaptureAuthorityError(
                    "evidence identity does not match supply/value-capture identity"
                )
            if evidence.quality_state != "accepted" or evidence.conflict_state not in {
                "none",
                "resolved",
            }:
                raise SupplyAndValueCaptureAuthorityError(
                    "non-authoritative evidence cannot support an authoritative snapshot"
                )

    def _normalize(self, record: Any) -> Any:
        logical_id = _logical_id(record)
        content_hash = _content_hash(record, logical_id=logical_id)
        record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
        return replace(
            record,
            logical_id=logical_id,
            content_hash=content_hash,
            record_id=record_id,
        )


def _logical_id(record: Any) -> str:
    identity = record.identity
    category = ""
    if isinstance(record, FundamentalEvidenceRecord):
        category = f"evidence:{record.evidence_type}:{record.source_reference}"
    elif isinstance(record, SupplyBasisSnapshot):
        category = f"supply:{record.supply_basis_type}"
    elif isinstance(record, ValueCaptureRuleSnapshot):
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
    payload = _json_safe(payload)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
