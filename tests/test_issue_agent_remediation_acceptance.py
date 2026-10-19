"""ADR 0039 §5 (binding): the end-to-end acceptance simulation of the review → knowledge → prevention → loop.

Real stack: local bare remotes, real signed Issue and knowledge ledgers, the real result contract, the real
credential-free validator running the real RED->GREEN regression, the real deterministic promotion, the real
exact-lease fast-forward, the real eligibility decision and the real exact-head resolution. Only the model, the
privileged isolation uid and the GitHub API facts are substituted, and every transition is driven through a
brand new store, so a restart is the default rather than an exception.

The two findings are the ones the owner named: A belongs to a known family, B is genuinely new.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_control as ct
import test_issue_agent_remediation_reconcile as rr
import test_issue_agent_remediation_resolution as rs
import test_issue_agent_remediation_roles as rt

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorizationVerifier,
    verify_signed_authorization,
)
from hunter.evidence_intelligence.engineering_context_authority import (
    ENGINEERING_IMPLEMENT_TASK_KEY,
    EngineeringContextAuthority,
)
from hunter.task_scope import TaskScopeContract

KNOWN = "DFF-001"
NEW_FAMILY = {
    "title": "guard-accepts-a-repeated-value",
    "invariant": "a guard rejects a value it has already seen",
}
TEST = rt.TEST_ID
OTHER_TEST = rt.OTHER_TEST_ID
SECOND_AUTHORIZATION = "hunter-issue-agent-authorization:" + "e" * 64


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY",
        "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737",
    )


def observation(
    comment: int,
    *,
    family: str | None = None,
    reviewed_head: str = rs.REMEDIATED,
    path: str = rt.GUARD,
    claim: str = "This accepts a repeated value",
) -> dict[str, Any]:
    """One trusted-reviewer thread observation, exactly as the authenticated collector reports it."""

    tag = f" [family:{family}]" if family else ""
    return {
        "source": "github-review",
        "provider": "github-review",
        "event_id": f"review-comment-{comment}",
        "source_pr": rt.PULL_REQUEST,
        "reviewed_head_sha": reviewed_head,
        "reviewed_base_sha": "b" * 40,
        "source_event_head_sha": reviewed_head,
        "reviewer": "chatgpt-codex-connector[bot]",
        "path": path,
        "line": 3,
        "message": f"**[P1] {claim}{tag}** The guard returns the value again.",
        "availability": "available",
    }


def applicable(family: str, path: str) -> bool | None:
    """The seeded canonical registry's applicability for DFF-001, and no other family."""

    return True if family == KNOWN and path.startswith("src/hunter/") else None


def store_write(store: state.GitLedgerStore, writes: list[knowledge.Write], *, job: str, role: str) -> int:
    return knowledge.append(
        store,
        writes,
        trust=rt.TRUST,
        provenance=rt.trusted,
        signing_key=rt.KEY,
        recorded_by={
            "workflow_path": control.RECONCILE_WORKFLOW,
            "job": job,
            "role": role,
            "run_id": 300,
            "run_attempt": 1,
            "head_sha": "e" * 40,
        },
        recorded_at="2026-10-04T12:00:00Z",
    )[2]


def fresh_store(remote: str, root: Path, tag: str) -> state.GitLedgerStore:
    """A brand new store over the anchored remote: everything durable, nothing in process."""

    return state.GitLedgerStore(remote, workdir=root / tag)


def read_knowledge(store: state.GitLedgerStore) -> knowledge.KnowledgeView:
    return knowledge.read(store, trust=rt.TRUST, provenance=rt.trusted)[1]


def read_issue(store: state.GitLedgerStore) -> state.LedgerView:
    _commit, entries = store.read(rt.ISSUE)
    return state.verify_chain(
        [entry.record for entry in entries],
        repository_id=1,
        issue_number=rt.ISSUE,
        trust=rt.TRUST,
        provenance=rt.trusted,
        indexes=[entry.index for entry in entries],
    )


def ingest_twice(store: state.GitLedgerStore, *observations: Mapping[str, Any]) -> knowledge.KnowledgeView:
    """Deliver every observation twice, as a collector re-run would; only one record per finding survives."""

    writes: list[knowledge.Write] = []
    for batch in (observations, observations):
        produced, _refusals = knowledge.ingestion_writes(
            read_knowledge(store),
            list(batch),
            repository_id=1,
            trusted_reviewers=frozenset({"chatgpt-codex-connector"}),
            applicability=applicable,
        )
        writes.extend(produced)
    store_write(store, writes, job="knowledge-ingest", role="reconcile")
    return read_knowledge(store)


@pytest.fixture
def unprivileged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run the two privileged ports as the current user; the uid boundary is the only substitution."""

    def root(prefix: str) -> Path:
        path = tmp_path / prefix
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(core, "run_privileged", lambda *args, **kw: None)
    monkeypatch.setattr(core, "new_isolation_root", root)
    monkeypatch.setattr(core, "remove_isolation_root", lambda path, _user: shutil.rmtree(path, ignore_errors=True))
    monkeypatch.setattr(core, "isolated_command", lambda _user, _environment, argv: tuple(argv))
    monkeypatch.setattr(core, "REGRESSION_PYTHON", sys.executable)
    return tmp_path


@pytest.fixture
def signing(tmp_path: Path) -> str:
    return rt.signing_key(tmp_path)


class Loop:
    """One repository, one Issue ledger, and the knowledge ledger, driven only through fresh stores."""

    def __init__(self, tmp_path: Path) -> None:
        self.repos = rt.repos.__wrapped__(tmp_path)
        self.remote = str(self.repos["remote"])
        self.root = self.repos["tmp"]
        self.ledger = rt.Ledger(self.repos)
        rt.complete_parent_into(self.ledger, self.repos)
        self.head = str(self.ledger.view.authorizations[rt.PARENT].evidence[state.PUBLISHED]["head_sha"])
        self.serial = 0

    def restart(self, purpose: str) -> state.GitLedgerStore:
        self.serial += 1
        return fresh_store(self.remote, self.root, f"{purpose}-{self.serial}")

    def known(self) -> knowledge.KnowledgeView:
        return read_knowledge(self.restart("knowledge"))

    def detect(self, known: knowledge.KnowledgeView) -> control.RemediationCandidate:
        """One reconcile pass: a fresh store, then the real eligibility decision at the PR's current head."""

        facts = rr.Facts().open_pr(head=self.head)
        candidate = control.eligible_remediation(facts, ct.CONFIG, read_issue(self.restart("detect")), known, rt.ISSUE)
        assert candidate is not None, "an ingested open finding at the PR head must be eligible"
        return candidate

    def authorize(
        self, candidate: control.RemediationCandidate, finding_id: str, *, authorization_id: str
    ) -> dict[str, Any]:
        """Mint and sign the bounded remediation authorization, and verify the signature as authorize does."""

        _ledger, handoff, _sha, scope = rt.remediating(
            self.repos,
            ledger=self.ledger,
            bound_head=self.head,
            authorization_id=authorization_id,
            finding_ids=[finding_id],
        )
        group = remediation.remediation_group(
            parent_authorization_id=candidate.parent_authorization_id,
            issue_number=rt.ISSUE,
            pull_request_number=candidate.pull_request_number,
            bound_head_sha=candidate.bound_head_sha,
            attempt=1,
            findings=[
                {
                    "finding_id": finding_id,
                    "path": rt.GUARD,
                    "claim": candidate.findings[0]["claim"],
                }
            ],
        )
        authorization = remediation.remediation_authorization(
            self.issue_document(), repository=rt.REPOSITORY, owner_login="fafa33", remediation=group
        )
        parent = self.ledger.view.authorizations[candidate.parent_authorization_id]
        signed = remediation.sign_remediation(
            authorization,
            remediation.remediation_scope(authorization, parent.evidence[state.AUTHORIZED]["task_scope"]),
            signing_key=rt.KEY,
        )
        verifier = IssueAgentAuthorizationVerifier(rt.KEY.public_key().public_bytes_raw())
        assert verify_signed_authorization(
            signed, issuer_verifier=verifier, repository=rt.REPOSITORY, owner_login="fafa33"
        )
        assert (
            authorization.authorization_id
            == remediation.remediation_authorization(
                self.issue_document(), repository=rt.REPOSITORY, owner_login="fafa33", remediation=group
            ).authorization_id
        )  # a duplicate pass recomputes the identical identity
        return {"authorization": authorization, "signed": signed, "scope": scope, "handoff": handoff}

    @staticmethod
    def issue_document() -> dict[str, Any]:
        return {
            "number": rt.ISSUE,
            "state": "open",
            "html_url": f"https://github.com/{rt.REPOSITORY}/issues/{rt.ISSUE}",
            "title": "Canary",
            "body": "Create docs/ISSUE_AGENT_CANARY.md.",
            "updated_at": "2026-10-04T10:00:00Z",
            "labels": [{"name": "hunter-agent-execute"}],
        }

    def remediate(
        self,
        finding_id: str,
        disposition: Mapping[str, Any],
        *,
        authorization_id: str,
        signing_key: str,
        guard: str = rt.GUARD,
        test: str = rt.TEST,
        test_source: str = rt.TEST_SOURCE,
        regression: str = rt.TEST_ID,
        source: str = rt.SOURCE_FIXED,
    ) -> dict[str, Any]:
        """Execute once, bind, prove RED->GREEN, promote, and fast-forward from the exact bound head."""

        candidate = self.detect(self.known())
        minted = self.authorize(candidate, finding_id, authorization_id=authorization_id)
        # The reconcile job records the bounded request before dispatching, so a lost dispatch is a read-back.
        store_write(
            self.restart("request"),
            remediation.remediation_writes(candidate, authorization_id=minted["authorization"].authorization_id),
            job="remediate",
            role="reconcile",
        )
        outcome = rt.execute(
            self.repos, self.ledger, minted["handoff"], rt.agent_script(), authorization_id=authorization_id
        )
        assert outcome.advisory_code is None and outcome.sealed_result is not None
        sealed = rt.bound_result(
            self.ledger,
            minted["scope"],
            files=rt.candidate_files(guard=guard, test=test, tests=test_source, source=source),
            authorization_id=authorization_id,
            proposal={
                "finding_id": finding_id,
                "disposition": dict(disposition),
                "regression_tests": [regression],
            },
        )
        receipt = rt.validate(
            self.repos,
            self.ledger,
            sealed,
            authorization_id=authorization_id,
            port=roles.RemediationPort(red_green=core.red_green_regression_proof, promote=rt.local_promotion),
        )
        assert receipt["remediation"]["proven_finding_ids"] == [finding_id]
        rt.validated_state(self.ledger, receipt, authorization_id=authorization_id)
        publication = rt.publish(self.repos, self.ledger, sealed, signing_key, authorization_id=authorization_id)
        self.finalize(authorization_id, receipt, publication)
        return {"receipt": receipt, "publication": publication, "finding_id": finding_id}

    def finalize(self, authorization_id: str, receipt: Mapping[str, Any], publication: Any) -> None:
        """PUBLISHED then COMPLETED, exactly as the real recorder writes them from the observed head."""

        bound = self.ledger.view.authorizations[authorization_id].evidence[state.AUTHORIZED]
        self.ledger.write(
            authorization_id,
            state.PUBLISHED,
            {
                "writer_login": rt.WRITER.login,
                "publication_identity": state.publication_identity(
                    repository_id=1,
                    issue_number=rt.ISSUE,
                    authorization_id=authorization_id,
                    base_sha=bound["base_sha"],
                    task_scope_sha256=bound["task_scope_sha256"],
                    execution_id=bound["execution_id"],
                    result_sha256=receipt["result_sha256"],
                    tree_sha=receipt["tree_sha"],
                    unsigned_commit_sha=receipt["unsigned_commit_sha"],
                    control_sha=bound["control_sha"],
                    writer_login=rt.WRITER.login,
                ),
                "head_sha": publication.head_sha,
                "commit_verified": True,
                "publish_attempts": 1,
                "deadline_completed_at": "2026-10-04T18:00:00Z",
            },
            "finalize",
        )
        self.ledger.write(
            authorization_id,
            state.COMPLETED,
            {
                "pull_request_number": rt.PULL_REQUEST,
                "pull_request_node_id": "PR_kwNode",
                "pull_request_head_sha": publication.head_sha,
                "draft": True,
                "preflight_run_id": 555,
                "preflight_conclusion": "success",
            },
            "candidate-pr-record",
        )

    def close(
        self, run: Mapping[str, Any], store: state.GitLedgerStore, *, authorization_id: str, comment_id: int
    ) -> Any:
        """Prove the published head, record proof/recurrence, answer the exact thread and record the resolution."""

        publication, finding_id = run["publication"], run["finding_id"]
        self.head = publication.head_sha
        proof_group = dict(run["receipt"]["remediation"])
        authorization = state.AuthorizationView(
            authorization_id,
            state.PUBLISHED,
            [],
            {
                state.AUTHORIZED: {
                    "remediation": {
                        "bound_head_sha": publication.head_sha,
                        "finding_ids": proof_group["finding_ids"],
                    }
                },
                state.VALIDATED: {"receipt_sha256": "7" * 64},
                state.PUBLISHED: {"head_sha": publication.head_sha},
            },
        )
        authorization.records.append({"issue_number": rt.ISSUE, "repository_id": 1})
        ledger_view = state.LedgerView(1, rt.ISSUE, {authorization_id: authorization}, [], None, None, 0)
        # The record-validation job makes the proven mapping permanent knowledge before anything resolves.
        store_write(
            store,
            remediation.proof_writes(
                read_knowledge(store), authorization_id=authorization_id, validation=run["receipt"]
            ),
            job="record-validation",
            role="record-validation",
        )
        world = rs.World(head=publication.head_sha)
        world.thread(comment=comment_id, outdated=True)
        proof = control.exact_head_proof(world, ct.CONFIG, read_knowledge(store), ledger_view, finding_id)
        assert proof is not None, "the published head must carry the exact-head proof"
        assert proof.remediated_head_sha == publication.head_sha
        store_write(
            store,
            remediation.proven_writes(
                read_knowledge(store),
                finding_id,
                authorization_id=authorization_id,
                remediated_head_sha=proof.remediated_head_sha,
                receipt_sha256=proof.receipt_sha256,
                preflight_run_id=proof.preflight_run_id,
            ),
            job="resolve",
            role="reconcile",
        )
        replied = control.resolve_thread(
            world,
            ct.CONFIG,
            proof,
            remediation.evidence_reply(
                proof.finding_id,
                remediated_head_sha=proof.remediated_head_sha,
                family=proof.family_id or "a new family candidate",
                tests=proof.regression_tests,
            ),
        )
        assert replied == 9001 and world.resolved == [rs.THREAD_NODE]
        store_write(
            store,
            remediation.resolution_writes(
                read_knowledge(store),
                finding_id,
                remediated_head_sha=proof.remediated_head_sha,
                reply_comment_id=replied,
            ),
            job="resolve",
            role="reconcile",
        )
        return world


# --- §5.1-5.3: finding A, a known family ------------------------------------------------------------------


def test_a_known_family_finding_runs_the_whole_loop_exactly_once(
    tmp_path: Path, unprivileged: Path, signing: str
) -> None:
    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    identity = next(iter(known.findings))

    # §5.1: A is delivered twice and mapped deterministically, before any remediation exists.
    assert len(known.findings) == 1
    classification = known.findings[identity].classification
    assert classification is not None
    assert (classification["outcome"], classification["family_id"], classification["basis"]) == (
        "matched",
        KNOWN,
        "deterministic",
    )

    run = loop.remediate(identity, {"family_id": KNOWN}, authorization_id=rt.REMEDIATION, signing_key=signing)
    world = loop.close(run, store, authorization_id=rt.REMEDIATION, comment_id=4242)

    # §5.2: the exact head resolved the exact thread, and the reply names only identities and digests.
    assert world.replies[0][1]["in_reply_to"] == 4242
    assert identity[:12] in world.replies[0][1]["body"] and KNOWN in world.replies[0][1]["body"]

    final = loop.known()
    item = final.findings[identity]
    assert item.proven is not None and item.proven["remediated_head_sha"] == run["publication"].head_sha
    assert item.resolved is not None and item.open is False
    assert (KNOWN, identity) in final.recurrences

    # §5.3: one record per kind per finding, despite the duplicate delivery and the restarts.
    assert len(item.remediations) == 1
    assert sorted(item.classifications) == ["deterministic", "proven"]
    assert len(final.findings) == 1

    # §5.2: the known family gained the regression evidence in the same remediation commit.
    registry = json.loads(
        rt.git(loop.repos["trusted"], "show", f"{run['publication'].head_sha}:docs/DEFECT_REGISTRY.json")
    )
    family = next(item for item in registry["families"] if item["id"] == KNOWN)
    assert family["regression_evidence"][-1] == TEST
    dispositions = json.loads(
        rt.git(loop.repos["trusted"], "show", f"{run['publication'].head_sha}:docs/REVIEWER_FINDING_DISPOSITIONS.json")
    )
    assert dispositions["findings"][-1]["mapped_defect_id"] == KNOWN
    assert dispositions["findings"][-1]["test_reference"] == TEST


def test_a_known_family_finding_is_never_resolved_before_the_exact_head_proof(
    tmp_path: Path, unprivileged: Path, signing: str
) -> None:
    """§5.4: a stale or preflight-less head is never proof, so the thread stays open and readiness blocked."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    identity = next(iter(known.findings))
    run = loop.remediate(identity, {"family_id": KNOWN}, authorization_id=rt.REMEDIATION, signing_key=signing)
    proof_group = dict(run["receipt"]["remediation"])
    authorization = state.AuthorizationView(
        rt.REMEDIATION,
        state.PUBLISHED,
        [],
        {
            state.AUTHORIZED: {
                "remediation": {
                    "bound_head_sha": run["publication"].head_sha,
                    "finding_ids": proof_group["finding_ids"],
                }
            },
            state.VALIDATED: {"receipt_sha256": "7" * 64},
            state.PUBLISHED: {"head_sha": run["publication"].head_sha},
        },
    )
    authorization.records.append({"issue_number": rt.ISSUE, "repository_id": 1})
    ledger_view = state.LedgerView(1, rt.ISSUE, {rt.REMEDIATION: authorization}, [], None, None, 0)

    stale = rs.World(head=run["publication"].head_sha, preflight=None)
    stale.thread(comment=4242, outdated=True)
    assert control.exact_head_proof(stale, ct.CONFIG, known, ledger_view, identity) is None
    unresolved = rs.World(head=run["publication"].head_sha)
    unresolved.thread(comment=4242, outdated=False)
    assert control.exact_head_proof(unresolved, ct.CONFIG, known, ledger_view, identity) is None
    assert loop.known().findings[identity].open is True
    assert loop.known().findings[identity].proven is None


def test_a_moved_pull_request_head_is_never_remediated(tmp_path: Path, unprivileged: Path, signing: str) -> None:
    """§5.4: a provenance or head mismatch fails closed before any model runs."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    facts = rr.Facts().open_pr(head="8" * 40)
    assert (
        control.eligible_remediation(facts, ct.CONFIG, read_issue(loop.restart("detect")), loop.known(), rt.ISSUE)
        is None
    )


def test_an_ambiguous_mapping_is_never_remediated(tmp_path: Path, unprivileged: Path) -> None:
    """§5.4: an unknown family tag is ambiguous, and an ambiguous finding needs a human, not a guess."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family="DFF-404", reviewed_head=loop.head))
    identity = next(iter(known.findings))
    classification = known.findings[identity].classification
    assert classification is not None and classification["outcome"] == "ambiguous"
    assert (
        control.eligible_remediation(
            rr.Facts().open_pr(head=loop.head), ct.CONFIG, read_issue(loop.restart("detect")), known, rt.ISSUE
        )
        is None
    )


def test_a_malformed_finding_is_refused_and_records_nothing(tmp_path: Path, unprivileged: Path) -> None:
    """§5.4: a malformed or unauthenticated observation writes nothing and is reported."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    produced, refusals = knowledge.ingestion_writes(
        read_knowledge(store),
        [
            {**observation(4242, reviewed_head=loop.head), "reviewer": "somebody-else"},
            {**observation(4243, reviewed_head=loop.head), "path": ""},
            {**observation(4244, reviewed_head=loop.head), "event_id": "review-summary"},
        ],
        repository_id=1,
        trusted_reviewers=frozenset({"chatgpt-codex-connector"}),
        applicability=applicable,
    )
    # The unauthenticated reviewer and the pathless inline comment are refused with a reason; a top-level
    # review summary is not an inline finding at all, so it is skipped rather than recorded.
    assert produced == [] and len(refusals) == 2
    assert read_knowledge(store).findings == {}


def test_reviewer_unavailability_is_non_blocking(tmp_path: Path, unprivileged: Path, signing: str) -> None:
    """§5.6: an unavailable reviewer does not block; the owner's label and the ledger are what authorize."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    identity = next(iter(known.findings))
    run = loop.remediate(identity, {"family_id": KNOWN}, authorization_id=rt.REMEDIATION, signing_key=signing)
    world = loop.close(run, store, authorization_id=rt.REMEDIATION, comment_id=4242)
    assert world.resolved and loop.known().findings[identity].open is False


def test_a_withdrawn_owner_label_stops_the_loop(tmp_path: Path, unprivileged: Path, signing: str) -> None:
    """§5.6: the standing consent is the label, so withdrawing it refuses the whole remediation."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    identity = next(iter(known.findings))
    document = {**loop.issue_document(), "labels": []}
    candidate = loop.detect(known)
    with pytest.raises(remediation.RemediationRefused, match="OWNER_WITHDREW"):
        remediation.remediation_authorization(
            document,
            repository=rt.REPOSITORY,
            owner_login="fafa33",
            remediation=remediation.remediation_group(
                parent_authorization_id=candidate.parent_authorization_id,
                issue_number=rt.ISSUE,
                pull_request_number=candidate.pull_request_number,
                bound_head_sha=candidate.bound_head_sha,
                attempt=1,
                findings=candidate.findings,
            ),
        )
    assert loop.known().findings[identity].proven is None
    del signing


# --- §5.5: prevention knowledge reaches the next task before its model runs -------------------------------


def test_a_new_family_finding_and_the_prevention_knowledge_reach_the_next_task(
    tmp_path: Path, unprivileged: Path, signing: str
) -> None:
    """§5.1/§5.2/§5.5: A is a known family, B is genuinely new, and both reach a later task before its model."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    known = ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    identity_a = next(iter(known.findings))

    run_a = loop.remediate(identity_a, {"family_id": KNOWN}, authorization_id=rt.REMEDIATION, signing_key=signing)
    loop.close(run_a, store, authorization_id=rt.REMEDIATION, comment_id=4242)
    first_head = run_a["publication"].head_sha
    assert loop.head == first_head

    # The re-review at the remediated head raises the second, genuinely new finding.
    known = ingest_twice(
        store,
        observation(
            4243,
            reviewed_head=loop.head,
            path="src/hunter/other.py",
            claim="This silently truncates a long list",
        ),
    )
    identity_b = next(identity for identity in known.findings if identity != identity_a)
    # No family tag and a different fingerprint, so B is genuinely unclassified: only a proven RED->GREEN
    # regression can create its family, which is exactly what the second remediation does.
    assert known.findings[identity_b].classification is None

    run_b = loop.remediate(
        identity_b,
        {"new_family": NEW_FAMILY},
        authorization_id=SECOND_AUTHORIZATION,
        signing_key=signing,
        guard=rt.OTHER_GUARD,
        test=rt.OTHER_TEST,
        test_source=rt.OTHER_TEST_SOURCE,
        regression=rt.OTHER_TEST_ID,
        source=rt.OTHER_FIXED,
    )
    loop.close(run_b, store, authorization_id=SECOND_AUTHORIZATION, comment_id=4243)
    assert run_b["publication"].head_sha != first_head

    after = loop.known()
    assert all(after.findings[identity].proven is not None for identity in (identity_a, identity_b))
    assert (KNOWN, identity_a) in after.recurrences

    # §5.2: the known family gained the regression evidence in its own remediation commit...
    registry_a = json.loads(rt.git(loop.repos["trusted"], "show", f"{first_head}:docs/DEFECT_REGISTRY.json"))
    assert TEST in next(item for item in registry_a["families"] if item["id"] == KNOWN)["regression_evidence"]
    # ...and the genuinely new family became the smallest truthful next family in the second commit.
    registry_b = json.loads(
        rt.git(loop.repos["trusted"], "show", f"{run_b['publication'].head_sha}:docs/DEFECT_REGISTRY.json")
    )
    created = next(item for item in registry_b["families"] if item["id"] != KNOWN)
    assert created["invariant"] == NEW_FAMILY["invariant"]
    assert created["lifecycle"] == "regression-tested"
    assert created["regression_evidence"] == [OTHER_TEST]
    dispositions_b = json.loads(
        rt.git(
            loop.repos["trusted"],
            "show",
            f"{run_b['publication'].head_sha}:docs/REVIEWER_FINDING_DISPOSITIONS.json",
        )
    )
    assert dispositions_b["findings"][-1]["classification"] == "new_systemic_defect"
    assert dispositions_b["findings"][-1]["mapped_defect_id"] == created["id"]

    # §5.5: the invariant of the brand-new family is prevention knowledge a later task receives.
    overlay = knowledge.overlay_families(after)
    assert any(entry["invariant"] == NEW_FAMILY["invariant"] for entry in overlay)


def test_a_later_task_receives_the_open_finding_prevention_before_its_model_runs(
    tmp_path: Path, unprivileged: Path
) -> None:
    """§5.5: prevention knowledge reaches a fresh task's DPM context before its model runs."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    ingest_twice(
        store,
        observation(4242, family=KNOWN, reviewed_head=loop.head),
        observation(4243, reviewed_head=loop.head),
    )
    known = loop.known()
    assert len(known.findings) == 2

    # Before any remediation both open findings are prevention knowledge at their own guard path.
    before = knowledge.overlay_families(known)
    assert sorted(entry["id"][:3] for entry in before) == ["KF-", "KF-"]
    later = TaskScopeContract(
        task_id="later", branch_pattern="issue-999-*", base_sha=loop.head, allowed_paths=("src/hunter/",)
    )
    rendered = {
        str(entry["id"])
        for entry in EngineeringContextAuthority(knowledge_overlay=before).compile(
            ENGINEERING_IMPLEMENT_TASK_KEY, scope=later
        )["applicable_defect_families"]
    }
    assert {entry["id"] for entry in before} <= rendered


def test_the_prevention_overlay_is_bound_into_a_later_task_scope(
    tmp_path: Path, unprivileged: Path, signing: str
) -> None:
    """§5.5: DPM adds the overlay for the intersecting paths only, so an unrelated task is unaffected."""

    loop = Loop(tmp_path)
    store = loop.restart("ingest")
    ingest_twice(store, observation(4242, family=KNOWN, reviewed_head=loop.head))
    known = loop.known()
    guard = TaskScopeContract(
        task_id="guard", branch_pattern="issue-1-*", base_sha="a" * 40, allowed_paths=("src/hunter/",)
    )
    unrelated = TaskScopeContract(
        task_id="docs", branch_pattern="issue-2-*", base_sha="a" * 40, allowed_paths=("docs/",)
    )
    authority = EngineeringContextAuthority(knowledge_overlay=knowledge.overlay_families(known))
    guard_families = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=guard)
    docs_families = authority.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=unrelated)
    overlay_ids = {entry["id"] for entry in knowledge.overlay_families(known)}
    rendered = {
        str(entry["id"]) for entry in guard_families["applicable_defect_families"]
    }  # fmt: skip
    assert overlay_ids <= rendered, "the anchored overlay must reach a task that touches the finding's path"
    assert not overlay_ids & {
        str(entry["id"]) for entry in docs_families["applicable_defect_families"]
    }, "and must not reach a task whose paths do not intersect it"
    del signing
