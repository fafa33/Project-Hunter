from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hunter.market_facts.models import MarketFactIdentity, MarketFactRequest, ObservedMarketFactRecord
from hunter.market_facts.providers import CoinGeckoObservedMarketFactProvider, JsonTransport
from hunter.market_facts.registry import MarketFactSourceRegistry
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.value_capture.models import EconomicClaimIdentity, SupplyBasisSnapshot
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

_ENTITY_ID = "entity:sky"
_ECONOMIC_CLAIM_ID = "economic-claim:sky-smart-burn-engine"
_ASSET_ID = "asset:sky"
_REPRESENTATION_ID = "representation:sky-ethereum"
_TOKEN_ID = "sky"
_CHAIN = "ethereum"
_CONTRACT_ADDRESS = "0x56072c95faa701256059aa122697b133aded9279"
_PROVIDER_LISTING_ID = "sky"

_MARKET_FACT_SOURCE_ID = "coingecko-coin-market-facts"
_MARKET_FACT_PROVIDER_ID = "coingecko"
_MARKET_FACT_TYPES = ("circulating_supply", "total_supply", "max_supply")

_VALUE_CAPTURE_SOURCE_ID = "sky-protocol-tokenomics-disclosure"
_VALUE_CAPTURE_ENDPOINT = "https://developers.sky.money/core-protocol/smart-burn-engine/"

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_SIGNING_KEY_ID_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID"
_SIGNING_KEY_ENV = "HUNTER_VALUE_CAPTURE_SIGNING_KEY"

# Row types this script is authorized to change. Any other record_type
# changing between the "before" and "after" snapshot indicates an unrelated
# code path wrote to the canonical database and must fail the run rather than
# be committed.
_ALLOWED_CHANGED_RECORD_TYPES = frozenset({"snapshot"})


class AcquisitionError(RuntimeError):
    """Raised for any expected, actionable failure in this script."""


@dataclass(frozen=True)
class DatabaseRowCounts:
    counts: dict[str, int]

    @classmethod
    def capture(cls, db_path: Path) -> DatabaseRowCounts:
        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                "SELECT record_type, COUNT(*) FROM persistence_records GROUP BY record_type"
            ).fetchall()
        finally:
            connection.close()
        return cls(counts=dict(rows))

    def unexpected_changes(self, after: DatabaseRowCounts) -> dict[str, tuple[int, int]]:
        changed: dict[str, tuple[int, int]] = {}
        for record_type in sorted(set(self.counts) | set(after.counts)):
            if record_type in _ALLOWED_CHANGED_RECORD_TYPES:
                continue
            before_count = self.counts.get(record_type, 0)
            after_count = after.counts.get(record_type, 0)
            if before_count != after_count:
                changed[record_type] = (before_count, after_count)
        return changed


def application_root() -> Path:
    configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
    if not configured:
        raise AcquisitionError(f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root")
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise AcquisitionError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return root.resolve()


def signing_key() -> tuple[str, bytes]:
    key_id = os.environ.get(_SIGNING_KEY_ID_ENV, "").strip()
    key_hex = os.environ.get(_SIGNING_KEY_ENV, "").strip()
    if not key_id:
        raise AcquisitionError(f"{_SIGNING_KEY_ID_ENV} must identify the value-capture provider signing key")
    if not key_hex:
        raise AcquisitionError(f"{_SIGNING_KEY_ENV} must supply the value-capture provider signing key as hex")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise AcquisitionError(f"{_SIGNING_KEY_ENV} must be a hex-encoded byte string") from exc
    if len(key) < 32:
        raise AcquisitionError(f"{_SIGNING_KEY_ENV} must decode to at least 32 bytes (got {len(key)})")
    return key_id, key


def _market_fact_identity() -> MarketFactIdentity:
    return MarketFactIdentity(
        entity_id=_ENTITY_ID,
        asset_id=_ASSET_ID,
        representation_id=_REPRESENTATION_ID,
        chain=_CHAIN,
        contract_address=_CONTRACT_ADDRESS,
        provider_listing_id=_PROVIDER_LISTING_ID,
    )


def _value_capture_identity() -> EconomicClaimIdentity:
    return EconomicClaimIdentity(
        entity_id=_ENTITY_ID,
        economic_claim_id=_ECONOMIC_CLAIM_ID,
        asset_id=_ASSET_ID,
        representation_id=_REPRESENTATION_ID,
        token_id=_TOKEN_ID,
        chain=_CHAIN,
        contract_address=_CONTRACT_ADDRESS,
    )


def existing_supply_snapshot(
    repository: SupplyAndValueCaptureRepository, *, now: datetime
) -> SupplyBasisSnapshot | None:
    """Canonical idempotency check: use the repository's own strict-known lookup,
    never a hand-rolled reimplementation of its logical-ID hash."""
    return repository.strict_known_supply(
        entity_id=_ENTITY_ID,
        economic_claim_id=_ECONOMIC_CLAIM_ID,
        representation_id=_REPRESENTATION_ID,
        effective_as_of=now,
        known_by=now,
        supply_basis_type="circulating_supply",
    )


def required_evidence_record_id(repository: SupplyAndValueCaptureRepository, *, now: datetime) -> str:
    evidence = repository.strict_known_evidence(
        entity_id=_ENTITY_ID,
        economic_claim_id=_ECONOMIC_CLAIM_ID,
        representation_id=_REPRESENTATION_ID,
        effective_as_of=now,
        known_by=now,
        evidence_type="official_disclosure",
    )
    if evidence is None:
        raise AcquisitionError(
            "no accepted Sky FundamentalEvidenceRecord found via strict_known_evidence — "
            "Milestone 1's evidence leg must already be persisted before this script runs"
        )
    return evidence.record_id


def require_rule_precondition(repository: SupplyAndValueCaptureRepository, *, now: datetime) -> None:
    rule = repository.strict_known_rule(
        entity_id=_ENTITY_ID,
        economic_claim_id=_ECONOMIC_CLAIM_ID,
        representation_id=_REPRESENTATION_ID,
        effective_as_of=now,
        known_by=now,
        rule_type="buyback_and_burn",
    )
    if rule is None:
        raise AcquisitionError(
            "no accepted Sky ValueCaptureRuleSnapshot found via strict_known_rule — "
            "Milestone 1's rule leg must already be persisted before this script runs"
        )


def acquire_market_facts(
    *,
    registry: MarketFactSourceRegistry,
    repository: ObservedMarketFactRepository,
    now: datetime,
    transport: JsonTransport | None,
) -> tuple[ObservedMarketFactRecord, ...]:
    # requested_at must precede the provider's own acquisition clock (checked
    # by the service as result.acquired_at/known_at >= request.requested_at),
    # so it is anchored a safety margin before `now` rather than at `now`
    # itself, which could otherwise race a fast in-process provider call.
    request = MarketFactRequest(
        source_id=_MARKET_FACT_SOURCE_ID,
        provider_id=_MARKET_FACT_PROVIDER_ID,
        identity=_market_fact_identity(),
        quote_currency="usd",
        requested_fact_types=_MARKET_FACT_TYPES,
        requested_at=now - timedelta(minutes=5),
    )
    provider = (
        CoinGeckoObservedMarketFactProvider(registry, transport=transport)
        if transport is not None
        else CoinGeckoObservedMarketFactProvider(registry)
    )
    result = provider.collect(request)
    if result.status != "success":
        raise AcquisitionError(
            f"market fact acquisition did not succeed: status={result.status!r} failure_reason={result.failure_reason!r}"
        )
    service = ObservedMarketFactService(repository, registry)
    # recorded_at must not precede the provider's own known_at; the provider's
    # real acquisition clock is authoritative here, not the caller's `now`.
    records = service.ingest(request, result, recorded_at=result.known_at)
    if not records:
        raise AcquisitionError("market fact acquisition returned a success status but produced no records")
    return records


def build_supply_payload(
    *,
    records: tuple[ObservedMarketFactRecord, ...],
    evidence_record_id: str,
    now: datetime,
) -> dict[str, Any]:
    by_type = {record.fact_type: record for record in records}
    circulating = by_type.get("circulating_supply")
    if circulating is None:
        raise AcquisitionError("circulating_supply was not returned by market fact acquisition")

    quantity_components = [["circulating_supply", circulating.value]]
    total = by_type.get("total_supply")
    if total is not None:
        quantity_components.append(["total_supply", total.value])
    max_supply = by_type.get("max_supply")
    if max_supply is not None:
        # Always persist the observed max_supply as fully_diluted_supply, verbatim, even when it
        # is fractionally below total_supply (observed for Sky: a provider rounding/timing
        # artifact, not a real economic distinction). SupplyBasisSnapshot's own coherence check
        # now absorbs this case itself (persisting as accepted within a small precision tolerance,
        # or flagging conflict_state="open" for a larger gap) rather than this script silently
        # deciding whether to include the value.
        quantity_components.append(["fully_diluted_supply", max_supply.value])

    return {
        "supply_basis_type": "circulating_supply",
        "quantity": circulating.value,
        "unit": "native_units",
        "denominator_meaning": (
            "Provider-observed circulating SKY units (CoinGecko), cross-referenced with"
            " Sky Protocol's official tokenomics disclosure."
        ),
        "supply_policy_id": "canonical-token-supply-v1",
        "supply_policy_version": "1.0.0",
        "quantity_components": quantity_components,
        "observed_market_fact_ids": [record.record_id for record in records],
        "observed_market_fact_versions": [record.semantic_version for record in records],
        "source_record_id": f"sky-tokenomics-disclosure-supply-{now.date().isoformat()}",
        "source_record_version": now.date().isoformat(),
        "confidence": "0.80",
        "uncertainty": "0.20",
        "effective_at": now,
        "evidence_record_ids": [evidence_record_id],
        "quality_state": "accepted",
        "conflict_state": "none",
    }


def run(
    *,
    root: Path,
    transport: JsonTransport | None = None,
    now: datetime | None = None,
) -> str | None:
    """Run the full Sky SupplyBasisSnapshot acquisition.

    Returns the persisted record_id, or None if an accepted SupplyBasisSnapshot
    already existed and no new acquisition was performed (idempotent no-op).
    """
    now = now or datetime.now(UTC)
    db_path = root / "data" / "data_ops.sqlite"
    key_id, key = signing_key()

    mf_registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    mf_repository = ObservedMarketFactRepository(db_path)
    vc_registry = ValueCaptureSourceRegistry.from_yaml(root / "configs" / "value_capture_sources.yaml")
    vc_repository = SupplyAndValueCaptureRepository(db_path)

    existing = existing_supply_snapshot(vc_repository, now=now)
    if existing is not None:
        print(f"SupplyBasisSnapshot already exists (record_id={existing.record_id}) — idempotent no-op.")
        return None

    require_rule_precondition(vc_repository, now=now)
    evidence_record_id = required_evidence_record_id(vc_repository, now=now)

    before = DatabaseRowCounts.capture(db_path)

    market_fact_records = acquire_market_facts(
        registry=mf_registry, repository=mf_repository, now=now, transport=transport
    )
    for record in market_fact_records:
        print(f"  market fact: {record.fact_type}={record.value} {record.unit} [{record.record_id}]")

    # The supply-basis snapshot's timestamp must not precede any market fact
    # it references (checked by _require_observed_market_facts as
    # effective_at/recorded_at/known_at ordering). Market facts are recorded
    # against the provider's own real acquisition clock (see
    # acquire_market_facts), which can run later than the `now` captured at
    # the top of this function, so the supply timestamp is anchored to
    # whichever is later.
    supply_acquired_at = max(now, *(record.known_at for record in market_fact_records))

    verification_keys = ValueCaptureVerificationKeyRegistry({key_id: key})
    vc_service = SupplyAndValueCaptureService(
        registry=vc_registry,
        repository=vc_repository,
        verification_keys=verification_keys,
        market_fact_repository=mf_repository,
    )
    source = vc_registry.require(_VALUE_CAPTURE_SOURCE_ID)
    provider = RegisteredValueCaptureProvider(source, signing_key_id=key_id, signing_key=key)
    payload = build_supply_payload(
        records=market_fact_records, evidence_record_id=evidence_record_id, now=supply_acquired_at
    )
    acquisition_id = f"sky-supply-basis-milestone-1-{supply_acquired_at.strftime('%Y%m%dT%H%M%SZ')}"
    acquisition = provider.acquisition(
        kind="supply",
        capability="supply:circulating_supply",
        endpoint=_VALUE_CAPTURE_ENDPOINT,
        acquisition_id=acquisition_id,
        acquired_at=supply_acquired_at,
        identity=_value_capture_identity(),
        payload=payload,
    )
    record = vc_service.ingest_supply(provider, acquisition)

    after = DatabaseRowCounts.capture(db_path)
    unexpected = before.unexpected_changes(after)
    if unexpected:
        raise AcquisitionError(
            "database diff touched record types outside this script's authorized scope"
            f" (record_type: (before, after)): {unexpected}"
        )

    print(f"SupplyBasisSnapshot persisted: record_id={record.record_id} logical_id={record.logical_id}")
    print(f"  quality_state={record.quality_state} conflict_state={record.conflict_state}")
    return record.record_id


def main(argv: list[str]) -> int:
    del argv
    try:
        result = run(root=application_root())
    except AcquisitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("No new SupplyBasisSnapshot was required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
