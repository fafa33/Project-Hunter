"""The trusted Draft PR decision for governed Issue Agent candidates (contract I6).

Adversarial pairs for the branch-shape parser and every evidence gate: content
that looks like an agent candidate but is not one must never open a PR, and a
genuine candidate must never be refused.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import hunter_issue_agent_candidate_pr as candidate_pr
import pytest

HEAD = "c" * 40
BASE = "b" * 40
BRANCH = "issue-423-0123456789abcdef"
SIGNERS = frozenset({"fafa33", "claude"})


def _commit(sha: str, parent: str, *, signer: str = "fafa33", verified: bool = True, reason: str = "valid") -> dict:
    return {
        "sha": sha,
        "parents": [{"sha": parent}],
        "committer": {"login": signer},
        "commit": {"verification": {"verified": verified, "reason": reason}},
    }


def _evidence(**overrides: Any) -> candidate_pr.CandidateEvidence:
    evidence = candidate_pr.CandidateEvidence(
        branch=BRANCH,
        workflow_head_sha=HEAD,
        branch_head_sha=HEAD,
        issue={"number": 423, "state": "open", "title": "Governed change"},
        open_pull_requests=(),
        range_commits=(_commit("a" * 40, BASE), _commit(HEAD, "a" * 40)),
        authorized_signers=SIGNERS,
    )
    return replace(evidence, **overrides)


def test_a_genuine_candidate_opens_exactly_one_draft_pr_against_main() -> None:
    decision = candidate_pr.decide_candidate_pr(_evidence())
    assert decision.open is True
    assert (decision.head, decision.base, decision.draft) == (BRANCH, "main", True)
    assert decision.issue_number == 423
    assert decision.title == "Issue Agent candidate for #423: Governed change"
    for heading in ("## Summary", "## Scope / architecture impact", "## Verification", "## Review disposition"):
        assert heading in decision.body


@pytest.mark.parametrize(
    "branch",
    [
        "issue-423-feature",
        "issue-423-0123456789ABCDEF",
        "issue-423-0123456789abcde",
        "issue-423-0123456789abcdef0",
        "issue-0423-0123456789abcdef",
        "issue-0-0123456789abcdef",
        "claude/issue-423-0123456789abcdef",
        "issue-423-0123456789abcdef/extra",
        " issue-423-0123456789abcdef",
        "issue-agent-execution",
        "main",
    ],
)
def test_only_the_exact_governed_agent_branch_shape_is_eligible(branch: str) -> None:
    decision = candidate_pr.decide_candidate_pr(_evidence(branch=branch))
    assert decision.open is False
    assert "not a governed Issue Agent branch" in decision.reason


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"branch_head_sha": "d" * 40}, "no longer the branch head"),
        ({"branch_head_sha": None}, "no longer the branch head"),
        ({"workflow_head_sha": "HEAD"}, "exact commit SHA"),
        ({"issue": None}, "does not exist"),
        ({"issue": {"number": 424, "state": "open"}}, "does not exist"),
        ({"issue": {"number": 423, "state": "open", "pull_request": {}}}, "is a pull request"),
        ({"issue": {"number": 423, "state": "closed"}}, "is not open"),
        ({"open_pull_requests": ({"number": 7},)}, "already open"),
        ({"range_commits": ()}, "no candidate commits"),
        ({"range_commits": (_commit("a" * 40, BASE),)}, "does not end at the head"),
    ],
)
def test_every_evidence_gate_refuses_its_failure(overrides: dict[str, Any], reason: str) -> None:
    decision = candidate_pr.decide_candidate_pr(_evidence(**overrides))
    assert decision.open is False
    assert reason in decision.reason


@pytest.mark.parametrize(
    ("commit", "reason"),
    [
        ({**_commit(HEAD, "a" * 40), "parents": [{"sha": "a" * 40}, {"sha": BASE}]}, "merge commit"),
        ({**_commit(HEAD, "a" * 40), "parents": []}, "merge commit"),
        (_commit(HEAD, "a" * 40, verified=False), "no verified signature"),
        (_commit(HEAD, "a" * 40, reason="unknown_key"), "signature is not valid"),
        (_commit(HEAD, "a" * 40, signer="mallory"), "unauthorized signer mallory"),
        ({**_commit(HEAD, "a" * 40), "committer": None}, "unauthorized signer unknown"),
        ({**_commit(HEAD, "a" * 40), "commit": {}}, "no verified signature"),
    ],
)
def test_every_commit_in_the_range_must_be_linear_and_signed_by_an_authorized_signer(commit: dict, reason: str) -> None:
    decision = candidate_pr.decide_candidate_pr(_evidence(range_commits=(_commit("a" * 40, BASE), commit)))
    assert decision.open is False
    assert reason in decision.reason


def test_an_unsigned_ancestor_cannot_hide_behind_a_signed_head() -> None:
    decision = candidate_pr.decide_candidate_pr(
        _evidence(range_commits=(_commit("a" * 40, BASE, verified=False), _commit(HEAD, "a" * 40)))
    )
    assert decision.open is False
    assert "no verified signature" in decision.reason


class FakeGitHub:
    def __init__(self, *, total_commits: int | None = None, issue_status: int = 200) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.total_commits = total_commits
        self.issue_status = issue_status

    def __call__(self, repository: str, token: str, method: str, path: str, payload: Any = None) -> Any:
        self.calls.append((token, method, path, payload))
        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": HEAD}}
        if path.startswith("issues/"):
            if self.issue_status == 404:
                error = RuntimeError("GitHub HTTP 404")
                error.status_code = 404  # type: ignore[attr-defined]
                raise error
            return {"number": 423, "state": "open", "title": "Governed change"}
        if path.startswith("pulls?"):
            return []
        if path.startswith("compare/"):
            commits = [_commit("a" * 40, BASE), _commit(HEAD, "a" * 40)]
            return {"commits": commits, "total_commits": self.total_commits or len(commits)}
        if method == "POST" and path == "pulls":
            return {"number": 900}
        raise AssertionError(f"unexpected request {method} {path}")


ENVIRON = {"HUNTER_ISSUE_AGENT_PR_TOKEN": "pr-token", "GITHUB_TOKEN": "read-token"}


def test_run_opens_the_draft_pr_with_the_dedicated_token_only_for_the_write() -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        branch=BRANCH,
        head_sha=HEAD,
        environ=ENVIRON,
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (0, True)
    writes = [call for call in github.calls if call[1] == "POST"]
    assert len(writes) == 1
    token, _method, path, payload = writes[0]
    assert (token, path) == ("pr-token", "pulls")
    assert payload["draft"] is True and payload["base"] == "main" and payload["head"] == BRANCH
    assert all(call[0] == "read-token" for call in github.calls if call[1] == "GET")


def test_run_fails_closed_without_the_pr_token_and_writes_nothing() -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        branch=BRANCH,
        head_sha=HEAD,
        environ={"GITHUB_TOKEN": "read-token"},
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert code == 2
    assert decision.open is False
    assert "HUNTER_ISSUE_AGENT_PR_TOKEN" in decision.reason
    assert github.calls == []


def test_run_ignores_non_agent_branches_without_touching_github() -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        branch="issue-423-human-feature",
        head_sha=HEAD,
        environ={},
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (0, False)
    assert github.calls == []


def test_run_refuses_a_truncated_commit_range() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        candidate_pr.run(
            repository="fafa33/Project-Hunter",
            branch=BRANCH,
            head_sha=HEAD,
            environ=ENVIRON,
            request_json=FakeGitHub(total_commits=300),
            authorized_signers=SIGNERS,
        )


def test_run_refuses_a_missing_governing_issue_without_writing() -> None:
    github = FakeGitHub(issue_status=404)
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        branch=BRANCH,
        head_sha=HEAD,
        environ=ENVIRON,
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (0, False)
    assert not [call for call in github.calls if call[1] == "POST"]


def test_the_workflow_runs_the_trusted_module_only_after_a_green_push_preflight() -> None:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-issue-agent-candidate-pr.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"workflow_run": {"workflows": ["Hunter / Pre-PR Preflight"], "types": ["completed"]}}
    # The workflow token can read only; the one write uses the dedicated token.
    assert workflow["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}

    (job,) = workflow["jobs"].values()
    condition = " ".join(job["if"].split())
    assert "github.event.workflow_run.event == 'push'" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition

    checkout, _python, step = job["steps"]
    assert checkout["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "path": "engine",
        "persist-credentials": False,
    }
    assert step["working-directory"] == "engine/scripts"
    assert step["env"]["HUNTER_ISSUE_AGENT_PR_TOKEN"] == "${{ secrets.HUNTER_ISSUE_AGENT_PR_TOKEN }}"
    # Event data reaches the script only through the environment, never the shell text.
    assert "${{" not in step["run"]
    assert "python hunter_issue_agent_candidate_pr.py" in step["run"]
