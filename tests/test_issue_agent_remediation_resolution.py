"""ADR 0039 S5b-4: the anchored proof/recurrence/resolution records and the exact-head thread resolution (RD-6).

Every write is a decision the control domain makes about *observed* state: nothing here trusts a model field, a
stored node id, or a claim about a head other than the one the review query and the pull request both report.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_control as ct
import test_issue_agent_remediation_reconcile as rr

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_state as state

REPOSITORY = rr.REPOSITORY
ISSUE = rr.ISSUE
FINDING = "1" * 64
HEAD = rr.HEAD
GUARD = rr.GUARD
TEST = "tests/test_issue_agent_roles.py::test_the_model_runs_once_through_isolation_and_its_result_is_sealed"
THREAD_NODE = "PRRT_kwDOA"
COMMENT_ID = 4242
REMEDIATED = "9" * 40
FAMILY = "DFF-049"
RECEIPT = "7" * 64


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


class World:
    """The observed world: threads at a head, a preflight verdict, and what the writes actually did."""

    def __init__(self, *, head: str = REMEDIATED, preflight: str | None = "success") -> None:
        self.head = head
        self.preflight = preflight
        self.threads: list[dict[str, Any]] = []
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.resolved: list[str] = []
        self.graphql_fails = False
        self.post_fails = False
        self.resolve_reports_resolved = True

    def thread(
        self,
        *,
        resolved: bool = False,
        outdated: bool = False,
        comment: int = COMMENT_ID,
        path: str = GUARD,
        body: str = "The second pull request must not be overwritten.",
    ) -> dict[str, Any]:
        node = {
            "id": THREAD_NODE,
            "isResolved": resolved,
            "isOutdated": outdated,
            "comments": {"nodes": [{"databaseId": comment, "path": path, "body": body}]},
        }
        self.threads.append(node)
        return node

    def get(self, path: str) -> control.Read:
        if path.split("?", 1)[0].endswith("/actions/workflows/hunter-pre-pr-preflight.yml/runs"):
            if self.preflight is None:
                return control.Read("ok", {"workflow_runs": []})
            return control.Read(
                "ok",
                {
                    "workflow_runs": [
                        {
                            "head_sha": self.head,
                            "head_branch": rr.BRANCH,
                            "status": "completed",
                            "conclusion": self.preflight,
                            "run_number": 7,
                            "id": 555,
                        }
                    ]
                },
            )
        return control.Read("unknown")

    def graphql(self, query: str, variables: Mapping[str, Any]) -> control.Read:
        if self.graphql_fails:
            return control.Read("unknown")
        if "resolveReviewThread(input" in query:
            if self.graphql_fails:
                return control.Read("unknown")
            self.resolved.append(str(variables["threadId"]))
            return control.Read(
                "ok",
                {
                    "resolveReviewThread": {
                        "thread": {"id": variables["threadId"], "isResolved": self.resolve_reports_resolved}
                    }
                },
            )
        return control.Read(
            "ok", {"repository": {"pullRequest": {"headRefOid": self.head, "reviewThreads": {"nodes": self.threads}}}}
        )

    def reply(self, path: str, body: Mapping[str, Any]) -> control.Read:
        if self.post_fails:
            return control.Read("unknown")
        self.replies.append((path, dict(body)))
        return control.Read("ok", {"id": 9001})

    def dispatch(self, workflow_file: str, inputs: Mapping[str, str]) -> bool:
        return True


# --- the record derivation --------------------------------------------------------------------------------


def proven_view(store: state.GitLedgerStore, *, disposition: Mapping[str, Any] | None = None) -> Any:
    """A knowledge ledger holding one ingested finding with a proven classification."""

    view = rr.ingested(store, {**rr.OBSERVATION, "reviewed_head_sha": HEAD, "path": GUARD})
    identity = next(iter(view.findings))
    tests = [TEST]
    chosen = dict(disposition or {"family_id": FAMILY})
    writes: list[rr.knowledge.Write] = []
    if "new_family" in chosen:
        proposal = chosen["new_family"]
        candidate = knowledge.candidate_id(str(proposal["invariant"]), [GUARD])
        writes.append(
            knowledge.Write(
                "family_candidate",
                {
                    "candidate_id": candidate,
                    "title": proposal["title"],
                    "invariant": proposal["invariant"],
                    "changed_paths": [GUARD],
                    "source_finding_ids": [identity],
                    "regression_tests": tests,
                },
            )
        )
        writes.append(
            knowledge.Write(
                "finding_classified",
                {
                    "finding_id": identity,
                    "outcome": "candidate-new-family",
                    "family_id": None,
                    "candidate_id": candidate,
                    "basis": "proven",
                    "regression_tests": tests,
                    "authorization_id": "hunter-issue-agent-authorization:" + "b" * 64,
                },
            )
        )
    else:
        writes.append(
            knowledge.Write(
                "finding_classified",
                {
                    "finding_id": identity,
                    "outcome": "matched",
                    "family_id": FAMILY,
                    "candidate_id": None,
                    "basis": "proven",
                    "regression_tests": tests,
                    "authorization_id": "hunter-issue-agent-authorization:" + "b" * 64,
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
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "record",
            "role": "record-validation",
            "run_id": 100,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, proven = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    return identity, proven


def proof_group(**changes: Any) -> dict[str, Any]:
    group = {
        "bound_head_sha": HEAD,
        "finding_ids": [FINDING],
        "proven_finding_ids": [FINDING],
        "regression_tests": [TEST],
        "disposition": {"family_id": FAMILY},
        "promotion_sha256": "8" * 64,
    }
    group.update(changes)
    return group


def test_a_proven_matched_family_becomes_permanent_classification(store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    writes = remediation.proof_writes(
        view,
        authorization_id="hunter-issue-agent-authorization:" + "b" * 64,
        validation={"remediation": proof_group(finding_ids=[identity], proven_finding_ids=[identity])},
    )
    assert [write.kind for write in writes] == ["finding_classified"]
    evidence = writes[0].evidence
    assert evidence["outcome"] == "matched" and evidence["basis"] == "proven"
    assert evidence["family_id"] == FAMILY and evidence["regression_tests"] == [TEST]


def test_a_proven_new_family_also_records_the_smallest_truthful_candidate(
    store: state.GitLedgerStore,
) -> None:
    identity, view = proven_view(
        store,
        disposition={"new_family": {"title": "a-brand-new-invariant", "invariant": "a brand new proven invariant"}},
    )
    writes = remediation.proof_writes(
        view,
        authorization_id="hunter-issue-agent-authorization:" + "b" * 64,
        validation={
            "remediation": proof_group(
                finding_ids=[identity],
                proven_finding_ids=[identity],
                disposition={
                    "new_family": {"title": "a-brand-new-invariant", "invariant": "a brand new proven invariant"}
                },
            )
        },
    )
    assert [write.kind for write in writes] == ["family_candidate", "finding_classified"]
    candidate, classification = writes
    assert candidate.evidence["changed_paths"] == [GUARD] and candidate.evidence["regression_tests"] == [TEST]
    assert classification.evidence["outcome"] == "candidate-new-family"
    assert classification.evidence["candidate_id"] == candidate.evidence["candidate_id"]
    assert remediation.proven_family(view, identity) is None  # a new family has no family id until it is promoted


def test_a_fix_without_a_proven_mapping_earns_no_permanent_knowledge(
    store: state.GitLedgerStore,
) -> None:
    _identity, view = proven_view(store)
    assert (
        remediation.proof_writes(
            view, authorization_id="x", validation={"remediation": proof_group(proven_finding_ids=[])}
        )
        == []
    )
    assert (
        remediation.proof_writes(view, authorization_id="x", validation={"remediation": proof_group(disposition=None)})
        == []
    )
    assert remediation.proof_writes(view, authorization_id="x", validation={}) == []


def test_a_classification_naming_an_uningested_finding_writes_nothing(
    store: state.GitLedgerStore,
) -> None:
    _identity, view = proven_view(store)
    writes = remediation.proof_writes(view, authorization_id="x", validation={"remediation": proof_group()})
    assert writes == []


# --- the exact-head proof ---------------------------------------------------------------------------------


def published_at(head: str, finding_id: str = FINDING) -> dict[str, Any]:
    """A PUBLISHED remediation authorization of this Issue at exactly ``head``."""

    return {
        state.AUTHORIZED: {"remediation": {"bound_head_sha": head, "finding_ids": [finding_id]}},
        state.VALIDATED: {"receipt_sha256": RECEIPT},
        state.PUBLISHED: {"head_sha": head},
    }


def ledger_with(*authorizations: tuple[str, str, str], finding_id: str = FINDING) -> state.LedgerView:
    """A verified Issue-ledger view whose authorizations are PUBLISHED at the given heads."""

    view = state.empty_view(1, ISSUE)
    for identity, _state_name, head in authorizations:
        evidence = published_at(head, finding_id)
        entry = state.AuthorizationView(identity, state.PUBLISHED, [], dict(evidence), None, {})
        entry.records.append({"issue_number": ISSUE, "repository_id": 1})
        view.authorizations[identity] = entry
    view.active = None
    return view


REMEDIATION_AUTHORIZATION = "hunter-issue-agent-authorization:" + "b" * 64


def proof_for(world: World, view: Any, identity: str) -> Any:
    return control.exact_head_proof(
        world,
        ct.CONFIG,
        view,
        ledger_with((REMEDIATION_AUTHORIZATION, "PUBLISHED", REMEDIATED), finding_id=identity),
        identity,
    )


@pytest.fixture
def world() -> World:
    """The realistic post-fix state: the finding's own thread is outdated and nothing current repeats it."""

    value = World()
    value.thread(outdated=True)
    return value


def test_an_exactly_proven_head_resolves_the_exact_thread(world: World, store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    proof = proof_for(world, view, identity)
    assert proof is not None
    assert (proof.thread.node_id, proof.thread.comment_id) == (THREAD_NODE, COMMENT_ID)
    assert proof.remediated_head_sha == REMEDIATED and proof.preflight_run_id == 555
    assert proof.regression_tests == (TEST,) and proof.family_id == FAMILY
    assert control.resolve_thread(world, ct.CONFIG, proof, "evidence") == 9001
    assert world.resolved == [THREAD_NODE]
    path, body = world.replies[0]
    assert path == f"/repos/{REPOSITORY}/pulls/600/comments/{COMMENT_ID}/replies"
    assert body["in_reply_to"] == COMMENT_ID and body["body"] == "evidence"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda w: setattr(w, "preflight", None), "no preflight run at the head"),
        (lambda w: setattr(w, "preflight", "failure"), "the exact-head preflight failed"),
        (lambda w: setattr(w, "head", "8" * 40), "the head is not the remediated head"),
        (lambda w: w.threads.clear(), "the exact authenticated thread is not observable"),
        (lambda w: w.thread(resolved=False, outdated=True), "only an outdated thread carries the claim"),
        (
            lambda w: w.thread(outdated=False, comment=5555, body=rr.OBSERVATION["message"]),
            "a current thread repeats the claim at this head",
        ),
    ],
)
def test_no_head_is_ever_proven_without_every_condition(
    world: World, store: state.GitLedgerStore, mutate: Any, reason: str
) -> None:
    identity, view = proven_view(store)
    mutate(world)
    assert proof_for(world, view, identity) is None, reason


def test_a_thread_someone_else_already_resolved_is_not_touched_again(world: World, store: state.GitLedgerStore) -> None:
    """An already-resolved thread carries no recurrence, so the gate holds; the post is still idempotent."""

    identity, view = proven_view(store)
    world.threads.clear()
    world.thread(resolved=True)
    proof = proof_for(world, view, identity)
    assert proof is not None and proof.thread.resolved is True
    assert view.findings[identity].resolved is None  # the anchored record is written only after the post


def test_an_unresolved_thread_with_the_same_fingerprint_blocks_the_resolution(
    world: World, store: state.GitLedgerStore
) -> None:
    identity, view = proven_view(store)
    world.threads.clear()
    world.thread(comment=7777)  # a different comment carrying the very same claim
    assert proof_for(world, view, identity) is None


def test_no_published_remediation_at_this_head_means_no_proof(world: World, store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    assert (
        control.exact_head_proof(
            world,
            ct.CONFIG,
            view,
            ledger_with(("hunter-issue-agent-authorization:" + "c" * 64, "PUBLISHED", "1" * 40)),
            identity,
        )
        is None
    )
    assert control.exact_head_proof(world, ct.CONFIG, view, state.empty_view(1, ISSUE), identity) is None


def test_two_published_remediations_at_one_head_are_ambiguous_and_never_guessed(
    world: World, store: state.GitLedgerStore
) -> None:
    identity, view = proven_view(store)
    ambiguous = ledger_with(
        (REMEDIATION_AUTHORIZATION, "PUBLISHED", REMEDIATED),
        ("hunter-issue-agent-authorization:" + "d" * 64, "PUBLISHED", REMEDIATED),
        finding_id=identity,
    )
    assert control.exact_head_proof(world, ct.CONFIG, view, ambiguous, identity) is None


def test_an_indefinite_thread_listing_is_never_read_as_no_recurrence(world: World, store: state.GitLedgerStore) -> None:
    """An indefinite observation is never a negative fact, so it stops the pass instead of resolving."""

    identity, view = proven_view(store)
    world.graphql_fails = True
    with pytest.raises(control.FactsUnavailable):
        proof_for(world, view, identity)


def test_an_unproven_or_unknown_finding_is_never_resolved(world: World, store: state.GitLedgerStore) -> None:
    _identity, view = proven_view(store)
    assert control.exact_head_proof(world, ct.CONFIG, view, ledger_with(), "0" * 64) is None


# --- the resolution records --------------------------------------------------------------------------------


def test_the_proof_and_recurrence_records_follow_the_exact_head(
    store: state.GitLedgerStore,
) -> None:
    identity, view = proven_view(store)
    writes = remediation.proven_writes(
        view,
        identity,
        authorization_id=REMEDIATION_AUTHORIZATION,
        remediated_head_sha=REMEDIATED,
        receipt_sha256=RECEIPT,
        preflight_run_id=555,
    )
    assert [write.kind for write in writes] == ["finding_proven", "recurrence"]
    assert writes[0].evidence["remediated_head_sha"] == REMEDIATED and writes[0].evidence["preflight_run_id"] == 555
    assert writes[1].evidence == {"family_id": FAMILY, "finding_id": identity}


def test_a_repeated_pass_writes_no_second_proof(store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    writes = remediation.proven_writes(
        view,
        identity,
        authorization_id=REMEDIATION_AUTHORIZATION,
        remediated_head_sha=REMEDIATED,
        receipt_sha256=RECEIPT,
        preflight_run_id=555,
    )
    rr.knowledge.append(
        store,
        writes,
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "resolve",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, again = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    assert (
        remediation.proven_writes(
            again,
            identity,
            authorization_id=REMEDIATION_AUTHORIZATION,
            remediated_head_sha=REMEDIATED,
            receipt_sha256=RECEIPT,
            preflight_run_id=555,
        )
        == []
    )


def test_a_proof_is_impossible_without_a_proven_classification(store: state.GitLedgerStore) -> None:
    """ADR 0039 L2/L3.2: a fixed-but-unclassified finding is a fix, not permanent knowledge."""

    view = rr.ingested(store, {**rr.OBSERVATION, "reviewed_head_sha": HEAD, "path": GUARD})
    identity = next(iter(view.findings))
    assert view.findings[identity].classifications == {}  # unclassified: no tag, no matching fingerprint
    assert (
        remediation.proven_writes(
            view,
            identity,
            authorization_id=REMEDIATION_AUTHORIZATION,
            remediated_head_sha=REMEDIATED,
            receipt_sha256=RECEIPT,
            preflight_run_id=555,
        )
        == []
    )
    assert remediation.proven_family(view, identity) is None
    assert view.findings[identity].proven is None


def test_a_proof_for_an_uningested_finding_is_impossible(store: state.GitLedgerStore) -> None:
    _identity, view = proven_view(store)
    assert (
        remediation.proven_writes(
            view,
            "0" * 64,
            authorization_id=REMEDIATION_AUTHORIZATION,
            remediated_head_sha=REMEDIATED,
            receipt_sha256=RECEIPT,
            preflight_run_id=555,
        )
        == []
    )


def test_a_resolution_is_impossible_before_the_proof(store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    assert remediation.resolution_writes(view, identity, remediated_head_sha=REMEDIATED, reply_comment_id=9001) == []


def test_the_resolution_record_is_written_only_after_the_reply(
    store: state.GitLedgerStore,
) -> None:
    identity, view = proven_view(store)
    rr.knowledge.append(
        store,
        remediation.proven_writes(
            view,
            identity,
            authorization_id=REMEDIATION_AUTHORIZATION,
            remediated_head_sha=REMEDIATED,
            receipt_sha256=RECEIPT,
            preflight_run_id=555,
        ),
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "resolve",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, proven = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    writes = remediation.resolution_writes(proven, identity, remediated_head_sha=REMEDIATED, reply_comment_id=9001)
    assert [write.kind for write in writes] == ["thread_resolved"]
    rr.knowledge.append(
        store,
        writes,
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "resolve",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, closed = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    assert closed.findings[identity].open is False
    assert remediation.resolution_writes(closed, identity, remediated_head_sha=REMEDIATED, reply_comment_id=9001) == []


def test_a_resolution_at_another_head_is_never_written(store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    rr.knowledge.append(
        store,
        remediation.proven_writes(
            view,
            identity,
            authorization_id=REMEDIATION_AUTHORIZATION,
            remediated_head_sha=REMEDIATED,
            receipt_sha256=RECEIPT,
            preflight_run_id=555,
        ),
        trust=rr.TRUST,
        provenance=rr.rt.trusted,
        signing_key=rr.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": "resolve",
            "role": "reconcile",
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )
    _head, proven = knowledge.read(store, trust=rr.TRUST, provenance=rr.rt.trusted)
    assert remediation.resolution_writes(proven, identity, remediated_head_sha="8" * 40, reply_comment_id=1) == []
    assert remediation.resolution_writes(proven, identity, remediated_head_sha=REMEDIATED, reply_comment_id=0) == []


def test_the_evidence_reply_carries_only_identities_and_digests() -> None:
    body = remediation.evidence_reply(FINDING, remediated_head_sha=REMEDIATED, family=FAMILY, tests=[TEST])
    assert FINDING in body and REMEDIATED in body and FAMILY in body and TEST in body
    assert "reviewed head" in body and "ADR 0039" in body


def test_a_definitively_unresolved_thread_is_not_a_resolution(world: World, store: state.GitLedgerStore) -> None:
    """GitHub answering "not resolved" is a definitive negative, and the finding must stay open."""

    identity, view = proven_view(store)
    proof = proof_for(world, view, identity)
    assert proof is not None
    world.resolve_reports_resolved = False
    assert control.resolve_thread(world, ct.CONFIG, proof, "evidence") is None
    assert world.replies and world.resolved == [THREAD_NODE]


def test_a_refused_resolve_is_not_a_resolution(world: World, store: state.GitLedgerStore) -> None:
    identity, view = proven_view(store)
    proof = proof_for(world, view, identity)
    assert proof is not None
    world.graphql_fails = True
    assert control.resolve_thread(world, ct.CONFIG, proof, "evidence") is None
    assert world.replies and not world.resolved
