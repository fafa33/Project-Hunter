from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from hunter.value_capture.models import EconomicClaimIdentity, SupplyBasisSnapshot

NOW = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def snapshot() -> SupplyBasisSnapshot:
    return SupplyBasisSnapshot(
        record_id="supply-record-1",
        logical_id="supply-logical-1",
        schema_version="supply-value-capture-v3.5.0",
        semantic_version="1.0.0",
        identity=EconomicClaimIdentity(
            entity_id="api3-project",
            economic_claim_id="api3-token-claim",
            asset_id="api3-token",
            representation_id="api3-ethereum",
            token_id="api3",
            chain="ethereum",
            contract_address="0x0b38210ea11411557c13457d4da7dc6ea731b88a",
        ),
        supply_basis_type="circulating_supply",
        quantity="86000000",
        unit="native_units",
        denominator_meaning="Canonical circulating units.",
        supply_policy_id="canonical-token-supply-v1",
        supply_policy_version="1.0.0",
        quantity_components=(
            ("circulating_supply", "86000000"),
            ("total_supply", "100000000"),
            ("fully_diluted_supply", "115000000"),
        ),
        observed_market_fact_ids=("market-fact-api3-circulating",),
        observed_market_fact_versions=("observed-market-fact-v2",),
        source_record_id="official-api3-supply-disclosure",
        source_record_version="2026-07-24",
        confidence="0.9",
        uncertainty="0.1",
        effective_at=NOW,
        recorded_at=NOW,
        known_at=NOW,
        source_id="official-api3-tokenomics",
        parser_version="official-tokenomics-v1",
        evidence_record_ids=("fundamental-evidence-1",),
        raw_payload_hash="a" * 64,
        quality_state="accepted",
        conflict_state="none",
        acquisition_id="supply-acquisition-1",
    )


@pytest.mark.parametrize("value", ["", "   ", 1, None])
def test_supply_snapshot_rejects_invalid_observed_market_fact_ids(value: object) -> None:
    with pytest.raises(ValueError, match="observed_market_fact_ids must contain non-blank strings"):
        replace(snapshot(), observed_market_fact_ids=(value,))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "   ", 1, None])
def test_supply_snapshot_rejects_invalid_observed_market_fact_versions(value: object) -> None:
    with pytest.raises(ValueError, match="observed_market_fact_versions must contain non-blank strings"):
        replace(snapshot(), observed_market_fact_versions=(value,))  # type: ignore[arg-type]
