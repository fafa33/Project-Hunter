from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal

from hunter.historical_acquisition.providers import _closest_market_point
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.models import RawOnChainObservation


@dataclass(frozen=True)
class UsdValuation:
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: str | None = None
    chain_id: int | None = None
    asset_contract: str | None = None
    coingecko_id: str | None = None
    target_at: datetime | None = None
    effective_at: datetime | None = None
    retrieval_at: datetime | None = None
    price_usd: Decimal | None = None
    amount: Decimal | None = None
    value_usd: Decimal | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    freshness_seconds: int | None = None

    def __post_init__(self) -> None:
        for field in ("target_at", "effective_at", "retrieval_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, value.astimezone(UTC))


class HistoricalUsdValuator:
    def __init__(
        self,
        config: OnChainConfig,
        repository: HistoricalEvidenceRepository,
        *,
        max_price_age: timedelta = timedelta(hours=1),
    ) -> None:
        self.config = config
        self.repository = repository
        self.max_price_age = max_price_age

    def value(self, observation: RawOnChainObservation) -> UsdValuation:
        contract = (observation.asset_contract or "").lower()
        asset = next(
            (
                item
                for item in self.config.tracked_assets
                if item.chain_id == observation.chain_id
                and item.contract_address
                and item.contract_address.lower() == contract
                and item.coingecko_id
            ),
            None,
        )
        if asset is None or asset.coingecko_id is None:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="exact chain/contract USD provider mapping unavailable",
                chain_id=observation.chain_id,
                asset_contract=contract or None,
                target_at=observation.block_timestamp,
            )

        amount = observation.normalized_amount
        if amount is None or observation.decimals is None or observation.raw_amount is None:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="signed normalized amount unavailable",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
            )
        try:
            if not amount.is_finite() or amount <= 0:
                return UsdValuation(
                    status="UNAVAILABLE",
                    reason="signed normalized amount is not positive",
                    chain_id=observation.chain_id,
                    asset_contract=contract,
                    coingecko_id=asset.coingecko_id,
                    target_at=observation.block_timestamp,
                )
        except InvalidOperation:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="signed normalized amount is not a finite number",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
            )

        points: dict[datetime, dict[str, Any]] = {}
        provenance: dict[datetime, tuple[str, str, datetime]] = {}
        conflicting: set[datetime] = set()
        for row in sorted(
            self.repository.raw(), key=lambda item: (item.data_availability_timestamp, item.repository_id)
        ):
            if row.metric != "historical_market":
                continue
            if row.payload.get("coingecko_id") != asset.coingecko_id:
                continue
            price = _decimal_price(row.payload.get("price"))
            if price is None:
                continue
            effective = row.data_availability_timestamp.astimezone(UTC)
            if effective in points:
                if points[effective]["price"] != price:
                    conflicting.add(effective)
                else:
                    provenance[effective] = (row.repository_id, row.source_url, row.retrieval_timestamp)
                continue
            points[effective] = {"price": price}
            provenance[effective] = (row.repository_id, row.source_url, row.retrieval_timestamp)

        selected = _closest_market_point(points, observation.block_timestamp)
        if selected is None:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="causal at-or-before USD price point unavailable",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
            )
        effective_at, values = selected
        if effective_at in conflicting:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="conflicting historical USD price points at the selected availability time",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
                effective_at=effective_at,
            )
        freshness = observation.block_timestamp - effective_at
        if freshness > self.max_price_age:
            return UsdValuation(
                status="UNAVAILABLE",
                reason="historical USD price freshness limit exceeded",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
                effective_at=effective_at,
                freshness_seconds=int(freshness.total_seconds()),
            )

        price = values["price"]
        if not isinstance(price, Decimal):
            return UsdValuation(
                status="UNAVAILABLE",
                reason="historical USD price is not an exact decimal",
                chain_id=observation.chain_id,
                asset_contract=contract,
                coingecko_id=asset.coingecko_id,
                target_at=observation.block_timestamp,
                effective_at=effective_at,
            )
        precision = max(28, len(amount.as_tuple().digits) + len(price.as_tuple().digits) + 8)
        with localcontext() as context:
            context.prec = precision
            value_usd = amount * price
        source_record_id, source_url, retrieval_at = provenance[effective_at]
        return UsdValuation(
            status="AVAILABLE",
            chain_id=observation.chain_id,
            asset_contract=contract,
            coingecko_id=asset.coingecko_id,
            target_at=observation.block_timestamp,
            effective_at=effective_at,
            retrieval_at=retrieval_at,
            price_usd=price,
            amount=amount,
            value_usd=value_usd,
            source_record_id=source_record_id,
            source_url=source_url,
            freshness_seconds=int(freshness.total_seconds()),
        )


def _decimal_price(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not price.is_finite() or price <= 0:
        return None
    return price
