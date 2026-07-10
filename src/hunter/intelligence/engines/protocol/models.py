from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.protocol.exceptions import ProtocolValidationError

PROTOCOL_DOMAINS = (
    "active_users",
    "new_users",
    "returning_users",
    "user_retention",
    "transaction_activity",
    "transaction_quality",
    "fees",
    "revenue",
    "tvl",
    "organic_tvl",
    "liquidity_depth",
    "liquidity_stability",
    "capital_efficiency",
    "protocol_utilization",
    "application_breadth",
    "application_concentration",
    "validator_node_health",
    "network_reliability",
    "outages_incidents",
    "upgrade_delivery",
    "governance_participation",
    "treasury_health",
    "incentive_dependence",
    "emissions_dependence",
    "economic_sustainability",
    "value_capture",
    "ecosystem_expansion",
    "protocol_resilience",
    "protocol_momentum",
    "protocol_deterioration",
)


@dataclass(frozen=True)
class ProtocolSnapshot:
    id: str
    project: str
    protocol: str
    source: str
    timestamp: datetime
    reliability: float
    reference: str = ""
    chain: str | None = None
    deployment: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _base_validate(self.id, self.project, self.protocol, self.source, self.timestamp)
        object.__setattr__(self, "reliability", _clamp(self.reliability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class UsageSnapshot(ProtocolSnapshot):
    active_users: int | None = None
    new_users: int | None = None
    returning_users: int | None = None
    retained_users: int | None = None
    bot_activity_ratio: float | None = None
    sybil_activity_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.active_users, "active users")
        _nonnegative_optional_int(self.new_users, "new users")
        _nonnegative_optional_int(self.returning_users, "returning users")
        _nonnegative_optional_int(self.retained_users, "retained users")


@dataclass(frozen=True)
class UserSnapshot(ProtocolSnapshot):
    wallet_count: int | None = None
    concentrated_wallet_share: float | None = None
    organic_user_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.wallet_count, "wallet count")


@dataclass(frozen=True)
class TransactionSnapshot(ProtocolSnapshot):
    transaction_count: int | None = None
    economically_meaningful_count: int | None = None
    duplicate_ratio: float | None = None
    bridge_pass_through_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.transaction_count, "transaction count")
        _nonnegative_optional_int(self.economically_meaningful_count, "economically meaningful count")


@dataclass(frozen=True)
class FeeSnapshot(ProtocolSnapshot):
    fees_usd: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.fees_usd, "fees")


@dataclass(frozen=True)
class RevenueSnapshot(ProtocolSnapshot):
    revenue_usd: float | None = None
    protocol_income_usd: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.revenue_usd, "revenue")
        _nonnegative_optional_float(self.protocol_income_usd, "protocol income")


@dataclass(frozen=True)
class TVLSnapshot(ProtocolSnapshot):
    tvl_usd: float | None = None
    organic_tvl_usd: float | None = None
    incentive_tvl_usd: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.tvl_usd, "tvl")
        _nonnegative_optional_float(self.organic_tvl_usd, "organic tvl")
        _nonnegative_optional_float(self.incentive_tvl_usd, "incentive tvl")


@dataclass(frozen=True)
class LiquiditySnapshot(ProtocolSnapshot):
    liquidity_usd: float | None = None
    depth_usd: float | None = None
    stable_liquidity_ratio: float | None = None
    utilization_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.liquidity_usd, "liquidity")
        _nonnegative_optional_float(self.depth_usd, "depth")


@dataclass(frozen=True)
class ApplicationSnapshot(ProtocolSnapshot):
    application_id: str = ""
    volume_share: float | None = None
    active: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.application_id, "application id")


@dataclass(frozen=True)
class ValidatorSnapshot(ProtocolSnapshot):
    active_validators: int | None = None
    online_ratio: float | None = None
    concentration_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.active_validators, "active validators")


@dataclass(frozen=True)
class IncidentSnapshot(ProtocolSnapshot):
    severity: float = 0.0
    resolved: bool = True
    duration_minutes: int | None = None
    incident_type: str = "incident"

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "severity", _clamp(self.severity))
        _nonnegative_optional_int(self.duration_minutes, "incident duration")


@dataclass(frozen=True)
class GovernanceSnapshot(ProtocolSnapshot):
    proposals: int | None = None
    voter_count: int | None = None
    participation_ratio: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_int(self.proposals, "proposals")
        _nonnegative_optional_int(self.voter_count, "voter count")


@dataclass(frozen=True)
class TreasurySnapshot(ProtocolSnapshot):
    treasury_usd: float | None = None
    monthly_expense_usd: float | None = None
    runway_months: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.treasury_usd, "treasury")
        _nonnegative_optional_float(self.monthly_expense_usd, "monthly expense")
        _nonnegative_optional_float(self.runway_months, "runway")


@dataclass(frozen=True)
class IncentiveSnapshot(ProtocolSnapshot):
    incentives_usd: float | None = None
    emissions_usd: float | None = None
    revenue_usd: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _nonnegative_optional_float(self.incentives_usd, "incentives")
        _nonnegative_optional_float(self.emissions_usd, "emissions")
        _nonnegative_optional_float(self.revenue_usd, "incentive revenue")


@dataclass(frozen=True)
class ProtocolEvent(ProtocolSnapshot):
    event_type: str = "event"
    value: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "event_type", self.event_type.strip().lower().replace("-", "_").replace(" ", "_"))


ProtocolRecord = (
    ProtocolSnapshot
    | UsageSnapshot
    | UserSnapshot
    | TransactionSnapshot
    | FeeSnapshot
    | RevenueSnapshot
    | TVLSnapshot
    | LiquiditySnapshot
    | ApplicationSnapshot
    | ValidatorSnapshot
    | IncidentSnapshot
    | GovernanceSnapshot
    | TreasurySnapshot
    | IncentiveSnapshot
    | ProtocolEvent
)


@dataclass(frozen=True)
class ProtocolDataset:
    project: str = "global-crypto"
    protocol: str = "unknown"
    records: tuple[ProtocolRecord, ...] = ()
    usage: tuple[UsageSnapshot, ...] = ()
    users: tuple[UserSnapshot, ...] = ()
    transactions: tuple[TransactionSnapshot, ...] = ()
    fees: tuple[FeeSnapshot, ...] = ()
    revenues: tuple[RevenueSnapshot, ...] = ()
    tvl: tuple[TVLSnapshot, ...] = ()
    liquidity: tuple[LiquiditySnapshot, ...] = ()
    applications: tuple[ApplicationSnapshot, ...] = ()
    validators: tuple[ValidatorSnapshot, ...] = ()
    incidents: tuple[IncidentSnapshot, ...] = ()
    governance: tuple[GovernanceSnapshot, ...] = ()
    treasury: tuple[TreasurySnapshot, ...] = ()
    incentives: tuple[IncentiveSnapshot, ...] = ()
    events: tuple[ProtocolEvent, ...] = ()
    missing_fields: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def chains(self) -> tuple[str, ...]:
        return tuple(sorted({record.chain for record in self.records if record.chain}))

    def deployments(self) -> tuple[str, ...]:
        return tuple(sorted({record.deployment for record in self.records if record.deployment}))


@dataclass(frozen=True)
class ProtocolIndicator:
    name: str
    value: float
    direction: str
    confidence: float
    description: str
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtocolAnalysis:
    indicators: tuple[ProtocolIndicator, ...]
    health: str
    operational_trend: str
    economic_trend: str
    adoption_trend: str
    resilience: str
    sustainability: str
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


def _base_validate(id_: str, project: str, protocol: str, source: str, timestamp: datetime) -> None:
    _require_text(id_, "id")
    _require_text(project, "project")
    _require_text(protocol, "protocol")
    _require_text(source, "source")
    if not isinstance(timestamp, datetime):
        raise ProtocolValidationError("Missing timestamp")


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ProtocolValidationError(f"Missing {field_name}")


def _nonnegative_optional_int(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ProtocolValidationError(f"Invalid negative {field_name}")


def _nonnegative_optional_float(value: float | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ProtocolValidationError(f"Invalid negative {field_name}")


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
