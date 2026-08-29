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
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
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


def test_pending_candidate_waits_without_redrafting(monkeypatch, capsys) -> None:
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
        lambda *_args: (_ for _ in ()).throw(AssertionError("pending must not redraft")),
    )

    assert admission.enforce_candidate_admission("fafa33/Project-Hunter", "token", 369) == 0
    assert "candidate admission is pending" in capsys.readouterr().out


def test_admitted_candidate_stays_ready(monkeypatch) -> None:
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


def test_already_draft_candidate_does_not_requery_admission(monkeypatch) -> None:
    monkeypatch.setattr(
        admission.governance,
        "read_mergeability",
        lambda _repo, _token, _number: _pr(draft=True),
    )
    monkeypatch.setattr(
        admission.governance,
        "candidate_admission",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not query")),
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

    assert "pull_request_target:" in workflow
    assert "workflow_run:" in workflow
    assert "Hunter / Pre-PR Preflight" in workflow
    assert "completed" in workflow
    assert "ready_for_review" in workflow
    assert "pull-requests: write" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "github.event.workflow_run.pull_requests[0].number" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert '--head-sha "${EVENT_HEAD_SHA}"' in workflow
    assert "hunter_candidate_admission.py" in workflow
