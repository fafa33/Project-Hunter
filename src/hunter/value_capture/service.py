from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any, cast

from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.value_capture.models import (
    VALUE_CAPTURE_SCHEMA_VERSION,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.providers import (
    AcquisitionReceipt,
    RegisteredValueCaptureProvider,
    ValueCaptureAcquisitionResult,
    ValueCaptureVerificationKeyRegistry,
)
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import (
    SupplyAndValueCaptureRepository,
    ValueCaptureIntegrityError,
    receipt_snapshot,
    record_snapshot,
)

Record = FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot
PersistCapability = Callable[[RegisteredValueCaptureProvider, ValueCaptureAcquisitionResult, str], Record]


class SupplyAndValueCaptureAuthorityError(ValueError):
    pass


class SupplyAndValueCaptureService:
    def __init__(
        self,
        *,
        registry: ValueCaptureSourceRegistry,
        repository: SupplyAndValueCaptureRepository,
        verification_keys: ValueCaptureVerificationKeyRegistry,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.__verification_keys = verification_keys

        def persist_capability(
            provider: RegisteredValueCaptureProvider,
            result: ValueCaptureAcquisitionResult,
            expected_kind: str,
        ) -> Record:
            self._authorize_result(provider, result, expected_kind=expected_kind)
            record = self._record_from_result(result, expected_kind=expected_kind)
            self._authorize_correction(record)
            if isinstance(record, (SupplyBasisSnapshot, ValueCaptureRuleSnapshot)):
                self._require_evidence(record)
            if not self.__verification_keys.verify_receipt(result.receipt):
                raise ValueCaptureIntegrityError("receipt hash or signature is not verification-key authorized")
            validate_receipt_binding(result.receipt, record)

            engine = create_sqlite_engine(self.repository.path)
            session = SessionFactory(engine).create()
            try:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                snapshots = RepositoryFactory(session).snapshots()
                insert_receipt(snapshots, result.receipt)
                insert_record(snapshots, table_for(record), record)
                session.commit()
            except PersistenceIdentityConflictError as exc:
                session.rollback()
                raise ValueCaptureIntegrityError(str(exc)) from exc
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
                engine.dispose()
            return record

        def validate_receipt_binding(receipt: AcquisitionReceipt, record: Record) -> None:
            raw_hash = (
                record.raw_content_hash if isinstance(record, FundamentalEvidenceRecord) else record.raw_payload_hash
            )
            if receipt.acquisition_id != record.acquisition_id:
                raise ValueCaptureIntegrityError("record acquisition_id does not match receipt")
            if receipt.raw_payload_hash != raw_hash:
                raise ValueCaptureIntegrityError("record payload hash does not match receipt")
            if receipt.source_id != record.source_id or receipt.parser_version != record.parser_version:
                raise ValueCaptureIntegrityError("record source/parser does not match receipt")
            if receipt.identity != record.identity:
                raise ValueCaptureIntegrityError("record identity does not match receipt")
            if receipt.acquired_at != record.recorded_at or receipt.acquired_at != record.known_at:
                raise ValueCaptureIntegrityError("record chronology does not match receipt")

        def insert_receipt(snapshots: Any, receipt: AcquisitionReceipt) -> None:
            snapshots.save(receipt_snapshot(receipt))

        def insert_record(snapshots: Any, table: str, record: Record) -> None:
            snapshot = record_snapshot(record)
            predecessor = record.supersedes_record_id
            if predecessor is not None:
                prior = snapshots.load(predecessor)
                if prior is None or prior.snapshot_type != snapshot.snapshot_type:
                    raise ValueCaptureIntegrityError("superseded record does not exist")
                if str(prior.payload.get("logical_id")) != record.logical_id:
                    raise ValueCaptureIntegrityError("correction must preserve logical_id")
                if datetime.fromisoformat(str(prior.payload["recorded_at"])) >= record.recorded_at:
                    raise ValueCaptureIntegrityError("correction recorded_at must follow predecessor")
                if datetime.fromisoformat(str(prior.payload["known_at"])) >= record.known_at:
                    raise ValueCaptureIntegrityError("correction known_at must follow predecessor")
                successors = (
                    item
                    for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                    if item.snapshot_type == snapshot.snapshot_type
                    if item.payload.get("supersedes_record_id") == predecessor
                )
                if next(successors, None) is not None:
                    raise ValueCaptureIntegrityError("branching correction lineage is prohibited")
            snapshots.save(snapshot)

        def table_for(record: Record) -> str:
            if isinstance(record, FundamentalEvidenceRecord):
                return "fundamental_evidence_records"
            if isinstance(record, SupplyBasisSnapshot):
                return "supply_basis_snapshots"
            return "value_capture_rule_snapshots"

        self.__persist_capability: PersistCapability = persist_capability

    def ingest_evidence(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> FundamentalEvidenceRecord:
        return cast(FundamentalEvidenceRecord, self.__persist_capability(provider, result, "evidence"))

    def ingest_supply(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> SupplyBasisSnapshot:
        return cast(SupplyBasisSnapshot, self.__persist_capability(provider, result, "supply"))

    def ingest_rule(
        self,
        provider: RegisteredValueCaptureProvider,
        result: ValueCaptureAcquisitionResult,
    ) -> ValueCaptureRuleSnapshot:
        return cast(ValueCaptureRuleSnapshot, self.__persist_capability(provider, result, "rule"))

    def evidence_history(self, logical_id: str) -> tuple[FundamentalEvidenceRecord, ...]:
        return self.repository.evidence_history(logical_id)

    def supply_history(self, logical_id: str) -> tuple[SupplyBasisSnapshot, ...]:
        return self.repository.supply_history(logical_id)

    def rule_history(self, logical_id: str) -> tuple[ValueCaptureRuleSnapshot, ...]:
        return self.repository.rule_history(logical_id)

    def strict_known_supply(self, **kwargs: Any) -> SupplyBasisSnapshot | None:
        return self.repository.strict_known_supply(**kwargs)

    def strict_known_rule(self, **kwargs: Any) -> ValueCaptureRuleSnapshot | None:
        return self.repository.strict_known_rule(**kwargs)

    def _record_from_result(self, result: ValueCaptureAcquisitionResult, *, expected_kind: str) -> Record:
        payload = result.payload
        if expected_kind == "evidence":
            record: Record = FundamentalEvidenceRecord(
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
                accounting_period_start=_datetime(payload["accounting_period_start"]),
                accounting_period_end=_datetime(payload["accounting_period_end"]),
                attribution_rule_id=_required_payload_text(payload, "attribution_rule_id"),
                source_methodology=_required_payload_text(payload, "source_methodology"),
                source_record_id=_required_payload_text(payload, "source_record_id"),
                source_record_version=_required_payload_text(payload, "source_record_version"),
                entity_link_confidence=_required_payload_text(payload, "entity_link_confidence"),
                evidence_confidence=_required_payload_text(payload, "evidence_confidence"),
                uncertainty=_required_payload_text(payload, "uncertainty"),
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
        elif expected_kind == "supply":
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
                supply_policy_id=_required_payload_text(payload, "supply_policy_id"),
                supply_policy_version=_required_payload_text(payload, "supply_policy_version"),
                quantity_components=_quantity_components(payload),
                observed_market_fact_ids=_required_payload_tuple(payload, "observed_market_fact_ids"),
                observed_market_fact_versions=_required_payload_tuple(payload, "observed_market_fact_versions"),
                source_record_id=_required_payload_text(payload, "source_record_id"),
                source_record_version=_required_payload_text(payload, "source_record_version"),
                confidence=_required_payload_text(payload, "confidence"),
                uncertainty=_required_payload_text(payload, "uncertainty"),
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
        elif expected_kind == "rule":
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
                mechanism_policy_id=_required_payload_text(payload, "mechanism_policy_id"),
                mechanism_policy_version=_required_payload_text(payload, "mechanism_policy_version"),
                dilution_treatment=_required_payload_text(payload, "dilution_treatment"),
                claim_seniority=_required_payload_text(payload, "claim_seniority"),
                applicability_start=_datetime(payload["applicability_start"]),
                applicability_end=_datetime(payload["applicability_end"]),
                limitations=_required_payload_tuple(payload, "limitations"),
                evidence_record_versions=_required_payload_tuple(payload, "evidence_record_versions"),
                source_record_id=_required_payload_text(payload, "source_record_id"),
                source_record_version=_required_payload_text(payload, "source_record_version"),
                confidence=_required_payload_text(payload, "confidence"),
                uncertainty=_required_payload_text(payload, "uncertainty"),
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
        else:
            raise SupplyAndValueCaptureAuthorityError("unsupported canonical record kind")
        return self._normalize(record)

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
        evidence_versions = (
            record.evidence_record_versions
            if isinstance(record, ValueCaptureRuleSnapshot)
            else (None,) * len(record.evidence_record_ids)
        )
        for evidence_id, expected_version in zip(record.evidence_record_ids, evidence_versions, strict=True):
            evidence = self.repository.evidence(evidence_id)
            if evidence is None:
                raise SupplyAndValueCaptureAuthorityError(f"authoritative evidence does not exist: {evidence_id}")
            if evidence.identity != record.identity:
                raise SupplyAndValueCaptureAuthorityError("evidence identity does not match snapshot identity")
            if expected_version is not None and evidence.semantic_version != expected_version:
                raise SupplyAndValueCaptureAuthorityError("evidence version does not match snapshot reference")
            if evidence.source_id != record.source_id or evidence.parser_version != record.parser_version:
                raise SupplyAndValueCaptureAuthorityError("evidence source/parser provenance mismatch")
            if evidence.effective_at > record.effective_at:
                raise SupplyAndValueCaptureAuthorityError("future-effective evidence cannot support snapshot")
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


def _required_payload_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SupplyAndValueCaptureAuthorityError(f"{name} must be a nonblank string")
    return value


def _required_payload_tuple(payload: dict[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name)
    if not isinstance(value, (list, tuple)) or not value:
        raise SupplyAndValueCaptureAuthorityError(f"{name} must be a nonempty sequence")
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise SupplyAndValueCaptureAuthorityError(f"{name} must contain nonblank strings")
    return result


def _quantity_components(
    payload: dict[str, Any],
) -> tuple[tuple[Any, str], ...]:
    value = payload.get("quantity_components")
    if not isinstance(value, (list, tuple)) or not value:
        raise SupplyAndValueCaptureAuthorityError("quantity_components must be a nonempty sequence")
    result: list[tuple[Any, str]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise SupplyAndValueCaptureAuthorityError("quantity_components entries must be type/value pairs")
        component_type, component_value = item
        if not isinstance(component_type, str) or not component_type.strip():
            raise SupplyAndValueCaptureAuthorityError("quantity component type must be nonblank")
        if not isinstance(component_value, str) or not component_value.strip():
            raise SupplyAndValueCaptureAuthorityError("quantity component value must be nonblank")
        result.append((component_type, component_value))
    return tuple(result)
