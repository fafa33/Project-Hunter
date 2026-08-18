import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import hunter_coderabbit_review_adapter as adapter

FIXTURE = Path(__file__).parent / "fixtures" / "coderabbit_exact_pair_review.json"
PR_AUTHOR = "fafa33"
BASE = "10bac54ad32d9b71ecbd5149900e6c4a9ca33aaf"
HEAD = "c5e535e80e4684f307c1129497b926d10806aaca"


def _review(**overrides):
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
    review = {
        "id": 123,
        "html_url": "https://github.com/fafa33/Project-Hunter/pull/278#pullrequestreview-123",
        "submitted_at": "2026-08-18T17:22:12Z",
        "user": {"login": frozen["author"]},
        "state": frozen["state"],
        "commit_id": frozen["head"],
        "body": frozen["body_fragment"],
    }
    review.update(overrides)
    return review


def test_valid_exact_pair_coderabbit_commented_review_is_accepted() -> None:
    qualified = adapter.select_qualified_coderabbit_review(
        [_review()], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
    )

    assert qualified.author == "coderabbitai"
    assert qualified.state == "commented"
    assert qualified.head_sha == HEAD
    assert qualified.base_sha == BASE
    body = adapter.canonical_attestation_body(qualified)
    assert f"head={HEAD} base={BASE} outcome=no-blocking-findings" in body
    assert "Unresolved blocking findings: 0" in body


def test_stale_coderabbit_head_is_rejected() -> None:
    stale = "1" * 40
    with pytest.raises(adapter.AdapterError, match="current head"):
        adapter.select_qualified_coderabbit_review(
            [_review(commit_id=stale)], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
        )


def test_wrong_base_in_review_body_is_rejected() -> None:
    wrong_base = "2" * 40
    body = f"Reviewing files that changed from the base of the PR and between {wrong_base} and {HEAD}."
    with pytest.raises(adapter.AdapterError, match="No valid exact-pair"):
        adapter.select_qualified_coderabbit_review(
            [_review(body=body)], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
        )


def test_self_review_identity_is_rejected() -> None:
    with pytest.raises(adapter.AdapterError, match="No independent CodeRabbit review"):
        adapter.select_qualified_coderabbit_review(
            [_review(user={"login": "coderabbitai"})],
            pr_author="coderabbitai",
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_changes_requested_coderabbit_review_is_rejected() -> None:
    with pytest.raises(adapter.AdapterError, match="adverse"):
        adapter.select_qualified_coderabbit_review(
            [_review(state="CHANGES_REQUESTED")],
            pr_author=PR_AUTHOR,
            head_sha=HEAD,
            base_sha=BASE,
        )


def test_empty_or_malformed_review_does_not_qualify() -> None:
    with pytest.raises(adapter.AdapterError, match="No valid exact-pair"):
        adapter.select_qualified_coderabbit_review(
            [_review(body="")], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
        )


def test_later_empty_review_does_not_erase_valid_substantive_exact_pair_review() -> None:
    empty = _review(
        id=124,
        submitted_at="2026-08-18T17:47:07Z",
        body="",
    )
    qualified = adapter.select_qualified_coderabbit_review(
        [_review(), empty], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
    )
    assert qualified.review_id == 123


def test_conflicting_pair_claims_are_rejected() -> None:
    body = (
        f"Reviewing files that changed from the base of the PR and between {BASE} and {HEAD}.\n"
        f"Reviewing files that changed from the base of the PR and between {'3' * 40} and {HEAD}."
    )
    with pytest.raises(adapter.AdapterError, match="conflicting exact-pair"):
        adapter.select_qualified_coderabbit_review(
            [_review(body=body)], pr_author=PR_AUTHOR, head_sha=HEAD, base_sha=BASE
        )


def test_attest_rejects_if_pr_pair_changes_before_post(monkeypatch: pytest.MonkeyPatch) -> None:
    changed_head = "4" * 40
    pr_responses = iter(
        [
            {"user": {"login": PR_AUTHOR}, "head": {"sha": HEAD}, "base": {"sha": BASE}},
            {"user": {"login": PR_AUTHOR}, "head": {"sha": changed_head}, "base": {"sha": BASE}},
        ]
    )
    posted_payloads: list[object] = []

    def fake_request_json(method, path, *, token, payload=None):
        del token
        if method == "GET" and path == "repos/fafa33/Project-Hunter/pulls/280":
            return next(pr_responses)
        if method == "POST":
            posted_payloads.append(payload)
            return {}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)
    monkeypatch.setattr(adapter, "_paged_reviews", lambda repository, pr_number, *, token: [_review()])

    with pytest.raises(adapter.AdapterError, match="changed during attestation"):
        adapter.attest("fafa33/Project-Hunter", 280, token="test-token")

    assert posted_payloads == []
