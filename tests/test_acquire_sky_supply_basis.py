from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import acquire_sky_supply_basis as script
import pytest
import yaml

from hunter.market_facts.registry import MarketFactSourceRegistry
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.providers import RegisteredValueCaptureProvider, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.registry import ValueCaptureSourceRegistry
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

# This script is intentionally Sky-specific (temporary one-shot escape hatch,
# not the generic entity-agnostic CLI), so the fixture below reproduces the
# real production Sky identity binding and value-capture source verbatim
# rather than depending on the PR #110 branch's config content being fetched
# into the local git state — that branch is not guaranteed to be a locally
# available ref during a normal CI checkout of this branch.
_MARKET_FACT_SOURCES_YAML = """
sources:
  - source_id: coingecko-coin-market-facts
    provider_id: coingecko
    endpoint_template: https://api.coingecko.com/api/v3/coins/{listing_id}
    allowed_hosts:
      - api.coingecko.com
    parser_version: coingecko-coin-v1
    enabled: true
    capabilities:
      - spot_price
      - circulating_supply
      - total_supply
      - max_supply
      - market_capitalization
      - fully_diluted_valuation
      - trading_volume
    quote_currencies:
      - usd
    units:
      spot_price: quote_currency_per_unit
      circulating_supply: native_units
      total_supply: native_units
      max_supply: native_units
      market_capitalization: quote_currency
      fully_diluted_valuation: quote_currency
      trading_volume: quote_currency_per_24h
    supported_entity_scope:
      - canonical_asset_representation
    identity_bindings:
      - entity_id: entity:sky
        asset_id: asset:sky
        representation_id: representation:sky-ethereum
        chain: ethereum
        contract_address: "0x56072c95faa701256059aa122697b133aded9279"
        provider_listing_id: sky
    freshness_seconds: 3600
    observation_confidence: "0.80"
    historical_support: current endpoint with provider last_updated timestamp only
    limitations: Provider-observed market facts only.
"""

_VALUE_CAPTURE_SOURCES_YAML = """
sources:
  - source_id: sky-protocol-tokenomics-disclosure
    authority_tier: official
    source_type: official_disclosure
    allowed_hosts:
      - developers.sky.money
    endpoint_patterns:
      - https://developers.sky.money/
    parser_version: official-tokenomics-v1
    capabilities:
      - evidence:official_disclosure
      - supply:circulating_supply
      - supply:total_supply
      - supply:burned_supply
      - rule:buyback_and_burn
    enabled: true
"""

SIGNING_KEY_ID = "test-sky-supply-basis-key-v1"
SIGNING_KEY_HEX = secrets.token_hex(32)


class StaticTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url: str, timeout_seconds: float) -> object:
        assert timeout_seconds > 0
        self.urls.append(url)
        return self.payload


def _application_root(tmp_path: Path) -> Path:
    root = tmp_path / "application-root"
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "market_fact_sources.yaml").write_text(_MARKET_FACT_SOURCES_YAML, encoding="utf-8")
    (configs / "value_capture_sources.yaml").write_text(_VALUE_CAPTURE_SOURCES_YAML, encoding="utf-8")
    return root


def test_fixture_configs_match_real_yaml_shape() -> None:
    """Guard against the fixture silently drifting from valid YAML."""
    market_facts = yaml.safe_load(_MARKET_FACT_SOURCES_YAML)
    value_capture = yaml.safe_load(_VALUE_CAPTURE_SOURCES_YAML)
    assert market_facts["sources"][0]["source_id"] == "coingecko-coin-market-facts"
    assert value_capture["sources"][0]["source_id"] == "sky-protocol-tokenomics-disclosure"


def _coingecko_payload(*, now: datetime) -> dict[str, object]:
    return {
        "id": "sky",
        "last_updated": now.isoformat(),
        "market_data": {
            "circulating_supply": 1_000_000_000,
            "total_supply": 1_200_000_000,
            "max_supply": None,
        },
    }


def _seed_evidence_and_rule(root: Path, *, now: datetime) -> None:
    """Seed the two prerequisite records this script requires to already exist,
    using the exact same real production services this script itself uses."""
    db_path = root / "data" / "data_ops.sqlite"
    vc_registry = ValueCaptureSourceRegistry.from_yaml(root / "configs" / "value_capture_sources.yaml")
    vc_repository = SupplyAndValueCaptureRepository(db_path)
    verification_keys = ValueCaptureVerificationKeyRegistry({SIGNING_KEY_ID: bytes.fromhex(SIGNING_KEY_HEX)})
    service = SupplyAndValueCaptureService(
        registry=vc_registry,
        repository=vc_repository,
        verification_keys=verification_keys,
    )
    source = vc_registry.require("sky-protocol-tokenomics-disclosure")
    provider = RegisteredValueCaptureProvider(
        source, signing_key_id=SIGNING_KEY_ID, signing_key=bytes.fromhex(SIGNING_KEY_HEX)
    )
    identity = EconomicClaimIdentity(
        entity_id="entity:sky",
        economic_claim_id="economic-claim:sky-smart-burn-engine",
        asset_id="asset:sky",
        representation_id="representation:sky-ethereum",
        token_id="sky",
        chain="ethereum",
        contract_address="0x56072c95faa701256059aa122697b133aded9279",
    )
    evidence_acq = provider.acquisition(
        kind="evidence",
        capability="evidence:official_disclosure",
        endpoint="https://developers.sky.money/core-protocol/smart-burn-engine/",
        acquisition_id="test-sky-evidence",
        acquired_at=now,
        identity=identity,
        payload={
            "evidence_type": "official_disclosure",
            "source_reference": "https://developers.sky.money/core-protocol/smart-burn-engine/",
            "extracted_claim": "Sky Protocol operates the Smart Burn Engine.",
            "accounting_period_start": (now - timedelta(days=30)).isoformat(),
            "accounting_period_end": now.isoformat(),
            "attribution_rule_id": "sky-smart-burn-engine-buyback-and-burn-v1",
            "source_methodology": "official-disclosure-manual-attestation-v1",
            "source_record_id": "developers.sky.money/core-protocol/smart-burn-engine",
            "source_record_version": now.date().isoformat(),
            "entity_link_confidence": "1",
            "evidence_confidence": "0.9",
            "uncertainty": "0.1",
            "effective_at": now.isoformat(),
            "quality_state": "accepted",
            "conflict_state": "none",
        },
    )
    evidence = service.ingest_evidence(provider, evidence_acq)

    rule_acq = provider.acquisition(
        kind="rule",
        capability="rule:buyback_and_burn",
        endpoint="https://developers.sky.money/core-protocol/smart-burn-engine/",
        acquisition_id="test-sky-rule",
        acquired_at=now + timedelta(minutes=1),
        identity=identity,
        payload={
            "rule_type": "buyback_and_burn",
            "entitlement_scope": "Protocol-directed buyback-and-burn of circulating SKY.",
            "beneficiary_scope": "All circulating SKY holders.",
            "source_economic_flow": "Protocol surplus (USDS).",
            "destination_economic_flow": "Permanent SKY token burn.",
            "trigger_condition": "Governance-ratified Smart Burn Engine parameters.",
            "distribution_formula": "Governance-set periodic USDS allocation converted to SKY buybacks.",
            "governance_or_contract_authority": "Ratified by Sky governance (Maker Core).",
            "mechanism_policy_id": "sky-smart-burn-engine-v1",
            "mechanism_policy_version": "1.0.0",
            "dilution_treatment": "Burned SKY permanently reduces circulating and total supply.",
            "claim_seniority": "No priority claim.",
            "applicability_start": now.isoformat(),
            "applicability_end": (now + timedelta(days=365)).isoformat(),
            "limitations": ["Rate is governance-directed and may change."],
            "evidence_record_ids": [evidence.record_id],
            "evidence_record_versions": [evidence.semantic_version],
            "source_record_id": "sky-governance-forum-smart-burn-engine-proposal",
            "source_record_version": now.date().isoformat(),
            "confidence": "0.9",
            "uncertainty": "0.1",
            "effective_at": now.isoformat(),
            "quality_state": "accepted",
            "conflict_state": "none",
        },
    )
    service.ingest_rule(provider, rule_acq)


def _set_env(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", SIGNING_KEY_ID)
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", SIGNING_KEY_HEX)


def test_signing_key_rejects_missing_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", raising=False)
    with pytest.raises(script.AcquisitionError, match="HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID"):
        script.signing_key()


def test_signing_key_rejects_invalid_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "k")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", "not-hex!!")
    with pytest.raises(script.AcquisitionError, match="hex-encoded"):
        script.signing_key()


def test_signing_key_rejects_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", "k")
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", "aa" * 8)
    with pytest.raises(script.AcquisitionError, match="at least 32 bytes"):
        script.signing_key()


def test_signing_key_accepts_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", SIGNING_KEY_ID)
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", SIGNING_KEY_HEX)
    key_id, key = script.signing_key()
    assert key_id == SIGNING_KEY_ID
    assert key == bytes.fromhex(SIGNING_KEY_HEX)


def test_application_root_requires_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", "relative/path")
    with pytest.raises(script.AcquisitionError, match="absolute"):
        script.application_root()


def test_acquire_market_facts_uses_real_success_literal(tmp_path: Path) -> None:
    # This test would have failed against the original workflow's `status != "ok"`
    # check: the provider's real successful status is "success", never "ok".
    root = _application_root(tmp_path)
    registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    now = datetime.now(UTC)
    transport = StaticTransport(_coingecko_payload(now=now))

    records = script.acquire_market_facts(registry=registry, repository=repository, now=now, transport=transport)

    fact_types = {record.fact_type for record in records}
    assert fact_types == {"circulating_supply", "total_supply"}
    assert transport.urls == ["https://api.coingecko.com/api/v3/coins/sky"]


def test_acquire_market_facts_raises_on_non_success_status(tmp_path: Path) -> None:
    root = _application_root(tmp_path)
    registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    now = datetime.now(UTC)
    transport = StaticTransport({"id": "sky"})  # malformed: no market_data

    with pytest.raises(script.AcquisitionError, match="did not succeed"):
        script.acquire_market_facts(registry=registry, repository=repository, now=now, transport=transport)


def test_required_evidence_record_id_raises_when_absent(tmp_path: Path) -> None:
    root = _application_root(tmp_path)
    repository = SupplyAndValueCaptureRepository(root / "data" / "data_ops.sqlite")
    with pytest.raises(script.AcquisitionError, match="FundamentalEvidenceRecord"):
        script.required_evidence_record_id(repository, now=datetime.now(UTC))


def test_require_rule_precondition_raises_when_absent(tmp_path: Path) -> None:
    root = _application_root(tmp_path)
    repository = SupplyAndValueCaptureRepository(root / "data" / "data_ops.sqlite")
    with pytest.raises(script.AcquisitionError, match="ValueCaptureRuleSnapshot"):
        script.require_rule_precondition(repository, now=datetime.now(UTC))


def test_run_end_to_end_persists_and_links_supply_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    root = _application_root(tmp_path)
    _set_env(monkeypatch, root)
    _seed_evidence_and_rule(root, now=now)
    transport = StaticTransport(_coingecko_payload(now=now))

    record_id = script.run(root=root, transport=transport, now=now + timedelta(minutes=5))

    assert record_id is not None
    repository = SupplyAndValueCaptureRepository(root / "data" / "data_ops.sqlite")
    persisted = repository.supply(record_id)
    assert persisted is not None
    assert persisted.identity.entity_id == "entity:sky"
    assert persisted.identity.representation_id == "representation:sky-ethereum"
    assert persisted.quality_state == "accepted"
    assert persisted.conflict_state in {"none", "resolved"}

    # Deterministic cross-link check: entity/representation, evidence linkage,
    # observed-market-fact linkage, and valid/recorded/known time ordering.
    evidence = repository.strict_known_evidence(
        entity_id="entity:sky",
        economic_claim_id="economic-claim:sky-smart-burn-engine",
        representation_id="representation:sky-ethereum",
        effective_as_of=now + timedelta(days=1),
        known_by=now + timedelta(days=1),
        evidence_type="official_disclosure",
    )
    assert evidence is not None
    assert evidence.record_id in persisted.evidence_record_ids
    assert persisted.effective_at <= persisted.recorded_at <= persisted.known_at

    market_fact_repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    for fact_id, fact_version in zip(
        persisted.observed_market_fact_ids, persisted.observed_market_fact_versions, strict=True
    ):
        fact = market_fact_repository.record(fact_id)
        assert fact is not None
        assert fact.semantic_version == fact_version
        assert fact.identity.entity_id == persisted.identity.entity_id
        assert fact.identity.representation_id == persisted.identity.representation_id
        assert fact.effective_at <= persisted.effective_at
        assert fact.recorded_at <= persisted.recorded_at
        assert fact.known_at <= persisted.known_at


def test_run_is_idempotent_on_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    root = _application_root(tmp_path)
    _set_env(monkeypatch, root)
    _seed_evidence_and_rule(root, now=now)
    transport = StaticTransport(_coingecko_payload(now=now))

    first = script.run(root=root, transport=transport, now=now + timedelta(minutes=5))
    assert first is not None

    before_counts = script.DatabaseRowCounts.capture(root / "data" / "data_ops.sqlite")
    second = script.run(root=root, transport=transport, now=now + timedelta(minutes=10))
    after_counts = script.DatabaseRowCounts.capture(root / "data" / "data_ops.sqlite")

    assert second is None
    assert before_counts.counts == after_counts.counts


def test_database_row_counts_detects_unexpected_record_type_changes() -> None:
    before = script.DatabaseRowCounts(counts={"snapshot": 5, "automation-job": 10, "automation-run": 20})
    after_clean = script.DatabaseRowCounts(counts={"snapshot": 9, "automation-job": 10, "automation-run": 20})
    after_polluted = script.DatabaseRowCounts(counts={"snapshot": 9, "automation-job": 13, "automation-run": 20})

    assert before.unexpected_changes(after_clean) == {}
    assert before.unexpected_changes(after_polluted) == {"automation-job": (10, 13)}


def test_build_supply_payload_references_exactly_the_returned_market_facts(tmp_path: Path) -> None:
    root = _application_root(tmp_path)
    registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    now = datetime.now(UTC)
    transport = StaticTransport(_coingecko_payload(now=now))
    records = script.acquire_market_facts(registry=registry, repository=repository, now=now, transport=transport)

    payload = script.build_supply_payload(records=records, evidence_record_id="evidence-record-id", now=now)

    assert payload["supply_basis_type"] == "circulating_supply"
    assert payload["evidence_record_ids"] == ["evidence-record-id"]
    assert set(payload["observed_market_fact_ids"]) == {record.record_id for record in records}
    assert len(payload["observed_market_fact_ids"]) == len(payload["observed_market_fact_versions"])
    component_types = {component[0] for component in payload["quantity_components"]}
    assert component_types == {"circulating_supply", "total_supply"}


def test_build_supply_payload_omits_fully_diluted_supply_when_max_supply_is_below_total_supply(
    tmp_path: Path,
) -> None:
    """Reproduces real CoinGecko data observed for Sky: max_supply reported
    fractionally below total_supply for the same snapshot. Passing both
    through unchanged would violate SupplyBasisSnapshot's canonical
    total<=fully_diluted invariant, so fully_diluted_supply must be omitted
    rather than fabricating or rounding either value."""
    root = _application_root(tmp_path)
    registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    now = datetime.now(UTC)
    payload_source = {
        "id": "sky",
        "last_updated": now.isoformat(),
        "market_data": {
            "circulating_supply": 23355983976.864086,
            "total_supply": 23462665147.36596,
            "max_supply": 23462665147.3,
        },
    }
    transport = StaticTransport(payload_source)
    records = script.acquire_market_facts(registry=registry, repository=repository, now=now, transport=transport)

    payload = script.build_supply_payload(records=records, evidence_record_id="evidence-record-id", now=now)

    component_types = {component[0] for component in payload["quantity_components"]}
    assert component_types == {"circulating_supply", "total_supply"}
    assert "fully_diluted_supply" not in component_types


def test_build_supply_payload_includes_fully_diluted_supply_when_max_supply_meets_total_supply(
    tmp_path: Path,
) -> None:
    root = _application_root(tmp_path)
    registry = MarketFactSourceRegistry.from_file(root / "configs" / "market_fact_sources.yaml")
    repository = script.ObservedMarketFactRepository(root / "data" / "data_ops.sqlite")
    now = datetime.now(UTC)
    payload_source = {
        "id": "sky",
        "last_updated": now.isoformat(),
        "market_data": {
            "circulating_supply": 1_000_000_000,
            "total_supply": 1_200_000_000,
            "max_supply": 1_200_000_000,
        },
    }
    transport = StaticTransport(payload_source)
    records = script.acquire_market_facts(registry=registry, repository=repository, now=now, transport=transport)

    payload = script.build_supply_payload(records=records, evidence_record_id="evidence-record-id", now=now)

    component_types = {component[0] for component in payload["quantity_components"]}
    assert component_types == {"circulating_supply", "total_supply", "fully_diluted_supply"}
    fully_diluted = next(v for t, v in payload["quantity_components"] if t == "fully_diluted_supply")
    assert fully_diluted == "1200000000"
