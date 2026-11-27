"""Issue #575: trusted resolve writer identity and canonical manual-PR adoption (ADR 0039 L7).

Two verified defects are covered here:

1. ``cmd_resolve`` recorded a ledger writer job id (``resolve``) that no trusted workflow run can
   ever have (the real job is ``resolve-finding``), so every resolution record failed the real
   run-provenance check.
2. A manually created PR has no Issue Agent execution ledger, remediation authorization or
   ``PUBLISHED`` record. Its exact-head evidence is a signed Knowledge Receipt plus the owner's
   authenticated disposition reply, so resolution must be possible from those facts alone -- never
   by fabricating an authorization the lifecycle never issued.

Every test below is a decision about observed state: no secret, node id, head or owner reply is
invented by the resolver.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import pytest
import test_issue_agent_control as ct
import test_issue_agent_remediation_reconcile as rr
import test_issue_agent_remediation_resolution as res
import test_issue_agent_roles as rt

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_state as state

ROOT = Path(__file__).resolve().parents[1]
HEAD = res.REMEDIATED
FAMILY = res.FAMILY
TESTS = [
    res.TEST,
    "tests/test_issue_575_preventive_obligations.py::test_missing_focused_test_fails_closed_in_bound_record",
]
RECORDED_AT = "2026-11-27T08:28:09Z"


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY",
        "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737",
    )


@pytest.fixture
def repos(tmp_path: Path) -> dict[str, Any]:
    return rr.repos.__wrapped__(tmp_path)


@pytest.fixture
def store(repos: dict[str, Any]) -> Any:
    return state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "work")


# --- the trusted writer identity ---------------------------------------------------------------------------


def test_the_trusted_resolve_finding_job_identity_is_allowed() -> None:
    """The real reconcile job id is a valid writer; a made-up one is refused by the real checker."""

    check = control.run_provenance(ct.FakeGitHub(), ct.CONFIG)
    recorded = {
        "workflow_path": control.RECONCILE_WORKFLOW,
        "role": "reconcile",
        "run_id": 300,
        "run_attempt": 1,
        "head_sha": rt.CONTROL,
    }
    assert check({**recorded, "job": "resolve-finding"}, {})
    assert not check({**recorded, "job": "resolve"}, {})
    assert not check({**recorded, "job": "authorize"}, {})


def test_cmd_resolve_records_the_actual_trusted_workflow_job() -> None:
    """A structural guard: the resolver's writer identity must name the job that actually exists."""

    tree = ast.parse((ROOT / "scripts" / "hunter_issue_agent_lifecycle.py").read_text(encoding="utf-8"))
    command = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "cmd_resolve")
    writer_calls = [
        node
        for node in ast.walk(command)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_writer"
    ]
    assert len(writer_calls) == 1

    def value(node: ast.expr) -> Any:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "control":
            return getattr(control, node.attr)
        return ast.literal_eval(node)

    args = [value(arg) for arg in writer_calls[0].args]
    assert args == ["reconcile", "resolve-finding", control.RECONCILE_WORKFLOW]


# --- a verified manual-PR knowledge view -------------------------------------------------------------------


def manual_view(
    store: state.GitLedgerStore,
    *,
    head: str = HEAD,
    family: str | None = FAMILY,
    tests: list[str] | None = None,
    outcome: str = "matched-family",
    receipt: bool = True,
) -> tuple[str, Any]:
    """An ingested finding with a signed deterministic family link andKnowledge Receipt at ``head``."""

    view = rr.ingested(store, {**rr.OBSERVATION, "reviewed_head_sha": res.HEAD, "path": res.GUARD})
    identity = next(iter(view.findings))
    chosen_tests = list(tests if tests is not None else TESTS)
    writes = [
        knowledge.Write(
            "finding_classified",
            {
                "finding_id": identity,
                "outcome": "matched" if family else "ambiguous",
                "family_id": family,
                "candidate_id": None,
                "basis": "deterministic",
                "regression_tests": [],
                "authorization_id": None,
            },
        )
    ]
    if receipt:
        writes.append(
            knowledge.Write(
                "knowledge_receipt",
                {
                    "finding_id": identity,
                    "fingerprint": view.findings[identity].ingested["fingerprint"],
                    "invariant": "Exact-head remediation evidence for a manually created pull request",
                    "affected_paths": [res.GUARD],
                    "correction_commit": head,
                    "regression_evidence": chosen_tests,
                    "outcome": outcome,
                    "family_id": family if outcome == "matched-family" else None,
                },
            )
        )
    rr.knowledge.append(
        store,
        writes,
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.KNOWLEDGE_WORKFLOW,
            "job": "knowledge-ingest",
            "role": "knowledge-ingest",
            "run_id": 100,
            "run_attempt": 1,
            "head_sha": rt.CONTROL,
        },
        recorded_at=RECORDED_AT,
    )
    _head, verified = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    return identity, verified


def owner_reply_body(head: str = HEAD, family: str = FAMILY, tests: list[str] | None = None) -> str:
    chosen = list(tests if tests is not None else TESTS)
    selectors = " ".join(f"[test:{item}]" for item in chosen)
    return (
        f"Confirmed and fixed in {head}. [family:{family}] "
        f"Exact-head regression evidence: {selectors}. "
        f"Hosted exact-head proof succeeded at {head}."
    )


def owner_reply(comment_id: int = 9001, *, login: str = "fafa33", body: str | None = None) -> dict[str, Any]:
    return {
        "id": comment_id,
        "user": {"login": login},
        "in_reply_to_id": res.COMMENT_ID,
        "body": body if body is not None else owner_reply_body(),
    }


class ManualWorld(res.World):
    """The post-fix world of a manually created PR: a signed receipt and an owner reply on the thread."""

    def __init__(self, *, head: str = HEAD) -> None:
        super().__init__(head=head)
        self.comments: list[dict[str, Any]] = []
        self.resolve_only_fails = False
        self.mutations = 0

    def get(self, path: str) -> control.Read:
        if path.split("?", 1)[0].endswith(f"/pulls/{rr.PULL_REQUEST}/comments"):
            return control.Read("ok", list(self.comments))
        return super().get(path)

    def graphql(self, query: str, variables: Any) -> control.Read:
        if "resolveReviewThread(input" in query and self.resolve_only_fails:
            return control.Read("unknown")
        return super().graphql(query, variables)

    def reply(self, path: str, body: Any) -> control.Read:
        self.mutations += 1
        return super().reply(path, body)


def manual_world() -> ManualWorld:
    world = ManualWorld()
    world.thread(outdated=True)
    world.comments = [owner_reply()]
    return world


def append_resolution(store: state.GitLedgerStore, view: Any, identity: str, reply_id: int, receipt_digest: str) -> Any:
    writes = remediation.resolution_writes(
        view,
        identity,
        remediated_head_sha=HEAD,
        reply_comment_id=reply_id,
        adoption_receipt_sha256=receipt_digest,
    )
    rr.knowledge.append(
        store,
        writes,
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "resolve-finding",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": rt.CONTROL,
        },
        recorded_at=RECORDED_AT,
    )
    _head, closed = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    return closed


# --- RED-to-GREEN: valid adoption, resolution and idempotency ----------------------------------------------


def test_a_manually_created_pr_is_adopted_from_signed_receipt_evidence(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    proof = control.manual_adoption_proof(world, ct.CONFIG, view, identity)
    assert proof is not None
    assert proof.remediated_head_sha == HEAD and proof.preflight_run_id == 555
    assert proof.authorization_id == ""  # no execution authorization is invented for a manual PR
    assert proof.family_id == FAMILY and set(proof.regression_tests) == set(TESTS)
    assert proof.adoption_receipt_sha256
    assert proof.existing_reply_id == 9001
    assert (
        remediation.proven_writes(
            view,
            identity,
            authorization_id="x",
            remediated_head_sha=HEAD,
            receipt_sha256=res.RECEIPT,
            preflight_run_id=555,
        )
        == []
    )


def test_manual_adoption_resolves_the_exact_thread_and_records_it_once(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    proof = control.manual_adoption_proof(world, ct.CONFIG, view, identity)
    assert proof is not None
    assert control.resolve_thread(world, ct.CONFIG, proof, "unused") == 9001
    assert world.replies == []  # the owner's valid reply is reused; no duplicate is posted
    assert world.resolved == [res.THREAD_NODE]
    closed = append_resolution(store, view, identity, 9001, proof.adoption_receipt_sha256 or "")
    assert closed.findings[identity].open is False
    assert (
        remediation.resolution_writes(
            closed,
            identity,
            remediated_head_sha=HEAD,
            reply_comment_id=9001,
            adoption_receipt_sha256=proof.adoption_receipt_sha256,
        )
        == []
    )


def test_an_interrupted_resolve_retries_without_a_second_reply(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    proof = control.manual_adoption_proof(world, ct.CONFIG, view, identity)
    assert proof is not None
    world.resolve_only_fails = True
    assert control.resolve_thread(world, ct.CONFIG, proof, "unused") is None
    assert world.replies == [] and world.mutations == 0
    world.resolve_only_fails = False
    assert control.resolve_thread(world, ct.CONFIG, proof, "unused") == 9001
    assert world.resolved == [res.THREAD_NODE] and world.mutations == 0


def test_a_thread_already_resolved_by_the_owner_needs_no_mutation(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    world.threads.clear()
    world.thread(resolved=True, outdated=True)
    proof = control.manual_adoption_proof(world, ct.CONFIG, view, identity)
    assert proof is not None and proof.existing_reply_id == 9001
    assert control.resolve_thread(world, ct.CONFIG, proof, "unused") == 9001
    assert world.replies == [] and world.resolved == [] and world.mutations == 0


def test_a_head_that_moves_after_proof_is_never_mutated(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    proof = control.manual_adoption_proof(world, ct.CONFIG, view, identity)
    assert proof is not None
    world.head = "8" * 40
    assert control.resolve_thread(world, ct.CONFIG, proof, "unused") is None
    assert world.replies == [] and world.resolved == [] and world.mutations == 0


# --- RED-to-GREEN: missing, stale, ambiguous and unauthorized evidence fails closed ------------------------


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda w, s: manual_view(s, receipt=False)[1], id="no-signed-receipt"),
        pytest.param(lambda w, s: manual_view(s, head="8" * 40)[1], id="stale-receipt-head"),
        pytest.param(
            lambda w, s: manual_view(s, outcome="pending-adjudication")[1],
            id="no-family-in-receipt",
        ),
        pytest.param(lambda w, s: manual_view(s, tests=["tests/x.py::test_wrong"])[1], id="wrong-tests"),
    ],
)
def test_invalid_receipt_evidence_is_never_adopted(store: state.GitLedgerStore, setup: Any) -> None:
    world = manual_world()
    bad_view = setup(world, store)
    identity = next(iter(bad_view.findings))
    assert control.manual_adoption_proof(world, ct.CONFIG, bad_view, identity) is None


@pytest.mark.parametrize(
    "comments",
    [
        pytest.param([], id="no-owner-reply"),
        pytest.param([owner_reply(login="someone-else")], id="reply-by-non-owner"),
        pytest.param([owner_reply(body=owner_reply_body(family="DFF-999"))], id="wrong-family"),
        pytest.param([owner_reply(body=owner_reply_body(tests=["tests/x.py::test_wrong"]))], id="wrong-tests"),
        pytest.param([owner_reply(body=owner_reply_body(head="8" * 40))], id="stale-reply-head"),
        pytest.param([owner_reply(body="Looks good to me.")], id="no-disposition"),
    ],
)
def test_unauthorized_or_mismatched_owner_replies_are_never_adopted(
    store: state.GitLedgerStore, comments: list[dict[str, Any]]
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    world.comments = comments
    assert control.manual_adoption_proof(world, ct.CONFIG, view, identity) is None


def test_a_current_thread_repeating_the_claim_blocks_adoption(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = ManualWorld()
    world.thread(resolved=False, outdated=False, comment=7777)
    world.comments = [owner_reply()]
    assert control.manual_adoption_proof(world, ct.CONFIG, view, identity) is None


def test_a_missing_or_failed_exact_head_preflight_blocks_adoption(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    world.preflight = None
    assert control.manual_adoption_proof(world, ct.CONFIG, view, identity) is None
    world.preflight = "failure"
    assert control.manual_adoption_proof(world, ct.CONFIG, view, identity) is None


def test_an_indefinite_thread_read_never_becomes_adoption(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    world = manual_world()
    world.graphql_fails = True
    with pytest.raises(control.FactsUnavailable):
        control.manual_adoption_proof(world, ct.CONFIG, view, identity)


def test_a_resolution_record_requires_the_signed_receipt_digest(
    store: state.GitLedgerStore,
) -> None:
    identity, view = manual_view(store)
    digest = view.keys[("knowledge_receipt", identity)]
    assert (
        remediation.resolution_writes(
            view, identity, remediated_head_sha=HEAD, reply_comment_id=9001, adoption_receipt_sha256="0" * 64
        )
        == []
    )
    assert remediation.resolution_writes(view, identity, remediated_head_sha=HEAD, reply_comment_id=9001) == []
    writes = remediation.resolution_writes(
        view, identity, remediated_head_sha=HEAD, reply_comment_id=9001, adoption_receipt_sha256=digest
    )
    assert [item.kind for item in writes] == ["thread_resolved"]
    assert writes[0].evidence["adoption_receipt_sha256"] == digest


# --- RED-to-GREEN: the resolver's control flow, not only its proof helpers ----------------------------------


def _proof(finding_id: str, number: int, *, manual: bool = False) -> control.ResolutionProof:
    return control.ResolutionProof(
        finding_id=finding_id,
        pull_request_number=number,
        thread=control.ReviewThread("thread-" + finding_id[:8], False, True, 9001, "src/x.py", None),
        remediated_head_sha=HEAD,
        preflight_run_id=555,
        authorization_id="" if manual else "auth-" + finding_id[:8],
        receipt_sha256="1" * 64,
        regression_tests=(TESTS[0],),
        family_id=FAMILY,
        adoption_receipt_sha256="2" * 64 if manual else None,
        existing_reply_id=9001 if manual else None,
    )


def _resolve_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active_issues: list[int],
    ledger_failures: set[int],
    governed_findings: set[str],
    manual_findings: set[str],
) -> tuple[list[str], list[tuple[str, list[Any]]], dict[str, int]]:
    findings = {
        identity: SimpleNamespace(
            open=True,
            classifications={},
            proven=None,
            resolved=None,
            receipt={"family_id": FAMILY} if identity in governed_findings else None,
            ingested={},
        )
        for identity in sorted(governed_findings | manual_findings)
    }
    view = SimpleNamespace(findings=findings)
    outputs: list[str] = []
    appends: list[tuple[str, list[Any]]] = []
    calls = {"governed": 0, "manual": 0, "ledger": 0}

    monkeypatch.setenv("GITHUB_RUN_ID", "300")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", rt.CONTROL)
    monkeypatch.setattr(lifecycle, "_github", lambda _configuration: object())
    monkeypatch.setattr(lifecycle, "_store", lambda *_a, **_k: object())
    monkeypatch.setattr(lifecycle, "_knowledge_view", lambda *_a, **_k: view)
    monkeypatch.setattr(lifecycle, "_active_issues", lambda _configuration: list(active_issues))
    monkeypatch.setattr(
        lifecycle,
        "_writer",
        lambda *_a, **_k: SimpleNamespace(recorded_by=lambda: {"job": "resolve-finding"}),
    )
    monkeypatch.setattr(lifecycle, "_ed25519", lambda *_a, **_k: b"0" * 64)
    monkeypatch.setattr(lifecycle, "_output", lambda **values: outputs.extend(f"{k}={v}\n" for k, v in values.items()))
    monkeypatch.setattr(control, "require_anchor", lambda *_a, **_k: None)
    monkeypatch.setattr(control, "run_provenance", lambda *_a, **_k: (lambda *_x, **_y: True))

    def load_ledger(_store: Any, _configuration: Any, _provenance: Any, issue: int) -> tuple[str, Any]:
        calls["ledger"] += 1
        if issue in ledger_failures:
            raise control.FactsUnavailable("no ledger for this manual PR")
        return "head", SimpleNamespace(authorizations={})

    monkeypatch.setattr(control, "load_ledger", load_ledger)

    def governed(_github: Any, _configuration: Any, _known: Any, _ledger: Any, finding_id: str) -> Any:
        calls["governed"] += 1
        return _proof(finding_id, 788) if finding_id in governed_findings else None

    def manual(_github: Any, _configuration: Any, _known: Any, finding_id: str) -> Any:
        calls["manual"] += 1
        return _proof(finding_id, 789, manual=True) if finding_id in manual_findings else None

    monkeypatch.setattr(control, "exact_head_proof", governed)
    monkeypatch.setattr(control, "manual_adoption_proof", manual)
    monkeypatch.setattr(control, "resolve_thread", lambda *_a, **_k: 9001)
    monkeypatch.setattr(remediation, "evidence_reply", lambda *_a, **_k: "evidence reply")
    monkeypatch.setattr(remediation, "proven_writes", lambda *_a, **_k: [SimpleNamespace(kind="finding_proven")])
    monkeypatch.setattr(remediation, "resolution_writes", lambda *_a, **_k: [SimpleNamespace(kind="thread_resolved")])
    monkeypatch.setattr(knowledge, "read", lambda *_a, **_k: ("head", view))
    monkeypatch.setattr(knowledge, "proven_receipt_writes", lambda *_a, **_k: [])
    monkeypatch.setattr(knowledge, "verified_receipt_at_head", lambda *_a, **_k: True)

    def append(_store: Any, writes: list[Any], **_kwargs: Any) -> tuple[str, Any, int]:
        appends.append(("head", list(writes)))
        return "head", view, len(writes)

    monkeypatch.setattr(knowledge, "append", append)
    return outputs, appends, calls


def test_manual_adoption_runs_without_any_active_issue_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manually created PR has no Issue ledger at all; its adoption is still evaluated."""

    outputs, appends, calls = _resolve_harness(
        monkeypatch,
        active_issues=[],
        ledger_failures=set(),
        governed_findings=set(),
        manual_findings={"b" * 64},
    )
    code = lifecycle.cmd_resolve(ct.CONFIG, argparse.Namespace(issue=None))
    assert code == 0
    assert calls["ledger"] == 0
    assert calls["manual"] == 1
    assert [kind for _head, writes in appends for kind in (item.kind for item in writes)] == ["thread_resolved"]
    assert "resolved_pr_numbers=789\n" in outputs


def test_manual_adoption_runs_with_multiple_and_mixed_ledgers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Governed and manual evidence are each evaluated once, even with several active ledgers."""

    outputs, appends, calls = _resolve_harness(
        monkeypatch,
        active_issues=[575, 576, 577],
        ledger_failures={575, 577},
        governed_findings={"a" * 64},
        manual_findings={"b" * 64},
    )
    code = lifecycle.cmd_resolve(ct.CONFIG, argparse.Namespace(issue=None))
    assert code == 0
    assert calls["ledger"] == 3
    assert calls["governed"] == 2  # the one readable ledger's two findings
    assert calls["manual"] == 1  # never re-evaluated merely because another ledger exists
    assert [kind for _head, writes in appends for kind in (item.kind for item in writes)] == [
        "finding_proven",
        "thread_resolved",
        "thread_resolved",
    ]
    assert "resolved_pr_numbers=788\n789\n" in outputs
