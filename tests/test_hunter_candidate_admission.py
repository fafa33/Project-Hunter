from __future__ import annotations

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
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
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
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
    stale = _pr(draft=True)
    changed = _pr(draft=True)
    changed["head"]["sha"] = "c" * 40
    states = iter([stale, changed])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_draft_fork_blocks_transition(monkeypatch):
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
    pr = _pr(draft=True)
    pr["head"]["repo"]["full_name"] = "other/fork"
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: pr)
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1


def test_draft_permission_failure_remains_draft(monkeypatch):
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
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
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
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


def test_owner_ready_authorization_requires_exact_head_and_owner(monkeypatch):
    comments = [
        {"user": {"login": "attacker"}, "body": f"OWNER-APPROVED-READY HEAD={HEAD}"},
        {"user": {"login": "fafa33"}, "body": "OWNER-APPROVED-READY HEAD=" + "b" * 40},
    ]
    monkeypatch.setattr(admission.governance, "request_json", lambda *_: comments)
    assert admission.owner_ready_authorized("fafa33/Project-Hunter", "token", 616, HEAD) is False
    comments.append({"user": {"login": "fafa33"}, "body": f"OWNER-APPROVED-READY HEAD={HEAD}"})
    assert admission.owner_ready_authorized("fafa33/Project-Hunter", "token", 616, HEAD) is True


def test_missing_owner_approval_preserves_draft(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: False)
    monkeypatch.setattr(admission, "mark_ready", lambda *_: (_ for _ in ()).throw(AssertionError("unauthorized")))
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 0


def test_ambiguous_mutation_with_confirmed_ready_is_success(monkeypatch):
    states = iter([_pr(draft=True), _pr(draft=True), _pr(draft=False)])
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: next(states))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
    monkeypatch.setattr(admission, "mark_ready", lambda *_: False)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 0


def test_ambiguous_mutation_still_draft_is_failure(monkeypatch):
    monkeypatch.setattr(admission.governance, "read_mergeability", lambda *_: _pr(draft=True))
    monkeypatch.setattr(admission.governance, "candidate_admission", lambda *_: ("success", "proof"))
    monkeypatch.setattr(admission, "owner_ready_authorized", lambda *_: True)
    monkeypatch.setattr(admission, "mark_ready", lambda *_: False)
    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 616) == 1
