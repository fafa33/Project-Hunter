"""The trusted Draft PR decision for governed Issue Agent candidates (contract I6).

Adversarial pairs for the branch-shape parser and every evidence gate: content
that looks like an agent candidate but is not one must never open a PR, and a
genuine candidate must never be refused.
"""

from __future__ import annotations

import hashlib
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
        self.open_pulls: list[dict[str, Any]] = []

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
            return list(self.open_pulls)
        if path.startswith("compare/"):
            commits = [_commit("a" * 40, BASE), _commit(HEAD, "a" * 40)]
            return {"commits": commits, "total_commits": self.total_commits or len(commits)}
        if method == "POST" and path == "pulls":
            pull = {"number": 900, "head": {"ref": payload["head"], "repo": {"full_name": repository}}}
            self.open_pulls.append(pull)
            return {"number": 900}
        raise AssertionError(f"unexpected request {method} {path}")


ENVIRON = {"HUNTER_ISSUE_AGENT_PR_TOKEN": "pr-token", "GITHUB_TOKEN": "read-token"}


def test_run_opens_the_draft_pr_with_the_dedicated_token_only_for_the_write() -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        head_repository="fafa33/Project-Hunter",
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
        head_repository="fafa33/Project-Hunter",
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
        head_repository="fafa33/Project-Hunter",
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
            head_repository="fafa33/Project-Hunter",
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
        head_repository="fafa33/Project-Hunter",
        branch=BRANCH,
        head_sha=HEAD,
        environ=ENVIRON,
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (0, False)
    assert not [call for call in github.calls if call[1] == "POST"]


def _workflow() -> dict:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-issue-agent-candidate-pr.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_workflow_runs_the_trusted_module_only_after_a_green_push_preflight() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"workflow_run": {"workflows": ["Hunter / Pre-PR Preflight"], "types": ["completed"]}}
    # The workflow token can read only; the one write uses the dedicated token.
    assert workflow["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}

    bind, job = workflow["jobs"]["bind-issue"], workflow["jobs"]["open-draft-pr"]
    condition = " ".join(bind["if"].split())
    assert "github.event.workflow_run.event == 'push'" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in condition

    _checkout, _python, step = job["steps"]
    assert step["working-directory"] == "engine/scripts"
    assert step["env"]["HUNTER_ISSUE_AGENT_PR_TOKEN"] == "${{ secrets.HUNTER_ISSUE_AGENT_PR_TOKEN }}"
    # Event data reaches the script only through the environment, never the shell text.
    assert "${{" not in step["run"]
    assert "python hunter_issue_agent_candidate_pr.py" in step["run"]
    assert '--head-repository "${HEAD_REPOSITORY}"' in step["run"]


def test_the_privileged_job_never_checks_out_or_executes_candidate_content() -> None:
    job = _workflow()["jobs"]["open-draft-pr"]
    checkout, python, step = job["steps"]

    # The only checkout is this workflow's own trusted default-branch commit:
    # no ref, repository or path taken from event data, and no persisted token.
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"path": "engine", "persist-credentials": False}
    assert python["uses"].startswith("actions/setup-python@")
    assert set(python["with"]) == {"python-version"}

    # Candidate identifiers appear only as environment values of the trusted
    # step, never in a checkout, a `uses`, a working directory or shell text.
    for current in job["steps"]:
        rendered = {key: value for key, value in current.items() if key != "env"}
        assert "workflow_run.head" not in repr(rendered)
    assert {name for name, value in step["env"].items() if "workflow_run.head" in value} == {
        "HEAD_REPOSITORY",
        "HEAD_BRANCH",
        "HEAD_SHA",
    }
    # The dedicated token exists in exactly one step, and only there.
    assert [current for current in job["steps"] if "secrets." in repr(current)] == [step]
    assert "secrets." not in repr({key: value for key, value in _workflow().items() if key != "jobs"})


@pytest.mark.parametrize(
    "head_repository",
    ["attacker/Project-Hunter", "fafa33/Project-Hunter-fork", "", "fafa33/Project-Hunter/../x", "fafa33"],
)
def test_a_fork_or_foreign_head_never_reaches_privileged_pr_creation(head_repository: str) -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        head_repository=head_repository,
        branch=BRANCH,
        head_sha=HEAD,
        environ=ENVIRON,
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (2, False)
    assert "is not" in decision.reason
    # Refused before any request, so the dedicated token is never used.
    assert github.calls == []


def test_the_command_line_requires_the_head_repository() -> None:
    with pytest.raises(SystemExit):
        candidate_pr.main(["--repository", "fafa33/Project-Hunter", "--branch", BRANCH, "--head-sha", HEAD])


@pytest.mark.parametrize("head_sha", ["HEAD", "C" * 40, "c" * 39, "c" * 40 + "?x=1", "../../pulls"])
def test_run_refuses_a_malformed_head_before_any_github_request(head_sha: str) -> None:
    github = FakeGitHub()
    code, decision = candidate_pr.run(
        repository="fafa33/Project-Hunter",
        head_repository="fafa33/Project-Hunter",
        branch=BRANCH,
        head_sha=head_sha,
        environ=ENVIRON,
        request_json=github,
        authorized_signers=SIGNERS,
    )
    assert (code, decision.open) == (2, False)
    assert github.calls == []


# --- PR #558 Copilot: one Issue -> one active Draft PR under real concurrency ---


class LinearizableGitHub:
    """A thread-safe stand-in for GitHub: atomic, ordered PR numbers, consistent reads."""

    def __init__(self, *, decision_barrier: Any = None) -> None:
        import threading

        self._lock = threading.Lock()
        self._next = 100
        self.open_pulls: dict[int, dict[str, Any]] = {}
        self.posts: list[int] = []
        self.closed: list[int] = []
        self._barrier = decision_barrier
        self._listed: set[int] = set()

    @staticmethod
    def head_for(branch: str) -> str:
        return hashlib.sha1(branch.encode()).hexdigest()

    def __call__(self, repository: str, token: str, method: str, path: str, payload: Any = None) -> Any:
        import threading

        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.head_for(path.rsplit("/", 1)[-1])}}
        if path.startswith("issues/"):
            return {"number": 423, "state": "open", "title": "Governed change"}
        if path.startswith("compare/"):
            head = path.split("...", 1)[1].split("?", 1)[0]
            commits = [_commit("a" * 40, BASE), _commit(head, "a" * 40)]
            return {"commits": commits, "total_commits": len(commits)}
        if path.startswith("pulls?"):
            ident = threading.get_ident()
            first = ident not in self._listed
            self._listed.add(ident)
            with self._lock:
                snapshot = list(self.open_pulls.values())
            if first and self._barrier is not None:
                # Force every racer past its pre-POST check before any POST lands.
                self._barrier.wait(timeout=10)
            return snapshot
        if method == "POST" and path == "pulls":
            with self._lock:
                number = self._next
                self._next += 1
                self.open_pulls[number] = {
                    "number": number,
                    "head": {"ref": payload["head"], "repo": {"full_name": repository}},
                }
                self.posts.append(number)
            return {"number": number}
        if method == "PATCH" and path.startswith("pulls/"):
            number = int(path.split("/", 1)[1])
            assert token == "pr-token" and payload == {"state": "closed"}
            with self._lock:
                self.open_pulls.pop(number, None)
                self.closed.append(number)
            return {"number": number, "state": "closed"}
        raise AssertionError(f"unexpected request {method} {path}")


def _race(github: LinearizableGitHub, branches: list[str]) -> list[Any]:
    import threading

    results: list[Any] = [None] * len(branches)

    def attempt(index: int, branch: str) -> None:
        results[index] = candidate_pr.run(
            repository="fafa33/Project-Hunter",
            head_repository="fafa33/Project-Hunter",
            branch=branch,
            head_sha=LinearizableGitHub.head_for(branch),
            environ=ENVIRON,
            request_json=github,
            authorized_signers=SIGNERS,
        )

    threads = [threading.Thread(target=attempt, args=(i, b)) for i, b in enumerate(branches)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    return results


def _branches(count: int, salt: str = "") -> list[str]:
    return [f"issue-423-{hashlib.sha1((salt + str(i)).encode()).hexdigest()[:16]}" for i in range(count)]


def test_racing_authorizations_for_one_issue_leave_exactly_one_active_pr() -> None:
    """The exact TOCTOU from review: both runs see no PR, both POST; one PR survives."""
    import threading

    branches = _branches(2)
    github = LinearizableGitHub(decision_barrier=threading.Barrier(2))
    results = _race(github, branches)
    assert len(github.posts) == 2  # the race really happened
    assert list(github.open_pulls) == [min(github.posts)]
    assert github.closed == [max(github.posts)]
    opened = [decision for _code, decision in results if decision.open]
    refused = [decision for _code, decision in results if not decision.open]
    assert len(opened) == 1 and len(refused) == 1
    assert f"closed duplicate #{max(github.posts)}" in refused[0].reason


def test_many_concurrent_authorizations_converge_on_the_lowest_pr_every_time() -> None:
    import threading

    for round_ in range(20):
        branches = _branches(4, salt=str(round_))
        github = LinearizableGitHub(decision_barrier=threading.Barrier(4) if round_ % 2 == 0 else None)
        results = _race(github, branches)
        assert len(github.open_pulls) == 1, (round_, github.open_pulls)
        assert list(github.open_pulls) == [min(github.posts)]
        assert sum(1 for _code, decision in results if decision.open) == 1
        assert sorted(github.closed + list(github.open_pulls)) == sorted(github.posts)


def test_a_serialized_second_authorization_never_posts() -> None:
    first, second = _branches(2, salt="serial")
    github = LinearizableGitHub()
    _race(github, [first])
    _race(github, [second])
    assert len(github.posts) == 1 and not github.closed
    assert list(github.open_pulls) == github.posts


def test_candidate_pr_creation_is_serialized_per_issue() -> None:
    workflow = _workflow()
    bind, job = workflow["jobs"]["bind-issue"], workflow["jobs"]["open-draft-pr"]
    assert job["needs"] == "bind-issue" and job["if"] == "needs.bind-issue.outputs.issue != ''"
    assert job["concurrency"] == {
        "group": "hunter-issue-agent-candidate-pr-issue-${{ needs.bind-issue.outputs.issue }}",
        "cancel-in-progress": False,
    }
    script = bind["steps"][0]["run"]
    # The workflow binds the Issue with exactly the trusted module's branch shape.
    assert "^issue-([1-9][0-9]{0,9})-[0-9a-f]{16}$" in script
    assert candidate_pr.AGENT_BRANCH_RE.pattern == "issue-([1-9][0-9]{0,9})-([0-9a-f]{16})"
    assert "secrets." not in repr(bind) and "${{" not in script
    assert not any(str(step.get("uses", "")).startswith("actions/checkout") for step in bind["steps"])
