from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Any, Literal

from hunter.onchain.adapters import EVMProviderUnavailable
from hunter.onchain.models import DexPoolConfig

V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
DECIMALS_SELECTOR = "0x313ce567"
_ADDRESS_PATTERN = re.compile(r"0x[0-9a-f]{40}")
_HEX_PATTERN = re.compile(r"0x[0-9a-f]+")
_INT256_MODULUS = 1 << 256


@dataclass(frozen=True, slots=True)
class DecodedSwap:
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: str | None = None
    dex: str = ""
    version: str = ""
    pool_id: str = ""
    event_topic: str = ""
    pool_address: str = ""
    sender: str | None = None
    recipient: str | None = None
    token_in: str | None = None
    token_out: str | None = None
    raw_amount_in: int = 0
    raw_amount_out: int = 0
    amount_in: Decimal = Decimal(0)
    amount_out: Decimal = Decimal(0)
    trade_direction: str = "unknown"


def v2_swap_data(
    pool_row: DexPoolConfig,
    *,
    side: str,
    token0_raw: int | None = None,
    token1_raw: int | None = None,
    usdc_raw: int | None = None,
    weth_raw: int | None = None,
) -> str:
    first = token0_raw if token0_raw is not None else usdc_raw or 0
    second = token1_raw if token1_raw is not None else weth_raw or 0
    amount0_in = first if _target_is_token0(pool_row) is not (side == "buy") else 0
    amount1_in = second if _target_is_token0(pool_row) is (side == "buy") else 0
    amount0_out = first if _target_is_token0(pool_row) is (side == "buy") else 0
    amount1_out = second if _target_is_token0(pool_row) is not (side == "buy") else 0
    return "0x" + "".join(_word(value) for value in (amount0_in, amount1_in, amount0_out, amount1_out))


def v3_swap_data(
    pool_row: DexPoolConfig,
    *,
    side: str,
    token0_raw: int | None = None,
    token1_raw: int | None = None,
    usdc_raw: int | None = None,
    weth_raw: int | None = None,
) -> str:
    first = token0_raw if token0_raw is not None else usdc_raw or 0
    second = token1_raw if token1_raw is not None else weth_raw or 0
    target_is_token0 = _target_is_token0(pool_row)
    amount0 = -first if target_is_token0 is (side == "buy") else first
    amount1 = second if target_is_token0 is (side == "buy") else -second
    return "0x" + "".join((_signed_word(amount0), _signed_word(amount1), _word(1 << 100), _word(1 << 128), _word(1)))


def decode_swap_log(pool_row: DexPoolConfig, log: dict[str, Any]) -> DecodedSwap:
    expected_topic = V2_SWAP_TOPIC if pool_row.version == "v2" else V3_SWAP_TOPIC if pool_row.version == "v3" else ""
    unavailable = DecodedSwap(
        status="UNAVAILABLE",
        reason="unsupported_dex_schema",
        dex=pool_row.dex,
        version=pool_row.version,
        pool_id=pool_row.pool_id,
    )
    if not expected_topic:
        return unavailable
    if str(log.get("address", "")).lower() != pool_row.address:
        return replace(unavailable, reason="pool_address_mismatch")
    topics = tuple(str(item).lower() for item in log.get("topics", ()))
    if len(topics) != 3 or topics[0] != expected_topic:
        return replace(unavailable, reason="unsupported_swap_event")
    sender = _address_from_topic(topics[1])
    recipient = _address_from_topic(topics[2])
    if sender is None or recipient is None:
        return replace(unavailable, reason="malformed_swap_topics")
    data = str(log.get("data", "")).lower()
    if _HEX_PATTERN.fullmatch(data) is None:
        return replace(unavailable, reason="malformed_swap_data")
    words = _words(data)
    if pool_row.version == "v2" and len(words) != 4:
        return replace(unavailable, reason="unsupported_v2_swap_data")
    if pool_row.version == "v3" and len(words) != 5:
        return replace(unavailable, reason="unsupported_v3_swap_data")
    base = DecodedSwap(
        status="AVAILABLE",
        dex=pool_row.dex,
        version=pool_row.version,
        pool_id=pool_row.pool_id,
        event_topic=expected_topic,
        pool_address=pool_row.address,
        sender=sender,
        recipient=recipient,
    )
    if pool_row.version == "v2":
        values = tuple(int(word, 16) for word in words[:4])
        decoded = _decode_v2(pool_row, base, values)
    else:
        values = tuple(_signed_int(word) for word in words[:2])
        decoded = _decode_v3(pool_row, base, values)
    if decoded.status != "AVAILABLE":
        return decoded
    if decoded.token_in not in (pool_row.token0, pool_row.token1) or decoded.token_out not in (
        pool_row.token0,
        pool_row.token1,
    ):
        return replace(base, status="UNAVAILABLE", reason="token_not_in_verified_pool")
    return decoded


def verify_pool_identity(pool_row: DexPoolConfig, adapter: Any, block_number: int) -> str | None:
    try:
        token0 = _address_from_word(adapter.contract_call(pool_row.address, TOKEN0_SELECTOR, block_number))
        token1 = _address_from_word(adapter.contract_call(pool_row.address, TOKEN1_SELECTOR, block_number))
        decimals0 = int(str(adapter.contract_call(pool_row.token0, DECIMALS_SELECTOR, block_number)), 16)
        decimals1 = int(str(adapter.contract_call(pool_row.token1, DECIMALS_SELECTOR, block_number)), 16)
    except (EVMProviderUnavailable, ValueError, TypeError):
        return "pool_identity_rpc_unavailable"
    if token0 != pool_row.token0 or token1 != pool_row.token1:
        return "pool_token_contracts_mismatch"
    if decimals0 != pool_row.token0_decimals or decimals1 != pool_row.token1_decimals:
        return "pool_token_decimals_mismatch"
    return None


def _decode_v2(pool_row: DexPoolConfig, base: DecodedSwap, values: tuple[int, int, int, int]) -> DecodedSwap:
    amount0_in, amount1_in, amount0_out, amount1_out = values
    if (amount0_in > 0) == (amount1_in > 0) or (amount0_out > 0) == (amount1_out > 0):
        return replace(base, status="UNAVAILABLE", reason="unsupported_swap_shape")
    if amount0_in and amount0_out or amount1_in and amount1_out:
        return replace(base, status="UNAVAILABLE", reason="unsupported_swap_shape")
    if amount0_in:
        return _finish(pool_row, base, pool_row.token0, amount0_in, pool_row.token1, amount1_out)
    if amount1_in:
        return _finish(pool_row, base, pool_row.token1, amount1_in, pool_row.token0, amount0_out)
    return replace(base, status="UNAVAILABLE", reason="unsupported_swap_shape")


def _decode_v3(pool_row: DexPoolConfig, base: DecodedSwap, values: tuple[int, int]) -> DecodedSwap:
    amount0, amount1 = values
    if (amount0 < 0) == (amount1 < 0) or amount0 == 0 or amount1 == 0:
        return replace(base, status="UNAVAILABLE", reason="unsupported_swap_shape")
    if amount0 > 0:
        return _finish(pool_row, base, pool_row.token0, amount0, pool_row.token1, -amount1)
    return _finish(pool_row, base, pool_row.token1, amount1, pool_row.token0, -amount0)


def _finish(
    pool_row: DexPoolConfig,
    base: DecodedSwap,
    token_in: str,
    raw_in: int,
    token_out: str,
    raw_out: int,
) -> DecodedSwap:
    if raw_in <= 0 or raw_out <= 0:
        return replace(base, status="UNAVAILABLE", reason="nonpositive_swap_amount")
    decimals_in = pool_row.token0_decimals if token_in == pool_row.token0 else pool_row.token1_decimals
    decimals_out = pool_row.token0_decimals if token_out == pool_row.token0 else pool_row.token1_decimals
    direction = (
        "buy" if token_out == pool_row.target_asset else "sell" if token_in == pool_row.target_asset else "unrelated"
    )
    if direction == "unrelated":
        return replace(base, status="UNAVAILABLE", reason="target_asset_not_in_swap")
    return replace(
        base,
        token_in=token_in,
        token_out=token_out,
        raw_amount_in=raw_in,
        raw_amount_out=raw_out,
        amount_in=_normalized(raw_in, decimals_in),
        amount_out=_normalized(raw_out, decimals_out),
        trade_direction=direction,
    )


def _target_is_token0(pool_row: DexPoolConfig) -> bool:
    return pool_row.target_asset == pool_row.token0


def _normalized(raw: int, decimals: int) -> Decimal:
    with localcontext() as context:
        context.prec = max(len(str(raw)) + decimals + 1, 28)
        return Decimal(raw).scaleb(-decimals)


def _word(value: int) -> str:
    return format(value & (_INT256_MODULUS - 1), "064x")


def _signed_word(value: int) -> str:
    return format(value % _INT256_MODULUS, "064x")


def _signed_int(word: str) -> int:
    value = int(word, 16)
    return value - _INT256_MODULUS if value >= _INT256_MODULUS // 2 else value


def _words(data: str) -> tuple[str, ...]:
    payload = data[2:]
    if len(payload) % 64:
        return ()
    return tuple(payload[index : index + 64] for index in range(0, len(payload), 64))


def _address_from_topic(topic: str) -> str | None:
    if len(topic) != 66 or topic[2:26] != "0" * 24:
        return None
    address = "0x" + topic[-40:]
    return address if _ADDRESS_PATTERN.fullmatch(address) else None


def _address_from_word(value: object) -> str:
    address = "0x" + str(value)[-40:].lower()
    if _ADDRESS_PATTERN.fullmatch(address) is None:
        raise ValueError("malformed pool token address")
    return address
