import json
from unittest.mock import patch

import pytest

from hunter.comparative_valuation.sky_burn_index import (
    SkyBurnIndexReader,
    parse_burn_index,
)


def record(**overrides):
    entry = {
        "datetime": "2026-10-12T14:01:47+00:00",
        "block_number": 26176859,
        "tx_hash": "0x" + "a" * 64,
        "amount": "7372288.000000000000000000",
        "month": "2026-09-01",
        "msc_slug": "msc-13",
    }
    entry.update(overrides)
    return entry


def wire(*entries, **extras):
    return json.dumps({"data": list(entries), **extras}).encode()


def test_parses_sky_event_with_non_proof_source():
    result = parse_burn_index(wire(record()))
    assert result[0].sky_amount == 7372288
    assert result[0].source_id == "sky-financial-burn-index"
    assert not hasattr(result[0], "amount_usd")
    assert not hasattr(result[0], "verified_attribution")
    assert len(result[0].response_digest) == 64


def test_reordered_inputs_have_canonical_order():
    a, b = record(), record(tx_hash="0x" + "b" * 64, block_number=26176860)
    assert [v.tx_hash for v in parse_burn_index(wire(b, a))] == [v.tx_hash for v in parse_burn_index(wire(a, b))]


@pytest.mark.parametrize(
    "wire_data",
    [
        wire(record(), record()),
        wire(record(amount="NaN")),
        wire(record(block_number=True)),
        wire(record(tx_hash="not-a-hash")),
        wire(record(datetime="2026-10-12")),
        wire(record(), has_more=True),
        wire(record(), next="https://example.test/next"),
        wire({"tx_hash": "0x" + "a" * 64}),
        b"{}",
        b"broken",
    ],
)
def test_rejects_incomplete_or_invalid_index(wire_data):
    with pytest.raises(ValueError):
        parse_burn_index(wire_data)


def test_reader_rejects_unregistered_or_suffix_endpoint():
    with pytest.raises(ValueError):
        SkyBurnIndexReader("https://sky.data.blockanalitica.com.evil.test/internal/buyback/burns/")
    with pytest.raises(ValueError):
        SkyBurnIndexReader("https://sky.data.blockanalitica.com/internal/buyback/burns/monthly/")


def test_reader_loads_bounded_response(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, n):
            assert n == 4_000_001
            return wire(record())

    with patch("hunter.comparative_valuation.sky_burn_index.urlopen", return_value=Response()):
        assert len(SkyBurnIndexReader().fetch()) == 1
