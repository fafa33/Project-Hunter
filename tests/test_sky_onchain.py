from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hunter.comparative_valuation.sky_onchain import SkyEconomicEvent, reconcile_sky_365d

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def event(*, kind="verified_burn", index=0, known=None, block=None, amount="10"):
    return SkyEconomicEvent(
        tx_hash="0x" + "a" * 64,
        log_index=index,
        block_number=20000000,
        block_hash="0x" + "b" * 64,
        block_time=block or NOW - timedelta(days=1),
        known_at=known or NOW,
        amount_usd=Decimal(amount),
        event_kind=kind,
        source_id="verified-onchain-value-capture",
        verified_attribution=True,
    )


def test_exact_window_and_deduplicated_log():
    a = event()
    assert reconcile_sky_365d((a, a), as_of=NOW, known_by=NOW) == Decimal("10")


def test_later_known_event_excluded_from_historical_replay():
    a = event(known=NOW + timedelta(days=1))
    assert reconcile_sky_365d((a,), as_of=NOW, known_by=NOW) == 0


def test_outside_365_day_window_excluded():
    a = event(block=NOW - timedelta(days=366))
    assert reconcile_sky_365d((a,), as_of=NOW, known_by=NOW) == 0


def test_burn_and_redemption_same_transaction_fail_closed():
    with pytest.raises(ValueError, match="double count"):
        reconcile_sky_365d((event(), event(kind="verified_redemption", index=1)), as_of=NOW, known_by=NOW)


def test_conflicting_same_log_fail_closed():
    with pytest.raises(ValueError, match="conflicting"):
        reconcile_sky_365d((event(), event(amount="11")), as_of=NOW, known_by=NOW)


def test_unverified_attribution_rejected():
    from dataclasses import replace
    with pytest.raises(ValueError, match="verified attribution"):
        replace(event(), verified_attribution=False)
