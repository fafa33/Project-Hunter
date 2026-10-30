from unittest.mock import patch

import pytest

from hunter.comparative_valuation.ethereum_rpc import EthereumRpcReader


def test_finalized_head_rejects_unfinalized_log_requests_without_getlogs():
    reader = EthereumRpcReader()
    calls = []

    def fake_call(method, params):
        calls.append(method)
        if method == "eth_chainId":
            return "0x1"
        if method == "eth_getBlockByNumber":
            assert params == ["finalized", False]
            return {"number": "0x64"}
        raise AssertionError("unfinalized block must not reach RPC log acquisition")

    with patch.object(reader, "call", side_effect=fake_call):
        with pytest.raises(ValueError, match="exceeds finalized"):
            reader.fetch_sky_transfers(from_block=101, to_block=101)
    assert "eth_getLogs" not in calls


def test_finalized_head_fails_closed_on_wrong_chain():
    reader = EthereumRpcReader()
    with patch.object(reader, "call", return_value="0x89"):
        with pytest.raises(ValueError, match="mainnet"):
            reader.finalized_block_number()


def test_raw_transfer_not_automatically_economic_burn():
    from hunter.comparative_valuation.ethereum_rpc import EthereumTransferObservation

    assert not hasattr(EthereumTransferObservation, "amount_usd")
    assert not hasattr(EthereumTransferObservation, "verified_attribution")


def test_historical_scan_rejects_unbounded_or_future_ranges_without_logs():
    reader = EthereumRpcReader()
    with patch.object(reader, "finalized_block_number", return_value=1000):
        with patch.object(reader, "fetch_sky_transfers") as fetch:
            with pytest.raises(ValueError, match="batch budget"):
                reader.scan_finalized_sky_transfers(from_block=0, to_block=1000, max_batches=2)
            with pytest.raises(ValueError, match="finalized"):
                reader.scan_finalized_sky_transfers(from_block=1001, to_block=1001)
            fetch.assert_not_called()


def test_historical_scan_covers_exact_blocks_in_bounded_batches():
    reader = EthereumRpcReader()
    with patch.object(reader, "finalized_block_number", return_value=1000):
        with patch.object(reader, "fetch_sky_transfers", return_value=()) as fetch:
            assert reader.scan_finalized_sky_transfers(from_block=500, to_block=702, max_batches=3) == ()
    assert [c.kwargs for c in fetch.call_args_list] == [
        {"from_block": 500, "to_block": 600},
        {"from_block": 601, "to_block": 701},
        {"from_block": 702, "to_block": 702},
    ]


def test_rpc_capability_probe_does_not_confuse_finality_with_log_availability():
    reader = EthereumRpcReader()
    with patch.object(reader, "finalized_block_number", return_value=1000):
        with patch.object(reader, "call", side_effect=ValueError("block range extends beyond current head")):
            assert reader.probe_log_range(block_number=900) is False
        with patch.object(reader, "call", return_value=[]):
            assert reader.probe_log_range(block_number=900) is True
            assert reader.probe_log_range(block_number=1001) is False


def test_rpc_endpoint_requires_registered_allowlist():
    with pytest.raises(ValueError, match="registry-authorized"):
        EthereumRpcReader(endpoint="https://unapproved.example/rpc")


def test_rpc_endpoint_from_environment_still_requires_registry(monkeypatch):
    monkeypatch.setenv("HUNTER_ETHEREUM_RPC_URL", "https://unapproved.example/rpc")
    with pytest.raises(ValueError, match="registry-authorized"):
        EthereumRpcReader()


def test_rpc_endpoint_rejects_url_credentials():
    with pytest.raises(ValueError):
        EthereumRpcReader(endpoint="https://user:pass@rpc.flashbots.net")


def test_alchemy_endpoint_authorized_without_leaking_key_to_observations():
    reader = EthereumRpcReader(endpoint="https://eth-mainnet.g.alchemy.com/v2/test-placeholder")
    assert reader.source_endpoint == "https://eth-mainnet.g.alchemy.com"
    assert "test-placeholder" not in reader.source_endpoint


def test_alchemy_endpoint_rejects_nonstandard_host():
    with pytest.raises(ValueError, match="registry-authorized"):
        EthereumRpcReader(endpoint="https://eth-mainnet.g.alchemy.com.evil.test/v2/key")


def test_alchemy_free_tier_uses_ten_block_batches():
    reader = EthereumRpcReader(endpoint="https://eth-mainnet.g.alchemy.com/v2/test-placeholder")
    assert reader.max_log_blocks == 10
    with patch.object(reader, "finalized_block_number", return_value=1000):
        with patch.object(reader, "fetch_sky_transfers", return_value=()) as fetch:
            reader.scan_finalized_sky_transfers(from_block=900, to_block=920, max_batches=3)
    assert [c.kwargs for c in fetch.call_args_list] == [
        {"from_block": 900, "to_block": 909},
        {"from_block": 910, "to_block": 919},
        {"from_block": 920, "to_block": 920},
    ]
