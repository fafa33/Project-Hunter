from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

VALUE_CAPTURE_SCHEMA_VERSION = "supply-value-capture-v3.5.0"

SupplyBasisType = Literal[
    "circulating_supply",
    "total_supply",
    "max_supply",
    "fully_diluted_supply",
    "issued_supply",
    "treasury_held_supply",
    "locked_supply",
    "vested_but_unreleased_supply",
    "burned_supply",
    "protocol_owned_supply",
]
ValueCaptureRuleType = Literal[
    "burn",
    "buyback",
    "buyback_and_burn",
    "staking_distribution",
    "fee_distribution",
    "revenue_distribution",
    "protocol_owned_liquidity_accrual",
    "redemption_entitlement",
    "collateral_entitlement",
    "governance_directed_distribution",
    "no_direct_value_capture",
    "unavailable",
    "unsupported",
]
EvidenceType = Literal[
    "official_disclosure",
    "onchain_observation",
    "governance_record",
    "audited_financial_disclosure",
    "market_fact_reference",
]
QualityState = Literal["accepted", "stale", "partial", "ambiguous", "unavailable", "unsupported"]
ConflictState = Literal["none", "open", "contested", "resolved"]

SUPPLY_BASIS_TYPES = frozenset(SupplyBasisType.__args__)  # type: ignore[attr-defined]
VALUE_CAPTURE_RULE_TYPES = frozenset(ValueCaptureRuleType.__args__)  # type: ignore[attr-defined]
EVIDENCE_TYPES = frozenset(EvidenceType.__args__)  # type: ignore[attr-defined]
QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class EconomicClaimIdentity:
    entity_id: str
    economic_claim_id: str
    asset_id: str
    representation_id: str
    token_id: str
    chain: str = ""
    contract_address: str = ""

    def __post_init__(self) -> None:
        _required_text(self, "entity_id", "economic_claim_id", "asset_id", "representation_id", "token_id")
        if bool(self.chain) != bool(self.contract_address):
            raise ValueError("chain and contract_address must either both be set or both be empty")


@dataclass(frozen=True)
class FundamentalEvidenceRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    evidence_type: EvidenceType
    source_id: str
    source_authority_tier: str
    source_reference: str
    parser_version: str
    extracted_claim: str
    amount: str | None
    unit: str | None
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    raw_content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    content_hash: str = ""
    acquisition_id: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "source_id",
            "source_authority_tier",
            "source_reference",
            "parser_version",
            "extracted_claim",
            "raw_content_hash",
            "acquisition_id",
        )
        _member("evidence_type", self.evidence_type, EVIDENCE_TYPES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _normalize_chronology(self)
        _validate_correction(self)
        if self.amount is not None:
            _decimal(self.amount)
            if self.unit is None or not self.unit.strip():
                raise ValueError("unit is required when amount is present")


@dataclass(frozen=True)
class SupplyBasisSnapshot:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    supply_basis_type: SupplyBasisType
    quantity: str
    unit: str
    denominator_meaning: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    source_id: str
    parser_version: str
    evidence_record_ids: tuple[str, ...]
    raw_payload_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    content_hash: str = ""
    acquisition_id: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "quantity",
            "unit",
            "denominator_meaning",
            "source_id",
            "parser_version",
            "raw_payload_hash",
            "acquisition_id",
        )
        _member("supply_basis_type", self.supply_basis_type, SUPPLY_BASIS_TYPES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if _decimal(self.quantity) < 0:
            raise ValueError("supply quantity must not be negative")
        if not self.evidence_record_ids:
            raise ValueError("evidence_record_ids must not be empty")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ValueCaptureRuleSnapshot:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    rule_type: ValueCaptureRuleType
    entitlement_scope: str
    beneficiary_scope: str
    source_economic_flow: str
    destination_economic_flow: str
    trigger_condition: str
    distribution_formula: str
    rate_or_proportion: str | None
    governance_or_contract_authority: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    source_id: str
    parser_version: str
    evidence_record_ids: tuple[str, ...]
    raw_payload_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    content_hash: str = ""
    acquisition_id: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "entitlement_scope",
            "beneficiary_scope",
            "source_economic_flow",
            "destination_economic_flow",
            "trigger_condition",
            "governance_or_contract_authority",
            "source_id",
            "parser_version",
            "raw_payload_hash",
            "acquisition_id",
        )
        _member("rule_type", self.rule_type, VALUE_CAPTURE_RULE_TYPES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.rate_or_proportion is not None and _decimal(self.rate_or_proportion) < 0:
            raise ValueError("rate_or_proportion must not be negative")
        if not self.evidence_record_ids:
            raise ValueError("evidence_record_ids must not be empty")
        _normalize_chronology(self)
        _validate_correction(self)


def _required_text(instance: object, *fields: str) -> None:
    for field in fields:
        value = getattr(instance, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be blank")


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"unsupported {name}: {value}")


def _normalize_chronology(instance: object) -> None:
    effective = _utc("effective_at", getattr(instance, "effective_at"))
    recorded = _utc("recorded_at", getattr(instance, "recorded_at"))
    known = _utc("known_at", getattr(instance, "known_at"))
    if effective > recorded:
        raise ValueError("effective_at must be <= recorded_at")
    if recorded > known:
        raise ValueError("recorded_at must be <= known_at")
    object.__setattr__(instance, "effective_at", effective)
    object.__setattr__(instance, "recorded_at", recorded)
    object.__setattr__(instance, "known_at", known)


def _validate_correction(instance: object) -> None:
    predecessor = getattr(instance, "supersedes_record_id")
    reason = getattr(instance, "correction_reason")
    if bool(predecessor) != bool(reason.strip()):
        raise ValueError("supersedes_record_id and correction_reason must be provided together")


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value}") from exc
