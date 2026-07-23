from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

MARKET_FACTS_SCHEMA_VERSION = "market-facts-v3.4.1"

MarketFactType = Literal[
    "spot_price",
    "circulating_supply",
    "total_supply",
    "max_supply",
    "market_capitalization",
    "fully_diluted_valuation",
    "trading_volume",
]
MarketFactStatus = Literal[
    "success",
    "unavailable",
    "rate_limited",
    "malformed",
    "partial",
    "conflicting",
    "unsupported",
]
ConflictState = Literal["none", "open", "contested", "resolved"]
QualityState = Literal["accepted", "stale", "partial", "ambiguous", "unavailable"]

MARKET_FACT_TYPES: frozenset[str] = frozenset(MarketFactType.__args__)  # type: ignore[attr-defined]
MARKET_FACT_STATUSES: frozenset[str] = frozenset(MarketFactStatus.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES: frozenset[str] = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]
QUALITY_STATES: frozenset[str] = frozenset(QualityState.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class MarketFactIdentity:
    entity_id: str
    asset_id: str
    representation_id: str
    chain: str
    contract_address: str
    provider_listing_id: str

    def __post_init__(self) -> None:
        _required_text(self, "entity_id", "asset_id", "representation_id", "provider_listing_id")
        if bool(self.chain) != bool(self.contract_address):
            raise ValueError("chain and contract_address must either both be set or both be empty")


@dataclass(frozen=True)
class MarketFactRequest:
    source_id: str
    provider_id: str
    identity: MarketFactIdentity
    quote_currency: str
    requested_fact_types: tuple[MarketFactType, ...]
    requested_at: datetime

    def __post_init__(self) -> None:
        _required_text(self, "source_id", "provider_id", "quote_currency")
        object.__setattr__(self, "quote_currency", self.quote_currency.lower())
        object.__setattr__(self, "requested_at", _utc("requested_at", self.requested_at))
        if not self.requested_fact_types:
            raise ValueError("requested_fact_types must not be empty")
        for fact_type in self.requested_fact_types:
            _member("fact_type", fact_type, MARKET_FACT_TYPES)


@dataclass(frozen=True)
class NormalizedMarketFact:
    fact_type: MarketFactType
    value: str
    unit: str
    quote_currency: str | None
    effective_at: datetime
    observed_at: datetime
    confidence: str = "1"
    quality_state: QualityState = "accepted"
    conflict_state: ConflictState = "none"
    venue_scope: str = "provider_aggregate"

    def __post_init__(self) -> None:
        _member("fact_type", self.fact_type, MARKET_FACT_TYPES)
        _required_text(self, "value", "unit", "venue_scope")
        _decimal(self.value)
        _confidence(self.confidence)
        object.__setattr__(self, "effective_at", _utc("effective_at", self.effective_at))
        object.__setattr__(self, "observed_at", _utc("observed_at", self.observed_at))
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.quote_currency is not None:
            if not self.quote_currency.strip():
                raise ValueError("quote_currency must not be blank")
            object.__setattr__(self, "quote_currency", self.quote_currency.lower())


@dataclass(frozen=True)
class MarketFactAcquisitionResult:
    source_id: str
    provider_id: str
    endpoint: str
    parser_version: str
    registry_fingerprint: str
    provider_source_record_id: str
    provider_source_record_version: str
    request: MarketFactRequest
    status: MarketFactStatus
    acquired_at: datetime
    known_at: datetime
    raw_payload_hash: str
    facts: tuple[NormalizedMarketFact, ...] = ()
    failure_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "source_id",
            "provider_id",
            "endpoint",
            "parser_version",
            "registry_fingerprint",
            "provider_source_record_id",
            "provider_source_record_version",
            "raw_payload_hash",
        )
        _member("status", self.status, MARKET_FACT_STATUSES)
        object.__setattr__(self, "acquired_at", _utc("acquired_at", self.acquired_at))
        object.__setattr__(self, "known_at", _utc("known_at", self.known_at))
        if self.status == "success" and not self.facts:
            raise ValueError("successful acquisition must contain facts")
        if self.status != "success" and self.facts:
            raise ValueError("non-successful acquisition must not contain facts")


@dataclass(frozen=True)
class ObservedMarketFactRecord:
    record_id: str
    logical_id: str
    semantic_version: str
    identity: MarketFactIdentity
    source_id: str
    provider_id: str
    endpoint: str
    parser_version: str
    fact_type: MarketFactType
    value: str
    unit: str
    quote_currency: str | None
    venue_scope: str
    effective_at: datetime
    observed_at: datetime
    recorded_at: datetime
    known_at: datetime
    raw_payload_hash: str
    provider_source_record_id: str
    provider_source_record_version: str
    confidence: str
    quality_state: QualityState
    conflict_state: ConflictState
    content_hash: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""
    schema_version: str = MARKET_FACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "semantic_version",
            "source_id",
            "provider_id",
            "endpoint",
            "parser_version",
            "value",
            "unit",
            "venue_scope",
            "raw_payload_hash",
            "provider_source_record_id",
            "provider_source_record_version",
            "confidence",
            "content_hash",
            "schema_version",
        )
        _member("fact_type", self.fact_type, MARKET_FACT_TYPES)
        _decimal(self.value)
        _confidence(self.confidence)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        object.__setattr__(self, "effective_at", _utc("effective_at", self.effective_at))
        object.__setattr__(self, "observed_at", _utc("observed_at", self.observed_at))
        object.__setattr__(self, "recorded_at", _utc("recorded_at", self.recorded_at))
        object.__setattr__(self, "known_at", _utc("known_at", self.known_at))
        if self.quote_currency is not None:
            object.__setattr__(self, "quote_currency", self.quote_currency.lower())
        if self.supersedes_record_id is not None and not self.supersedes_record_id.strip():
            raise ValueError("supersedes_record_id must not be blank")


@dataclass(frozen=True)
class MarketFactAvailabilityEvent:
    event_id: str
    source_id: str
    provider_id: str
    entity_id: str
    representation_id: str
    status: MarketFactStatus
    requested_at: datetime
    recorded_at: datetime
    known_at: datetime
    endpoint: str
    parser_version: str
    raw_payload_hash: str
    failure_reason: str
    schema_version: str = MARKET_FACTS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text(
            self,
            "event_id",
            "source_id",
            "provider_id",
            "entity_id",
            "representation_id",
            "endpoint",
            "parser_version",
            "raw_payload_hash",
            "schema_version",
        )
        _member("status", self.status, MARKET_FACT_STATUSES)
        object.__setattr__(self, "requested_at", _utc("requested_at", self.requested_at))
        object.__setattr__(self, "recorded_at", _utc("recorded_at", self.recorded_at))
        object.__setattr__(self, "known_at", _utc("known_at", self.known_at))


def validate_fact_value(fact_type: MarketFactType, value: str) -> None:
    number = _decimal(value)
    if fact_type == "spot_price" and number <= 0:
        raise ValueError("spot_price must be positive")
    if (
        fact_type
        in {
            "circulating_supply",
            "total_supply",
            "max_supply",
            "market_capitalization",
            "fully_diluted_valuation",
            "trading_volume",
        }
        and number < 0
    ):
        raise ValueError(f"{fact_type} must be non-negative")


def _decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be a finite decimal string") from exc
    if not number.is_finite():
        raise ValueError("value must be finite")
    return number


def _confidence(value: str) -> Decimal:
    number = _decimal(value)
    if number < 0 or number > 1:
        raise ValueError("confidence must be between 0 and 1")
    return number


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _required_text(instance: object, *names: str) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be blank")


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
