from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hunter_merge_readiness_entrypoint as policy  # noqa: E402


def comment(login: str, body: str) -> dict:
    return {"id": 1, "user": {"login": login}, "body": body}


def test_dependency_review_status_comment_is_exempt() -> None:
    assert policy.is_exempt_status_comment(
        comment(
            "github-actions[bot]",
            "Dependency Review\n<!-- dependency-review-pr-comment-marker -->",
        )
    )


def test_draft_promotion_status_comment_is_exempt() -> None:
    assert policy.is_exempt_status_comment(
        comment(
            "github-actions[bot]",
            "<!-- hunter-draft-promotion:abc123 -->\nReady to promote from Draft.",
        )
    )


def test_human_comment_is_not_exempt_even_with_marker() -> None:
    assert not policy.is_exempt_status_comment(
        comment(
            "reviewer",
            "<!-- dependency-review-pr-comment-marker -->\nPlease fix this.",
        )
    )


def test_unknown_bot_is_not_exempt_even_with_marker() -> None:
    assert not policy.is_exempt_status_comment(
        comment(
            "some-other-bot[bot]",
            "<!-- dependency-review-pr-comment-marker -->",
        )
    )


def test_github_actions_unknown_comment_is_not_exempt() -> None:
    assert not policy.is_exempt_status_comment(
        comment("github-actions[bot]", "An unknown automated advisory")
    )


def test_non_exempt_comment_delegates_to_existing_owner_ack(monkeypatch) -> None:
    seen = []

    def fake_owner_ack(comment_value: dict) -> bool:
        seen.append(comment_value)
        return True

    monkeypatch.setattr(policy, "_original_owner_acknowledged_comment", fake_owner_ack)
    item = comment("reviewer", "Human feedback")

    assert policy.owner_acknowledged_comment_with_bot_exemptions(item) is True
    assert seen == [item]


def test_exempt_comment_does_not_consume_reaction_lookup(monkeypatch) -> None:
    def fail_if_called(_comment: dict) -> bool:
        raise AssertionError("trusted status comments must not require owner reaction lookup")

    monkeypatch.setattr(policy, "_original_owner_acknowledged_comment", fail_if_called)
    item = comment(
        "github-actions[bot]",
        "<!-- hunter-draft-promotion:def456 -->\nReady.",
    )

    assert policy.owner_acknowledged_comment_with_bot_exemptions(item) is True


def test_exempt_comments_are_removed_from_freshness_input(monkeypatch) -> None:
    trusted = comment(
        "github-actions[bot]",
        "Dependency Review\n<!-- dependency-review-pr-comment-marker -->",
    )
    trusted["id"] = 10
    human = comment("reviewer", "Real review feedback")
    human["id"] = 11

    def fake_paged(path: str):
        if path.startswith("issues/246/comments"):
            return [trusted, human]
        return [{"id": 99}]

    seen = {}

    def fake_freshness(pr_number: int, _pr: dict):
        seen["comments"] = policy.core.paged(f"issues/{pr_number}/comments")
        seen["reviews"] = policy.core.paged(f"pulls/{pr_number}/reviews")
        return "freshness-result"

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)

    result = policy.get_latest_invalidation_time_with_bot_exemptions(246, {})

    assert result == "freshness-result"
    assert seen["comments"] == [human]
    assert seen["reviews"] == [{"id": 99}]
    assert policy.core.paged is fake_paged


def test_unknown_bot_comment_still_invalidates_freshness(monkeypatch) -> None:
    unknown = comment(
        "some-other-bot[bot]",
        "<!-- dependency-review-pr-comment-marker -->",
    )

    def fake_paged(path: str):
        if path.startswith("issues/246/comments"):
            return [unknown]
        return []

    seen = []

    def fake_freshness(pr_number: int, _pr: dict):
        seen.extend(policy.core.paged(f"issues/{pr_number}/comments"))
        return "freshness-result"

    monkeypatch.setattr(policy.core, "paged", fake_paged)
    monkeypatch.setattr(policy, "_original_get_latest_invalidation_time", fake_freshness)

    policy.get_latest_invalidation_time_with_bot_exemptions(246, {})

    assert seen == [unknown]
    assert policy.core.paged is fake_paged
