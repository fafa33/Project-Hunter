import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hunter_merge_readiness_entrypoint as policy  # noqa: E402

LOGIN = "github-actions[bot]"
DEPENDENCY_BODY = "Dependency Review\n" + policy.DEPENDENCY_REVIEW_MARKER
DRAFT_BODY = policy.DRAFT_PROMOTION_MARKER_PREFIX + "abc123 -->\nReady."


def comment(login: str, body: str) -> dict:
    return {"id": 1, "user": {"login": login}, "body": body}


def test_dependency_review_status_comment_is_exempt() -> None:
    item = comment(LOGIN, DEPENDENCY_BODY)
    assert policy.is_exempt_status_comment(item)


def test_draft_promotion_status_comment_is_exempt() -> None:
    item = comment(LOGIN, DRAFT_BODY)
    assert policy.is_exempt_status_comment(item)


def test_human_comment_is_not_exempt_even_with_marker() -> None:
    item = comment("reviewer", DEPENDENCY_BODY)
    assert not policy.is_exempt_status_comment(item)


def test_unknown_bot_is_not_exempt_even_with_marker() -> None:
    item = comment("some-other-bot[bot]", DEPENDENCY_BODY)
    assert not policy.is_exempt_status_comment(item)


def test_github_actions_unknown_comment_is_not_exempt() -> None:
    item = comment(LOGIN, "An unknown automated advisory")
    assert not policy.is_exempt_status_comment(item)


def test_non_exempt_comment_delegates_to_existing_owner_ack(monkeypatch) -> None:
    seen = []

    def fake_owner_ack(value: dict) -> bool:
        seen.append(value)
        return True

    monkeypatch.setattr(policy, "_original_owner_acknowledged_comment", fake_owner_ack)
    item = comment("reviewer", "Human feedback")
    assert policy.owner_acknowledged_comment_with_bot_exemptions(item)
    assert seen == [item]


def test_exempt_comment_does_not_consume_reaction_lookup(monkeypatch) -> None:
    def fail_if_called(_value: dict) -> bool:
        raise AssertionError("trusted status comment consumed reaction lookup")

    monkeypatch.setattr(policy, "_original_owner_acknowledged_comment", fail_if_called)
    item = comment(LOGIN, DRAFT_BODY)
    assert policy.owner_acknowledged_comment_with_bot_exemptions(item)


def test_semantic_pr_neutralizes_aggregate_updated_at() -> None:
    pr = {
        "created_at": "2026-08-12T06:16:37Z",
        "updated_at": "2026-08-12T06:16:51Z",
        "body": "unchanged",
    }
    semantic = policy.semantic_pr_view(pr)
    assert semantic["updated_at"] == pr["created_at"]
    assert semantic["body"] == pr["body"]
    assert pr["updated_at"] == "2026-08-12T06:16:51Z"


def test_aggregate_pr_timestamp_drift_does_not_stale_governance(monkeypatch) -> None:
    seen = {}
    base_time = datetime(2026, 8, 12, 6, 16, 37, tzinfo=UTC)

    def fake_paged(_path: str):
        return []

    def fake_freshness(_pr_number: int, pr: dict):
        seen["created_at"] = pr.get("created_at")
        seen["updated_at"] = pr.get("updated_at")
        return base_time

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)
    result = policy.get_latest_invalidation_time_with_bot_exemptions(
        249,
        {
            "created_at": "2026-08-12T06:16:37Z",
            "updated_at": "2026-08-12T06:16:51Z",
        },
    )
    assert result == base_time
    assert seen == {
        "created_at": "2026-08-12T06:16:37Z",
        "updated_at": "2026-08-12T06:16:37Z",
    }


def test_durable_semantic_edit_marker_stales_older_governance(monkeypatch) -> None:
    base_time = datetime(2026, 8, 12, 6, 16, 37, tzinfo=UTC)
    edit_time = datetime(2026, 8, 12, 6, 16, 55, tzinfo=UTC)

    monkeypatch.setattr(policy.core, "paged", lambda _path: [])
    monkeypatch.setattr(
        policy,
        "_original_get_latest_invalidation_time",
        lambda _n, _pr: base_time,
    )
    monkeypatch.setattr(
        policy.core,
        "latest_commit_status",
        lambda _sha, context: {
            "context": context,
            "created_at": "2026-08-12T06:16:55Z",
        },
    )

    result = policy.get_latest_invalidation_time_with_bot_exemptions(
        249,
        {
            "created_at": "2026-08-12T06:16:37Z",
            "updated_at": "2026-08-12T06:17:20Z",
            "head": {"sha": "abc123"},
        },
    )
    assert result == edit_time


# ====================================================================================
# MIGRATION / BOOTSTRAP BACKFILL REGRESSION TESTS (A - D)
# ====================================================================================


def test_migration_backfill_fresh_pr_remains_eligible(monkeypatch) -> None:
    """Test A: a PR with no durable marker yet (i.e. open since before this
    policy started evaluating it) whose raw updated_at reflects only
    legitimate pre-migration activity must be backfilled to that exact raw
    value -- not to "now" -- so it is not permanently deadlocked by a
    baseline that looks like a brand-new edit.
    """
    posts = []
    base_time = datetime(2020, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", lambda _n, _pr: base_time)
    monkeypatch.setattr(policy.core, "latest_commit_status", lambda _sha, _ctx: None)
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(
        policy.core,
        "request_json",
        lambda method, path, payload: posts.append((method, path, payload)),
    )
    monkeypatch.setattr(policy.core, "retry_transient", lambda operation, **kwargs: operation())

    pr = {
        "head": {"sha": "abcdef123456"},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-10T12:00:00Z",
    }
    result = policy.get_latest_invalidation_time_with_bot_exemptions(249, pr)

    assert result == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    assert len(posts) == 1
    method, path, payload = posts[0]
    assert method == "POST"
    assert path == "statuses/abcdef123456"
    assert payload["context"] == policy.INVALIDATION_CONTEXT
    assert policy.BACKFILL_DESCRIPTION_PREFIX in payload["description"]
    assert "2026-08-10T12:00:00Z" in payload["description"]


def test_migration_backfill_stale_pr_requires_fresh_governance(monkeypatch) -> None:
    """Test B: a PR whose raw updated_at reflects a real pre-migration edit
    that postdates an already-completed Governance Review's start time must
    not be silently forgiven -- the backfilled baseline must still be later
    than that stale run, so it is correctly rejected as stale.
    """
    monkeypatch.setattr(
        policy, "_original_get_latest_invalidation_time", lambda _n, _pr: datetime(2020, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(policy.core, "latest_commit_status", lambda _sha, _ctx: None)
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(policy.core, "request_json", lambda method, path, payload: None)
    monkeypatch.setattr(policy.core, "retry_transient", lambda operation, **kwargs: operation())

    pr = {
        "head": {"sha": "abcdef123456"},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-12T06:16:55Z",
    }
    governance_started_at = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)

    result = policy.get_latest_invalidation_time_with_bot_exemptions(249, pr)

    assert result == datetime(2026, 8, 12, 6, 16, 55, tzinfo=UTC)
    assert governance_started_at < result


def test_migration_post_migration_bookkeeping_drift_does_not_restale(monkeypatch) -> None:
    """Test C: once a durable marker exists (post-migration steady state), a
    later bookkeeping-only aggregate updated_at drift must NOT further stale
    governance and must NOT re-trigger backfill -- aggregate updated_at no
    longer participates once a durable marker is present.
    """
    backfill_marker = {
        "context": policy.INVALIDATION_CONTEXT,
        "description": f"{policy.BACKFILL_DESCRIPTION_PREFIX} 2026-08-10T12:00:00Z",
        "created_at": "2026-08-11T00:00:00Z",
    }
    monkeypatch.setattr(
        policy, "_original_get_latest_invalidation_time", lambda _n, _pr: datetime(2020, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(policy.core, "latest_commit_status", lambda _sha, _ctx: backfill_marker)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("backfill/persistence must not run once a durable marker already exists")

    monkeypatch.setattr(policy.core, "request_json", fail_if_called)

    pr = {
        "head": {"sha": "abcdef123456"},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-12T09:00:00Z",
    }
    result = policy.get_latest_invalidation_time_with_bot_exemptions(249, pr)

    assert result == datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_migration_post_migration_real_edit_stales_governance(monkeypatch) -> None:
    """Test D: after migration, a genuine PR edit must still record a fresh
    real invalidation marker and stale governance exactly like the
    non-migration case -- migration must not weaken steady-state semantics.
    """
    real_edit_marker = {
        "context": policy.INVALIDATION_CONTEXT,
        "description": "Semantic PR invalidation recorded: edited",
        "created_at": "2026-08-12T09:30:00Z",
    }
    monkeypatch.setattr(
        policy, "_original_get_latest_invalidation_time", lambda _n, _pr: datetime(2020, 1, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(policy.core, "latest_commit_status", lambda _sha, _ctx: real_edit_marker)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("backfill must not run when a durable (real) marker already exists")

    monkeypatch.setattr(policy.core, "request_json", fail_if_called)

    pr = {
        "head": {"sha": "abcdef123456"},
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-12T09:00:00Z",
    }
    result = policy.get_latest_invalidation_time_with_bot_exemptions(249, pr)

    assert result == datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def test_semantic_edit_event_persists_invalidation_status(monkeypatch) -> None:
    calls = []
    lock_calls = []

    def fake_acquire(pr_number, sha):
        lock_calls.append(("acquire", pr_number, sha))
        return "refs/hunter-merge-readiness-locks/pr-249"

    def fake_release(lock_ref):
        lock_calls.append(("release", lock_ref))

    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(
        policy.core,
        "request_json",
        lambda method, path, payload: calls.append((method, path, payload)),
    )
    monkeypatch.setattr(policy.core, "acquire_pr_lock", fake_acquire)
    monkeypatch.setattr(policy.core, "release_pr_lock", fake_release)

    policy.record_semantic_pr_invalidation(
        {
            "action": "edited",
            "pull_request": {"number": 249, "head": {"sha": "abcdef123456"}},
        }
    )

    assert lock_calls == [
        ("acquire", 249, "abcdef123456"),
        ("release", "refs/hunter-merge-readiness-locks/pr-249"),
    ]
    assert len(calls) == 1
    method, path, payload = calls[0]
    assert method == "POST"
    assert path == "statuses/abcdef123456"
    assert payload["context"] == policy.INVALIDATION_CONTEXT
    assert payload["state"] == "success"
    assert "edited" in payload["description"]


def test_semantic_edit_missing_pr_number_fails_closed(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "request_json", lambda *args: calls.append(args))

    try:
        policy.record_semantic_pr_invalidation(
            {
                "action": "edited",
                "pull_request": {"head": {"sha": "abcdef123456"}},
            }
        )
        raise AssertionError("expected RuntimeError for missing pr_number")
    except RuntimeError:
        pass
    assert calls == []


def test_nonsemantic_review_request_does_not_persist_invalidation(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "request_json", lambda *args: calls.append(args))

    policy.record_semantic_pr_invalidation(
        {
            "action": "review_requested",
            "pull_request": {"number": 249, "head": {"sha": "abcdef123456"}},
        }
    )
    assert calls == []


def test_semantic_invalidation_lock_failure_fails_closed(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "request_json", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(policy.core, "acquire_pr_lock", lambda pr_number, sha: None)

    try:
        policy.record_semantic_pr_invalidation(
            {
                "action": "edited",
                "pull_request": {"number": 249, "head": {"sha": "abcdef123456"}},
            }
        )
        raise AssertionError("expected RuntimeError when the reconciliation lock cannot be acquired")
    except RuntimeError:
        pass
    assert calls == []


def test_semantic_invalidation_write_retries_transient_then_succeeds(monkeypatch) -> None:
    attempts = []
    sleeps = []
    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(
        policy.core, "acquire_pr_lock", lambda pr_number, sha: "refs/hunter-merge-readiness-locks/pr-249"
    )
    monkeypatch.setattr(policy.core, "release_pr_lock", lambda lock_ref: None)

    def flaky_request_json(method, path, payload):
        attempts.append((method, path, payload))
        if len(attempts) < 3:
            raise policy.core.urllib.error.HTTPError("https://api.github.com", 503, "unavailable", {}, None)
        return {}

    monkeypatch.setattr(policy.core, "request_json", flaky_request_json)

    original_retry = policy.core.retry_transient

    def patched_retry(operation, **kwargs):
        kwargs.setdefault("sleep_fn", sleeps.append)
        return original_retry(operation, **kwargs)

    monkeypatch.setattr(policy.core, "retry_transient", patched_retry)

    policy.record_semantic_pr_invalidation(
        {
            "action": "edited",
            "pull_request": {"number": 249, "head": {"sha": "abcdef123456"}},
        }
    )

    assert len(attempts) == 3
    assert len(sleeps) == 2


def test_semantic_invalidation_write_permanent_failure_raises(monkeypatch) -> None:
    attempts = []
    sleeps = []
    monkeypatch.setattr(policy.core, "event_name", "pull_request_target")
    monkeypatch.setattr(policy.core, "run_url", "https://example.invalid/run")
    monkeypatch.setattr(
        policy.core, "acquire_pr_lock", lambda pr_number, sha: "refs/hunter-merge-readiness-locks/pr-249"
    )
    released = []
    monkeypatch.setattr(policy.core, "release_pr_lock", lambda lock_ref: released.append(lock_ref))

    def always_fails(method, path, payload):
        attempts.append((method, path, payload))
        raise policy.core.urllib.error.HTTPError("https://api.github.com", 503, "unavailable", {}, None)

    monkeypatch.setattr(policy.core, "request_json", always_fails)

    original_retry = policy.core.retry_transient

    def patched_retry(operation, **kwargs):
        kwargs.setdefault("sleep_fn", sleeps.append)
        return original_retry(operation, **kwargs)

    monkeypatch.setattr(policy.core, "retry_transient", patched_retry)

    try:
        policy.record_semantic_pr_invalidation(
            {
                "action": "edited",
                "pull_request": {"number": 249, "head": {"sha": "abcdef123456"}},
            }
        )
        raise AssertionError("expected the permanent write failure to propagate (fail closed)")
    except policy.core.urllib.error.HTTPError:
        pass

    assert len(attempts) == policy.core.RETRY_ATTEMPTS
    # The lock must always be released, even when the durable write ultimately fails,
    # so a permanently-failing marker write cannot also leak the reconciliation lock.
    assert released == ["refs/hunter-merge-readiness-locks/pr-249"]


def test_exempt_comments_are_removed_from_freshness_input(monkeypatch) -> None:
    trusted = comment(LOGIN, DEPENDENCY_BODY)
    trusted["id"] = 10
    human = comment("reviewer", "Real review feedback")
    human["id"] = 11

    def fake_paged(path: str):
        if path.startswith("issues/246/comments"):
            return [trusted, human]
        return [{"id": 99}]

    seen = {}
    base_time = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)

    def fake_freshness(pr_number: int, _pr: dict):
        comments_path = f"issues/{pr_number}/comments"
        reviews_path = f"pulls/{pr_number}/reviews"
        seen["comments"] = policy.core.paged(comments_path)
        seen["reviews"] = policy.core.paged(reviews_path)
        return base_time

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)
    result = policy.get_latest_invalidation_time_with_bot_exemptions(246, {})
    assert result == base_time
    assert seen["comments"] == [human]
    assert seen["reviews"] == [{"id": 99}]
    assert policy.core.paged is fake_paged


def test_unknown_bot_comment_still_invalidates_freshness(monkeypatch) -> None:
    unknown = comment("some-other-bot[bot]", DEPENDENCY_BODY)
    base_time = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)

    def fake_paged(path: str):
        if path.startswith("issues/246/comments"):
            return [unknown]
        return []

    seen = []

    def fake_freshness(pr_number: int, _pr: dict):
        path = f"issues/{pr_number}/comments"
        seen.extend(policy.core.paged(path))
        return base_time

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)
    policy.get_latest_invalidation_time_with_bot_exemptions(246, {})
    assert seen == [unknown]
    assert policy.core.paged is fake_paged
