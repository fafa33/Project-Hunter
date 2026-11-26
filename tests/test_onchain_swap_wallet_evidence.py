from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.discovery.automation import DISCOVERY_JOB_IDS, require_discovery_automation
from hunter.execution.clock import FixedClock
from hunter.historical_acquisition.models import RawHistoricalEvidence
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.historical_acquisition.usd_valuation import HistoricalUsdValuator, UsdValuation
from hunter.market_validation.acquisition_sources import _swap_wallet_context
from hunter.onchain.adapters import EVMJsonRpcAdapter, EVMProviderUnavailable, RpcAttemptBudgetExhausted
from hunter.onchain.configuration import OnChainConfig
from hunter.onchain.dex import (
    V2_SWAP_TOPIC,
    V3_SWAP_TOPIC,
    decode_swap_log,
    v2_swap_data,
    v3_swap_data,
)
from hunter.onchain.engine import TRANSFER_TOPIC, CapitalFlowEngine, _preserved_signed_row, evm_log_evidence_id
from hunter.onchain.evidence import (
    CORROBORATION_POLICY,
    signature_id,
    verified_swap_rows,
    verify_observation,
    write_signing_key,
    write_trust_document,
)
from hunter.onchain.models import AssetConfig, ChainConfig, DexPoolConfig, OnChainSurface, RawOnChainObservation
from hunter.onchain.repository import OnChainRepository
from hunter.onchain.wallet_performance import wallet_performance_from_swaps

NOW = datetime(2026, 11, 25, 9, 41, 35, tzinfo=UTC)
PRICE_AT = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
WALLET = "0x0000000000000000000000000000000000001111"
SECOND_WALLET = "0x0000000000000000000000000000000000002222"
ROUTER = "0x0000000000000000000000000000000000003333"
OTHER_POOL = "0x0000000000000000000000000000000000004444"
TX = "0x" + "ab" * 32
BLOCK_HASH = "0x" + "ef" * 32


class SwapTransport:
    def __init__(
        self,
        *,
        chain_id: int = 1,
        logs: tuple[dict[str, Any], ...] = (),
        receipt_logs: tuple[dict[str, Any], ...] | None = None,
        blocks: dict[int, tuple[str, int]] | None = None,
        fail_on_call: int | None = None,
        fail_on_calls: frozenset[int] = frozenset(),
    ) -> None:
        self.chain_id = chain_id
        self.logs = logs
        self.receipt_logs = logs if receipt_logs is None else receipt_logs
        self.blocks = blocks or {}
        self.fail_on_call = fail_on_call
        self.fail_on_calls = fail_on_calls
        self.calls: list[str] = []

    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        self.calls.append(method)
        if self.fail_on_call == len(self.calls) or len(self.calls) in self.fail_on_calls:
            raise EVMProviderUnavailable("forced RPC failure")
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(1_000)
        if method == "eth_getBlockByNumber":
            number = 936 if params and params[0] == "finalized" else int(str(params[0]), 16)
            block_hash, timestamp = self.blocks.get(number, (BLOCK_HASH, int(NOW.timestamp())))
            return {"number": hex(number), "hash": block_hash, "timestamp": hex(timestamp)}
        if method == "eth_getBalance":
            return "0x0"
        if method == "eth_getLogs":
            return list(self.logs)
        if method == "eth_getTransactionReceipt":
            tx = str(params[0]).lower() if params else TX
            matching = tuple(item for item in self.receipt_logs if str(item.get("transactionHash", "")).lower() == tx)
            first = matching[0] if matching else None
            return {
                "status": "0x1",
                "logs": list(matching) if matching else list(self.receipt_logs),
                "from": WALLET,
                "transactionHash": tx,
                "blockNumber": str(first["blockNumber"]) if first is not None else "0x384",
                "blockHash": str(first["blockHash"]) if first is not None else BLOCK_HASH,
            }
        if method == "eth_getCode":
            return "0x"
        if method == "eth_call":
            address = str(params[0].get("to", "")).lower() if params and isinstance(params[0], dict) else ""
            selector = str(params[0].get("data", "")) if params and isinstance(params[0], dict) else ""
            if selector == "0x0dfe1681":
                return "0x" + USDC[2:].zfill(64)
            if selector == "0xd21220a7":
                return "0x" + WETH[2:].zfill(64)
            if selector == "0x313ce567":
                return hex(6 if address == USDC else 18)
            raise AssertionError(selector)
        raise AssertionError(method)


def chain(
    *,
    swap_rpc_budget: int = 12,
    acquisition_rpc_budget: int = 40,
    retry_limit: int = 0,
    rpc_endpoints: tuple[str, ...] = ("https://first.test",),
) -> ChainConfig:
    return ChainConfig(
        chain_id=1,
        network="ethereum",
        family="evm",
        enabled=True,
        rpc_endpoint=rpc_endpoints[0],
        rpc_endpoints=rpc_endpoints,
        rpc_env=None,
        explorer_url="https://etherscan.io",
        finality_depth=64,
        max_block_range=5000,
        polling_interval_seconds=300,
        retry_limit=retry_limit,
        timeout_seconds=1,
        swap_rpc_budget=swap_rpc_budget,
        acquisition_rpc_budget=acquisition_rpc_budget,
    )


def pool(
    *,
    pool_id: str = "uniswap-v3-usdc-weth-0005",
    address: str = POOL,
    version: str = "v3",
    routers: tuple[str, ...] = (),
) -> DexPoolConfig:
    return DexPoolConfig(
        pool_id=pool_id,
        project="uniswap",
        chain_id=1,
        address=address,
        dex="uniswap",
        version=version,
        token0=USDC,
        token1=WETH,
        token0_decimals=6,
        token1_decimals=18,
        target_asset=USDC,
        target_asset_symbol="USDC",
        source_url="https://docs.uniswap.org/contracts/v3/reference/deployments/ethereum-deployments",
        source_type="official_documentation",
        verification_timestamp=NOW,
        confidence=0.95,
        active=True,
        valid_from=NOW - timedelta(days=1),
        evidence_id=f"pool-evidence:{pool_id}",
        routers=routers,
    )


def config(*pools: DexPoolConfig, chains: tuple[ChainConfig, ...] | None = None) -> OnChainConfig:
    return OnChainConfig(
        enabled=True,
        version="test",
        chains=chains or (chain(),),
        tracked_assets=(
            AssetConfig(1, "USDC", "stablecoin", 6, USDC, "usd-coin"),
            AssetConfig(1, "WETH", "wrapped_native", 18, WETH, "ethereum"),
        ),
        surfaces=(),
        snapshot_windows=("24h",),
        retention={"runtime_root": "unused"},
        dex_pools=pools or (pool(),),
    )


def v2_log(*, side: str, pool_row: DexPoolConfig, usdc_raw: int = 17_000_000_000, weth_raw: int = 5 * 10**18):
    sender = "0x" + WALLET[2:].zfill(64)
    recipient = "0x" + WALLET[2:].zfill(64)
    data = v2_swap_data(pool_row, side=side, usdc_raw=usdc_raw, weth_raw=weth_raw)
    return {
        "address": pool_row.address,
        "topics": [V2_SWAP_TOPIC, sender, recipient],
        "data": data,
        "transactionHash": TX,
        "logIndex": "0x7",
        "blockNumber": "0x384",
        "blockHash": BLOCK_HASH,
    }


def v3_log(*, side: str, pool_row: DexPoolConfig, usdc_raw: int = 17_000_000_000, weth_raw: int = 5 * 10**18):
    sender = "0x" + WALLET[2:].zfill(64)
    recipient = "0x" + WALLET[2:].zfill(64)
    data = v3_swap_data(pool_row, side=side, usdc_raw=usdc_raw, weth_raw=weth_raw)
    return {
        "address": pool_row.address,
        "topics": [V3_SWAP_TOPIC, sender, recipient],
        "data": data,
        "transactionHash": TX,
        "logIndex": "0x7",
        "blockNumber": "0x384",
        "blockHash": BLOCK_HASH,
    }


def signing_material(tmp_path: Path, key: Ed25519PrivateKey | None = None):
    root = Path(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    resolved = key or Ed25519PrivateKey.generate()
    key_path = root / "signing.key"
    trust_path = root / "trust.json"
    write_signing_key(key_path, resolved)
    write_trust_document(trust_path, resolved)
    from hunter.onchain.evidence import load_trust

    return resolved, trust_path, load_trust(trust_path)


def signed_swap(
    tmp_path: Path,
    *,
    key: Ed25519PrivateKey | None = None,
    pool_row: DexPoolConfig | None = None,
    side: str = "buy",
    log_index: int = 7,
    block_number: int = 900,
    wallet: str = WALLET,
    attribution_status: str = "AVAILABLE",
    swap_status: str = "AVAILABLE",
    usdc_raw: int = 17_000_000_000,
    weth_raw: int = 5 * 10**18,
    **overrides: Any,
):
    resolved_key, _trust_path, trust = signing_material(tmp_path / f"swap-{log_index}", key)
    resolved_pool = pool_row or pool()
    log = (
        v3_log(side=side, pool_row=resolved_pool, usdc_raw=usdc_raw, weth_raw=weth_raw)
        if resolved_pool.version == "v3"
        else v2_log(side=side, pool_row=resolved_pool, usdc_raw=usdc_raw, weth_raw=weth_raw)
    )
    decoded = decode_swap_log(resolved_pool, log)
    raw_amount = usdc_raw
    normalized = Decimal(usdc_raw).scaleb(-6)
    fields: dict[str, Any] = {
        "project": resolved_pool.project,
        "chain_id": resolved_pool.chain_id,
        "provider": "evm-json-rpc",
        "endpoint_identity": "first.test",
        "block_number": block_number,
        "block_hash": BLOCK_HASH,
        "block_timestamp": NOW + timedelta(seconds=log_index),
        "observed_address": resolved_pool.address,
        "acquisition_timestamp": NOW + timedelta(minutes=1),
        "finality_status": "finalized",
        "source_reference": resolved_pool.source_url,
        "evidence_id": evm_log_evidence_id(resolved_pool.chain_id, TX, log_index),
        "transaction_hash": TX,
        "log_index": log_index,
        "counterparty_address": wallet,
        "asset_contract": USDC,
        "asset_symbol": "USDC",
        "raw_amount": str(raw_amount),
        "decimals": 6,
        "normalized_amount": normalized,
        "direction": "unknown",
        "event_type": "dex_swap",
        "log_address": resolved_pool.address,
        "log_endpoint": "https://first.test",
        "receipt_endpoint": "https://first.test",
        "corroborating_endpoints": ("https://second.test", "https://third.test"),
        "receipt_validation_policy": CORROBORATION_POLICY,
        "receipt_verified_at": NOW + timedelta(minutes=2),
        "log_topics": tuple(str(item).lower() for item in log["topics"]),
        "log_data": str(log["data"]),
        "swap_pool_id": resolved_pool.pool_id,
        "swap_dex": resolved_pool.dex,
        "swap_version": resolved_pool.version,
        "swap_event_topic": str(log["topics"][0]).lower(),
        "token_in_contract": decoded.token_in,
        "token_out_contract": decoded.token_out,
        "token_in_raw_amount": str(decoded.raw_amount_in),
        "token_out_raw_amount": str(decoded.raw_amount_out),
        "token_in_amount": decoded.amount_in,
        "token_out_amount": decoded.amount_out,
        "target_asset_contract": USDC,
        "trade_direction": side,
        "attributed_wallet": wallet if attribution_status == "AVAILABLE" else None,
        "attribution_status": attribution_status,
        "attribution_reason": None if attribution_status == "AVAILABLE" else "unverified_wallet_attribution",
        "receipt_from": wallet,
        "receipt_status": "0x1",
        "wallet_code": "0x" if attribution_status == "AVAILABLE" else None,
        "swap_status": swap_status,
        "rpc_request_count": 7,
    }
    fields.update(overrides)
    row = RawOnChainObservation(**fields)
    from hunter.onchain.evidence import receipt_log_digest, sign_observation

    if "receipt_log_digest" not in overrides:
        row = replace(row, receipt_log_digest=receipt_log_digest(row))
    signed = sign_observation(
        row, resolved_pool.as_surface(), resolved_key, verification_timestamp=NOW + timedelta(minutes=2)
    )
    repo = OnChainRepository(tmp_path / f"runtime-{log_index}")
    repo.save_raw((row,))
    repo.save_evidence_signatures((signed,))
    cfg = config(resolved_pool)
    return row, signed, cfg, repo, trust


def price_evidence(
    *,
    effective: datetime = PRICE_AT,
    price: object = "1.000000000000000000",
    coingecko_id: str = "usd-coin",
    asset: str = "usdc",
) -> RawHistoricalEvidence:
    return RawHistoricalEvidence(
        provider="coingecko-historical",
        collector="coingecko-history",
        raw_source_id=f"historical:{asset}:{effective.isoformat()}",
        case_id=f"{asset}-point",
        project_id="uniswap",
        metric="historical_market",
        event_timestamp=effective,
        publication_timestamp=effective,
        data_availability_timestamp=effective,
        retrieval_timestamp=effective + timedelta(minutes=1),
        payload={"coingecko_id": coingecko_id, "price": price},
        source_url=f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart/range",
        repository_id=f"historical:{asset}:{effective.isoformat()}",
    )


def valuation(evidence_id: str, amount: str, price: str = "1.000000000000000000") -> UsdValuation:
    value = Decimal(amount) * Decimal(price)
    return UsdValuation(
        status="AVAILABLE",
        chain_id=1,
        asset_contract=USDC,
        coingecko_id="usd-coin",
        target_at=NOW,
        effective_at=PRICE_AT,
        retrieval_at=PRICE_AT + timedelta(minutes=1),
        price_usd=Decimal(price),
        amount=Decimal(amount),
        value_usd=value,
        source_record_id="historical:usdc:09-00",
        source_url="https://api.coingecko.com/api/v3/coins/usd-coin/market_chart/range",
        freshness_seconds=2495,
    )


def test_v2_and_v3_logs_decode_exact_direction_and_amounts() -> None:
    v2_pool = replace(pool(), version="v2")
    for pool_row, log, expected_topic in (
        (v2_pool, v2_log(side="buy", pool_row=v2_pool), V2_SWAP_TOPIC),
        (pool(), v3_log(side="sell", pool_row=pool()), V3_SWAP_TOPIC),
    ):
        decoded = decode_swap_log(pool_row, log)

        assert decoded.status == "AVAILABLE"
        assert decoded.event_topic == expected_topic
        assert decoded.trade_direction == ("buy" if log is not None and expected_topic == V2_SWAP_TOPIC else "sell")
        if expected_topic == V2_SWAP_TOPIC:
            assert decoded.token_in == WETH
            assert decoded.token_out == USDC
            assert decoded.amount_out == Decimal("17000")
        else:
            assert decoded.token_in == USDC
            assert decoded.token_out == WETH
            assert decoded.amount_in == Decimal("17000")


def test_unsupported_failed_and_unknown_pool_logs_are_rejected(tmp_path: Path) -> None:
    failed, _signed, cfg, repo, trust = signed_swap(tmp_path, receipt_status="0x0", log_index=1)
    unsupported, _signed, cfg, repo, trust = signed_swap(
        tmp_path / "unsupported", event_type="dex_swap_unsupported", log_index=2
    )
    wrong_pool, _signed, _wrong_cfg, repo, trust = signed_swap(
        tmp_path / "wrong-pool", pool_row=replace(pool(), address=OTHER_POOL), log_index=3
    )
    known_cfg = config(pool())

    assert verified_swap_rows("uniswap", (failed,), config=cfg, repository=repo, trust=trust, as_of=NOW) == ()
    assert verified_swap_rows("uniswap", (unsupported,), config=cfg, repository=repo, trust=trust, as_of=NOW) == ()
    assert verified_swap_rows("uniswap", (wrong_pool,), config=known_cfg, repository=repo, trust=trust, as_of=NOW) == ()


def test_router_and_pool_addresses_are_not_attributed_wallets(tmp_path: Path) -> None:
    router_claim, _signed, cfg, repo, trust = signed_swap(
        tmp_path, wallet=ROUTER, attribution_status="AVAILABLE", wallet_code="0x", log_index=1
    )
    unattributed, _signed, cfg, repo, trust = signed_swap(
        tmp_path / "unattributed",
        wallet=ROUTER,
        attribution_status="UNAVAILABLE",
        attributed_wallet=None,
        wallet_code=None,
        log_index=2,
    )

    assert verified_swap_rows("uniswap", (router_claim,), config=cfg, repository=repo, trust=trust, as_of=NOW) == ()
    accepted = verified_swap_rows(
        "uniswap", (unattributed,), config=cfg, repository=repo, trust=trust, as_of=NOW + timedelta(minutes=2)
    )
    assert [row.evidence_id for row in accepted] == [unattributed.evidence_id]
    performance = wallet_performance_from_swaps(
        accepted, {unattributed.evidence_id: valuation(unattributed.evidence_id, "17000")}, as_of=NOW
    )
    assert all(item.status == "UNAVAILABLE" for item in performance)


def test_multi_hop_same_asset_direction_is_ambiguous_not_double_counted(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    first, first_signed, cfg, _repo, trust = signed_swap(
        tmp_path, key=key, side="buy", log_index=1, swap_status="AMBIGUOUS"
    )
    second, second_signed, cfg, _repo, trust = signed_swap(
        tmp_path / "second", key=key, side="buy", log_index=2, swap_status="AMBIGUOUS"
    )
    combined = OnChainRepository(tmp_path / "runtime-combined")
    combined.save_raw((first, second))
    combined.save_evidence_signatures((first_signed, second_signed))
    accepted = verified_swap_rows(
        "uniswap", (first, second), config=cfg, repository=combined, trust=trust, as_of=NOW + timedelta(minutes=2)
    )

    assert len(accepted) == 2
    result = wallet_performance_from_swaps(
        accepted,
        {
            first.evidence_id: valuation(first.evidence_id, "17000"),
            second.evidence_id: valuation(second.evidence_id, "17000"),
        },
        as_of=NOW,
    )
    assert len(result) == 1
    assert result[0].status == "UNAVAILABLE"
    assert "multi_hop" in (result[0].reason or "")
    assert result[0].realized_pnl_usd is None


def test_swap_amount_decimal_precision_is_exact(tmp_path: Path) -> None:
    raw = "12345678901234567890123456"
    row, _signed, cfg, repo, trust = signed_swap(tmp_path, usdc_raw=int(raw), log_index=1)

    accepted = verified_swap_rows(
        "uniswap", (row,), config=cfg, repository=repo, trust=trust, as_of=NOW + timedelta(minutes=2)
    )

    assert [item.evidence_id for item in accepted] == [row.evidence_id]
    assert accepted[0].normalized_amount == Decimal("12345678901234567890.123456")


def test_swap_usd_valuation_rejects_future_stale_and_missing_prices(tmp_path: Path) -> None:
    row, _signed, _cfg, _repo, _trust = signed_swap(tmp_path, log_index=1)
    repository = HistoricalEvidenceRepository(tmp_path / "historical")
    repository.save_raw((price_evidence(),))

    causal = HistoricalUsdValuator(config(), repository).value(row)
    stale = HistoricalUsdValuator(config(), repository, max_price_age=timedelta(seconds=1)).value(row)
    future_repository = HistoricalEvidenceRepository(tmp_path / "future")
    future_repository.save_raw((price_evidence(effective=NOW + timedelta(hours=1)),))

    assert causal.status == "AVAILABLE"
    assert causal.value_usd == Decimal("17000")
    assert stale.status == "UNAVAILABLE"
    assert "freshness" in (stale.reason or "")
    assert HistoricalUsdValuator(config(), future_repository).value(row).status == "UNAVAILABLE"
    assert (
        HistoricalUsdValuator(config(), HistoricalEvidenceRepository(tmp_path / "empty")).value(row).status
        == "UNAVAILABLE"
    )


def test_wallet_performance_partial_inventory_fees_and_cross_chain_are_unavailable() -> None:
    buy = RawOnChainObservation(
        **{
            **vars(signed_observation(1, "buy", "100", WALLET)),
        }
    )
    sell = signed_observation(2, "sell", "150", WALLET)
    other_chain = replace(signed_observation(3, "sell", "50", SECOND_WALLET), chain_id=137)
    valuations = {
        buy.evidence_id: valuation(buy.evidence_id, "100"),
        sell.evidence_id: valuation(sell.evidence_id, "150"),
        other_chain.evidence_id: valuation(other_chain.evidence_id, "50"),
    }

    result = wallet_performance_from_swaps((buy, sell, other_chain), valuations, as_of=NOW)

    assert {item.wallet for item in result} == {WALLET, SECOND_WALLET}
    assert all(item.status == "UNAVAILABLE" for item in result)
    assert any("partial_inventory" in (item.reason or "") for item in result)
    assert all(item.net_pnl_usd is None for item in result)
    assert all("fees" in item.unavailable_fields for item in result)


def test_wallet_performance_fifo_profitable_and_losing_cases() -> None:
    buys = (signed_observation(1, "buy", "100", WALLET), signed_observation(2, "buy", "100", WALLET))
    sell = signed_observation(3, "sell", "150", WALLET)
    valuations = {
        buys[0].evidence_id: valuation(buys[0].evidence_id, "100", "1"),
        buys[1].evidence_id: valuation(buys[1].evidence_id, "100", "2"),
        sell.evidence_id: valuation(sell.evidence_id, "150", "3"),
    }

    result = wallet_performance_from_swaps((*buys, sell), valuations, as_of=NOW)[0]

    assert result.status == "AVAILABLE"
    assert result.realized_pnl_usd == Decimal("250")
    assert result.cost_basis_usd == Decimal("200")
    assert result.realized_return == Decimal("1.25")
    assert result.coverage == Decimal("1")
    assert result.sample_size == 1
    assert result.net_pnl_usd is None

    losing_sell = signed_observation(4, "sell", "50", WALLET)
    losing_valuations = {**valuations, losing_sell.evidence_id: valuation(losing_sell.evidence_id, "50", "0.5")}
    losing = wallet_performance_from_swaps((*buys, sell, losing_sell), losing_valuations, as_of=NOW)[0]

    assert losing.realized_pnl_usd == Decimal("175")
    assert losing.win_rate == Decimal("0.5")


def test_wallet_performance_fifo_keeps_exact_remaining_lot_at_large_scale() -> None:
    huge = Decimal("1000000000000000000000000000000")
    remaining = Decimal("999999999999999999999999999999")

    def exact_valuation(row: RawOnChainObservation, usd: str) -> UsdValuation:
        return replace(valuation(row.evidence_id, "1", "1"), amount=row.normalized_amount, value_usd=Decimal(usd))

    buy = signed_observation(1, "buy", str(huge), WALLET)
    small_sell = signed_observation(2, "sell", "1", WALLET)
    remaining_sell = signed_observation(3, "sell", str(remaining), WALLET)
    excess_sell = signed_observation(4, "sell", str(huge), WALLET)
    valuations = {
        buy.evidence_id: exact_valuation(buy, str(huge)),
        small_sell.evidence_id: exact_valuation(small_sell, "2"),
        remaining_sell.evidence_id: exact_valuation(remaining_sell, "2999999999999999999999999999997"),
        excess_sell.evidence_id: exact_valuation(excess_sell, "3000000000000000000000000000000"),
    }

    sufficient = wallet_performance_from_swaps((buy, small_sell, remaining_sell), valuations, as_of=NOW)[0]
    insufficient = wallet_performance_from_swaps((buy, small_sell, excess_sell), valuations, as_of=NOW)[0]

    assert sufficient.status == "AVAILABLE"
    assert sufficient.coverage == Decimal("1")
    assert sufficient.cost_basis_usd == huge
    assert insufficient.status == "UNAVAILABLE"
    assert "partial_inventory" in (insufficient.reason or "")
    assert insufficient.realized_pnl_usd is None


def test_swap_evidence_rejects_post_cutoff_provenance(tmp_path: Path) -> None:
    cutoff = NOW
    row, _signed, cfg, repo, trust = signed_swap(
        tmp_path,
        log_index=1,
        block_timestamp=cutoff - timedelta(minutes=1),
    )

    assert row.block_timestamp == cutoff - timedelta(minutes=1)
    assert row.acquisition_timestamp == cutoff + timedelta(minutes=1)
    assert row.receipt_verified_at == cutoff + timedelta(minutes=2)

    assert verified_swap_rows("uniswap", (row,), config=cfg, repository=repo, trust=trust, as_of=cutoff) == ()
    assert _swap_wallet_context("uniswap", (row,), as_of=cutoff, config=cfg, repository=repo, trust=trust) is None

    causal = verified_swap_rows(
        "uniswap", (row,), config=cfg, repository=repo, trust=trust, as_of=cutoff + timedelta(minutes=2)
    )
    assert [item.evidence_id for item in causal] == [row.evidence_id]


def test_swap_evidence_replay_and_tamper_rejection(tmp_path: Path) -> None:
    row, _signed, cfg, repo, trust = signed_swap(tmp_path, log_index=1)

    first = verified_swap_rows(
        "uniswap", (row,), config=cfg, repository=repo, trust=trust, as_of=NOW + timedelta(minutes=2)
    )
    second = verified_swap_rows(
        "uniswap", (row,), config=cfg, repository=repo, trust=trust, as_of=NOW + timedelta(minutes=2)
    )
    tampered = replace(row, raw_amount="99999999999999")

    assert first == second
    assert verified_swap_rows("uniswap", (tampered,), config=cfg, repository=repo, trust=trust, as_of=NOW) == ()


def test_market_validation_swap_wallet_context_uses_identity_and_usd_integration(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    buy, buy_signed, cfg, repo, trust = signed_swap(tmp_path, key=key, side="buy", log_index=1)
    sell, sell_signed, cfg, repo, trust = signed_swap(
        tmp_path / "sell", key=key, side="sell", log_index=2, usdc_raw=8_000_000_000, weth_raw=2 * 10**18
    )
    combined = OnChainRepository(tmp_path / "runtime-combined")
    combined.save_raw((buy, sell))
    combined.save_evidence_signatures((buy_signed, sell_signed))
    historical = HistoricalEvidenceRepository(tmp_path / "historical")
    historical.save_raw(
        (
            price_evidence(),
            price_evidence(coingecko_id="ethereum", asset="weth", price="2500"),
        )
    )

    source = _swap_wallet_context(
        "uniswap",
        (buy, sell),
        as_of=NOW + timedelta(minutes=2),
        config=cfg,
        repository=combined,
        trust=trust,
        usd_valuator=HistoricalUsdValuator(cfg, historical).value,
    )

    assert source is not None
    assert source.engine == "whale_intelligence"
    assert source.status == "AVAILABLE"
    assert source.raw_input_metrics["verified_swap_count"] == 2
    assert source.raw_input_metrics["fifo_policy"] == "deterministic-fifo"
    assert source.raw_input_metrics["net_pnl_usd"] == "UNAVAILABLE"
    assert source.raw_input_metrics["usd_provenance"] == "coingecko-historical-market-chart"
    expected_cost = Decimal("12500") * Decimal("8000") / Decimal("17000")
    assert source.raw_input_metrics["cost_basis_usd"] == str(expected_cost)
    assert source.raw_input_metrics["realized_pnl_usd"] == str(Decimal("5000") - expected_cost)


def test_swap_wallet_context_does_not_substitute_target_notional_for_consideration(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    buy, buy_signed, cfg, repo, trust = signed_swap(tmp_path, key=key, side="buy", log_index=1)
    sell, sell_signed, cfg, repo, trust = signed_swap(
        tmp_path / "sell", key=key, side="sell", log_index=2, usdc_raw=8_000_000_000, weth_raw=2 * 10**18
    )
    combined = OnChainRepository(tmp_path / "runtime-combined")
    combined.save_raw((buy, sell))
    combined.save_evidence_signatures((buy_signed, sell_signed))
    historical = HistoricalEvidenceRepository(tmp_path / "historical")
    historical.save_raw((price_evidence(),))

    source = _swap_wallet_context(
        "uniswap",
        (buy, sell),
        as_of=NOW + timedelta(minutes=2),
        config=cfg,
        repository=combined,
        trust=trust,
        usd_valuator=HistoricalUsdValuator(cfg, historical).value,
    )

    assert source is not None
    assert source.status == "UNAVAILABLE"
    assert source.raw_input_metrics["cost_basis_usd"] is None
    assert source.raw_input_metrics["realized_pnl_usd"] is None
    assert source.raw_input_metrics["usd_provenance"] == "coingecko-historical-market-chart"


def test_swap_observations_use_each_logs_own_block_hash_and_timestamp(tmp_path: Path) -> None:
    resolved_pool = pool()
    first_tx = "0x" + "aa" * 32
    second_tx = "0x" + "bb" * 32
    first_hash = "0x" + "11" * 32
    second_hash = "0x" + "22" * 32
    first_at = int((NOW - timedelta(minutes=5)).timestamp())
    second_at = int(NOW.timestamp())
    first_log = {
        **v3_log(side="buy", pool_row=resolved_pool),
        "transactionHash": first_tx,
        "blockNumber": "0x2bc",
        "blockHash": first_hash,
        "logIndex": "0x1",
    }
    second_log = {
        **v3_log(side="buy", pool_row=resolved_pool),
        "transactionHash": second_tx,
        "blockNumber": "0x384",
        "blockHash": second_hash,
        "logIndex": "0x2",
    }
    wide_chain = chain(
        swap_rpc_budget=30, rpc_endpoints=("https://first.test", "https://second.test", "https://third.test")
    )
    transport = SwapTransport(
        logs=(first_log, second_log),
        blocks={700: (first_hash, first_at), 900: (second_hash, second_at)},
    )
    adapter = EVMJsonRpcAdapter(wide_chain, transport=transport)
    engine = CapitalFlowEngine(
        config(resolved_pool, chains=(wide_chain,)),
        repository=OnChainRepository(tmp_path),
        adapters={1: adapter},
        evidence_signing_key=Ed25519PrivateKey.generate(),
        clock=FixedClock(NOW),
    )

    rows, _signatures, unavailable = engine._swap_observations(
        resolved_pool, adapter, start_block=700, end_block=900, block_hash=second_hash, end_timestamp=NOW
    )

    assert unavailable == ()
    assert len(rows) == 2
    assert rows[0].block_number == 700
    assert rows[0].block_hash == first_hash
    assert rows[0].block_timestamp == datetime.fromtimestamp(first_at, tz=UTC)
    assert rows[1].block_hash == second_hash
    assert rows[1].block_timestamp == datetime.fromtimestamp(second_at, tz=UTC)


def test_swap_rpc_meter_charges_each_attempt_before_dispatch_including_failures() -> None:
    from hunter.onchain.adapters import RpcAttemptMeter

    transport = SwapTransport(fail_on_call=2)
    adapter = EVMJsonRpcAdapter(chain(swap_rpc_budget=2), transport=transport)

    with adapter.request_budget(2) as meter:
        assert isinstance(meter, RpcAttemptMeter)
        assert adapter.chain_id() == 1
        with pytest.raises(EVMProviderUnavailable):
            adapter.logs(address=POOL, from_block=900, to_block=936, topics=(V3_SWAP_TOPIC,))

    assert meter.used == 2
    assert transport.calls == ["eth_chainId", "eth_getLogs"]


def test_local_rpc_budget_exhaustion_propagates_without_provider_cooldown() -> None:
    from hunter.onchain import adapters

    budget_error = adapters.RpcAttemptBudgetExhausted
    transport = SwapTransport()
    adapter = EVMJsonRpcAdapter(chain(swap_rpc_budget=2), transport=transport)

    with adapter.request_budget(1) as meter:
        assert adapter.latest_finalized_block() == 936
        with pytest.raises(budget_error) as exc:
            adapter.logs(address=POOL, from_block=900, to_block=936, topics=(V3_SWAP_TOPIC,))

    assert exc.value.failure_type == "local_budget_exhausted"
    assert meter.used == 1
    assert transport.calls == ["eth_blockNumber"]
    assert adapter._cooldown_until == {}
    assert adapter._cooldown_failure_type == {}


def test_retry_attempts_are_charged_to_the_total_acquisition_budget() -> None:
    transport = SwapTransport(fail_on_calls=frozenset({1, 2}))
    adapter = EVMJsonRpcAdapter(chain(retry_limit=2), transport=transport)

    with adapter.request_budget(3) as meter:
        assert adapter.chain_id() == 1

    assert meter.used == 3
    assert transport.calls == ["eth_chainId", "eth_chainId", "eth_chainId"]


def test_nested_swap_budget_cannot_reset_total_acquisition_budget() -> None:
    transport = SwapTransport()
    adapter = EVMJsonRpcAdapter(chain(), transport=transport)

    with adapter.request_budget(1) as total:
        assert adapter.latest_finalized_block() == 936
        with adapter.request_budget(12) as swap:
            assert swap.limit == total.limit
            with pytest.raises(RpcAttemptBudgetExhausted):
                adapter.logs(address=POOL, from_block=900, to_block=936, topics=(V3_SWAP_TOPIC,))
        assert total.used == 1

    assert transport.calls == ["eth_blockNumber"]


def test_sync_total_acquisition_budget_bounds_transfers_and_preserves_partial_coverage(tmp_path: Path) -> None:
    bounded = chain(
        acquisition_rpc_budget=20,
        rpc_endpoints=("https://first.test", "https://second.test", "https://third.test"),
    )
    counterparty = "0x" + "22" * 20

    def transfer_log(index: int) -> dict[str, Any]:
        return {
            "address": USDC,
            "topics": [
                TRANSFER_TOPIC,
                "0x" + counterparty[2:].zfill(64),
                "0x" + WALLET[2:].zfill(64),
            ],
            "data": "0x" + format(1_000_000, "064x"),
            "transactionHash": "0x" + f"{index:064x}",
            "logIndex": hex(index),
            "blockNumber": hex(900),
            "blockHash": BLOCK_HASH,
        }

    logs = tuple(transfer_log(index) for index in range(1, 101))
    transport = SwapTransport(logs=logs, receipt_logs=logs)
    surface = OnChainSurface(
        project="uniswap",
        chain_id=1,
        network="ethereum",
        address=WALLET,
        address_type="other_documented",
        protocol_role="tracked_wallet",
        asset_scope="tracked_transfer",
        source_url="https://etherscan.io/address/" + WALLET,
        source_type="verified_block_explorer",
        verification_timestamp=NOW,
        confidence=1.0,
        active=True,
        valid_from=NOW - timedelta(days=1),
        evidence_id="transfer-surface",
    )
    cfg = replace(
        config(chains=(bounded,)),
        tracked_assets=(AssetConfig(1, "USDC", "stablecoin", 6, USDC),),
        surfaces=(surface,),
        dex_pools=(),
    )
    repository = OnChainRepository(tmp_path)
    engine = CapitalFlowEngine(
        cfg,
        repository=repository,
        adapters={1: EVMJsonRpcAdapter(bounded, transport=transport)},
        evidence_signing_key=Ed25519PrivateKey.generate(),
        clock=FixedClock(NOW),
    )

    snapshot = engine.sync(end_block=936)[0]

    assert len(transport.calls) <= bounded.acquisition_rpc_budget
    assert snapshot.status == "partial"
    assert "rpc_budget_exhausted" in snapshot.unavailable_fields
    assert repository.checkpoints() == ()
    transfers = [row for row in repository.raw() if row["event_type"] == "erc20_transfer"]
    assert transfers


def test_swap_observations_record_actual_rpc_attempt_count(tmp_path: Path) -> None:
    resolved_pool = pool()
    wide_chain = chain(
        swap_rpc_budget=30,
        rpc_endpoints=("https://first.test", "https://second.test", "https://third.test"),
    )
    transport = SwapTransport(logs=(v3_log(side="buy", pool_row=resolved_pool),))
    adapter = EVMJsonRpcAdapter(wide_chain, transport=transport)
    engine = CapitalFlowEngine(
        config(resolved_pool, chains=(wide_chain,)),
        repository=OnChainRepository(tmp_path),
        adapters={1: adapter},
        evidence_signing_key=Ed25519PrivateKey.generate(),
        clock=FixedClock(NOW),
    )

    rows, _signatures, _unavailable = engine._swap_observations(
        resolved_pool, adapter, start_block=900, end_block=936, block_hash=BLOCK_HASH, end_timestamp=NOW
    )

    assert len(rows) == 1
    setup_calls = transport.calls.index("eth_getBlockByNumber")
    assert rows[0].rpc_request_count == len(transport.calls) - setup_calls
    assert rows[0].rpc_request_count < len(transport.calls)
    assert len(transport.calls) <= 30


def test_each_swap_records_its_own_rpc_attempt_count(tmp_path: Path) -> None:
    resolved_pool = pool()
    wide_chain = chain(
        swap_rpc_budget=40,
        rpc_endpoints=("https://first.test", "https://second.test", "https://third.test"),
    )
    first_log = v3_log(side="buy", pool_row=resolved_pool)
    second_log = {
        **v3_log(side="buy", pool_row=resolved_pool),
        "transactionHash": "0x" + "cd" * 32,
        "logIndex": "0x8",
    }

    def collect(logs: tuple[dict[str, Any], ...], directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        transport = SwapTransport(logs=logs, receipt_logs=logs)
        adapter = EVMJsonRpcAdapter(wide_chain, transport=transport)
        engine = CapitalFlowEngine(
            config(resolved_pool, chains=(wide_chain,)),
            repository=OnChainRepository(directory),
            adapters={1: adapter},
            evidence_signing_key=Ed25519PrivateKey.generate(),
            clock=FixedClock(NOW),
        )
        rows, _signatures, unavailable = engine._swap_observations(
            resolved_pool, adapter, start_block=900, end_block=936, block_hash=BLOCK_HASH, end_timestamp=NOW
        )
        return rows, transport.calls, unavailable

    single_rows, single_calls, single_unavailable = collect((first_log,), tmp_path / "single")
    both_rows, both_calls, both_unavailable = collect((first_log, second_log), tmp_path / "both")

    assert len(single_rows) == 1, single_unavailable
    assert len(both_rows) == 2, both_unavailable
    single_count = len(single_calls)
    total_count = len(both_calls)
    per_swap_count = single_rows[0].rpc_request_count
    setup_count = single_count - per_swap_count
    assert setup_count > 0
    assert [row.rpc_request_count for row in both_rows] == [per_swap_count, per_swap_count]
    assert total_count == setup_count + sum(row.rpc_request_count for row in both_rows)
    assert both_rows[1].rpc_request_count < total_count


def test_overlapping_swap_replay_preserves_original_signed_metadata(tmp_path: Path) -> None:
    first, first_signed, _cfg, _repo, trust = signed_swap(tmp_path, log_index=1, rpc_request_count=12)
    replay = replace(
        first,
        rpc_request_count=7,
        acquisition_timestamp=NOW + timedelta(minutes=3),
        receipt_verified_at=NOW + timedelta(minutes=3),
    )

    preserved = _preserved_signed_row(replay, vars(first))

    assert preserved is not None
    assert preserved.rpc_request_count == 12
    assert preserved.acquisition_timestamp == first.acquisition_timestamp
    assert preserved.receipt_verified_at == first.receipt_verified_at
    assert (
        verify_observation(
            preserved,
            (first_signed,),
            pool().as_surface(),
            trust,
            as_of=NOW + timedelta(minutes=10),
            pool=pool(),
        )
        == preserved
    )
    assert _preserved_signed_row(replace(replay, raw_amount="99999999999999"), vars(first)) is None
    assert _preserved_signed_row(replace(replay, event_type="transfer"), vars(first)) is None
    assert _preserved_signed_row(replace(replay, provider="other"), vars(first)) is None
    assert _preserved_signed_row(replace(replay, source_reference="other"), vars(first)) is None
    assert _preserved_signed_row(replace(replay, finality_status="pending"), vars(first)) is None


def test_wallet_performance_isolates_fifo_by_exact_target_asset() -> None:
    usdc_buy = signed_observation(1, "buy", "100", WALLET)
    weth_sell = replace(
        signed_observation(2, "sell", "100", WALLET),
        target_asset_contract=WETH,
        asset_contract=WETH,
        asset_symbol="WETH",
        decimals=18,
    )
    valuations = {
        usdc_buy.evidence_id: valuation(usdc_buy.evidence_id, "100"),
        weth_sell.evidence_id: valuation(weth_sell.evidence_id, "100"),
    }

    result = wallet_performance_from_swaps((usdc_buy, weth_sell), valuations, as_of=NOW)

    assert len(result) == 2
    assert {item.target_asset_contract for item in result} == {USDC, WETH}
    assert all(item.status == "UNAVAILABLE" for item in result)
    assert all(item.realized_pnl_usd is None for item in result)


def test_swap_wallet_freshness_is_decreasing_and_normalized() -> None:
    from hunter.market_validation.acquisition_sources import normalized_freshness

    assert normalized_freshness(NOW, NOW) == 1.0
    assert normalized_freshness(NOW - timedelta(hours=12), NOW) == 0.5
    assert normalized_freshness(NOW - timedelta(hours=24), NOW) == 0.0
    assert normalized_freshness(NOW - timedelta(hours=25), NOW) == 0.0
    assert normalized_freshness(NOW + timedelta(seconds=2), NOW) == 1.0


def test_swap_wallet_context_uses_normalized_freshness(tmp_path: Path) -> None:
    cutoff = NOW + timedelta(minutes=2)
    row, _signed, cfg, repo, trust = signed_swap(tmp_path, log_index=1, block_timestamp=cutoff)

    source = _swap_wallet_context("uniswap", (row,), as_of=cutoff, config=cfg, repository=repo, trust=trust)

    assert source is not None
    assert source.freshness == 1.0
    assert row.evidence_id in source.evidence_ids


def test_full_receipt_multihop_leg_is_not_wallet_attributed(tmp_path: Path) -> None:
    resolved_pool = pool()
    other_pool = replace(pool(), pool_id="other-pool", address=OTHER_POOL)
    first_log = v3_log(side="buy", pool_row=resolved_pool)
    second_log = {
        **v3_log(side="sell", pool_row=other_pool),
        "address": OTHER_POOL,
        "logIndex": "0x8",
    }
    wide_chain = chain(
        swap_rpc_budget=30,
        rpc_endpoints=("https://first.test", "https://second.test", "https://third.test"),
    )
    transport = SwapTransport(logs=(first_log,), receipt_logs=(first_log, second_log))
    adapter = EVMJsonRpcAdapter(wide_chain, transport=transport)
    engine = CapitalFlowEngine(
        config(resolved_pool, chains=(wide_chain,)),
        repository=OnChainRepository(tmp_path),
        adapters={1: adapter},
        evidence_signing_key=Ed25519PrivateKey.generate(),
        clock=FixedClock(NOW),
    )

    rows, _signatures, _unavailable = engine._swap_observations(
        resolved_pool, adapter, start_block=900, end_block=936, block_hash=BLOCK_HASH, end_timestamp=NOW
    )

    assert len(rows) == 1
    assert rows[0].swap_status == "AMBIGUOUS"
    assert rows[0].attributed_wallet is None
    assert rows[0].attribution_status != "AVAILABLE"


def test_identity_scheduler_configuration_is_enabled_and_gated() -> None:
    jobs = require_discovery_automation(Path("configs/automation.yaml"))
    payload = yaml.safe_load(Path("configs/automation.yaml").read_text(encoding="utf-8"))
    job = next(item for item in payload["jobs"] if item["job_id"] == "discovery-identity-resolution")

    assert jobs == DISCOVERY_JOB_IDS
    assert job["enabled"] is True
    assert job["target"]["id"] == "discovery_identity_resolve"
    assert job["metadata"]["evidence_gated"] is True
    assert job["metadata"]["bounded_fair_batch"] is True
    assert job["metadata"]["no_parallel_daemon"] is True


def test_adapter_enforces_bounded_swap_rpc_budget(tmp_path: Path) -> None:
    from hunter.onchain.engine import CapitalFlowEngine

    bounded = chain(swap_rpc_budget=3, rpc_endpoints=("https://first.test",))
    transport = SwapTransport()
    adapter = EVMJsonRpcAdapter(bounded, transport=transport)
    engine = CapitalFlowEngine(
        config(pool(), chains=(bounded,)),
        repository=OnChainRepository(tmp_path),
        evidence_signing_key=Ed25519PrivateKey.generate(),
        adapters={1: adapter},
    )

    observations, signatures, unavailable = engine._swap_observations(
        pool(), adapter, start_block=900, end_block=936, block_hash=BLOCK_HASH, end_timestamp=NOW
    )

    assert observations == ()
    assert signatures == ()
    assert "swap_rpc_budget_exhausted" in unavailable
    assert transport.calls.count("eth_getTransactionReceipt") <= 3


def test_sync_persists_verified_swap_evidence_on_existing_engine_seam(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    resolved_pool = pool()
    sync_chain = chain(rpc_endpoints=("https://first.test", "https://second.test", "https://third.test"))
    cfg = replace(
        config(resolved_pool, chains=(sync_chain,)),
        tracked_assets=(),
        surfaces=(resolved_pool.as_surface(),),
    )
    transport = SwapTransport(logs=(v3_log(side="buy", pool_row=resolved_pool),))
    repository = OnChainRepository(tmp_path / "runtime-sync")
    _, _, trust = signing_material(tmp_path / "trust", key)
    engine = CapitalFlowEngine(
        cfg,
        repository=repository,
        adapters={1: EVMJsonRpcAdapter(sync_chain, transport=transport)},
        evidence_signing_key=key,
        clock=FixedClock(NOW),
    )

    snapshots = engine.sync(end_block=936)

    assert len(snapshots) == 1
    swaps = verified_swap_rows(
        "uniswap",
        repository.raw(),
        config=cfg,
        repository=repository,
        trust=trust,
        as_of=NOW + timedelta(minutes=1),
    )
    assert len(swaps) == 1
    assert swaps[0].attributed_wallet == WALLET
    assert swaps[0].swap_pool_id == resolved_pool.pool_id
    assert any(row.get("signature_id") == signature_id(swaps[0]) for row in repository.evidence_signatures())


def signed_observation(log_index: int, side: str, amount: str, wallet: str) -> RawOnChainObservation:
    raw = int(Decimal(amount) * 10**6)
    return RawOnChainObservation(
        project="uniswap",
        chain_id=1,
        provider="evm-json-rpc",
        endpoint_identity="first.test",
        block_number=900 + log_index,
        block_hash=BLOCK_HASH,
        block_timestamp=NOW + timedelta(seconds=log_index),
        observed_address=POOL,
        acquisition_timestamp=NOW + timedelta(minutes=1),
        finality_status="finalized",
        source_reference="https://docs.uniswap.org",
        evidence_id=f"swap-{log_index}",
        transaction_hash="0x" + format(log_index, "064x"),
        log_index=log_index,
        counterparty_address=wallet,
        asset_contract=USDC,
        asset_symbol="USDC",
        raw_amount=str(raw),
        decimals=6,
        normalized_amount=Decimal(amount),
        direction="unknown",
        event_type="dex_swap",
        log_address=POOL,
        swap_pool_id="uniswap-v3-usdc-weth-0005",
        swap_dex="uniswap",
        swap_version="v3",
        swap_event_topic=V3_SWAP_TOPIC,
        token_in_contract=WETH if side == "buy" else USDC,
        token_out_contract=USDC if side == "buy" else WETH,
        token_in_raw_amount=str(5 * 10**18) if side == "buy" else str(raw),
        token_out_raw_amount=str(raw) if side == "buy" else str(5 * 10**18),
        token_in_amount=Decimal("5") if side == "buy" else Decimal(amount),
        token_out_amount=Decimal(amount) if side == "buy" else Decimal("5"),
        target_asset_contract=USDC,
        trade_direction=side,
        attributed_wallet=wallet,
        attribution_status="AVAILABLE",
        receipt_from=wallet,
        receipt_status="0x1",
        wallet_code="0x",
        swap_status="AVAILABLE",
        rpc_request_count=7,
    )
