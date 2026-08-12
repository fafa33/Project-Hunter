import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hunter_merge_readiness_entrypoint as policy  # noqa: E402


def test_marker_effective_time_prefers_semantic_event_time_over_persistence_time() -> None:
    marker = {
        "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:36:33Z",
        "created_at": "2026-08-12T11:36:46Z",
    }

    assert policy._marker_effective_time(marker) == datetime(2026, 8, 12, 11, 36, 33, tzinfo=UTC)


def test_governance_started_after_event_but_before_marker_persistence_is_fresh() -> None:
    marker = {
        "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:36:33Z",
        "created_at": "2026-08-12T11:36:46Z",
    }
    governance_started = datetime(2026, 8, 12, 11, 36, 40, tzinfo=UTC)

    invalidation_time = policy._marker_effective_time(marker)

    assert invalidation_time is not None
    assert governance_started >= invalidation_time
    assert governance_started < datetime(2026, 8, 12, 11, 36, 46, tzinfo=UTC)


def test_later_semantic_event_stales_older_governance() -> None:
    marker = {
        "description": "Semantic PR invalidation recorded: edited; event_time=2026-08-12T11:37:02Z",
        "created_at": "2026-08-12T11:37:05Z",
    }
    governance_started = datetime(2026, 8, 12, 11, 36, 40, tzinfo=UTC)

    invalidation_time = policy._marker_effective_time(marker)

    assert invalidation_time is not None
    assert governance_started < invalidation_time


def test_latest_semantic_invalidation_time_uses_maximum_effective_marker_time(monkeypatch) -> None:
    """A late rerun of an older lifecycle event must never move the boundary backward."""
    statuses = [
        {
            "id": 300,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:36:33Z",
            "created_at": "2026-08-12T11:40:00Z",
        },
        {
            "id": 200,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: edited; event_time=2026-08-12T11:37:02Z",
            "created_at": "2026-08-12T11:37:05Z",
        },
    ]
    monkeypatch.setattr(policy.core, "paged", lambda _path: statuses)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    assert result == datetime(2026, 8, 12, 11, 37, 2, tzinfo=UTC)


def test_latest_semantic_invalidation_time_returns_max_in_normal_persistence_order(monkeypatch) -> None:
    """Markers persisted in the same order as their semantic event times (the
    common case, no out-of-order rerun) must still resolve to the maximum
    (i.e. most recent) effective time.
    """
    statuses = [
        {
            "id": 100,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:30:00Z",
            "created_at": "2026-08-12T11:30:05Z",
        },
        {
            "id": 200,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: edited; event_time=2026-08-12T11:40:00Z",
            "created_at": "2026-08-12T11:40:05Z",
        },
    ]
    monkeypatch.setattr(policy.core, "paged", lambda _path: statuses)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    assert result == datetime(2026, 8, 12, 11, 40, 0, tzinfo=UTC)


def test_latest_semantic_invalidation_time_max_includes_legacy_marker(monkeypatch) -> None:
    """A legacy marker (no encoded event_time, created_at fallback only) must
    still participate correctly in the monotonic maximum when mixed with
    event-time-encoded markers, on either side of the comparison.
    """
    statuses = [
        {
            "id": 100,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: edited",  # legacy, no event_time
            "created_at": "2026-08-12T11:50:00Z",
        },
        {
            "id": 200,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:30:00Z",
            "created_at": "2026-08-12T11:30:05Z",
        },
    ]
    monkeypatch.setattr(policy.core, "paged", lambda _path: statuses)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    # The legacy marker's created_at (11:50:00) is later than the other
    # marker's event_time (11:30:00), so it must win the max().
    assert result == datetime(2026, 8, 12, 11, 50, 0, tzinfo=UTC)


def test_latest_semantic_invalidation_time_max_includes_migration_backfill_marker(monkeypatch) -> None:
    """A migration backfill marker's encoded historical baseline must
    correctly participate in the monotonic maximum alongside real markers.
    """
    statuses = [
        {
            "id": 50,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Migration backfill baseline (raw updated_at): 2026-08-12T12:00:00Z",
            "created_at": "2026-08-12T12:00:01Z",
        },
        {
            "id": 100,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:30:00Z",
            "created_at": "2026-08-12T11:30:05Z",
        },
    ]
    monkeypatch.setattr(policy.core, "paged", lambda _path: statuses)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    # The backfill baseline (12:00:00) is later than the real event's
    # event_time (11:30:00), so the backfill marker must win the max().
    assert result == datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


def test_latest_semantic_invalidation_time_walks_multiple_pages(monkeypatch) -> None:
    """The maximum must be found even when the invalidation-context status
    lives on a later page than the first 100 statuses -- this exercises real
    pagination via core.paged/request_json, not a canned status list.
    """
    older_unrelated = [
        {"id": i, "context": "Some Other Status", "description": "", "created_at": "2026-08-12T00:00:00Z"}
        for i in range(100)
    ]
    second_page = [
        {
            "id": 500,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: synchronize; event_time=2026-08-12T11:30:00Z",
            "created_at": "2026-08-12T11:30:05Z",
        },
        {
            "id": 501,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: edited; event_time=2026-08-12T11:45:00Z",
            "created_at": "2026-08-12T11:45:05Z",
        },
    ]

    def fake_request_json(method, path):
        assert method == "GET"
        # "page=1"/"page=2" would also match inside "per_page=100"; disambiguate
        # with the leading "&" that only precedes the real page parameter.
        if path.endswith("&page=1"):
            return older_unrelated
        if path.endswith("&page=2"):
            return second_page
        raise AssertionError(f"unexpected page request: {path}")

    monkeypatch.setattr(policy.core, "request_json", fake_request_json)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    assert result == datetime(2026, 8, 12, 11, 45, 0, tzinfo=UTC)


def test_marker_with_malformed_event_time_falls_back_conservatively_not_silently() -> None:
    """A marker whose event_time cannot be parsed must not be silently
    dropped from consideration (which would under-count staleness) -- it
    must fall back to created_at, the server-assigned persistence time,
    which is always >= the true semantic event time and therefore only ever
    makes the marker MORE conservative (more likely to correctly stale an
    old Governance run), never less.
    """
    marker = {
        "description": "Semantic PR invalidation recorded: edited; event_time=not-a-real-timestamp",
        "created_at": "2026-08-12T11:36:46Z",
    }

    result = policy._marker_effective_time(marker)

    assert result == datetime(2026, 8, 12, 11, 36, 46, tzinfo=UTC)


def test_latest_semantic_invalidation_time_ignores_unrelated_status_contexts(monkeypatch) -> None:
    statuses = [
        {
            "id": 400,
            "context": "Some Other Status",
            "description": "event_time=2026-08-12T12:00:00Z",
            "created_at": "2026-08-12T12:00:01Z",
        },
        {
            "id": 300,
            "context": policy.INVALIDATION_CONTEXT,
            "description": "Semantic PR invalidation recorded: edited; event_time=2026-08-12T11:37:02Z",
            "created_at": "2026-08-12T11:37:05Z",
        },
    ]
    monkeypatch.setattr(policy.core, "paged", lambda _path: statuses)

    result = policy.latest_semantic_invalidation_time({"head": {"sha": "abcdef123456"}})

    assert result == datetime(2026, 8, 12, 11, 37, 2, tzinfo=UTC)


def test_record_semantic_invalidation_persists_github_event_time(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(
        policy.core,
        "request_json",
        lambda method, path, payload: calls.append((method, path, payload)),
    )
    monkeypatch.setattr(
        policy.core,
        "acquire_pr_lock",
        lambda _pr_number, _sha: "refs/hunter-merge-readiness-locks/pr-249",
    )
    monkeypatch.setattr(policy.core, "release_pr_lock", lambda _lock: None)
    monkeypatch.setattr(policy.core, "retry_transient", lambda operation, **_kwargs: operation())

    policy.record_semantic_pr_invalidation(
        {
            "action": "synchronize",
            "pull_request": {
                "number": 249,
                "head": {"sha": "abcdef123456"},
                "created_at": "2026-08-12T06:16:37Z",
                "updated_at": "2026-08-12T11:36:33Z",
            },
        }
    )

    assert len(calls) == 1
    method, path, payload = calls[0]
    assert method == "POST"
    assert path == "statuses/abcdef123456"
    assert payload["context"] == policy.INVALIDATION_CONTEXT
    assert "synchronize" in payload["description"]
    assert "event_time=2026-08-12T11:36:33Z" in payload["description"]


def test_legacy_marker_without_event_time_keeps_created_at_fallback() -> None:
    marker = {
        "description": "Semantic PR invalidation recorded: edited",
        "created_at": "2026-08-12T11:36:46Z",
    }

    assert policy._marker_effective_time(marker) == datetime(2026, 8, 12, 11, 36, 46, tzinfo=UTC)


def test_migration_marker_still_prefers_encoded_baseline() -> None:
    marker = {
        "description": "Migration backfill baseline (raw updated_at): 2026-08-10T12:00:00Z",
        "created_at": "2026-08-12T11:36:46Z",
    }

    assert policy._marker_effective_time(marker) == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
