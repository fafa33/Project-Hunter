from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

import yaml

from hunter.execution.canonicalization import canonicalize
from hunter.tokenomics.identity import tokenomics_id
from hunter.tokenomics.models import (
    AllocationCategory,
    AvailabilityState,
    CoverageState,
    EvidenceLifecycleStatus,
    SourceAuthorityTier,
    SupplyMetric,
)

TokenomicsProviderStatus = Literal[
    "success",
    "unavailable",
    "rate_limited",
    "malformed",
    "partial",
    "conflicting",
    "unsupported",
]
TokenomicsCapability = Literal["supply", "allocation", "vesting", "unlock"]
TokenomicsSourceType = Literal["official_project_controlled", "public_aggregator", "verified_onchain_rpc"]

PROVIDER_STATUS_TO_AVAILABILITY: dict[TokenomicsProviderStatus, AvailabilityState] = {
    "success": "available",
    "unavailable": "unavailable",
    "rate_limited": "unavailable",
    "malformed": "ambiguous",
    "partial": "partial",
    "conflicting": "ambiguous",
    "unsupported": "unavailable",
}

PROVIDER_STATUS_TO_COVERAGE: dict[TokenomicsProviderStatus, CoverageState] = {
    "success": "complete",
    "unavailable": "unavailable",
    "rate_limited": "unavailable",
    "malformed": "unknown",
    "partial": "partial",
    "conflicting": "partial",
    "unsupported": "unavailable",
}


@dataclass(frozen=True)
class TokenomicsProviderDescriptor:
    provider_id: str
    adapter_version: str
    supported_chains: tuple[str, ...]
    capabilities: tuple[TokenomicsCapability, ...]
    authority_tier: SourceAuthorityTier
    historical_depth: str
    freshness: str
    rate_limit_behavior: str
    finality_behavior: str
    source_limitations: str


@dataclass(frozen=True)
class TokenomicsSourceConfig:
    source_id: str
    provider_id: str
    asset_id: str
    candidate_id: str
    symbol: str
    name: str
    chain: str
    contract_address: str
    decimals: int
    source_type: TokenomicsSourceType
    endpoint: str
    allowed_hosts: tuple[str, ...]
    response_format: str
    parser_version: str
    authority_tier: SourceAuthorityTier
    enabled: bool
    capabilities: tuple[TokenomicsCapability, ...]
    historical_depth: str
    freshness: str
    source_limitations: str
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        if urllib.parse.urlparse(self.endpoint).scheme != "https":
            raise ValueError("tokenomics source endpoints must be HTTPS")
        endpoint_host = urllib.parse.urlparse(self.endpoint).hostname or ""
        if endpoint_host not in self.allowed_hosts:
            raise ValueError("tokenomics source endpoint host must be explicitly allowed")
        if self.effective_at is not None:
            object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))


class TokenomicsSourceRegistry:
    def __init__(self, sources: tuple[TokenomicsSourceConfig, ...]) -> None:
        self._sources = sources

    @classmethod
    def from_file(cls, path: str | Path = "configs/tokenomics_sources.yaml") -> TokenomicsSourceRegistry:
        config_path = Path(path)
        if not config_path.exists():
            return cls(())
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, Mapping):
            raise ValueError("tokenomics source registry must be a mapping")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> TokenomicsSourceRegistry:
        rows = payload.get("sources", ())
        if not isinstance(rows, list):
            raise ValueError("tokenomics source registry sources must be a list")
        return cls(tuple(_source_config(row) for row in rows if isinstance(row, Mapping)))

    def resolve(self, *, provider_id: str, asset_id: str) -> TokenomicsSourceConfig | None:
        for source in self._sources:
            if source.enabled and source.provider_id == provider_id and source.asset_id == asset_id:
                return source
        return None


@dataclass(frozen=True)
class TokenomicsProviderRequest:
    asset_id: str
    candidate_id: str
    symbol: str
    name: str
    chain: str
    contract_address: str
    source_uri: str
    requested_at: datetime
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_at", self.requested_at.astimezone(UTC))
        if self.effective_at is not None:
            object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))


@dataclass(frozen=True)
class TokenomicsRawArtifact:
    source_uri: str
    source_type: str
    content: str
    observed_at: datetime
    published_at: datetime | None
    source_authority: SourceAuthorityTier
    parser_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        if self.published_at is not None:
            object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))

    @property
    def content_hash(self) -> str:
        return f"sha256:{sha256(self.content.encode('utf-8')).hexdigest()}"

    @property
    def artifact_id(self) -> str:
        return tokenomics_id(
            "source-artifact",
            {
                "source_uri": self.source_uri,
                "content_hash": self.content_hash,
                "parser_version": self.parser_version,
            },
        )


@dataclass(frozen=True)
class NormalizedSupplyClaim:
    metric: SupplyMetric
    amount: str
    unit: str
    effective_at: datetime
    observed_at: datetime
    source_role: str = "source"
    status: EvidenceLifecycleStatus = "active"

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))


@dataclass(frozen=True)
class NormalizedAllocationClaim:
    category: AllocationCategory
    percentage: float | None
    amount: str | None
    unit: str
    effective_start_at: datetime
    effective_end_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_start_at", self.effective_start_at.astimezone(UTC))
        if self.effective_end_at is not None:
            object.__setattr__(self, "effective_end_at", self.effective_end_at.astimezone(UTC))


@dataclass(frozen=True)
class NormalizedVestingSegment:
    segment_key: str
    start_at: datetime
    end_at: datetime
    amount: str | None
    percentage: float | None
    segment_state: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_at", self.start_at.astimezone(UTC))
        object.__setattr__(self, "end_at", self.end_at.astimezone(UTC))


@dataclass(frozen=True)
class NormalizedUnlockEvent:
    event_key: str
    unlock_at: datetime
    amount: str | None
    percentage: float | None
    unlock_state: str = "scheduled"

    def __post_init__(self) -> None:
        object.__setattr__(self, "unlock_at", self.unlock_at.astimezone(UTC))


@dataclass(frozen=True)
class NormalizedVestingSchedule:
    schedule_key: str
    allocation_category: AllocationCategory
    effective_start_at: datetime
    effective_end_at: datetime | None
    schedule_state: str
    segments: tuple[NormalizedVestingSegment, ...] = ()
    unlocks: tuple[NormalizedUnlockEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_start_at", self.effective_start_at.astimezone(UTC))
        if self.effective_end_at is not None:
            object.__setattr__(self, "effective_end_at", self.effective_end_at.astimezone(UTC))


@dataclass(frozen=True)
class TokenomicsIngestionResult:
    provider: TokenomicsProviderDescriptor
    request: TokenomicsProviderRequest
    status: TokenomicsProviderStatus
    artifacts: tuple[TokenomicsRawArtifact, ...] = ()
    supply_claims: tuple[NormalizedSupplyClaim, ...] = ()
    allocations: tuple[NormalizedAllocationClaim, ...] = ()
    vesting_schedules: tuple[NormalizedVestingSchedule, ...] = ()
    failure_reason: str = ""
    source_limitations: str = ""
    observed_at: datetime | None = None
    source_config_id: str = ""
    registry_approved: bool = False

    def __post_init__(self) -> None:
        if self.observed_at is not None:
            object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))

    @property
    def coverage_state(self) -> CoverageState:
        return PROVIDER_STATUS_TO_COVERAGE[self.status]

    @property
    def availability_state(self) -> AvailabilityState:
        return PROVIDER_STATUS_TO_AVAILABILITY[self.status]


class JsonHttpTransport(Protocol):
    def get_json(self, url: str, timeout_seconds: float) -> object: ...


class JsonRpcTransport(Protocol):
    def post_json(self, url: str, payload: Mapping[str, object], timeout_seconds: float) -> object: ...


class UrllibJsonTransport:
    def get_json(self, url: str, timeout_seconds: float) -> object:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, url: str, payload: Mapping[str, object], timeout_seconds: float) -> object:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


class OfficialTokenomicsDisclosureAdapter:
    descriptor = TokenomicsProviderDescriptor(
        provider_id="official-tokenomics-disclosure",
        adapter_version="official-tokenomics-disclosure-v1",
        supported_chains=("registry",),
        capabilities=("supply", "allocation", "vesting", "unlock"),
        authority_tier="authoritative",
        historical_depth="as-declared-by-source-document",
        freshness="document-published-or-retrieved-time",
        rate_limit_behavior="single public document fetch; unavailable/rate_limited states are explicit",
        finality_behavior="not-applicable-offchain-disclosure",
        source_limitations="requires a structured public project-controlled tokenomics disclosure document",
    )

    def __init__(
        self,
        registry: TokenomicsSourceRegistry | None = None,
        transport: JsonHttpTransport | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.registry = registry or TokenomicsSourceRegistry.from_file()
        self.transport = transport or UrllibJsonTransport()
        self.timeout_seconds = timeout_seconds

    def collect(self, request: TokenomicsProviderRequest) -> TokenomicsIngestionResult:
        source = self.registry.resolve(provider_id=self.descriptor.provider_id, asset_id=request.asset_id)
        if source is None:
            return _provider_result(self.descriptor, request, "unsupported", failure_reason="source_not_registered")
        if source.source_type != "official_project_controlled":
            return _provider_result(
                self.descriptor,
                _request_from_source(request, source),
                "unsupported",
                failure_reason="source_type_mismatch",
            )
        if not set(self.descriptor.capabilities).issubset(set(source.capabilities)):
            return _provider_result(
                self.descriptor,
                _request_from_source(request, source),
                "unsupported",
                failure_reason="capability_not_registered",
            )
        checked = _request_from_source(request, source)
        try:
            payload = self.transport.get_json(source.endpoint, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status: TokenomicsProviderStatus = "rate_limited" if exc.code == 429 else "unavailable"
            return _provider_result(self.descriptor, checked, status, failure_reason=f"http_{exc.code}", source=source)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return _provider_result(
                self.descriptor, checked, "unavailable", failure_reason=exc.__class__.__name__, source=source
            )
        if not isinstance(payload, Mapping):
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason="json_root_not_object", source=source
            )
        declared_status = str(payload.get("status", "success"))
        if declared_status in {"unavailable", "rate_limited", "unsupported"}:
            return _provider_result(
                self.descriptor,
                checked,
                declared_status,  # type: ignore[arg-type]
                failure_reason=str(payload.get("reason", declared_status)),
                source=source,
            )
        try:
            return self._normalize(checked, payload, source)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason=exc.__class__.__name__, source=source
            )

    def _normalize(
        self, request: TokenomicsProviderRequest, payload: Mapping[str, object], source: TokenomicsSourceConfig
    ) -> TokenomicsIngestionResult:
        if str(payload.get("schema_version", "")) != source.response_format:
            raise ValueError("schema version mismatch")
        if str(payload.get("asset_id", source.asset_id)) != source.asset_id:
            raise ValueError("asset id mismatch")
        observed_at = _parse_time(payload.get("observed_at")) or request.requested_at
        published_at = _parse_time(payload.get("published_at"))
        artifact = TokenomicsRawArtifact(
            source_uri=source.endpoint,
            source_type="official_tokenomics_disclosure",
            content=canonicalize(payload).decode("utf-8"),
            observed_at=observed_at,
            published_at=published_at,
            source_authority=source.authority_tier,
            parser_version=source.parser_version,
        )
        supply_claims = _supply_claims(payload.get("supply"), request, observed_at)
        allocations = _allocation_claims(payload.get("allocations"), request, observed_at)
        vesting = _vesting_schedules(payload.get("vesting"), request, observed_at)
        status: TokenomicsProviderStatus = "success"
        if not supply_claims or not allocations or not vesting:
            status = "partial"
        if _has_conflicting_supply_values(supply_claims):
            status = "conflicting"
        return TokenomicsIngestionResult(
            provider=self.descriptor,
            request=request,
            status=status,
            artifacts=(artifact,),
            supply_claims=supply_claims,
            allocations=allocations,
            vesting_schedules=vesting,
            source_limitations=source.source_limitations,
            observed_at=observed_at,
            source_config_id=source.source_id,
            registry_approved=True,
        )


class PublicTokenomicsAggregatorAdapter(OfficialTokenomicsDisclosureAdapter):
    descriptor = TokenomicsProviderDescriptor(
        provider_id="public-tokenomics-aggregator",
        adapter_version="public-tokenomics-aggregator-v1",
        supported_chains=("registry",),
        capabilities=("supply",),
        authority_tier="secondary",
        historical_depth="aggregator-retained-current-or-historical-snapshot",
        freshness="retrieved-time-and-provider-timestamp-when-available",
        rate_limit_behavior="single public document fetch; unavailable/rate_limited states are explicit",
        finality_behavior="not-applicable-secondary-public-aggregator",
        source_limitations="secondary public aggregator supply evidence; must not override authoritative project or on-chain evidence",
    )

    def collect(self, request: TokenomicsProviderRequest) -> TokenomicsIngestionResult:
        source = self.registry.resolve(provider_id=self.descriptor.provider_id, asset_id=request.asset_id)
        if source is None:
            return _provider_result(self.descriptor, request, "unsupported", failure_reason="source_not_registered")
        if source.source_type != "public_aggregator":
            return _provider_result(
                self.descriptor,
                _request_from_source(request, source),
                "unsupported",
                failure_reason="source_type_mismatch",
            )
        if "supply" not in source.capabilities:
            return _provider_result(
                self.descriptor,
                _request_from_source(request, source),
                "unsupported",
                failure_reason="capability_not_registered",
                source=source,
            )
        checked = _request_from_source(request, source)
        try:
            payload = self.transport.get_json(source.endpoint, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status: TokenomicsProviderStatus = "rate_limited" if exc.code == 429 else "unavailable"
            return _provider_result(self.descriptor, checked, status, failure_reason=f"http_{exc.code}", source=source)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return _provider_result(
                self.descriptor, checked, "unavailable", failure_reason=exc.__class__.__name__, source=source
            )
        if not isinstance(payload, Mapping):
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason="json_root_not_object", source=source
            )
        try:
            return self._normalize_aggregator(checked, payload, source)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason=exc.__class__.__name__, source=source
            )

    def _normalize_aggregator(
        self, request: TokenomicsProviderRequest, payload: Mapping[str, object], source: TokenomicsSourceConfig
    ) -> TokenomicsIngestionResult:
        if source.response_format != "coingecko-coin-v3":
            raise ValueError("unsupported aggregator schema")
        if str(payload["id"]) != source.source_id.split(":", 1)[-1]:
            raise ValueError("aggregator asset mismatch")
        market_data = payload.get("market_data")
        if not isinstance(market_data, Mapping):
            raise ValueError("missing market_data")
        observed_at = request.requested_at
        artifact = TokenomicsRawArtifact(
            source_uri=source.endpoint,
            source_type="public_tokenomics_aggregator",
            content=canonicalize(payload).decode("utf-8"),
            observed_at=observed_at,
            published_at=_parse_time(payload.get("last_updated")),
            source_authority=source.authority_tier,
            parser_version=source.parser_version,
        )
        supply_claims = _coingecko_supply_claims(market_data, request, observed_at)
        return TokenomicsIngestionResult(
            provider=self.descriptor,
            request=request,
            status="success" if supply_claims else "partial",
            artifacts=(artifact,),
            supply_claims=supply_claims,
            source_limitations=source.source_limitations,
            observed_at=observed_at,
            source_config_id=source.source_id,
            registry_approved=True,
        )


class EvmErc20SupplyAdapter:
    descriptor = TokenomicsProviderDescriptor(
        provider_id="evm-erc20-public-rpc",
        adapter_version="evm-erc20-public-rpc-v1",
        supported_chains=("ethereum", "arbitrum", "base", "optimism", "polygon"),
        capabilities=("supply",),
        authority_tier="authoritative",
        historical_depth="current-state-only-unless-rpc-endpoint-supports-archive",
        freshness="latest-finalized-or-provider-latest-block",
        rate_limit_behavior="public RPC failures/rate limits become explicit acquisition outcomes",
        finality_behavior="uses latest public RPC state; finality depends on endpoint",
        source_limitations="totalSupply only; does not infer circulating, locked, burned, vested, or unlocked supply",
    )

    def __init__(
        self,
        registry: TokenomicsSourceRegistry | None = None,
        transport: JsonRpcTransport | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.registry = registry or TokenomicsSourceRegistry.from_file()
        self.transport = transport or UrllibJsonTransport()
        self.timeout_seconds = timeout_seconds

    def collect(self, request: TokenomicsProviderRequest) -> TokenomicsIngestionResult:
        source = self.registry.resolve(provider_id=self.descriptor.provider_id, asset_id=request.asset_id)
        if source is None or source.source_type != "verified_onchain_rpc":
            return _provider_result(self.descriptor, request, "unsupported", failure_reason="source_not_registered")
        checked = _request_from_source(request, source)
        if "supply" not in source.capabilities:
            return _provider_result(
                self.descriptor, checked, "unsupported", failure_reason="capability_not_registered", source=source
            )
        if checked.chain not in self.descriptor.supported_chains or not checked.contract_address:
            return _provider_result(
                self.descriptor, checked, "unsupported", failure_reason="unsupported_chain_or_token", source=source
            )
        payload: Mapping[str, object] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": ({"to": checked.contract_address, "data": "0x18160ddd"}, "latest"),
        }
        try:
            response = self.transport.post_json(source.endpoint, payload, self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status: TokenomicsProviderStatus = "rate_limited" if exc.code == 429 else "unavailable"
            return _provider_result(self.descriptor, checked, status, failure_reason=f"http_{exc.code}", source=source)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            return _provider_result(
                self.descriptor, checked, "unavailable", failure_reason=exc.__class__.__name__, source=source
            )
        if not isinstance(response, Mapping):
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason="json_root_not_object", source=source
            )
        if "error" in response:
            return _provider_result(self.descriptor, checked, "unavailable", failure_reason="rpc_error", source=source)
        result = response.get("result")
        if not isinstance(result, str) or not result.startswith("0x"):
            return _provider_result(
                self.descriptor, checked, "malformed", failure_reason="missing_total_supply_hex", source=source
            )
        amount = str(int(result, 16))
        observed_at = checked.requested_at
        artifact = TokenomicsRawArtifact(
            source_uri=source.endpoint,
            source_type="evm_erc20_total_supply_rpc",
            content=canonicalize({"request": payload, "response": response}).decode("utf-8"),
            observed_at=observed_at,
            published_at=None,
            source_authority=source.authority_tier,
            parser_version=source.parser_version,
        )
        return TokenomicsIngestionResult(
            provider=self.descriptor,
            request=checked,
            status="success",
            artifacts=(artifact,),
            supply_claims=(
                NormalizedSupplyClaim(
                    metric="total_supply",
                    amount=amount,
                    unit=checked.symbol,
                    effective_at=checked.effective_at or observed_at,
                    observed_at=observed_at,
                ),
            ),
            source_limitations=source.source_limitations,
            observed_at=observed_at,
            source_config_id=source.source_id,
            registry_approved=True,
        )


def _provider_result(
    descriptor: TokenomicsProviderDescriptor,
    request: TokenomicsProviderRequest,
    status: TokenomicsProviderStatus,
    *,
    failure_reason: str = "",
    source: TokenomicsSourceConfig | None = None,
) -> TokenomicsIngestionResult:
    if not request.source_uri:
        request = TokenomicsProviderRequest(
            asset_id=request.asset_id,
            candidate_id=request.candidate_id,
            symbol=request.symbol,
            name=request.name,
            chain=request.chain,
            contract_address=request.contract_address,
            source_uri=f"unsupported://{descriptor.provider_id}/{request.asset_id}",
            requested_at=request.requested_at,
            effective_at=request.effective_at,
        )
    return TokenomicsIngestionResult(
        provider=descriptor,
        request=request,
        status=status,
        failure_reason=failure_reason,
        source_limitations=source.source_limitations if source else descriptor.source_limitations,
        observed_at=request.requested_at,
        source_config_id=source.source_id if source else "",
        registry_approved=source is not None,
    )


def _source_config(raw: Mapping[str, object]) -> TokenomicsSourceConfig:
    effective_raw = raw.get("effective_at")
    capabilities_raw = raw.get("capabilities", ())
    if not isinstance(capabilities_raw, tuple | list):
        raise ValueError("tokenomics source capabilities must be a sequence")
    return TokenomicsSourceConfig(
        source_id=str(raw["source_id"]),
        provider_id=str(raw["provider_id"]),
        asset_id=str(raw["asset_id"]),
        candidate_id=str(raw["candidate_id"]),
        symbol=str(raw["symbol"]),
        name=str(raw["name"]),
        chain=str(raw["chain"]),
        contract_address=str(raw.get("contract_address", "")),
        decimals=int(str(raw.get("decimals", 0))),
        source_type=cast(TokenomicsSourceType, str(raw["source_type"])),
        endpoint=str(raw["endpoint"]),
        allowed_hosts=tuple(str(item) for item in raw.get("allowed_hosts", ())),
        response_format=str(raw["response_format"]),
        parser_version=str(raw["parser_version"]),
        authority_tier=cast(SourceAuthorityTier, str(raw["authority_tier"])),
        enabled=bool(raw.get("enabled", True)),
        capabilities=tuple(cast(TokenomicsCapability, str(item)) for item in capabilities_raw),
        historical_depth=str(raw.get("historical_depth", "")),
        freshness=str(raw.get("freshness", "")),
        source_limitations=str(raw.get("source_limitations", "")),
        effective_at=datetime.fromisoformat(str(effective_raw)).astimezone(UTC) if effective_raw else None,
    )


def _request_from_source(
    request: TokenomicsProviderRequest, source: TokenomicsSourceConfig
) -> TokenomicsProviderRequest:
    return TokenomicsProviderRequest(
        asset_id=source.asset_id,
        candidate_id=source.candidate_id,
        symbol=source.symbol,
        name=source.name,
        chain=source.chain,
        contract_address=source.contract_address,
        source_uri=source.endpoint,
        requested_at=request.requested_at,
        effective_at=request.effective_at or source.effective_at,
    )


def _supply_claims(
    raw: object, request: TokenomicsProviderRequest, observed_at: datetime
) -> tuple[NormalizedSupplyClaim, ...]:
    if not isinstance(raw, Mapping):
        return ()
    rows: list[NormalizedSupplyClaim] = []
    metric_map: dict[str, SupplyMetric] = {
        "circulating": "circulating_supply",
        "circulating_supply": "circulating_supply",
        "total": "total_supply",
        "total_supply": "total_supply",
        "max": "max_supply",
        "max_supply": "max_supply",
        "burned": "burned_supply",
        "burned_supply": "burned_supply",
        "locked": "locked_supply",
        "locked_supply": "locked_supply",
        "vested": "vested_supply",
        "vested_supply": "vested_supply",
        "unlocked": "unlocked_supply",
        "unlocked_supply": "unlocked_supply",
    }
    for key, metric in metric_map.items():
        if key not in raw:
            continue
        values = raw[key] if isinstance(raw[key], list) else (raw[key],)
        for value in values:
            rows.append(
                NormalizedSupplyClaim(
                    metric=metric,
                    amount=_decimal_string(value),
                    unit=str(raw.get("unit") or request.symbol),
                    effective_at=_parse_time(raw.get("effective_at")) or request.effective_at or observed_at,
                    observed_at=observed_at,
                )
            )
    return tuple(rows)


def _coingecko_supply_claims(
    market_data: Mapping[object, object], request: TokenomicsProviderRequest, observed_at: datetime
) -> tuple[NormalizedSupplyClaim, ...]:
    rows: list[NormalizedSupplyClaim] = []
    for key, metric in (
        ("circulating_supply", "circulating_supply"),
        ("total_supply", "total_supply"),
        ("max_supply", "max_supply"),
    ):
        value = market_data.get(key)
        if value is None:
            continue
        rows.append(
            NormalizedSupplyClaim(
                metric=metric,  # type: ignore[arg-type]
                amount=_decimal_string(value),
                unit=request.symbol,
                effective_at=request.effective_at or observed_at,
                observed_at=observed_at,
            )
        )
    return tuple(rows)


def _allocation_claims(
    raw: object,
    request: TokenomicsProviderRequest,
    observed_at: datetime,
) -> tuple[NormalizedAllocationClaim, ...]:
    if not isinstance(raw, list):
        return ()
    rows = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            NormalizedAllocationClaim(
                category=str(item["category"]),  # type: ignore[arg-type]
                percentage=_optional_float(item.get("percentage")),
                amount=None if item.get("amount") is None else _decimal_string(item.get("amount")),
                unit=str(item.get("unit") or request.symbol),
                effective_start_at=_parse_time(item.get("effective_start_at")) or request.effective_at or observed_at,
                effective_end_at=_parse_time(item.get("effective_end_at")),
            )
        )
    return tuple(rows)


def _vesting_schedules(
    raw: object,
    request: TokenomicsProviderRequest,
    observed_at: datetime,
) -> tuple[NormalizedVestingSchedule, ...]:
    if not isinstance(raw, list):
        return ()
    schedules = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        segments = []
        for segment in item.get("segments", ()) if isinstance(item.get("segments", ()), list) else ():
            if not isinstance(segment, Mapping):
                continue
            segments.append(
                NormalizedVestingSegment(
                    segment_key=str(segment.get("segment_key") or segment.get("type") or len(segments)),
                    start_at=_required_time(segment.get("start_at")),
                    end_at=_required_time(segment.get("end_at")),
                    amount=None if segment.get("amount") is None else _decimal_string(segment.get("amount")),
                    percentage=_optional_float(segment.get("percentage")),
                    segment_state=str(segment.get("segment_state") or "planned"),
                )
            )
        unlocks = []
        for unlock in item.get("unlocks", ()) if isinstance(item.get("unlocks", ()), list) else ():
            if not isinstance(unlock, Mapping):
                continue
            unlocks.append(
                NormalizedUnlockEvent(
                    event_key=str(unlock.get("event_key") or unlock.get("unlock_at") or len(unlocks)),
                    unlock_at=_required_time(unlock.get("unlock_at")),
                    amount=None if unlock.get("amount") is None else _decimal_string(unlock.get("amount")),
                    percentage=_optional_float(unlock.get("percentage")),
                    unlock_state=str(unlock.get("unlock_state") or "scheduled"),
                )
            )
        schedules.append(
            NormalizedVestingSchedule(
                schedule_key=str(item.get("schedule_key") or item["allocation_category"]),
                allocation_category=str(item["allocation_category"]),  # type: ignore[arg-type]
                effective_start_at=_parse_time(item.get("effective_start_at")) or request.effective_at or observed_at,
                effective_end_at=_parse_time(item.get("effective_end_at")),
                schedule_state=str(item.get("schedule_state") or "active"),
                segments=tuple(segments),
                unlocks=tuple(unlocks),
            )
        )
    return tuple(schedules)


def _has_conflicting_supply_values(claims: tuple[NormalizedSupplyClaim, ...]) -> bool:
    values: dict[str, set[str]] = {}
    for claim in claims:
        values.setdefault(claim.metric, set()).add(claim.amount)
    return any(len(items) > 1 for items in values.values())


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    raise ValueError("invalid timestamp")


def _required_time(value: object) -> datetime:
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError("required timestamp missing")
    return parsed


def _decimal_string(value: object) -> str:
    if value is None:
        raise ValueError("amount missing")
    return format(Decimal(str(value)), "f")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)))
