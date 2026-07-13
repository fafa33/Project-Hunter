from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hunter.onchain.adapters import EVMJsonRpcAdapter, EVMProviderUnavailable
from hunter.onchain.automation import ONCHAIN_AUTOMATION_JOBS, OnChainAutomationManager
from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.engine import CapitalFlowEngine, evm_log_evidence_id, normalize_flows, snapshot_from_flows
from hunter.onchain.models import AssetConfig, ChainConfig, OnChainSurface, RawOnChainObservation
from hunter.onchain.registry import SurfaceRegistry
from hunter.onchain.repository import OnChainRepository

NOW = datetime(2026, 7, 13, tzinfo=UTC)
SURFACE_ADDRESS = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
OTHER_SURFACE = "0x0000000000000000000000000000000000000a01"
COUNTERPARTY = "0x0000000000000000000000000000000000000b02"
PROJECT_TOKEN = "0x0000000000000000000000000000000000000c03"


class StaticTransport:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[str] = []

    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        self.calls.append(method)
        if self.failures:
            self.failures -= 1
            raise EVMProviderUnavailable("rate limited", failure_type="rate_limited")
        if method == "eth_chainId":
            return hex(1)
        if method == "eth_blockNumber":
            return hex(1_000)
        if method == "eth_getBlockByNumber":
            return {"hash": "0xblock", "timestamp": hex(int(NOW.timestamp()))}
        if method == "eth_getBalance":
            return hex(10**18)
        if method == "eth_getLogs":
            return [{"transactionHash": "0xtx", "logIndex": hex(7)}]
        raise AssertionError(method)


def chain(chain_id: int = 1, *, enabled: bool = True, finality_depth: int = 64, retry_limit: int = 1) -> ChainConfig:
    return ChainConfig(
        chain_id=chain_id,
        network="ethereum",
        family="evm",
        enabled=enabled,
        rpc_endpoint="https://ethereum.publicnode.com",
        rpc_endpoints=("https://ethereum.publicnode.com",),
        rpc_env=None,
        explorer_url="https://etherscan.io",
        finality_depth=finality_depth,
        max_block_range=5000,
        polling_interval_seconds=300,
        retry_limit=retry_limit,
        timeout_seconds=1,
    )


def surface(project: str = "aave", address: str = SURFACE_ADDRESS, chain_id: int = 1) -> OnChainSurface:
    return OnChainSurface(
        project=project,
        chain_id=chain_id,
        network="ethereum",
        address=address,
        address_type="lending_market",
        protocol_role="aave_v3_pool",
        asset_scope="protocol_collateral",
        source_url="https://etherscan.io/address/0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        source_type="verified_block_explorer",
        verification_timestamp=NOW,
        confidence=0.95,
        active=True,
        valid_from=NOW,
        evidence_id="surface-evidence",
    )


def config(*surfaces: OnChainSurface, chains: tuple[ChainConfig, ...] = (chain(),)) -> OnChainConfig:
    return OnChainConfig(
        enabled=True,
        version="test",
        chains=chains,
        tracked_assets=(AssetConfig(1, "ETH", "native", 18),),
        surfaces=surfaces or (surface(),),
        snapshot_windows=("24h",),
        retention={"runtime_root": "unused"},
    )


def observation(
    evidence_id: str,
    *,
    direction: str = "in",
    counterparty: str | None = COUNTERPARTY,
    event_type: str = "erc20_transfer",
    asset: str | None = "0x0000000000000000000000000000000000000d04",
    amount: Decimal = Decimal("5"),
    log_index: int = 1,
) -> RawOnChainObservation:
    return RawOnChainObservation(
        project="aave",
        chain_id=1,
        provider="fixture",
        endpoint_identity="fixture://evm",
        block_number=900,
        block_hash="0xblock",
        block_timestamp=NOW,
        observed_address=SURFACE_ADDRESS,
        acquisition_timestamp=NOW,
        finality_status="finalized",
        source_reference="https://etherscan.io/tx/0xabc",
        evidence_id=evidence_id,
        transaction_hash="0xabc",
        log_index=log_index,
        counterparty_address=counterparty,
        asset_contract=asset,
        raw_amount=str(amount),
        decimals=18,
        normalized_amount=amount,
        direction=direction,
        event_type=event_type,
    )


def test_registry_validation_rejects_invalid_duplicate_and_unsupported_surfaces() -> None:
    duplicate = surface(project="copy")
    invalid = surface(project="bad", address="0xnot-valid")
    unsupported = surface(project="future", address="0x0000000000000000000000000000000000000e05", chain_id=999)

    validation = SurfaceRegistry(config(surface(), duplicate, invalid, unsupported)).validate()

    assert not validation.valid
    assert any("duplicate_address" in issue for issue in validation.issues)
    assert any("invalid_address" in issue for issue in validation.issues)
    assert any("unsupported_chain" in issue for issue in validation.issues)


def test_evm_adapter_finality_retry_logs_and_timeout_failure() -> None:
    transport = StaticTransport(failures=1)
    adapter = EVMJsonRpcAdapter(chain(finality_depth=10, retry_limit=1), transport=transport)

    assert adapter.latest_finalized_block() == 990
    assert adapter.native_balance(SURFACE_ADDRESS, 990) == 10**18
    assert (
        adapter.logs(address=SURFACE_ADDRESS, from_block=989, to_block=990, topics=("0xtopic",))[0]["logIndex"] == "0x7"
    )
    assert transport.calls.count("eth_blockNumber") == 2

    failing = EVMJsonRpcAdapter(chain(retry_limit=1), transport=StaticTransport(failures=3))
    with pytest.raises(EVMProviderUnavailable):
        failing.latest_finalized_block()


def test_repository_compacts_repeated_sync_data_by_deterministic_identity(tmp_path) -> None:
    repo = OnChainRepository(tmp_path)
    raw = observation(evm_log_evidence_id(1, "0xABC", 7), log_index=7)

    repo.save_raw((raw,))
    repo.save_raw((raw,))

    assert len(repo.raw()) == 1
    assert repo.raw()[0]["evidence_id"] == evm_log_evidence_id(1, "0xabc", 7)


def test_capital_flow_normalization_preserves_economic_exclusions() -> None:
    surfaces = (
        surface(),
        surface(address=OTHER_SURFACE),
    )
    flows = normalize_flows(
        (
            observation("external-in", direction="in"),
            observation("internal", counterparty=OTHER_SURFACE),
            observation(
                "mint", direction="in", counterparty="0x0000000000000000000000000000000000000000", asset=PROJECT_TOKEN
            ),
            observation(
                "burn", direction="out", counterparty="0x0000000000000000000000000000000000000000", asset=PROJECT_TOKEN
            ),
            observation("unknown", event_type="native_balance", asset=None),
        ),
        surfaces,
        project_token_contracts=(PROJECT_TOKEN,),
    )

    categories = [flow.category for flow in flows]
    assert categories == ["external_inflow", "internal_transfer", "token_mint", "token_burn", "unknown_flow"]
    snapshot = snapshot_from_flows(surfaces[0], "24h", 800, 900, flows, (), NOW)
    assert snapshot.net_external_flow == Decimal("5")
    assert snapshot.native_token_movement == Decimal("10")
    assert "point_in_time_price" in snapshot.unavailable_fields
    assert all(flow.usd_value is None and flow.usd_valuation_status == "unavailable" for flow in flows)


def test_provider_unavailable_and_unsupported_chain_are_explicit_and_persisted(tmp_path) -> None:
    unsupported_surface = surface(
        project="unsupported", address="0x0000000000000000000000000000000000000e05", chain_id=999
    )
    repo = OnChainRepository(tmp_path)

    snapshots = CapitalFlowEngine(config(unsupported_surface), repository=repo, adapters={}).sync()

    assert snapshots[0].status == "unsupported_chain"
    assert repo.snapshots()[0]["status"] == "unsupported_chain"

    unavailable = EVMJsonRpcAdapter(chain(retry_limit=0), transport=StaticTransport(failures=99))
    repo = OnChainRepository(tmp_path / "unavailable")
    snapshots = CapitalFlowEngine(config(surface()), repository=repo, adapters={1: unavailable}).sync()

    assert snapshots[0].status == "provider_unavailable"
    assert repo.snapshots()[0]["unavailable_fields"] == ["live_provider"]


def test_successful_sync_is_idempotent_uses_finalized_block_and_links_evidence(tmp_path) -> None:
    repo = OnChainRepository(tmp_path)
    adapter = EVMJsonRpcAdapter(chain(finality_depth=64), transport=StaticTransport())
    engine = CapitalFlowEngine(config(surface()), repository=repo, adapters={1: adapter})

    first = engine.sync()
    second = engine.sync()

    assert first[0].status == "live"
    assert first[0].end_block == 936
    assert len(repo.raw()) == 1
    assert len(repo.flows()) == 1
    assert len(repo.snapshots()) == 1
    assert second[0].evidence_ids == first[0].evidence_ids


def test_provider_pool_failover_cooldown_wrong_chain_and_status_persistence(tmp_path) -> None:
    class PoolTransport:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def rpc(self, method: str, params: tuple[object, ...]) -> object:
            if "forbidden" in self.endpoint:
                raise EVMProviderUnavailable("HTTP Error 403: Forbidden", failure_type="forbidden")
            if method == "eth_chainId":
                return hex(999) if "wrong" in self.endpoint else hex(1)
            if method == "eth_blockNumber":
                return hex(1000)
            if method == "eth_getBlockByNumber":
                return {"hash": "0xblock", "timestamp": hex(int(NOW.timestamp()))}
            if method == "eth_getBalance":
                return hex(1)
            if method == "eth_getLogs":
                return []
            raise AssertionError(method)

    pooled = chain()
    object.__setattr__(
        pooled,
        "rpc_endpoints",
        ("https://forbidden.example", "https://wrong.example", "https://healthy.example"),
    )
    adapter = EVMJsonRpcAdapter(pooled, transport_factory=PoolTransport)
    repo = OnChainRepository(tmp_path)
    engine = CapitalFlowEngine(config(surface(), chains=(pooled,)), repository=repo, adapters={1: adapter})

    states = engine.check_providers()
    snapshot = engine.sync()[0]

    assert [state.status for state in states] == ["forbidden", "wrong_chain", "healthy"]
    assert snapshot.status == "live"
    assert repo.provider_states()[0]["status"] == "forbidden"
    assert repo.checkpoints()[0]["block_number"] == 936


def test_onchain_automation_install_is_idempotent_and_runtime_only(tmp_path) -> None:
    repo = OnChainRepository(tmp_path)
    manager = OnChainAutomationManager(config(surface()), repository=repo)

    first = manager.install()
    second = manager.install()
    manager.set_enabled(False)
    status = manager.status()

    assert first.created == len(ONCHAIN_AUTOMATION_JOBS)
    assert second.created == 0
    assert len(status) == len(ONCHAIN_AUTOMATION_JOBS)
    assert all(row["enabled"] is False for row in status)
    assert (tmp_path / "automation.yaml").exists()
