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
