from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

AddressType = Literal[
    "protocol_contract",
    "treasury",
    "staking",
    "vault",
    "lending_market",
    "liquidity_pool",
    "bridge",
    "fee_collector",
    "revenue_collector",
    "burn",
    "other_documented",
]

FlowCategory = Literal[
    "external_inflow",
    "external_outflow",
    "internal_transfer",
    "liquidity_added",
    "liquidity_removed",
    "staking_deposit",
    "staking_withdrawal",
    "vault_deposit",
    "vault_withdrawal",
    "bridge_inflow",
    "bridge_outflow",
    "fee_revenue",
    "protocol_revenue",
    "treasury_inflow",
    "treasury_outflow",
    "token_mint",
    "token_burn",
    "unknown_flow",
]


@dataclass(frozen=True)
class ChainConfig:
    chain_id: int
    network: str
    family: str
    enabled: bool
    rpc_endpoint: str
    rpc_env: str | None
    explorer_url: str
    finality_depth: int
    max_block_range: int
    polling_interval_seconds: int
    retry_limit: int
    timeout_seconds: int


@dataclass(frozen=True)
class AssetConfig:
    chain_id: int
    symbol: str
    asset_type: str
    decimals: int
    contract_address: str | None = None


@dataclass(frozen=True)
class OnChainSurface:
    project: str
    chain_id: int
    network: str
    address: str
    address_type: AddressType
    protocol_role: str
    asset_scope: str
    source_url: str
    source_type: str
    verification_timestamp: datetime
    confidence: float
    active: bool
    valid_from: datetime
    evidence_id: str
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", self.address.lower())
        object.__setattr__(self, "verification_timestamp", self.verification_timestamp.astimezone(UTC))
        object.__setattr__(self, "valid_from", self.valid_from.astimezone(UTC))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", self.valid_to.astimezone(UTC))


@dataclass(frozen=True)
class RawOnChainObservation:
    project: str
    chain_id: int
    provider: str
    endpoint_identity: str
    block_number: int
    block_hash: str
    block_timestamp: datetime
    observed_address: str
    acquisition_timestamp: datetime
    finality_status: str
    source_reference: str
    evidence_id: str
    transaction_hash: str | None = None
    log_index: int | None = None
    counterparty_address: str | None = None
    asset_contract: str | None = None
    asset_symbol: str | None = None
    raw_amount: str | None = None
    decimals: int | None = None
    normalized_amount: Decimal | None = None
    direction: str = "unknown"
    event_type: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_timestamp", self.block_timestamp.astimezone(UTC))
        object.__setattr__(self, "acquisition_timestamp", self.acquisition_timestamp.astimezone(UTC))
        object.__setattr__(self, "observed_address", self.observed_address.lower())
        if self.counterparty_address is not None:
            object.__setattr__(self, "counterparty_address", self.counterparty_address.lower())
        if self.asset_contract is not None:
            object.__setattr__(self, "asset_contract", self.asset_contract.lower())


@dataclass(frozen=True)
class CapitalFlowRecord:
    flow_id: str
    project: str
    chain_id: int
    category: FlowCategory
    asset_contract: str | None
    asset_symbol: str | None
    native_amount: Decimal | None
    usd_value: Decimal | None
    usd_valuation_status: str
    direction: str
    raw_evidence_ids: tuple[str, ...]
    block_number: int
    block_timestamp: datetime
    confidence: float
    unavailable_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_timestamp", self.block_timestamp.astimezone(UTC))
        object.__setattr__(self, "raw_evidence_ids", tuple(sorted(self.raw_evidence_ids)))
        object.__setattr__(self, "unavailable_fields", tuple(sorted(self.unavailable_fields)))


@dataclass(frozen=True)
class CapitalFlowSnapshot:
    snapshot_id: str
    project: str
    chain_id: int
    window: str
    start_block: int | None
    end_block: int | None
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    gross_inflow: Decimal
    gross_outflow: Decimal
    net_external_flow: Decimal
    liquidity_added: Decimal
    liquidity_removed: Decimal
    deposits: Decimal
    withdrawals: Decimal
    bridge_inflow: Decimal
    bridge_outflow: Decimal
    fees_revenue: Decimal
    treasury_flows: Decimal
    native_token_movement: Decimal
    unavailable_fields: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    confidence: float
    freshness: float
    completeness: float
    status: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self) -> None:
        for name in ("start_timestamp", "end_timestamp"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, value.astimezone(UTC))
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
        object.__setattr__(self, "unavailable_fields", tuple(sorted(self.unavailable_fields)))
        object.__setattr__(self, "evidence_ids", tuple(sorted(self.evidence_ids)))


@dataclass(frozen=True)
class ProviderState:
    chain_id: int
    network: str
    provider: str
    endpoint_identity: str
    status: str
    message: str
