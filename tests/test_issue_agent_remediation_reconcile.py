"""ADR 0039 L4/L8 (RD-4/RD-5): reconcile detects an eligible lifecycle PR and dispatches one bounded remediation.

The ledger is the real signed Issue ledger, the findings are the real anchored knowledge ledger, and the GitHub
facts come from a client that returns *indefinite* for anything it does not know, so a forgotten precondition
can never become a silent yes.
"""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_control as ct
import test_issue_agent_roles as rt

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_state as state

REPOSITORY = rt.REPOSITORY
ISSUE = rt.ISSUE
AUTH = rt.AUTH
BRANCH = ct.BRANCH
PULL_REQUEST = 600
HEAD = rt.CONTROL
GUARD = "src/hunter/automation/issue_agent_roles.py"
CLAIM = "[p ] this fast-forward overwrites a moved head the push uses an empty lease."
KEY = rt.KEY
TRUST = rt.TRUST

#: A finding on the pull request whose head is the one the parent authorization published.
OBSERVATION = {
    "source": "github-review",
    "provider": "github-review",
    "event_id": "review-comment-4242",
    "source_pr": PULL_REQUEST,
    "reviewed_head_sha": HEAD,
    "reviewed_base_sha": "b" * 40,
    "source_event_head_sha": HEAD,
    "reviewer": "chatgpt-codex-connector[bot]",
    "path": GUARD,
    "line": 812,
    "message": "**[P1] This fast-forward overwrites a moved head** The push uses an empty lease.",
    "availability": "available",
}
OTHER_PR = {
    **OBSERVATION,
    "event_id": "review-comment-4243",
    "source_pr": 601,
    "message": "**[P1] An unrelated finding** This is on another pull request entirely.",
}


class Facts:
    """Definitive GitHub facts by path; an unlisted path is indefinite, never a silent yes."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.dispatched: list[tuple[str, dict[str, str]]] = []
        self.reads: list[str] = []

    def open_pr(self, number: int = PULL_REQUEST, branch: str = BRANCH, head: str = HEAD) -> Facts:
        self.routes[f"/repos/{REPOSITORY}/pulls"] = [
            {"number": number, "draft": True, "head": {"ref": branch, "sha": head}, "base": {"ref": "main"}}
        ]
        return self

    def get(self, path: str) -> control.Read:
        self.reads.append(path)
        value = self.routes.get(path.split("?", 1)[0], False)
        if value is False:
            return control.Read("unknown")
        return control.Read("absent") if value is None else control.Read("ok", value)

    def dispatch(self, workflow_file: str, inputs: Mapping[str, str]) -> bool:
        self.dispatched.append((workflow_file, dict(inputs)))
        return True


def _pull_request(number: int = PULL_REQUEST, branch: str = BRANCH, head: str = HEAD) -> dict[str, Any]:
    return {"number": number, "draft": True, "head": {"ref": branch, "sha": head}, "base": {"ref": "main"}}


def _applicability(family: str, path: str) -> bool | None:
    return True if family == "DFF-049" else None


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY",
        "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737",
    )


@pytest.fixture
def repos(tmp_path: Path) -> dict[str, Any]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed)], check=True)
    (seed / "README.md").write_text("base\n")
    rt.git(seed, "add", "README.md")
    rt.git(seed, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    rt.git(seed, "push", "-q", str(remote), "HEAD:refs/heads/main")
    trusted_repo = tmp_path / "trusted"
    subprocess.run(["git", "clone", "--quiet", str(remote), str(trusted_repo)], check=True)
    return {
        "tmp": tmp_path,
        "remote": str(remote),
        "base": rt.git(seed, "rev-parse", "HEAD"),
        "trusted": trusted_repo,
    }


@pytest.fixture
def remote(repos: dict[str, Any]) -> str:
    return str(repos["remote"])


@pytest.fixture
def store(repos: dict[str, Any]) -> state.GitLedgerStore:
    return state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "work")


def complete_parent(repos: dict[str, Any]) -> state.LedgerView:
    """Drive the shared Issue ledger's authorization to COMPLETED, really publishing its branch."""

    ledger, envelope = rt.authorize(repos)
    outcome = rt.execute(repos, ledger, envelope, rt.RecordingIsolation(rt.CANARY))
    assert outcome.sealed_result is not None
    from hunter.automation.issue_agent_transport import header

    ledger.write(
        state.RESULT_BOUND,
        {
            "result_artifact": rt._artifact(outcome.sealed_result, 22),
            "result_plaintext_sha256": header(outcome.sealed_result).plaintext_sha256,
            "executor_job_id": 5,
            "executor_conclusion": "success",
            "executor_advisory_code": None,
        },
        "bind",
    )
    receipt = rt.validate(repos, ledger, outcome.sealed_result)
    ledger.write(
        state.VALIDATED,
        {
            "receipt_sha256": state.sha256_hex(state.canonical_json(receipt)),
            "result_sha256": receipt["result_sha256"],
            "tree_sha": receipt["tree_sha"],
            "unsigned_commit_sha": receipt["unsigned_commit_sha"],
            "validation_definition": "9" * 64,
            "toolchain_sha256": "8" * 64,
            "validator_run_id": 100,
            "validation_attempts": 1,
        },
        "record-validation",
    )
    published = rt.publish(repos, ledger, outcome.sealed_result, _signing_key(repos["tmp"]))
    ledger.write(
        state.PUBLISHED,
        {
            "writer_login": rt.WRITER.login,
            "publication_identity": state.publication_identity(
                repository_id=1,
                issue_number=ISSUE,
                authorization_id=AUTH,
                base_sha=repos["base"],
                task_scope_sha256=receipt["task_scope_sha256"],
                execution_id=ledger.view.authorizations[AUTH].evidence[state.AUTHORIZED]["execution_id"],
                result_sha256=receipt["result_sha256"],
                tree_sha=receipt["tree_sha"],
                unsigned_commit_sha=receipt["unsigned_commit_sha"],
                control_sha=rt.CONTROL,
                writer_login=rt.WRITER.login,
            ),
            "head_sha": published.head_sha,
            "commit_verified": True,
            "publish_attempts": 1,
            "deadline_completed_at": "2026-10-04T18:00:00Z",
        },
        "finalize",
    )
    ledger.write(
        state.COMPLETED,
        {
            "pull_request_number": PULL_REQUEST,
            "pull_request_node_id": "PR_kwNode",
            "pull_request_head_sha": published.head_sha,
            "draft": True,
            "preflight_run_id": 7,
            "preflight_conclusion": "success",
        },
        "candidate-pr-record",
    )
    return ledger.view


def _signing_key(root: Path) -> str:
    path = root / "signing-id"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return str(path)


def ingested(store: state.GitLedgerStore, *observations: Mapping[str, Any]) -> knowledge.KnowledgeView:
    _head, view = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    writes, _refusals = knowledge.ingestion_writes(
        view,
        list(observations),
        repository_id=1,
        trusted_reviewers=frozenset({"chatgpt-codex-connector"}),
        applicability=_applicability,
    )
    knowledge.append(
        store,
        writes,
        trust=TRUST,
        provenance=rt.trusted,
        signing_key=KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "knowledge-ingest",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, view = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    return view


def eligible(
    github: Facts, view: state.LedgerView, known: knowledge.KnowledgeView
) -> control.RemediationCandidate | None:
    return control.eligible_remediation(github, ct.CONFIG, view, known, ISSUE)


@pytest.fixture
def completed(repos: dict[str, Any]) -> state.LedgerView:
    return complete_parent(repos)


# --- eligibility ------------------------------------------------------------------------------------------


def test_a_completed_parent_with_an_open_finding_is_eligible_at_the_pr_head(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    candidate = eligible(facts, completed, known)
    assert candidate is not None
    assert (candidate.pull_request_number, candidate.branch, candidate.bound_head_sha) == (
        PULL_REQUEST,
        BRANCH,
        head,
    )
    assert candidate.parent_authorization_id == AUTH
    assert [item["path"] for item in candidate.findings] == [GUARD]
    assert candidate.attempts == (1,)
    assert candidate.findings[0]["claim"] == CLAIM


def test_a_finding_observed_at_another_head_is_not_yet_eligible(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    facts = Facts().open_pr(head=completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"])
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": "9" * 40})
    assert eligible(facts, completed, known) is None


def test_a_moved_pull_request_head_is_not_eligible(store: state.GitLedgerStore, completed: state.LedgerView) -> None:
    facts = Facts().open_pr(head="8" * 40)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": "8" * 40})
    assert eligible(facts, completed, known) is None


def test_no_ingested_finding_is_never_eligible(store: state.GitLedgerStore, completed: state.LedgerView) -> None:
    facts = Facts().open_pr(head=completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"])
    assert eligible(facts, completed, ingested(store)) is None


def test_an_ambiguous_finding_waits_for_a_human_disposition(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    """ADR 0039 L3.1: an unknown or inapplicable family tag is ambiguous, and an ambiguous finding is never guessed."""

    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head, "message": "[family:DFF-404] not a family"})
    assert eligible(facts, completed, known) is None
    classification = next(iter(known.findings.values())).classification
    assert classification is not None and classification["outcome"] == "ambiguous"


def test_an_active_authorization_blocks_a_second_remediation(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    active = state.LedgerView(1, ISSUE, dict(completed.authorizations), list(completed.claimed), AUTH, None, 9)
    assert eligible(facts, active, known) is None


def test_a_pull_request_on_a_branch_with_no_completed_authorization_is_never_eligible(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(branch=f"issue-{ISSUE}-{'f' * 16}", head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    assert eligible(facts, completed, known) is None


def test_two_open_lifecycle_pull_requests_are_ambiguous_and_never_guessed(
    remote: str, store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts()
    facts.routes[f"/repos/{REPOSITORY}/pulls"] = [
        _pull_request(PULL_REQUEST, BRANCH, head),
        _pull_request(601, f"issue-{ISSUE}-{'f' * 16}", head),
    ]
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    assert eligible(facts, completed, known) is None


def test_an_indefinite_pull_request_listing_is_never_a_yes(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    known = ingested(store, OBSERVATION)
    with pytest.raises(control.FactsUnavailable):
        eligible(Facts(), completed, known)


def test_a_finding_on_another_pull_request_is_never_borrowed(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OTHER_PR, "reviewed_head_sha": head})
    assert eligible(facts, completed, known) is None


# --- attempt budgets (L4) --------------------------------------------------------------------------------


def _spend(
    store: state.GitLedgerStore,
    view: knowledge.KnowledgeView,
    identities: Sequence[str],
    *,
    count: int = 1,
) -> None:
    """Record real remediation requests against real ingested findings, consuming the ADR 0039 L4 budgets."""

    writes = [
        knowledge.Write(
            "remediation_requested",
            {
                "finding_id": identity,
                "attempt": attempt,
                "pull_request_number": item.pull_request_number,
                "bound_head_sha": item.ingested["provenance"]["reviewed_head_sha"],
                "authorization_id": "hunter-issue-agent-authorization:"
                + state.sha256_hex(f"{identity}:{attempt}".encode())[:64],
            },
        )
        for identity in identities
        for item in (view.findings[identity],)
        for attempt in range(len(item.remediations) + 1, len(item.remediations) + 1 + count)
    ]
    knowledge.append(
        store,
        writes,
        trust=TRUST,
        provenance=rt.trusted,
        signing_key=KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "remediate",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )


def test_the_per_finding_budget_is_exhausted_after_two_attempts(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    assert eligible(facts, completed, known).attempts == (1,)
    identity = next(iter(known.findings))
    _spend(store, known, [identity])
    _head, view = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    assert eligible(facts, completed, view).attempts == (2,)
    _spend(store, view, [identity])
    _head, exhausted = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    assert eligible(facts, completed, exhausted) is None


def test_the_per_pull_request_budget_is_exhausted_after_five_attempts(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    ingested(
        store,
        {**OBSERVATION, "reviewed_head_sha": head},
        *(
            {
                **OBSERVATION,
                "event_id": f"review-comment-{5000 + index}",
                "reviewed_head_sha": head,
                "message": f"**[P1] Another real finding {index}** This is a distinct reviewer comment.",
            }
            for index in range(4)
        ),
    )
    _head, view = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    assert len(view.findings) == 5
    _spend(store, view, sorted(view.findings))  # exactly the five-request pull-request budget
    _head, view = knowledge.read(store, trust=TRUST, provenance=rt.trusted)
    assert eligible(facts, completed, view) is None


# --- the dispatch -----------------------------------------------------------------------------------------


def test_the_dispatch_carries_only_the_signed_authorization_document() -> None:
    facts = Facts()
    candidate = control.RemediationCandidate(PULL_REQUEST, BRANCH, HEAD, AUTH, (), ())
    document = remediation.sign_remediation.__doc__ or ""  # the dispatch never reads the code, only the bytes
    assert control.dispatch_remediation(facts, candidate, b'{"schema_version":"x"}')
    ((workflow, inputs),) = facts.dispatched
    assert workflow == "hunter-issue-agent-trigger.yml"
    assert set(inputs) == {control.REMEDIATION_DISPATCH_INPUT}
    assert base64.b64decode(inputs[control.REMEDIATION_DISPATCH_INPUT]) == b'{"schema_version":"x"}'
    del document


def test_a_minted_remediation_document_is_verifiable_and_binds_the_exact_head(
    store: state.GitLedgerStore, completed: state.LedgerView
) -> None:
    from hunter.automation.issue_agent_execution import (
        IssueAgentAuthorizationVerifier,
        verify_signed_authorization,
    )

    head = completed.authorizations[AUTH].evidence[state.PUBLISHED]["head_sha"]
    facts = Facts().open_pr(head=head)
    known = ingested(store, {**OBSERVATION, "reviewed_head_sha": head})
    candidate = eligible(facts, completed, known)
    assert candidate is not None
    issue = {
        "number": ISSUE,
        "state": "open",
        "html_url": f"https://github.com/{REPOSITORY}/issues/{ISSUE}",
        "title": "Canary",
        "body": "Create docs/ISSUE_AGENT_CANARY.md.",
        "updated_at": "2026-10-04T10:00:00Z",
        "labels": [{"name": "hunter-agent-execute"}],
    }
    group = remediation.remediation_group(
        parent_authorization_id=candidate.parent_authorization_id,
        issue_number=ISSUE,
        pull_request_number=candidate.pull_request_number,
        bound_head_sha=candidate.bound_head_sha,
        attempt=min(candidate.attempts),
        findings=candidate.findings,
    )
    authorization = remediation.remediation_authorization(
        issue, repository=REPOSITORY, owner_login="fafa33", remediation=group
    )
    scope = remediation.remediation_scope(
        authorization, completed.authorizations[AUTH].evidence[state.AUTHORIZED]["task_scope"]
    )
    signed = remediation.sign_remediation(authorization, scope, signing_key=KEY)
    verifier = IssueAgentAuthorizationVerifier(KEY.public_key().public_bytes_raw())
    assert verify_signed_authorization(signed, issuer_verifier=verifier, repository=REPOSITORY, owner_login="fafa33")
    assert signed.implementation_scope.base_sha == head
    assert json.loads(signed.to_json())["authorization"]["remediation"]["bound_head_sha"] == head
    # A second pass over the same facts mints the identical identity, so a duplicate dispatch loses the claim.
    again = remediation.remediation_authorization(issue, repository=REPOSITORY, owner_login="fafa33", remediation=group)
    assert again.authorization_id == authorization.authorization_id
