from __future__ import annotations

import sys
from pathlib import Path

import hunter_candidate_admission as admission

ROOT = Path(__file__).resolve().parents[1]
HEAD = "a" * 40


def _pr(*, draft: bool = False) -> dict:
    return {
        "state": "open",
        "draft": draft,
        "node_id": "PR_test_node",
        "head": {"sha": HEAD, "repo": {"full_name": "fafa33/Project-Hunter"}},
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "fafa33/Project-Hunter"}},
        "mergeable": True,
    }


def test_unadmitted_ready_candidate_is_returned_to_draft(monkeypatch) -> None:
    converted: list[tuple[str, str]] = []

    def convert(token: str, node_id: str) -> bool:
        converted.append((token, node_id))
        return True

    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda _repo, _token, _head, *_args: ("failure", "exact-head preflight failed"),
    )
    monkeypatch.setattr(
        admission,
        "convert_to_draft",
        convert,
    )

    assert (
        admission.enforce_candidate_admission(
            "fafa33/Project-Hunter",
            "token",
            371,
        )
        == 1
    )
    assert converted == [("token", "PR_test_node")]


def test_pending_candidate_stays_ready_without_merge_authority(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda _repo, _token, _head, *_args: ("pending", "Waiting for exact-head branch preflight to complete."),
    )
    monkeypatch.setattr(
        admission,
        "convert_to_draft",
        lambda *_args: True,
    )

    converted: list[str] = []

    def convert_to_draft(*_args: object) -> bool:
        converted.append("draft")
        return True

    monkeypatch.setattr(admission, "convert_to_draft", convert_to_draft)

    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 369) == 0
    assert converted == []
    assert "admission is pending" in capsys.readouterr().out


def test_admitted_candidate_stays_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        admission.governance,
        "request_json",
        lambda *args: (
            {
                "check_runs": [
                    {"id": n, "name": name, "status": "completed", "conclusion": "success"}
                    for n, name in enumerate(("Quality Gates", "dependency-review", "CodeQL"), 1)
                ]
            }
            if "check-runs" in args[-1]
            else [{"id": 1, "context": "Hunter Governance Review", "state": "success"}]
        ),
    )
    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda _repo, _token, _head, *_args: ("success", "exact-head preflight passed"),
    )
    monkeypatch.setattr(
        admission,
        "convert_to_draft",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not redraft")),
    )

    assert (
        admission.enforce_candidate_admission(
            "fafa33/Project-Hunter",
            "token",
            371,
        )
        == 0
    )


def test_draft_candidate_pending_does_not_mutate(monkeypatch) -> None:
    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(draft=True),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda *_args: ("pending", "proof in flight"),
    )

    assert (
        admission.enforce_candidate_admission(
            "fafa33/Project-Hunter",
            "token",
            371,
        )
        == 0
    )


def test_convert_to_draft_requires_graphql_confirmation(monkeypatch) -> None:
    captured: dict = {}

    def fake_graphql(**kwargs):
        captured.update(kwargs)
        return {"convertPullRequestToDraft": {"pullRequest": {"id": "PR_test_node", "isDraft": True}}}

    monkeypatch.setattr(admission.transport, "request_graphql_json", fake_graphql)
    assert admission.convert_to_draft("token", "PR_test_node") is True

    assert captured["variables"] == {"pullRequestId": "PR_test_node"}
    assert "convertPullRequestToDraft" in captured["query"]


def test_convert_to_draft_handles_forbidden_error_gracefully(monkeypatch) -> None:
    def fake_graphql(**kwargs):
        raise RuntimeError(
            "GraphQL query failed: [{'type': 'FORBIDDEN', 'message': 'Resource not accessible by integration'}]"
        )

    monkeypatch.setattr(admission.transport, "request_graphql_json", fake_graphql)
    assert admission.convert_to_draft("token", "PR_test_node") is False


def test_rejected_candidate_preserves_failure_when_draft_conversion_is_forbidden(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda _repo, _token, _head, *_args: ("failure", "exact-head preflight failed"),
    )

    def fake_graphql(**kwargs):
        raise RuntimeError("Resource not accessible by integration")

    monkeypatch.setattr(admission.transport, "request_graphql_json", fake_graphql)

    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 378) == 1
    output = capsys.readouterr().out
    assert "Resource not accessible by integration" in output
    assert "candidate admission is failure: exact-head preflight failed" in output


def test_candidate_admission_workflow_is_trusted_and_reconciles_after_preflight() -> None:
    workflow = (ROOT / ".github" / "workflows" / "hunter-candidate-admission.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" not in workflow
    assert "workflow_run:" in workflow
    assert "Hunter / Pre-PR Preflight" in workflow
    assert "completed" in workflow
    assert "ready_for_review" not in workflow
    assert "pull-requests: write" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "github.event.workflow_run.pull_requests[0].number" not in workflow
    assert "readiness.candidate_prs()" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert '--head-sha "${EVENT_HEAD_SHA}"' in workflow
    assert "hunter_candidate_admission.py" in workflow


def test_pull_request_target_does_not_run_admission_before_trusted_preflight_completion() -> None:
    """A synchronize event must not publish a false red while exact-head proof is still running."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "hunter-candidate-admission.yml").read_text())
    condition = workflow["jobs"]["candidate-admission"]["if"]
    assert "github.event_name == 'workflow_run'" in condition
    assert "github.event_name == 'pull_request_target'" not in condition


def test_draft_success_marks_ready_exact_head(monkeypatch):
    calls = []
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda token, node: calls.append(node) or True)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616, HEAD) == 0
    assert calls == ["PR_test_node"]


def test_draft_stale_head_blocks_transition(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616, "c" * 40) == 0


def test_draft_head_race_blocks_transition(monkeypatch):
    stale = _pr(draft=True)
    changed = _pr(draft=True)
    changed["head"]["sha"] = "c" * 40
    states = iter([stale, changed])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_draft_fork_blocks_transition(monkeypatch):
    pr = _pr(draft=True)
    pr["head"]["repo"]["full_name"] = "other/fork"
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: pr)
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_draft_permission_failure_remains_draft(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: False)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_duplicate_ready_event_does_not_mark_again(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=False))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("pending", "review"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("duplicate")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 0


def test_draft_base_race_blocks_transition(monkeypatch):
    first, second = _pr(draft=True), _pr(draft=True)
    second["base"]["sha"] = "c" * 40
    states = iter([first, second])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_draft_failed_proof_never_marks_ready(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("failure", "invalid digest"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_mark_ready_requires_graphql_confirmation(monkeypatch):
    monkeypatch.setattr(
        admission.transport,
        "request_graphql_json",
        lambda **_: {"markPullRequestReadyForReview": {"pullRequest": {"id": "PR_test_node", "isDraft": True}}},
    )
    assert admission.mark_ready("token", "PR_test_node") is False


def test_issue_616_policy_authorizes_automatic_ready_without_owner_comment(monkeypatch):
    """Canonical exact-head proof, not a per-HEAD owner comment, authorizes Ready."""
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(
        admission.governance,
        "request_json",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not request owner comments")),
    )
    called = []
    monkeypatch.setattr(admission, "mark_ready", lambda _token, node: called.append(node) or True)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616, HEAD) == 0
    assert called == ["PR_test_node"]


def test_agents_policy_separates_ready_from_merge_approval():
    rules = (ROOT / "AGENTS.md").read_text()
    assert "owner-approved Issue #616 policy" in rules
    assert "no per-PR or per-HEAD owner comment" in rules
    assert "merging always requires separate explicit owner approval" in rules


def test_ambiguous_mutation_with_confirmed_ready_is_success(monkeypatch):
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: False)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 0


def test_ambiguous_mutation_still_draft_is_failure(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: False)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_workflow_mints_app_token_with_pull_requests_write_only() -> None:
    """The Hunter App installation 422s if any scope beyond pull-requests:write is requested."""
    workflow = (ROOT / ".github" / "workflows" / "hunter-candidate-admission.yml").read_text(encoding="utf-8")
    assert "permission-pull-requests: write" in workflow
    for forbidden in (
        "permission-contents:",
        "permission-actions:",
        "permission-checks:",
        "permission-statuses:",
    ):
        assert forbidden not in workflow, f"App token must not request {forbidden!r}; installation rejects it with 422"


def test_workflow_routes_reads_to_github_token_and_mutations_to_app_token() -> None:
    """Reads must stay on GITHUB_TOKEN; only the mutation-token env may carry the App token."""
    workflow = (ROOT / ".github" / "workflows" / "hunter-candidate-admission.yml").read_text(encoding="utf-8")
    assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in workflow
    assert "HUNTER_APP_MUTATION_TOKEN: ${{ steps.admission-token.outputs.token }}" in workflow
    # The minted App token output must never be assigned directly to GITHUB_TOKEN.
    assert "GITHUB_TOKEN: ${{ steps.admission-token.outputs.token }}" not in workflow


def test_reads_use_primary_token_mutations_use_app_token(monkeypatch) -> None:
    """enforce_candidate_admission must route reads/mutations to distinct supplied tokens."""
    read_calls: list[str] = []
    mutation_calls: list[str] = []
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])

    def read_mergeability(_repo: str, token: str, _number: int) -> dict:
        read_calls.append(token)
        return next(states)

    def mark_ready(token: str, node: str) -> bool:
        mutation_calls.append(token)
        return True

    monkeypatch.setattr(admission.governance, "read_mergeability", read_mergeability)
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", mark_ready)

    assert (
        admission.enforce_candidate_admission(
            "fafa33/Project-Hunter",
            "read-token",
            616,
            HEAD,
            "app-mutation-token",
        )
        == 0
    )
    assert read_calls and all(token == "read-token" for token in read_calls)
    assert mutation_calls == ["app-mutation-token"]


def test_missing_mutation_token_falls_back_to_primary_token(monkeypatch) -> None:
    """An unset/empty mutation token must still allow mutations via the primary token (no silent skip)."""
    mutation_calls: list[str] = []
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])

    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda token, node: mutation_calls.append(token) or True)

    assert (
        admission.enforce_candidate_admission(
            "fafa33/Project-Hunter",
            "only-token",
            616,
            HEAD,
            None,
        )
        == 0
    )
    assert mutation_calls == ["only-token"]


def test_main_sources_mutation_token_from_dedicated_env_var(monkeypatch) -> None:
    """main() must read the App token from HUNTER_APP_MUTATION_TOKEN, not GITHUB_TOKEN."""
    captured: dict = {}

    def fake_enforce(repository: str, token: str, pr_number: int, head_sha, mutation_token) -> int:
        captured["token"] = token
        captured["mutation_token"] = mutation_token
        return 0

    monkeypatch.setattr(admission, "enforce_candidate_admission", fake_enforce)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hunter_candidate_admission.py", "--pr", "616", "--repository", "fafa33/Project-Hunter"],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    monkeypatch.setenv("HUNTER_APP_MUTATION_TOKEN", "app-token")

    assert admission.main() == 0
    assert captured == {"token": "read-token", "mutation_token": "app-token"}


def test_main_without_app_mutation_token_falls_back(monkeypatch) -> None:
    """When HUNTER_APP_MUTATION_TOKEN is absent, main() must not leave mutations tokenless."""
    captured: dict = {}

    def fake_enforce(repository: str, token: str, pr_number: int, head_sha, mutation_token) -> int:
        captured["token"] = token
        captured["mutation_token"] = mutation_token
        return 0

    monkeypatch.setattr(admission, "enforce_candidate_admission", fake_enforce)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hunter_candidate_admission.py", "--pr", "616", "--repository", "fafa33/Project-Hunter"],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "read-token")
    monkeypatch.delenv("HUNTER_APP_MUTATION_TOKEN", raising=False)

    assert admission.main() == 0
    assert captured == {"token": "read-token", "mutation_token": "read-token"}
