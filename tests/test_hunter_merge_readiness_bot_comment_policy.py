import sys
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

    name = "_original_owner_acknowledged_comment"
    monkeypatch.setattr(policy, name, fake_owner_ack)
    item = comment("reviewer", "Human feedback")
    assert policy.owner_acknowledged_comment_with_bot_exemptions(item)
    assert seen == [item]


def test_exempt_comment_does_not_consume_reaction_lookup(monkeypatch) -> None:
    def fail_if_called(_value: dict) -> bool:
        raise AssertionError("trusted status comment consumed reaction lookup")

    name = "_original_owner_acknowledged_comment"
    monkeypatch.setattr(policy, name, fail_if_called)
    item = comment(LOGIN, DRAFT_BODY)
    assert policy.owner_acknowledged_comment_with_bot_exemptions(item)


def test_semantic_pr_neutralizes_aggregate_updated_at() -> None:
    pr = {
        "created_at": "2026-08-12T06:16:37Z",
        "updated_at": "2026-08-12T06:16:51Z",
        "body": "unchanged",
    }
    semantic = policy.semantic_pr_for_freshness(pr)
    assert semantic["updated_at"] == pr["created_at"]
    assert semantic["body"] == pr["body"]
    assert pr["updated_at"] == "2026-08-12T06:16:51Z"


def test_aggregate_pr_timestamp_drift_does_not_stale_governance(monkeypatch) -> None:
    seen = {}

    def fake_paged(_path: str):
        return []

    def fake_freshness(_pr_number: int, pr: dict):
        seen["created_at"] = pr.get("created_at")
        seen["updated_at"] = pr.get("updated_at")
        return "freshness-result"

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)
    result = policy.get_latest_invalidation_time_with_bot_exemptions(
        249,
        {
            "created_at": "2026-08-12T06:16:37Z",
            "updated_at": "2026-08-12T06:16:51Z",
        },
    )
    assert result == "freshness-result"
    assert seen == {
        "created_at": "2026-08-12T06:16:37Z",
        "updated_at": "2026-08-12T06:16:37Z",
    }


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

    def fake_freshness(pr_number: int, _pr: dict):
        comments_path = f"issues/{pr_number}/comments"
        reviews_path = f"pulls/{pr_number}/reviews"
        seen["comments"] = policy.core.paged(comments_path)
        seen["reviews"] = policy.core.paged(reviews_path)
        return "freshness-result"

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    name = "_original_get_latest_invalidation_time"
    monkeypatch.setattr(policy, name, fake_freshness)
    result = policy.get_latest_invalidation_time_with_bot_exemptions(246, {})
    assert result == "freshness-result"
    assert seen["comments"] == [human]
    assert seen["reviews"] == [{"id": 99}]
    assert policy.core.paged is fake_paged


def test_unknown_bot_comment_still_invalidates_freshness(monkeypatch) -> None:
    unknown = comment("some-other-bot[bot]", DEPENDENCY_BODY)

    def fake_paged(path: str):
        if path.startswith("issues/246/comments"):
            return [unknown]
        return []

    seen = []

    def fake_freshness(pr_number: int, _pr: dict):
        path = f"issues/{pr_number}/comments"
        seen.extend(policy.core.paged(path))
        return "freshness-result"

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    name = "_original_get_latest_invalidation_time"
    monkeypatch.setattr(policy, name, fake_freshness)
    policy.get_latest_invalidation_time_with_bot_exemptions(246, {})
    assert seen == [unknown]
    assert policy.core.paged is fake_paged
