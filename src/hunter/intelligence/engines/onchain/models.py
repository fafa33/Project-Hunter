from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.onchain.exceptions import OnchainValidationError

ONCHAIN_DOMAINS = (
    "active_addresses",
    "new_addresses",
    "returning_addresses",
    "address_retention",
    "transaction_count",
    "transaction_volume",
    "adjusted_transaction_volume",
    "transfer_value",
    "net_capital_flow",
    "exchange_inflows",
    "exchange_outflows",
    "bridge_inflows",
    "bridge_outflows",
    "cross_chain_migration",
    "staking_inflows",
    "staking_outflows",
    "unstaking_activity",
    "holder_growth",
    "holder_retention",
    "long_term_holder_growth",
    "holder_concentration",
    "supply_distribution",
    "top_holder_concentration",
    "contract_activity",
    "contract_diversity",
    "contract_deployment",
    "interaction_breadth",
    "application_concentration",
    "token_velocity",
    "dormancy",
    "realized_activity",
    "churn",
    "circular_flow_risk",
    "wash_activity_risk",
    "sybil_activity_risk",
    "bot_activity_risk",
    "bridge_pass_through_risk",
    "treasury_activity",
    "burn_mint_activity",
    "network_participation",
    "validator_distribution",
    "governance_participation",
    "onchain_decentralization",
    "onchain_momentum",
    "onchain_deterioration",
)

ANOMALY_LEVELS = ("detected", "suspected", "insufficient_evidence")


@dataclass(frozen=True)
class OnchainRecord:
    id: str
    project: str
    asset: str
    chain: str
    source: str
    timestamp: datetime
    reliability: float
    reference: str
    block_height: int | None = None
    transaction_hash: str | None = None
    contract_address: str | None = None
    token_denomination: str | None = None
    attribution_quality: float | None = None
    entity_label_quality: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _base_validate(self.id, self.project, self.asset, self.chain, self.source, self.reference, self.timestamp)
        object.__setattr__(self, "asset", _normalize(self.asset))
        object.__setattr__(self, "chain", _normalize(self.chain))
        object.__setattr__(self, "source", _normalize(self.source))
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "attribution_quality", _clamp_optional(self.attribution_quality))
        object.__setattr__(self, "entity_label_quality", _clamp_optional(self.entity_label_quality))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))
        if self.block_height is not None and self.block_height < 0:
            raise OnchainValidationError("block height must be nonnegative")


@dataclass(frozen=True)
class AddressSnapshot(OnchainRecord):
    active_addresses: int | None = None
    new_addresses: int | None = None
    returning_addresses: int | None = None
    retained_addresses: int | None = None
    sybil_ratio: float | None = None
    bot_ratio: float | None = None
    wallet_creation_cluster_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.active_addresses, "active addresses")
        _nonnegative_optional_int(self.new_addresses, "new addresses")
        _nonnegative_optional_int(self.returning_addresses, "returning addresses")
        _nonnegative_optional_int(self.retained_addresses, "retained addresses")


@dataclass(frozen=True)
class TransactionSnapshot(OnchainRecord):
    transaction_count: int | None = None
    adjusted_transaction_count: int | None = None
    gross_volume: float | None = None
    adjusted_volume: float | None = None
    low_value_ratio: float | None = None
    repeated_pattern_ratio: float | None = None
    gas_anomaly_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.transaction_count, "transaction count")
        _nonnegative_optional_int(self.adjusted_transaction_count, "adjusted transaction count")
        _nonnegative_optional_float(self.gross_volume, "gross volume")
        _nonnegative_optional_float(self.adjusted_volume, "adjusted volume")


@dataclass(frozen=True)
class TransferSnapshot(OnchainRecord):
    transfer_value: float | None = None
    adjusted_transfer_value: float | None = None
    internal_transfer_ratio: float | None = None
    circular_transfer_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.transfer_value, "transfer value")
        _nonnegative_optional_float(self.adjusted_transfer_value, "adjusted transfer value")


@dataclass(frozen=True)
class CapitalFlowSnapshot(OnchainRecord):
    inflow: float | None = None
    outflow: float | None = None
    retained_capital: float | None = None
    repeated_flow_ratio: float | None = None
    circular_flow_ratio: float | None = None
    internal_flow_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.inflow, "capital inflow")
        _nonnegative_optional_float(self.outflow, "capital outflow")
        _nonnegative_optional_float(self.retained_capital, "retained capital")


@dataclass(frozen=True)
class ExchangeFlowSnapshot(OnchainRecord):
    inflow: float | None = None
    outflow: float | None = None
    exchange_label_quality: float | None = None
    redistribution_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.inflow, "exchange inflow")
        _nonnegative_optional_float(self.outflow, "exchange outflow")
        object.__setattr__(self, "exchange_label_quality", _clamp_optional(self.exchange_label_quality))


@dataclass(frozen=True)
class BridgeFlowSnapshot(OnchainRecord):
    inflow: float | None = None
    outflow: float | None = None
    bridge_label_quality: float | None = None
    pass_through_ratio: float | None = None
    source_chain: str | None = None
    target_chain: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.inflow, "bridge inflow")
        _nonnegative_optional_float(self.outflow, "bridge outflow")
        object.__setattr__(self, "bridge_label_quality", _clamp_optional(self.bridge_label_quality))
        if self.source_chain is not None:
            object.__setattr__(self, "source_chain", _normalize(self.source_chain))
        if self.target_chain is not None:
            object.__setattr__(self, "target_chain", _normalize(self.target_chain))


@dataclass(frozen=True)
class StakingFlowSnapshot(OnchainRecord):
    staked_inflow: float | None = None
    staked_outflow: float | None = None
    unstaked: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.staked_inflow, "staking inflow")
        _nonnegative_optional_float(self.staked_outflow, "staking outflow")
        _nonnegative_optional_float(self.unstaked, "unstaked")


@dataclass(frozen=True)
class HolderSnapshot(OnchainRecord):
    holder_count: int | None = None
    retained_holders: int | None = None
    long_term_holders: int | None = None
    top_holder_share: float | None = None
    treasury_holder_share: float | None = None
    dormant_supply_ratio: float | None = None
    active_supply_ratio: float | None = None
    accumulation_wallets: int | None = None
    distribution_wallets: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.holder_count, "holder count")
        _nonnegative_optional_int(self.retained_holders, "retained holders")
        _nonnegative_optional_int(self.long_term_holders, "long-term holders")
        _nonnegative_optional_int(self.accumulation_wallets, "accumulation wallets")
        _nonnegative_optional_int(self.distribution_wallets, "distribution wallets")


@dataclass(frozen=True)
class SupplyDistributionSnapshot(OnchainRecord):
    circulating_supply: float | None = None
    top_10_share: float | None = None
    top_100_share: float | None = None
    gini: float | None = None
    distribution_quality: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.circulating_supply, "circulating supply")


@dataclass(frozen=True)
class ContractActivitySnapshot(OnchainRecord):
    active_contracts: int | None = None
    interactions: int | None = None
    unique_callers: int | None = None
    protocol_owned_ratio: float | None = None
    spam_contract_ratio: float | None = None
    generated_contract_ratio: float | None = None
    classification_quality: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.active_contracts, "active contracts")
        _nonnegative_optional_int(self.interactions, "interactions")
        _nonnegative_optional_int(self.unique_callers, "unique callers")
        object.__setattr__(self, "classification_quality", _clamp_optional(self.classification_quality))


@dataclass(frozen=True)
class ContractDeploymentSnapshot(OnchainRecord):
    deployments: int | None = None
    upgrades: int | None = None
    proxy_changes: int | None = None
    abandoned_contract_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.deployments, "deployments")
        _nonnegative_optional_int(self.upgrades, "upgrades")
        _nonnegative_optional_int(self.proxy_changes, "proxy changes")


@dataclass(frozen=True)
class ApplicationActivitySnapshot(OnchainRecord):
    application_id: str = ""
    active_users: int | None = None
    transaction_share: float | None = None
    volume_share: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.application_id, "application id")
        _nonnegative_optional_int(self.active_users, "application active users")


@dataclass(frozen=True)
class TreasuryActivitySnapshot(OnchainRecord):
    inflow: float | None = None
    outflow: float | None = None
    treasury_label_quality: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.inflow, "treasury inflow")
        _nonnegative_optional_float(self.outflow, "treasury outflow")
        object.__setattr__(self, "treasury_label_quality", _clamp_optional(self.treasury_label_quality))


@dataclass(frozen=True)
class MintBurnSnapshot(OnchainRecord):
    minted: float | None = None
    burned: float | None = None
    anomaly_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.minted, "minted")
        _nonnegative_optional_float(self.burned, "burned")


@dataclass(frozen=True)
class ValidatorDistributionSnapshot(OnchainRecord):
    validator_count: int | None = None
    top_validator_share: float | None = None
    staker_count: int | None = None
    staker_concentration: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.validator_count, "validator count")
        _nonnegative_optional_int(self.staker_count, "staker count")


@dataclass(frozen=True)
class GovernanceActivitySnapshot(OnchainRecord):
    proposals: int | None = None
    voters: int | None = None
    participation_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.proposals, "proposals")
        _nonnegative_optional_int(self.voters, "voters")


@dataclass(frozen=True)
class OnchainEvent(OnchainRecord):
    event_type: str = "event"
    severity: float = 0.0
    description: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.event_type, "event type")
        _require_text(self.description, "description")
        object.__setattr__(self, "event_type", _normalize(self.event_type))
        object.__setattr__(self, "severity", _clamp(self.severity))


@dataclass(frozen=True)
class AnomalyAssessment:
    level: str
    circular_flow_risk: float
    wash_activity_risk: float
    sybil_risk: float
    bot_activity_risk: float
    bridge_pass_through_risk: float
    severity: float
    explanation: str

    def __post_init__(self) -> None:
        if self.level not in ANOMALY_LEVELS:
            raise OnchainValidationError("invalid anomaly level")
        object.__setattr__(self, "circular_flow_risk", _clamp(self.circular_flow_risk))
        object.__setattr__(self, "wash_activity_risk", _clamp(self.wash_activity_risk))
        object.__setattr__(self, "sybil_risk", _clamp(self.sybil_risk))
        object.__setattr__(self, "bot_activity_risk", _clamp(self.bot_activity_risk))
        object.__setattr__(self, "bridge_pass_through_risk", _clamp(self.bridge_pass_through_risk))
        object.__setattr__(self, "severity", _clamp(self.severity))


OnchainInput = (
    AddressSnapshot
    | TransactionSnapshot
    | TransferSnapshot
    | CapitalFlowSnapshot
    | ExchangeFlowSnapshot
    | BridgeFlowSnapshot
    | StakingFlowSnapshot
    | HolderSnapshot
    | SupplyDistributionSnapshot
    | ContractActivitySnapshot
    | ContractDeploymentSnapshot
    | ApplicationActivitySnapshot
    | TreasuryActivitySnapshot
    | MintBurnSnapshot
    | ValidatorDistributionSnapshot
    | GovernanceActivitySnapshot
    | OnchainEvent
)


@dataclass(frozen=True)
class OnchainDataset:
    project: str
    records: tuple[OnchainInput, ...] = ()
    addresses: tuple[AddressSnapshot, ...] = ()
    transactions: tuple[TransactionSnapshot, ...] = ()
    transfers: tuple[TransferSnapshot, ...] = ()
    capital_flows: tuple[CapitalFlowSnapshot, ...] = ()
    exchange_flows: tuple[ExchangeFlowSnapshot, ...] = ()
    bridge_flows: tuple[BridgeFlowSnapshot, ...] = ()
    staking_flows: tuple[StakingFlowSnapshot, ...] = ()
    holders: tuple[HolderSnapshot, ...] = ()
    supply: tuple[SupplyDistributionSnapshot, ...] = ()
    contract_activity: tuple[ContractActivitySnapshot, ...] = ()
    contract_deployments: tuple[ContractDeploymentSnapshot, ...] = ()
    applications: tuple[ApplicationActivitySnapshot, ...] = ()
    treasury: tuple[TreasuryActivitySnapshot, ...] = ()
    mint_burn: tuple[MintBurnSnapshot, ...] = ()
    validators: tuple[ValidatorDistributionSnapshot, ...] = ()
    governance: tuple[GovernanceActivitySnapshot, ...] = ()
    events: tuple[OnchainEvent, ...] = ()
    duplicates: tuple[str, ...] = ()
    overlapping_windows: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    cross_engine_alignment: float = 0.0


@dataclass(frozen=True)
class OnchainIndicator:
    name: str
    value: float
    direction: str
    confidence: float
    description: str
    missing_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _clamp(self.value))
        object.__setattr__(self, "confidence", _clamp(self.confidence))


@dataclass(frozen=True)
class OnchainAnalysis:
    indicators: tuple[OnchainIndicator, ...]
    anomaly: AnomalyAssessment
    health: str
    capital_flow_trend: str
    address_trend: str
    holder_trend: str
    concentration: str
    decentralization: str
    contract_activity: str
    migration: str
    strengths: tuple[str, ...]
    risks: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    metadata: dict[str, str] = field(default_factory=dict)


def _base_validate(*values: object) -> None:
    *texts, timestamp = values
    for value in texts:
        _require_text(str(value), "required field")
    if not isinstance(timestamp, datetime):
        raise OnchainValidationError("timestamp must be a datetime")


def _require_text(value: str, name: str) -> None:
    if not str(value).strip():
        raise OnchainValidationError(f"{name} is required")


def _normalize(value: str) -> str:
    return "_".join(str(value).strip().lower().replace("-", "_").split())


def _nonnegative_optional_int(value: int | None, name: str) -> None:
    if value is not None and value < 0:
        raise OnchainValidationError(f"{name} must be nonnegative")


def _nonnegative_optional_float(value: float | None, name: str) -> None:
    if value is not None and value < 0:
        raise OnchainValidationError(f"{name} must be nonnegative")


def _clamp_optional(value: float | None) -> float | None:
    return None if value is None else _clamp(value)


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
