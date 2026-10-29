"""Untrusted indexed SKY burn candidates from the public Sky financial analytics API.

This is a discovery feed, NOT an independent proof of economic attribution,
finalized block inclusion, USD flow, or point-in-time knowledge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen

from hunter.value_capture.registry import ValueCaptureSourceRegistry

BURN_INDEX_URL = "https://sky.data.blockanalitica.com/internal/buyback/burns/"


@dataclass(frozen=True)
class SkyIndexedBurn:
    tx_hash: str
    block_number: int
    block_time: datetime
    sky_amount: Decimal
    earmarked_month: str
    settlement: str
    source_id: str
    response_digest: str


def parse_burn_index(raw: bytes, *, max_events: int = 10000) -> tuple[SkyIndexedBurn, ...]:
    """Parse a complete finite response; reject truncation, conflicting IDs and bad schema."""
    if not raw or len(raw) > 4_000_000:
        raise ValueError("invalid indexed burn response size")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid indexed burn JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("indexed burn response missing data array")
    if len(payload["data"]) > max_events:
        raise ValueError("indexed burn response exceeds bounded event count")
    # Fail closed if the provider introduces a pagination envelope.
    if any(key in payload for key in ("next", "next_cursor", "has_more", "pagination", "count")):
        raise ValueError("indexed burn pagination/total completeness is not proven")
    if payload.get("next") or payload.get("next_cursor") or payload.get("has_more"):
        raise ValueError("incomplete paginated burn index")
    found: dict[str, SkyIndexedBurn] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            raise ValueError("invalid indexed burn entry")
        try:
            tx = item["tx_hash"]
            bn = item["block_number"]
            moment = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            amount = Decimal(item["amount"])
            month = item["month"]
            settlement = item["msc_slug"]
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError) as exc:
            raise ValueError("invalid indexed burn entry") from exc
        if (
            not isinstance(tx, str)
            or len(tx) != 66
            or not tx.startswith("0x")
            or any(x not in "0123456789abcdefABCDEF" for x in tx[2:])
            or type(bn) is not int
            or bn < 0
            or moment.tzinfo is None
            or not amount.is_finite()
            or amount <= 0
            or not isinstance(month, str)
            or len(month) != 10
            or not isinstance(settlement, str)
            or not settlement
        ):
            raise ValueError("invalid indexed burn semantics")
        value = SkyIndexedBurn(
            tx.lower(), bn, moment.astimezone(UTC), amount, month, settlement, "sky-financial-burn-index", digest
        )
        if value.tx_hash in found:
            raise ValueError("duplicate indexed burn transaction")
        found[value.tx_hash] = value
    return tuple(sorted(found.values(), key=lambda e: (e.block_number, e.tx_hash)))


class SkyBurnIndexReader:
    def __init__(self, endpoint: str = BURN_INDEX_URL) -> None:
        registry = ValueCaptureSourceRegistry.from_yaml(
            Path(__file__).resolve().parents[3] / "configs" / "value_capture_sources.yaml"
        )
        registry.require("sky-financial-burn-index").authorize(
            endpoint=endpoint,
            parser_version="sky-burn-index-v1",
            capability="evidence:indexed_burn_observation",
        )
        if endpoint != BURN_INDEX_URL:
            raise ValueError("indexed burn endpoint must be canonical")
        self.endpoint = endpoint

    def fetch(self) -> tuple[SkyIndexedBurn, ...]:
        request = Request(self.endpoint, headers={"Accept": "application/json", "User-Agent": "Project-Hunter/1.0"})
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError("indexed burn source HTTP failure")
            raw = response.read(4_000_001)
        return parse_burn_index(raw)
