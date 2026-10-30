"""Deterministic Ethereum log reconciliation for attributable Sky burn evidence.

Only caller-verified receipts with transaction/block provenance are accepted.
This module does not assert that a transfer to a sink is a verified economic burn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class SkyEconomicEvent:
    tx_hash: str
    log_index: int
    block_number: int
    block_hash: str
    block_time: datetime
    known_at: datetime
    amount_usd: Decimal
    event_kind: str
    source_id: str
    verified_attribution: bool

    def __post_init__(self) -> None:
        if not self.tx_hash.startswith("0x") or len(self.tx_hash) != 66:
            raise ValueError("transaction hash is required")
        if not self.block_hash.startswith("0x") or len(self.block_hash) != 66:
            raise ValueError("block hash is required")
        if self.log_index < 0 or self.block_number < 0:
            raise ValueError("invalid block/log position")
        if self.block_time.tzinfo is None or self.known_at.tzinfo is None:
            raise ValueError("event chronology must be timezone aware")
        if self.known_at < self.block_time:
            raise ValueError("event cannot be known before its block")
        if self.amount_usd <= 0 or not self.amount_usd.is_finite():
            raise ValueError("positive finite USD flow required")
        if self.event_kind not in {"verified_burn", "verified_redemption"}:
            raise ValueError("unsupported economic event")
        if not self.source_id or not self.verified_attribution:
            raise ValueError("primary source and verified attribution required")


def reconcile_sky_365d(events: tuple[SkyEconomicEvent, ...], *, as_of: datetime, known_by: datetime) -> Decimal:
    """Return exact-window flow, fail closed on duplicate/conflicting provenance."""
    if as_of.tzinfo is None or known_by.tzinfo is None or known_by > as_of:
        raise ValueError("invalid historical coordinates")
    as_of = as_of.astimezone(UTC)
    known_by = known_by.astimezone(UTC)
    start = as_of - timedelta(days=365)
    seen: dict[tuple[str, int], SkyEconomicEvent] = {}
    tx_kinds: dict[str, str] = {}
    total = Decimal(0)
    for event in events:
        if not start <= event.block_time.astimezone(UTC) <= as_of:
            continue
        if event.known_at.astimezone(UTC) > known_by:
            continue
        key = (event.tx_hash.lower(), event.log_index)
        if key in seen:
            if seen[key] != event:
                raise ValueError("conflicting Ethereum log provenance")
            continue
        seen[key] = event
        previous = tx_kinds.setdefault(event.tx_hash.lower(), event.event_kind)
        if previous != event.event_kind:
            raise ValueError("burn and redemption cannot double count a transaction")
        total += event.amount_usd
    return total
