from __future__ import annotations

import hunter_local_reviewer as reviewer

HEAD = "a" * 40


def test_local_reviewer_uses_github_diff_not_candidate_checkout():
    calls: list[str] = []

    def fetch_diff(*_args):
        calls.append("diff")
        return "patch"

    reviewer.review_pr(
        repository="owner/repo",
        pr_number=472,
        head_sha=HEAD,
        claims_id="b" * 64,
        fetch_diff=fetch_diff,
        review_diff=lambda *_args, **_kwargs: {
            "verdict": "clear",
            "summary": "No blockers found in reviewed diff.",
            "findings": [],
        },
    )
    assert calls == ["diff"]


def test_result_is_bound_to_requested_exact_head():
    result = reviewer.build_result(
        head_sha=HEAD,
        claims_id="b" * 64,
        model="qwen2.5-coder:7b",
        findings=[],
        summary="No blocking findings were identified in the reviewed patch.",
    )
    assert result["schema"] == "hunter.local-review.v1"
    assert result["head_sha"] == HEAD
    assert result["verdict"] == "clear"


def test_review_prompt_explicitly_checks_hunter_governance_failure_classes():
    prompt = reviewer.build_review_prompt("patch")
    for term in ("exact-head", "review authority", "retry", "failover", "timeout", "workflow permissions"):
        assert term in prompt.lower()
    assert "correct safeguards" in prompt.lower()
