from __future__ import annotations

import hunter_local_reviewer as reviewer
import pytest

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


def test_a_blocking_verdict_with_no_findings_is_never_published_as_clear():
    """An internally inconsistent model result must not be resolved permissively."""

    with pytest.raises(ValueError, match="contradicts"):
        reviewer.review_pr(
            repository="owner/repo",
            pr_number=472,
            head_sha=HEAD,
            claims_id="b" * 64,
            fetch_diff=lambda *_args: "patch",
            review_diff=lambda *_args, **_kwargs: {
                "verdict": "findings",
                "summary": "This change removes the exact-head binding.",
                "findings": [],
            },
        )


def test_a_clear_verdict_carrying_findings_is_also_rejected():
    with pytest.raises(ValueError, match="contradicts"):
        reviewer.build_result(
            head_sha=HEAD,
            claims_id="b" * 64,
            model="qwen2.5-coder:7b",
            findings=[{"severity": "blocking", "path": "scripts/x.py", "line": 1, "evidence": "unbounded retry"}],
            summary="Nothing to report.",
            verdict="clear",
        )


def test_an_unrecognised_verdict_is_rejected():
    with pytest.raises(ValueError, match="verdict is invalid"):
        reviewer.build_result(
            head_sha=HEAD,
            claims_id="b" * 64,
            model="qwen2.5-coder:7b",
            findings=[],
            summary="Nothing to report.",
            verdict="approved",
        )


def test_a_consistent_blocking_verdict_is_preserved():
    result = reviewer.review_pr(
        repository="owner/repo",
        pr_number=472,
        head_sha=HEAD,
        claims_id="b" * 64,
        fetch_diff=lambda *_args: "patch",
        review_diff=lambda *_args, **_kwargs: {
            "verdict": "findings",
            "summary": "The exact-head binding is dropped.",
            "findings": [{"severity": "blocking", "path": "scripts/x.py", "line": 1, "evidence": "stale head"}],
        },
    )
    assert result["verdict"] == "findings"


def test_review_prompt_explicitly_checks_hunter_governance_failure_classes():
    prompt = reviewer.build_review_prompt("patch")
    for term in ("exact-head", "review authority", "retry", "failover", "timeout", "workflow permissions"):
        assert term in prompt.lower()
    assert "correct safeguards" in prompt.lower()


# --- dispatch-input boundary --------------------------------------------------
#
# This adapter runs on a self-hosted runner and every one of its arguments comes
# from a workflow dispatch, so its arguments are chosen by whoever can trigger
# that workflow. Each is therefore re-validated here, at the process boundary,
# rather than trusted because the workflow quoted it.


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("owner/repo", True),
        ("fafa33/Project-Hunter", True),
        ("owner.name/repo_name-1", True),
        ("  owner/repo  ", True),
        ("owner/repo;curl evil", False),
        ("owner/repo/extra", False),
        ("owner", False),
        ("../../etc", False),
        ("owner/repo\nowner/other", False),
        ("", False),
    ],
)
def test_the_repository_argument_is_accepted_only_in_its_canonical_shape(value: str, accepted: bool) -> None:
    if accepted:
        assert reviewer._matching(reviewer.REPOSITORY_PATTERN, value, "repository") == value.strip()
    else:
        with pytest.raises(ValueError):
            reviewer._matching(reviewer.REPOSITORY_PATTERN, value, "repository")


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("a" * 40, True),
        ("0123456789abcdef" * 2 + "01234567", True),
        ("A" * 40, False),  # callers lowercase first; the pattern itself is exact
        ("a" * 39, False),
        ("a" * 41, False),
        ("g" * 40, False),
        ("", False),
    ],
)
def test_the_head_sha_argument_must_be_a_full_commit_sha(value: str, accepted: bool) -> None:
    if accepted:
        assert reviewer._matching(reviewer.HEAD_SHA_PATTERN, value, "head SHA") == value
    else:
        with pytest.raises(ValueError):
            reviewer._matching(reviewer.HEAD_SHA_PATTERN, value, "head SHA")


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("b" * 64, True),
        ("b" * 63, False),
        ("b" * 65, False),
        ("z" * 64, False),
    ],
)
def test_the_claims_id_argument_must_be_a_full_digest(value: str, accepted: bool) -> None:
    if accepted:
        assert reviewer._matching(reviewer.CLAIMS_ID_PATTERN, value, "claims id") == value
    else:
        with pytest.raises(ValueError):
            reviewer._matching(reviewer.CLAIMS_ID_PATTERN, value, "claims id")


@pytest.mark.parametrize(
    "value",
    [
        "reviewer-result.json",
        "nested/reviewer-result.json",
        "./reviewer-result.json",
    ],
)
def test_a_result_path_inside_the_workspace_is_accepted(tmp_path, value: str) -> None:
    resolved = reviewer.resolved_output_path(value, workspace=tmp_path)
    assert tmp_path in resolved.parents


@pytest.mark.parametrize(
    "value",
    [
        "../escaped.json",
        "nested/../../escaped.json",
        "/etc/hunter-result.json",
    ],
)
def test_a_result_path_outside_the_workspace_is_refused(tmp_path, value: str) -> None:
    with pytest.raises(ValueError, match="escapes the workspace"):
        reviewer.resolved_output_path(value, workspace=tmp_path)


def test_a_tilde_prefix_stays_a_literal_directory_name(tmp_path) -> None:
    """`~` is not expanded here, so it cannot reach a home directory either way."""
    resolved = reviewer.resolved_output_path("~root/result.json", workspace=tmp_path)
    assert resolved == tmp_path / "~root" / "result.json"


def test_a_symlink_cannot_redirect_the_result_out_of_the_workspace(tmp_path) -> None:
    """Resolution happens before the check, so a link is followed, not trusted."""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the workspace"):
        reviewer.resolved_output_path("link/escaped.json", workspace=tmp_path)


def test_the_workspace_root_is_not_itself_a_result_path(tmp_path) -> None:
    with pytest.raises(ValueError):
        reviewer.resolved_output_path(".", workspace=tmp_path)
