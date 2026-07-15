from __future__ import annotations

import sqlite3
import urllib.error
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hunter.tokenomics import (
    EvmErc20SupplyAdapter,
    OfficialTokenomicsDisclosureAdapter,
    PublicTokenomicsAggregatorAdapter,
    TokenomicsIngestionResult,
    TokenomicsIngestionService,
    TokenomicsIntegrityError,
    TokenomicsProviderRequest,
    TokenomicsRepository,
    TokenomicsSourceConfig,
    TokenomicsSourceRegistry,
)
from hunter.tokenomics.models import TokenomicsEvidenceClaim

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_official_provider_uses_registry_endpoint_and_validates_source_shape() -> None:
    http = _Http(_official_payload())
    result = OfficialTokenomicsDisclosureAdapter(_registry(), http).collect(_request(source_uri="https://evil.test/x"))

    assert http.calls == [("https://official.example.test/hunter-tokenomics.json", 10.0)]
    assert result.status == "success"
    assert result.registry_approved is True
    assert result.source_config_id == "official:hunter"
    assert result.request.source_uri == "https://official.example.test/hunter-tokenomics.json"
    assert result.artifacts[0].source_authority == "authoritative"
    assert result.artifacts[0].parser_version == "official-tokenomics-disclosure-v1"


def test_public_aggregator_uses_registry_endpoint_and_keeps_secondary_supply_only() -> None:
    http = _Http(_coingecko_payload())
    result = PublicTokenomicsAggregatorAdapter(_registry(), http).collect(
        _request(asset_id="asset:usdc:ethereum", source_uri="https://manual.test/payload.json")
    )

    assert http.calls == [
        (
            "https://api.coingecko.com/api/v3/coins/usd-coin?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false",
            10.0,
        )
    ]
    assert result.status == "success"
    assert result.registry_approved is True
    assert result.artifacts[0].source_authority == "secondary"
    assert [(claim.metric, claim.amount) for claim in result.supply_claims] == [
        ("circulating_supply", "120000000"),
        ("total_supply", "120100000"),
        ("max_supply", "120100000"),
    ]
    assert result.allocations == ()
    assert result.vesting_schedules == ()


def test_provider_statuses_are_explicit_without_accepting_unregistered_payloads() -> None:
    cases = {
        "malformed": ["not", "an", "object"],
        "partial": _official_payload(supply_only=True),
        "conflicting": _official_payload(total=["1000", "1200"]),
        "unsupported": {"status": "unsupported", "reason": "asset unsupported"},
    }
    for expected, payload in cases.items():
        result = OfficialTokenomicsDisclosureAdapter(_registry(), _Http(payload)).collect(_request())
        assert result.status == expected
        assert result.request.source_uri == "https://official.example.test/hunter-tokenomics.json"

    unavailable = OfficialTokenomicsDisclosureAdapter(_registry(), _Http({"status": "unavailable"})).collect(_request())
    assert unavailable.status == "unavailable"
    assert unavailable.supply_claims == ()

    rate_limited = OfficialTokenomicsDisclosureAdapter(
        _registry(), _Http(urllib.error.HTTPError("", 429, "rate", {}, None))
    ).collect(_request())
    assert rate_limited.status == "rate_limited"

    forbidden = OfficialTokenomicsDisclosureAdapter(
        _registry(), _Http(urllib.error.HTTPError("", 403, "forbidden", {}, None))
    ).collect(_request())
    assert forbidden.status == "unavailable"
    assert forbidden.failure_reason == "http_403"

    changed_schema = OfficialTokenomicsDisclosureAdapter(
        _registry(), _Http(_official_payload(schema_version="tokenomics-v9"))
    ).collect(_request())
    assert changed_schema.status == "malformed"


def test_unsupported_asset_and_unapproved_host_are_rejected_before_canonical_evidence(tmp_path: Path) -> None:
    registry = _registry()
    unsupported = OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload())).collect(
        _request(asset_id="asset:unknown")
    )
    assert unsupported.status == "unsupported"
    assert unsupported.artifacts == ()
    assert unsupported.registry_approved is False

    with pytest.raises(ValueError, match="host"):
        TokenomicsSourceConfig(
            source_id="bad:host",
            provider_id="official-tokenomics-disclosure",
            asset_id="asset:bad",
            candidate_id="bad",
            symbol="BAD",
            name="Bad",
            chain="ethereum",
            contract_address="",
            decimals=18,
            source_type="official_project_controlled",
            endpoint="https://evil.example.test/tokenomics.json",
            allowed_hosts=("official.example.test",),
            response_format="hunter-tokenomics-disclosure-v1",
            parser_version="official-tokenomics-disclosure-v1",
            authority_tier="authoritative",
            enabled=True,
            capabilities=("supply",),
            historical_depth="none",
            freshness="none",
            source_limitations="invalid",
        )

    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    summary = TokenomicsIngestionService(repo, registry).ingest(unsupported)
    assert summary.artifact_count == 0
    assert repo.count("tokenomics_evidence_artifacts") == 0
    assert repo.count("tokenomics_acquisition_outcomes") == 1


def test_caller_supplied_payload_cannot_become_production_canonical_evidence(tmp_path: Path) -> None:
    registry = _registry()
    forged = TokenomicsIngestionResult(
        provider=OfficialTokenomicsDisclosureAdapter.descriptor,
        request=_request(source_uri="https://manual.test/tokenomics.json"),
        status="success",
        artifacts=OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload()))
        .collect(_request())
        .artifacts,
        source_config_id="official:hunter",
        registry_approved=True,
    )

    with pytest.raises(TokenomicsIntegrityError):
        TokenomicsIngestionService(TokenomicsRepository(tmp_path / "tokenomics.sqlite"), registry).ingest(forged)


def test_ingestion_rejects_forged_registry_mismatches_before_persistence(tmp_path: Path) -> None:
    registry = _registry()
    valid = OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload())).collect(_request())
    cases = (
        replace(valid, provider=PublicTokenomicsAggregatorAdapter.descriptor),
        replace(valid, request=replace(valid.request, asset_id="asset:other")),
        replace(valid, source_config_id="official:other"),
        replace(valid, artifacts=(replace(valid.artifacts[0], source_authority="secondary"),)),
        replace(valid, artifacts=(replace(valid.artifacts[0], parser_version="parser-v9"),)),
    )
    for index, forged in enumerate(cases):
        repo = TokenomicsRepository(tmp_path / f"forged-{index}.sqlite")
        with pytest.raises(TokenomicsIntegrityError):
            TokenomicsIngestionService(repo, registry).ingest(forged)
        assert repo.count("tokenomics_evidence_artifacts") == 0
        assert repo.count("tokenomics_evidence_claims") == 0

    no_supply = replace(_source("official-tokenomics-disclosure", "asset:hunter"), capabilities=("allocation",))
    repo = TokenomicsRepository(tmp_path / "forged-capability.sqlite")
    with pytest.raises(TokenomicsIntegrityError, match="capability"):
        TokenomicsIngestionService(repo, TokenomicsSourceRegistry((no_supply,))).ingest(valid)
    assert repo.count("tokenomics_evidence_artifacts") == 0


def test_mocked_evm_rpc_uses_registry_endpoint_and_does_not_infer_zeroes() -> None:
    request = _request(asset_id="asset:usdc:ethereum", source_uri="https://manual.test/rpc")
    success = EvmErc20SupplyAdapter(_registry(), _Rpc({"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000)})).collect(
        request
    )
    assert success.status == "success"
    assert success.request.source_uri == "https://ethereum.publicnode.com"
    assert [(claim.metric, claim.amount) for claim in success.supply_claims] == [("total_supply", "1000000")]

    failure = EvmErc20SupplyAdapter(_registry(), _Rpc({"error": {"code": -32000, "message": "rate"}})).collect(request)
    assert failure.status == "unavailable"
    assert failure.supply_claims == ()

    unsupported = EvmErc20SupplyAdapter(_registry(), _Rpc({})).collect(
        _request(chain="solana", contract_address="So111")
    )
    assert unsupported.status == "unsupported"
    assert unsupported.supply_claims == ()


def test_supply_providers_do_not_request_when_supply_capability_is_missing() -> None:
    aggregator_source = replace(_source("public-tokenomics-aggregator", "asset:usdc:ethereum"), capabilities=())
    http = _Http(_coingecko_payload())
    aggregator = PublicTokenomicsAggregatorAdapter(TokenomicsSourceRegistry((aggregator_source,)), http).collect(
        _request(asset_id="asset:usdc:ethereum")
    )
    assert aggregator.status == "unsupported"
    assert aggregator.failure_reason == "capability_not_registered"
    assert aggregator.supply_claims == ()
    assert http.calls == []

    rpc_source = replace(_source("evm-erc20-public-rpc", "asset:usdc:ethereum"), capabilities=())
    rpc = _Rpc({"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000)})
    evm = EvmErc20SupplyAdapter(TokenomicsSourceRegistry((rpc_source,)), rpc).collect(
        _request(asset_id="asset:usdc:ethereum")
    )
    assert evm.status == "unsupported"
    assert evm.failure_reason == "capability_not_registered"
    assert evm.supply_claims == ()
    assert rpc.calls == []


def test_supply_providers_reject_disabled_unregistered_and_mismatched_sources_before_request() -> None:
    disabled = replace(_source("public-tokenomics-aggregator", "asset:usdc:ethereum"), enabled=False)
    http = _Http(_coingecko_payload())
    result = PublicTokenomicsAggregatorAdapter(TokenomicsSourceRegistry((disabled,)), http).collect(
        _request(asset_id="asset:usdc:ethereum")
    )
    assert result.status == "unsupported"
    assert http.calls == []

    wrong_type = replace(_source("evm-erc20-public-rpc", "asset:usdc:ethereum"), source_type="public_aggregator")
    rpc = _Rpc({"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000)})
    evm = EvmErc20SupplyAdapter(TokenomicsSourceRegistry((wrong_type,)), rpc).collect(
        _request(asset_id="asset:usdc:ethereum")
    )
    assert evm.status == "unsupported"
    assert rpc.calls == []


def test_ingestion_persists_supply_allocation_vesting_unlock_lineage_idempotently(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    service = TokenomicsIngestionService(repo, registry)
    result = OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload())).collect(_request())

    first = service.ingest(result)
    second = service.ingest(result)

    assert first == second
    assert repo.count("tokenomics_acquisition_attempts") == 1
    assert repo.count("tokenomics_acquisition_outcomes") == 1
    assert repo.count("tokenomics_evidence_artifacts") == 1
    assert repo.count("tokenomics_evidence_claims") == 4
    assert repo.count("tokenomics_supply_observations") == 2
    assert repo.count("tokenomics_allocation_definitions") == 1
    assert repo.count("tokenomics_vesting_schedules") == 1
    assert repo.count("tokenomics_vesting_schedule_segments") == 4
    assert repo.count("tokenomics_unlock_events") == 2
    assert repo.count("tokenomics_claim_artifact_links") == 4


def test_divergent_duplicate_immutable_phase_b_claim_is_rejected(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    TokenomicsIngestionService(repo, registry).ingest(
        OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload())).collect(_request())
    )
    claim = repo.evidence_claims(asset_id="asset:hunter", predicate="total_supply")[0]
    original = TokenomicsEvidenceClaim(
        claim_id=str(claim["claim_id"]),
        asset_id=str(claim["asset_id"]),
        subject=str(claim["subject"]),
        predicate=str(claim["predicate"]),
        value=str(claim["value"]),
        unit=str(claim["unit"]),
        evidence_status="active",
        confidence_state="high",
        effective_at=datetime.fromisoformat(str(claim["effective_at"])),
        recorded_at=datetime.fromisoformat(str(claim["recorded_at"])),
    )

    with pytest.raises(TokenomicsIntegrityError):
        repo.save_evidence_claim(replace(original, value="999999999"))

    assert repo.evidence_claims(asset_id="asset:hunter", predicate="total_supply")[0]["value"] == "1000000"


def test_authoritative_and_secondary_sources_keep_distinct_registry_provenance(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    service = TokenomicsIngestionService(repo, registry)
    service.ingest(OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload())).collect(_request()))
    service.ingest(
        PublicTokenomicsAggregatorAdapter(registry, _Http(_coingecko_payload())).collect(
            _request(asset_id="asset:usdc:ethereum", requested_at=NOW + timedelta(hours=1))
        )
    )

    artifacts = _rows(repo.path, "tokenomics_evidence_artifacts")
    assert sorted((row["source_authority"], row["parser_version"]) for row in artifacts) == [
        ("authoritative", "official-tokenomics-disclosure-v1"),
        ("secondary", "public-tokenomics-aggregator-v1"),
    ]


def test_conflicting_supply_definitions_coexist_and_create_conflict_records(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    result = OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload(total=["1000", "1200"]))).collect(
        _request()
    )

    summary = TokenomicsIngestionService(repo, registry).ingest(result)

    assert result.status == "conflicting"
    assert summary.conflict_count == 1
    assert [row["value"] for row in repo.evidence_claims(asset_id="asset:hunter", predicate="total_supply")] == [
        "1000",
        "1200",
    ]
    assert repo.count("tokenomics_observation_conflicts") == 1
    assert repo.count("tokenomics_conflict_members") == 2
    assert repo.count("tokenomics_supply_definition_reconciliations") == 3


def test_missing_schedule_evidence_is_not_normalized_as_no_unlock(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    result = OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload(supply_only=True))).collect(
        _request()
    )
    summary = TokenomicsIngestionService(repo, registry).ingest(result)

    assert result.status == "partial"
    assert summary.unlock_event_count == 0
    assert repo.count("tokenomics_unlock_events") == 0
    assert repo.count("tokenomics_vesting_schedules") == 0
    assert repo.count("tokenomics_acquisition_outcomes") == 1


def test_known_by_hunter_excludes_later_acquired_evidence_and_reconstruction_is_labeled(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    service = TokenomicsIngestionService(repo, registry)
    service.ingest(
        OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload(total="1000"))).collect(
            _request(requested_at=NOW)
        )
    )
    service.ingest(
        OfficialTokenomicsDisclosureAdapter(registry, _Http(_official_payload(total="1200"))).collect(
            _request(requested_at=NOW + timedelta(days=2), effective_at=NOW)
        )
    )

    strict = repo.token_asset_at("asset:hunter", NOW + timedelta(days=1), report_mode="known_by_hunter")
    reconstructed = repo.token_asset_at("asset:hunter", NOW + timedelta(days=1), report_mode="reconstructed")

    assert strict is not None
    assert strict["recorded_at"] == NOW.isoformat()
    assert strict["report_mode"] == "known_by_hunter"
    assert reconstructed is not None
    assert reconstructed["recorded_at"] == (NOW + timedelta(days=2)).isoformat()
    assert reconstructed["report_mode"] == "reconstructed"
    assert reconstructed["reconstructed_after_cutoff"] is True


def test_provider_failure_persists_availability_without_zero_supply_or_unlock(tmp_path: Path) -> None:
    registry = _registry()
    repo = TokenomicsRepository(tmp_path / "tokenomics.sqlite")
    result = OfficialTokenomicsDisclosureAdapter(
        registry, _Http({"status": "unavailable", "reason": "removed"})
    ).collect(_request())
    summary = TokenomicsIngestionService(repo, registry).ingest(result)

    assert summary.supply_observation_count == 0
    assert summary.unlock_event_count == 0
    assert repo.count("tokenomics_supply_observations") == 0
    assert repo.count("tokenomics_unlock_events") == 0
    outcome = _rows(repo.path, "tokenomics_acquisition_outcomes")[0]
    assert outcome["availability_outcome"] == "unavailable"
    assert outcome["failure_reason"] == "removed"


def test_phase_b_does_not_wire_tokenomics_into_runtime_or_automation_config() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_files = (
        "src/hunter/cli.py",
        "src/hunter/pipeline.py",
        "src/hunter/automation/execution.py",
        "configs/automation.yaml",
    )
    for filename in runtime_files:
        assert "tokenomics" not in (root / filename).read_text()


class _Http:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float]] = []

    def get_json(self, url: str, timeout_seconds: float) -> object:
        self.calls.append((url, timeout_seconds))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Rpc:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, object, float]] = []

    def post_json(self, url: str, payload: object, timeout_seconds: float) -> object:
        self.calls.append((url, payload, timeout_seconds))
        assert url == "https://ethereum.publicnode.com"
        assert payload == {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": ({"to": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "data": "0x18160ddd"}, "latest"),
        }
        return self.payload


def _request(
    *,
    asset_id: str = "asset:hunter",
    source_uri: str = "",
    requested_at: datetime = NOW,
    effective_at: datetime | None = None,
    chain: str = "ethereum",
    contract_address: str = "0xtoken",
) -> TokenomicsProviderRequest:
    return TokenomicsProviderRequest(
        asset_id=asset_id,
        candidate_id="candidate:hunter",
        symbol="HUNT",
        name="Hunter",
        chain=chain,
        contract_address=contract_address,
        source_uri=source_uri,
        requested_at=requested_at,
        effective_at=effective_at,
    )


def _source(provider_id: str, asset_id: str) -> TokenomicsSourceConfig:
    source = _registry().resolve(provider_id=provider_id, asset_id=asset_id)
    assert source is not None
    return source


def _registry() -> TokenomicsSourceRegistry:
    return TokenomicsSourceRegistry(
        (
            TokenomicsSourceConfig(
                source_id="official:hunter",
                provider_id="official-tokenomics-disclosure",
                asset_id="asset:hunter",
                candidate_id="candidate:hunter",
                symbol="HUNT",
                name="Hunter",
                chain="ethereum",
                contract_address="0xtoken",
                decimals=18,
                source_type="official_project_controlled",
                endpoint="https://official.example.test/hunter-tokenomics.json",
                allowed_hosts=("official.example.test",),
                response_format="hunter-tokenomics-disclosure-v1",
                parser_version="official-tokenomics-disclosure-v1",
                authority_tier="authoritative",
                enabled=True,
                capabilities=("supply", "allocation", "vesting", "unlock"),
                historical_depth="source document retained by project",
                freshness="document timestamp or retrieval time",
                source_limitations="test official source",
            ),
            TokenomicsSourceConfig(
                source_id="coingecko:usd-coin",
                provider_id="public-tokenomics-aggregator",
                asset_id="asset:usdc:ethereum",
                candidate_id="usdc",
                symbol="USDC",
                name="USD Coin",
                chain="ethereum",
                contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                decimals=6,
                source_type="public_aggregator",
                endpoint="https://api.coingecko.com/api/v3/coins/usd-coin?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false",
                allowed_hosts=("api.coingecko.com",),
                response_format="coingecko-coin-v3",
                parser_version="public-tokenomics-aggregator-v1",
                authority_tier="secondary",
                enabled=True,
                capabilities=("supply",),
                historical_depth="current snapshot",
                freshness="provider last_updated",
                source_limitations="test aggregator source",
            ),
            TokenomicsSourceConfig(
                source_id="evm-rpc:ethereum:usdc",
                provider_id="evm-erc20-public-rpc",
                asset_id="asset:usdc:ethereum",
                candidate_id="usdc",
                symbol="USDC",
                name="USD Coin",
                chain="ethereum",
                contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                decimals=6,
                source_type="verified_onchain_rpc",
                endpoint="https://ethereum.publicnode.com",
                allowed_hosts=("ethereum.publicnode.com",),
                response_format="evm-json-rpc-eth-call-total-supply",
                parser_version="evm-erc20-public-rpc-v1",
                authority_tier="authoritative",
                enabled=True,
                capabilities=("supply",),
                historical_depth="latest public RPC state",
                freshness="retrieval time",
                source_limitations="test onchain source",
            ),
        )
    )


def _official_payload(
    *,
    total: str | list[str] = "1000000",
    supply_only: bool = False,
    schema_version: str = "hunter-tokenomics-disclosure-v1",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "asset_id": "asset:hunter",
        "published_at": NOW.isoformat(),
        "observed_at": NOW.isoformat(),
        "supply": {"total": total, "circulating": "500000", "unit": "HUNT", "effective_at": NOW.isoformat()},
    }
    if supply_only:
        return payload
    payload["allocations"] = [
        {
            "category": "team",
            "percentage": 0.2,
            "amount": "200000",
            "unit": "HUNT",
            "effective_start_at": NOW.isoformat(),
        }
    ]
    payload["vesting"] = [
        {
            "schedule_key": "team",
            "allocation_category": "team",
            "effective_start_at": NOW.isoformat(),
            "effective_end_at": (NOW + timedelta(days=365)).isoformat(),
            "schedule_state": "active",
            "segments": [
                {
                    "segment_key": "linear",
                    "segment_state": "planned",
                    "start_at": NOW.isoformat(),
                    "end_at": (NOW + timedelta(days=90)).isoformat(),
                    "percentage": 0.05,
                },
                {
                    "segment_key": "cliff",
                    "segment_state": "planned",
                    "start_at": (NOW + timedelta(days=90)).isoformat(),
                    "end_at": (NOW + timedelta(days=90)).isoformat(),
                    "percentage": 0.05,
                },
                {
                    "segment_key": "periodic",
                    "segment_state": "planned",
                    "start_at": (NOW + timedelta(days=91)).isoformat(),
                    "end_at": (NOW + timedelta(days=180)).isoformat(),
                    "percentage": 0.05,
                },
                {
                    "segment_key": "non_linear_declared",
                    "segment_state": "planned",
                    "start_at": (NOW + timedelta(days=181)).isoformat(),
                    "end_at": (NOW + timedelta(days=365)).isoformat(),
                    "percentage": 0.05,
                },
            ],
            "unlocks": [
                {
                    "event_key": "cliff_unlock",
                    "unlock_at": (NOW + timedelta(days=90)).isoformat(),
                    "percentage": 0.05,
                },
                {
                    "event_key": "discrete_unlock",
                    "unlock_at": (NOW + timedelta(days=180)).isoformat(),
                    "amount": "100000",
                },
            ],
        }
    ]
    return payload


def _coingecko_payload() -> dict[str, object]:
    return {
        "id": "usd-coin",
        "last_updated": NOW.isoformat(),
        "market_data": {
            "circulating_supply": 120_000_000,
            "total_supply": 120_100_000,
            "max_supply": 120_100_000,
        },
    }


def _rows(path: Path, table: str) -> tuple[dict[str, Any], ...]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with conn:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    return tuple(dict(row) for row in rows)
