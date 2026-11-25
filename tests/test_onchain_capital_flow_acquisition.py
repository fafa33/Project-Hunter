from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_state import sign_record
from hunter.onchain.adapters import EVMJsonRpcAdapter, EVMProviderUnavailable
from hunter.onchain.automation import ONCHAIN_AUTOMATION_JOBS, OnChainAutomationManager
from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.engine import (
    TRANSFER_TOPIC,
    CapitalFlowEngine,
    evm_log_evidence_id,
    normalize_flows,
    snapshot_from_flows,
)
from hunter.onchain.evidence import (
    CORROBORATION_POLICY,
    EVIDENCE_SIGNING_DOMAIN,
    TRUST_FILE_ENV,
    CapitalFlowEvidenceError,
    load_trust,
    observation_payload,
    receipt_log_digest,
    sign_observation,
    verified_capital_flow_rows,
    write_signing_key,
    write_trust_document,
)
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
            number = 936 if params and params[0] == "finalized" else int(str(params[0]), 16)
            return {"number": hex(number), "hash": "0x" + "ef" * 32, "timestamp": hex(int(NOW.timestamp()))}
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


def signing_material(tmp_path, *, key: Ed25519PrivateKey | None = None):
    key = key or Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    trust_path = tmp_path / "trust.json"
    write_signing_key(key_path, key)
    write_trust_document(trust_path, key)
    return key, key_path, trust_path, load_trust(trust_path)


def signed_transfer(
    tmp_path,
    *,
    key: Ed25519PrivateKey | None = None,
    surface_row: OnChainSurface | None = None,
    **overrides: Any,
):
    key, key_path, trust_path, trust = signing_material(tmp_path, key=key)
    bound_surface = surface_row or surface()
    tx = "0x" + "ab" * 32
    fields: dict[str, Any] = {
        "project": "aave",
        "chain_id": 1,
        "provider": "evm-json-rpc",
        "endpoint_identity": "eth.drpc.org",
        "block_number": 900,
        "block_hash": "0x" + "ef" * 32,
        "block_timestamp": NOW - timedelta(minutes=1),
        "observed_address": SURFACE_ADDRESS,
        "acquisition_timestamp": NOW,
        "finality_status": "finalized",
        "source_reference": "https://etherscan.io/tx/" + tx,
        "evidence_id": evm_log_evidence_id(1, tx, 7),
        "transaction_hash": tx,
        "log_index": 7,
        "counterparty_address": COUNTERPARTY,
        "asset_contract": PROJECT_TOKEN,
        "asset_symbol": "TEST",
        "raw_amount": "2500000",
        "decimals": 6,
        "normalized_amount": Decimal("2.5"),
        "direction": "in",
        "event_type": "erc20_transfer",
        "log_endpoint": "https://eth.drpc.org",
        "receipt_endpoint": "https://eth.drpc.org",
        "corroborating_endpoints": ("https://rpc.flashbots.net", "https://rpc.mevblocker.io"),
        "receipt_validation_policy": CORROBORATION_POLICY,
        "receipt_verified_at": NOW,
        "log_topics": (
            TRANSFER_TOPIC,
            "0x" + COUNTERPARTY[2:].zfill(64),
            "0x" + SURFACE_ADDRESS[2:].zfill(64),
        ),
        "log_data": "0x" + format(2500000, "064x"),
    }
    fields.update(overrides)
    row = RawOnChainObservation(**fields)
    if "receipt_log_digest" not in overrides:
        row = replace(row, receipt_log_digest=receipt_log_digest(row))
    signed = sign_observation(row, bound_surface, key, verification_timestamp=NOW)
    repo = OnChainRepository(tmp_path / "runtime")
    repo.save_raw((row,))
    repo.save_evidence_signatures((signed,))
    return row, signed, config(bound_surface), repo, trust, key_path, trust_path


def store_signed_transfer(repo: OnChainRepository, row: RawOnChainObservation, key: Ed25519PrivateKey) -> None:
    repo.save_raw((row,))
    repo.save_evidence_signatures((sign_observation(row, surface(), key, verification_timestamp=NOW),))


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

    assert first[0].status == "partial"
    assert "aggregate_economic_flow" in first[0].unavailable_fields
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
                number = 936 if params and params[0] == "finalized" else int(str(params[0]), 16)
                return {"number": hex(number), "hash": "0x" + "ef" * 32, "timestamp": hex(int(NOW.timestamp()))}
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
    assert snapshot.status == "partial"
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


def test_tracked_transfer_acquisition_retains_real_log_and_deduplicates(tmp_path, monkeypatch) -> None:
    key, _key_path, trust_path, trust = signing_material(tmp_path)
    monkeypatch.setenv(TRUST_FILE_ENV, str(trust_path))

    class TransferTransport(StaticTransport):
        def rpc(self, method: str, params: tuple[object, ...]) -> object:
            if method == "eth_getTransactionReceipt":
                logs = self.rpc("eth_getLogs", ())
                assert isinstance(logs, list)
                return {**logs[0], "status": "0x1", "logs": logs}
            if method == "eth_getLogs":
                return [
                    {
                        "address": PROJECT_TOKEN,
                        "topics": [
                            TRANSFER_TOPIC,
                            "0x" + COUNTERPARTY[2:].zfill(64),
                            "0x" + SURFACE_ADDRESS[2:].zfill(64),
                        ],
                        "data": "0x" + format(2500000, "064x"),
                        "transactionHash": "0x" + "ab" * 32,
                        "logIndex": "0x7",
                        "blockNumber": "0x384",
                        "blockHash": "0x" + "ef" * 32,
                    }
                ]
            return super().rpc(method, params)

    cfg = replace(config(), tracked_assets=(AssetConfig(1, "TEST", "stablecoin", 6, PROJECT_TOKEN),))
    repo = OnChainRepository(tmp_path / "runtime")
    engine = CapitalFlowEngine(
        cfg,
        repository=repo,
        evidence_signing_key=key,
        adapters={
            1: EVMJsonRpcAdapter(
                replace(
                    chain(),
                    rpc_endpoints=("https://first.example", "https://second.example", "https://third.example"),
                ),
                transport_factory=lambda _: TransferTransport(),
            )
        },
    )
    snapshot = engine.sync()[0]
    engine.sync()
    transfers = [row for row in repo.raw() if row["event_type"] == "erc20_transfer"]
    signatures = repo.evidence_signatures()
    assert len(transfers) == 1
    assert len(signatures) == 1
    assert Decimal(transfers[0]["normalized_amount"]) == Decimal("2.5")
    assert transfers[0]["block_number"] == 900
    assert transfers[0]["direction"] == "in"
    assert transfers[0]["log_endpoint"] == "first.example"
    assert transfers[0]["receipt_endpoint"] == "first.example"
    assert transfers[0]["corroborating_endpoints"] == ["second.example", "third.example"]
    assert transfers[0]["receipt_validation_policy"] == CORROBORATION_POLICY
    assert transfers[0]["receipt_verified_at"]
    assert transfers[0]["receipt_log_digest"]
    assert verified_capital_flow_rows("aave", repo.raw(), config=cfg, repository=repo, trust=trust, as_of=NOW)
    assert snapshot.status == "partial"
    assert "aggregate_economic_flow" in snapshot.unavailable_fields
    assert any(row["category"] == "external_inflow" for row in repo.flows())


@pytest.mark.parametrize(
    "override",
    [
        {"removed": True},
        {"blockNumber": "0x999"},
        {"blockHash": "wrong"},
        {"data": "0x" + "g" * 64},
        {"transactionHash": "0x" + "g" * 64},
        {"address": OTHER_SURFACE},
        {"topics": []},
        {},
    ],
)
def test_hostile_transfer_rejected(tmp_path, override) -> None:
    key, _key_path, _trust_path, _trust = signing_material(tmp_path)

    class HostileTransport(StaticTransport):
        def rpc(self, method: str, params: tuple[object, ...]) -> object:
            if method == "eth_getTransactionReceipt":
                logs = self.rpc("eth_getLogs", ())
                assert isinstance(logs, list)
                return {"status": "0x1", "logs": logs}
            if method == "eth_getLogs":
                return [
                    dict(
                        {
                            "address": PROJECT_TOKEN,
                            "topics": [
                                TRANSFER_TOPIC,
                                "0x" + COUNTERPARTY[2:].zfill(64),
                                "0x" + SURFACE_ADDRESS[2:].zfill(64),
                            ],
                            "data": "0x" + format(1000000, "064x"),
                            "transactionHash": "0x" + "ab" * 32,
                            "logIndex": "0x1",
                            "blockNumber": "0x384",
                            "blockHash": "0x" + "ef" * 32,
                        },
                        **override,
                    )
                ]
            return super().rpc(method, params)

    cfg = replace(config(), tracked_assets=(AssetConfig(1, "TEST", "stablecoin", 6, PROJECT_TOKEN),))
    repo = OnChainRepository(tmp_path / "runtime")
    result = CapitalFlowEngine(
        cfg,
        repository=repo,
        evidence_signing_key=key,
        adapters={1: EVMJsonRpcAdapter(chain(), transport=HostileTransport())},
    ).sync()[0]
    assert not any(row["event_type"] == "erc20_transfer" for row in repo.raw())
    assert not repo.evidence_signatures()
    assert result.status == "partial"
    assert "aggregate_economic_flow" in result.unavailable_fields


def test_hostile_whale_context_never_becomes_buy_signal(tmp_path) -> None:
    from hunter.execution import FixedClock
    from hunter.intelligence.engines.whale import WhaleIntelligenceEngine
    from hunter.intelligence.engines.whale.configuration import WhaleEngineConfiguration
    from hunter.plugins.contracts import PipelineContext

    key, _key_path, _trust_path, trust = signing_material(tmp_path)
    valid, _signed, cfg, repo, _same_trust, _, _ = signed_transfer(tmp_path, key=key)
    hostile = (
        valid,
        replace(valid, evidence_id="stale", block_timestamp=NOW - timedelta(days=2)),
        replace(valid, evidence_id="future", block_timestamp=NOW + timedelta(days=1)),
        replace(valid, evidence_id="unfinalized", finality_status="pending"),
        replace(valid, evidence_id="nan", normalized_amount=Decimal("NaN")),
        replace(valid, evidence_id="forged", transaction_hash="fake"),
    )
    for row in hostile:
        store_signed_transfer(repo, row, key)
    context = PipelineContext(values={"whale_events": (), "capital_flow_observations": hostile}, clock=FixedClock(NOW))
    engine = WhaleIntelligenceEngine(
        configuration=WhaleEngineConfiguration(project="aave"),
        capital_flow_trust=trust,
        onchain_config=cfg,
        onchain_repository=repo,
    )
    result = engine.generate_intelligence(context, engine.analyze(context, engine.collect(context)))
    assert not result.signals
    assert [item.id for item in result.evidence] == [f"capital-context-{valid.evidence_id}"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("agree", True),
        ("mismatch", False),
        ("offline", False),
        ("wrong_chain", False),
        ("same_host", False),
        ("single", False),
    ],
)
def test_hostile_rpc_corroboration(tmp_path, mode, expected) -> None:
    key, _key_path, _trust_path, _trust = signing_material(tmp_path)
    log = {
        "address": PROJECT_TOKEN,
        "topics": [TRANSFER_TOPIC, "0x" + COUNTERPARTY[2:].zfill(64), "0x" + SURFACE_ADDRESS[2:].zfill(64)],
        "data": "0x" + format(1000000, "064x"),
        "transactionHash": "0x" + "ab" * 32,
        "logIndex": "0x1",
        "blockNumber": "0x384",
        "blockHash": "0x" + "ef" * 32,
    }

    class Transport(StaticTransport):
        def __init__(self, endpoint):
            super().__init__()
            self.secondary = "second" in endpoint

        def rpc(self, method, params):
            if self.secondary and mode == "offline":
                raise EVMProviderUnavailable("offline")
            if self.secondary and mode == "wrong_chain" and method == "eth_chainId":
                return "0x2"
            if method == "eth_getLogs":
                return [log]
            if method == "eth_getTransactionReceipt":
                entry = {**log, "data": "0x" + "0" * 64} if self.secondary and mode == "mismatch" else log
                return {**log, "status": "0x1", "logs": [entry]}
            return super().rpc(method, params)

    endpoints = ("https://first.example", "https://second.example")
    if mode == "same_host":
        endpoints = ("https://first.example", "https://first.example/second")
    if mode == "single":
        endpoints = ("https://first.example",)
    cfg = replace(config(), tracked_assets=(AssetConfig(1, "TEST", "stablecoin", 6, PROJECT_TOKEN),))
    repo = OnChainRepository(tmp_path / "runtime")
    adapter = EVMJsonRpcAdapter(replace(chain(), rpc_endpoints=endpoints, retry_limit=0), transport_factory=Transport)
    CapitalFlowEngine(cfg, repository=repo, evidence_signing_key=key, adapters={1: adapter}).sync()
    assert any(row["event_type"] == "erc20_transfer" for row in repo.raw()) is expected


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing_signature",
        "untrusted_signer",
        "wrong_domain",
        "tampered_signature",
        "wrong_schema",
        "wrong_project",
        "wrong_chain",
        "wrong_surface",
        "wrong_evidence_id",
        "stale",
        "future",
        "unfinalized",
        "non_transfer",
        "invalid_topics",
        "invalid_data",
        "forged_digest",
        "single_corroboration",
        "same_host_corroboration",
        "conflicting_signature",
        "tampered_row",
    ],
)
def test_capital_flow_evidence_hostile_cases(tmp_path, case) -> None:
    key, _key_path, trust_path, trust = signing_material(tmp_path)
    base, base_signature, cfg, _repo, _trust, _, _ = signed_transfer(tmp_path, key=key)
    row = base
    signature = base_signature
    signatures: tuple[dict[str, Any], ...] = (signature,)
    conflicting = False

    if case == "missing_signature":
        signatures = ()
    elif case == "untrusted_signer":
        signature = sign_observation(row, surface(), Ed25519PrivateKey.generate(), verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "wrong_domain":
        signature = {**signature, "signature": {**signature["signature"], "domain": "other-domain"}}
        signatures = (signature,)
    elif case == "tampered_signature":
        signature = {**signature, "signature": {**signature["signature"], "value": "00" * 64}}
        signatures = (signature,)
    elif case == "wrong_schema":
        payload = observation_payload(row, surface(), NOW)
        payload["schema_version"] = "wrong-schema"
        signed = sign_record(payload, key, domain=EVIDENCE_SIGNING_DOMAIN)
        signature = {**signature, "signature": signed["signature"]}
        signatures = (signature,)
    elif case == "wrong_project":
        row = replace(base, project="other")
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "wrong_chain":
        row = replace(base, chain_id=2)
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "wrong_surface":
        row = replace(base, observed_address=OTHER_SURFACE)
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "wrong_evidence_id":
        row = replace(base, evidence_id="forged-id")
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "stale":
        row = replace(base, block_timestamp=NOW - timedelta(days=2))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "future":
        row = replace(base, block_timestamp=NOW + timedelta(days=1))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "unfinalized":
        row = replace(base, finality_status="pending")
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "non_transfer":
        row = replace(base, event_type="native_balance")
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "invalid_topics":
        row = replace(base, log_topics=(TRANSFER_TOPIC, "0x" + "11" * 32, "0x" + "22" * 32))
        row = replace(row, receipt_log_digest=receipt_log_digest(row))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "invalid_data":
        row = replace(base, log_data="0x" + "00" * 32)
        row = replace(row, receipt_log_digest=receipt_log_digest(row))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "forged_digest":
        row = replace(base, receipt_log_digest="0x" + "00" * 32)
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "single_corroboration":
        row = replace(base, corroborating_endpoints=("https://rpc.flashbots.net",))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "same_host_corroboration":
        row = replace(base, corroborating_endpoints=("https://eth.drpc.org/alternate",))
        signature = sign_observation(row, surface(), key, verification_timestamp=NOW)
        signatures = (signature,)
    elif case == "conflicting_signature":
        conflicting = True
    elif case == "tampered_row":
        row = replace(base, raw_amount="9999999")
        signatures = (signature,)

    repo = OnChainRepository(tmp_path / "verification")
    repo.save_raw((row,))
    if conflicting:
        repo.save_evidence_signatures((base_signature,))
        alternate = sign_observation(base, surface(), Ed25519PrivateKey.generate(), verification_timestamp=NOW)
        path = tmp_path / "verification" / "evidence_signatures.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(alternate, sort_keys=True, default=str) + "\n")
    else:
        repo.save_evidence_signatures(signatures)

    accepted = verified_capital_flow_rows("aave", (row,), config=cfg, repository=repo, trust=trust, as_of=NOW)
    assert bool(accepted) is (case == "valid")


def test_evidence_trust_rejects_wrong_schema(tmp_path) -> None:
    key, _key_path, trust_path, _trust = signing_material(tmp_path)
    assert key is not None
    document = json.loads(trust_path.read_text(encoding="utf-8"))
    document["schema_version"] = "wrong-schema"
    trust_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CapitalFlowEvidenceError):
        load_trust(trust_path)


def test_repository_preserves_cross_project_evidence_id_collisions(tmp_path) -> None:
    repo = OnChainRepository(tmp_path)
    row = observation("shared-evidence-id")
    repo.save_raw((row, replace(row, project="uniswap")))

    assert len(repo.raw()) == 2
    assert not list(tmp_path.glob(".*.tmp"))
