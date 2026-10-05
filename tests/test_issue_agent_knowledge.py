"""ADR 0039 L1–L3, L8: the anchored knowledge ledger, finding identity and deterministic classification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation import issue_agent_knowledge as knowledge
from hunter.automation import issue_agent_state as state
from hunter.evidence_intelligence.engineering_context_authority import (
    ENGINEERING_IMPLEMENT_TASK_KEY,
    EngineeringContextAuthority,
    EngineeringContextAuthorityError,
)
from hunter.task_scope import TaskScopeContract

KEY = Ed25519PrivateKey.generate()
TRUST = state.TrustRoots({state.public_key_id(KEY.public_key()): KEY.public_key()}, repository_id=1)
REVIEWERS = frozenset({"copilot-pull-request-reviewer", "chatgpt-codex-connector"})
HEAD, BASE, LATER = "a" * 40, "b" * 40, "c" * 40
AUTH = "hunter-issue-agent-authorization:" + "d" * 64
TEST = "tests/test_issue_agent_state.py::test_completed_requires_a_successful_exact_head_preflight"
BY = {
    "workflow_path": ".github/workflows/hunter-issue-agent-reconcile.yml",
    "job": "reconcile",
    "role": "reconcile",
    "run_id": 300,
    "run_attempt": 1,
    "head_sha": "e" * 40,
}


def trusted(recorded_by: Any, _record: Any) -> bool:
    return recorded_by["run_attempt"] == 1


def observation(comment: int = 11, path: str = "src/hunter/automation/issue_agent_state.py", **kw: Any) -> dict:
    value = {
        "source": "github-review",
        "provider": "github-review",
        "event_id": f"review-comment-{comment}",
        "source_pr": 561,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "source_event_head_sha": HEAD,
        "reviewer": "chatgpt-codex-connector[bot]",
        "path": path,
        "line": 1063,
        "message": "**<sub>![P1](https://img.shields.io/x)</sub> Require a successful preflight before recording "
        "completion** When an open Draft PR and 2 failed preflights are observed, this records COMPLETED.",
        "availability": "available",
    }
    value.update(kw)
    return value


@pytest.fixture
def store(tmp_path: Path) -> state.GitLedgerStore:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    return state.GitLedgerStore(str(remote), workdir=tmp_path / "work")


def write(store: state.GitLedgerStore, *writes: knowledge.Write, by: dict | None = None) -> tuple:
    return knowledge.append(
        store,
        writes,
        trust=TRUST,
        provenance=trusted,
        signing_key=KEY,
        recorded_by=by or BY,
        recorded_at="2026-10-04T12:00:00Z",
    )


def applicability(family: str, path: str) -> bool | None:
    known = {"DFF-049": "src/hunter/automation/", "DFF-028": "scripts/"}
    if family not in known:
        return None
    return path.startswith(known[family])


def ingest(store: state.GitLedgerStore, *observations: dict) -> tuple:
    _, view = knowledge.read(store, trust=TRUST, provenance=trusted)
    writes, refusals = knowledge.ingestion_writes(
        view, observations, repository_id=1, trusted_reviewers=REVIEWERS, applicability=applicability
    )
    head, view, written = write(store, *writes)
    return view, written, refusals


# --- identity and normalization (L1) ---------------------------------------------------------------------


def test_the_claim_is_the_normalized_first_sentence() -> None:
    claim = knowledge.normalized_claim(observation()["message"])
    assert (
        claim
        == "require a successful preflight before recording completion when an open draft pr and failed preflights are observed, this records completed."
    )
    assert (
        knowledge.normalized_claim("x" * 400 + ".")[:5] == "xxxxx" and len(knowledge.normalized_claim("x" * 400)) == 280
    )


@pytest.mark.parametrize("message", ["", "   ", "<!-- only a comment -->", "![badge](https://x)", "12345"])
def test_a_finding_without_a_claim_is_refused(message: str) -> None:
    with pytest.raises(knowledge.FindingRefused):
        knowledge.normalized_claim(message)


def test_the_fingerprint_survives_new_heads_and_renumbering_but_not_another_path() -> None:
    first = knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS)
    moved = knowledge.finding_from_observation(
        observation(comment=99, reviewed_head_sha=LATER, line=2000, message=observation()["message"].replace("2", "3")),
        repository_id=1,
        trusted_reviewers=REVIEWERS,
    )
    other = knowledge.finding_from_observation(
        observation(path="scripts/x.py"), repository_id=1, trusted_reviewers=REVIEWERS
    )
    assert first["fingerprint"] == moved["fingerprint"] != other["fingerprint"]
    assert first["finding_id"] != moved["finding_id"]  # a different thread is a different finding


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": "review-77"},  # a top-level review is not an inline finding
        {"reviewer": "random-user"},
        {"path": None},
        {"reviewed_head_sha": "not-a-sha"},
        {"source_pr": 0},
        {"provider": "sonar"},
        {"message": "<!-- -->"},
    ],
)
def test_a_malformed_or_unauthenticated_finding_fails_closed(change: dict) -> None:
    with pytest.raises(knowledge.FindingRefused):
        knowledge.finding_from_observation(observation(**change), repository_id=1, trusted_reviewers=REVIEWERS)


# --- idempotent ingestion -------------------------------------------------------------------------------


def test_duplicate_delivery_and_restart_record_each_finding_once(store: state.GitLedgerStore, tmp_path: Path) -> None:
    view, written, _ = ingest(store, observation(), observation())
    assert written == 1 and len(view.findings) == 1
    # A restart on a fresh worker, and a re-observation of the same thread at a later head.
    fresh = state.GitLedgerStore(store._remote, workdir=tmp_path / "fresh")
    view, written, _ = ingest(fresh, observation(), observation(reviewed_head_sha=LATER))
    assert written == 0 and len(view.findings) == 1


def test_a_lost_acknowledgement_replays_as_a_no_op(store: state.GitLedgerStore) -> None:
    evidence = knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS)
    write(store, knowledge.Write("finding_ingested", evidence))
    _, _, written = write(store, knowledge.Write("finding_ingested", evidence))
    assert written == 0


def test_conflicting_evidence_under_one_key_is_refused(store: state.GitLedgerStore) -> None:
    evidence = knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS)
    write(store, knowledge.Write("finding_ingested", evidence))
    with pytest.raises(knowledge.KnowledgeLedgerError, match="different evidence"):
        write(store, knowledge.Write("finding_ingested", {**evidence, "claim": "something else entirely here"}))


def test_refusals_are_reported_never_silently_dropped(store: state.GitLedgerStore) -> None:
    _, written, refusals = ingest(store, observation(reviewer="random-user"), observation(comment=12))
    assert written == 1 and len(refusals) == 1 and "review-comment-11" in refusals[0]


# --- deterministic classification (L3.1) ------------------------------------------------------------------


def classification_of(view: knowledge.KnowledgeView, comment: int) -> Any:
    return view.findings[knowledge.finding_id(1, 561, comment)].classification


def test_deterministic_classification(store: state.GitLedgerStore) -> None:
    message = observation()["message"]
    view, _, _ = ingest(
        store,
        observation(11, message=message + " [family:DFF-049]"),  # tag, applicable
        observation(12, message="Something unrelated is wrong here. [family:DFF-028]"),  # tag, not applicable
        observation(13, message="Something else is wrong here. [family:DFF-999]"),  # tag, unknown family
        observation(14),  # same fingerprint as 11 (tag stripped) -> matched by fingerprint
        observation(15, message="A brand new class of problem appears here."),  # unclassified
    )
    assert classification_of(view, 11)["outcome"] == "matched" and classification_of(view, 11)["family_id"] == "DFF-049"
    assert classification_of(view, 12)["outcome"] == "ambiguous"
    assert classification_of(view, 13)["outcome"] == "ambiguous"
    assert classification_of(view, 14)["family_id"] == "DFF-049"
    assert classification_of(view, 15) is None


def test_two_family_tags_are_ambiguous_input_and_refused(store: state.GitLedgerStore) -> None:
    _, written, refusals = ingest(store, observation(message="Bad. [family:DFF-049] [family:DFF-028]"))
    assert written == 0 and "more than one family" in refusals[0]


# --- L2 legality ------------------------------------------------------------------------------------------


def seeded(store: state.GitLedgerStore) -> str:
    ingest(store, observation())
    return knowledge.finding_id(1, 561, 11)


def proven_classification(identity: str, **kw: Any) -> knowledge.Write:
    evidence = {
        "finding_id": identity,
        "outcome": "matched",
        "family_id": "DFF-049",
        "candidate_id": None,
        "basis": "proven",
        "regression_tests": [TEST],
        "authorization_id": AUTH,
    }
    evidence.update(kw)
    return knowledge.Write("finding_classified", evidence)


def remediation(identity: str, attempt: int) -> knowledge.Write:
    return knowledge.Write(
        "remediation_requested",
        {
            "finding_id": identity,
            "attempt": attempt,
            "pull_request_number": 561,
            "bound_head_sha": HEAD,
            "authorization_id": AUTH,
        },
    )


def proof(identity: str, **kw: Any) -> knowledge.Write:
    evidence = {
        "finding_id": identity,
        "authorization_id": AUTH,
        "remediated_head_sha": LATER,
        "receipt_sha256": "f" * 64,
        "preflight_run_id": 7,
        "regression_tests": [TEST],
    }
    evidence.update(kw)
    return knowledge.Write("finding_proven", evidence)


def test_the_full_finding_lifecycle_is_accepted_in_order(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    _, view, written = write(
        store,
        remediation(identity, 1),
        proven_classification(identity),
        proof(identity),
        knowledge.Write(
            "thread_resolved", {"finding_id": identity, "remediated_head_sha": LATER, "reply_comment_id": 5}
        ),
        knowledge.Write("recurrence", {"family_id": "DFF-049", "finding_id": identity}),
    )
    assert written == 5 and not view.findings[identity].open


@pytest.mark.parametrize(
    ("writes", "message"),
    [
        (lambda i: [proof(i)], "only after a proven classification"),
        (
            lambda i: [
                knowledge.Write(
                    "thread_resolved", {"finding_id": i, "remediated_head_sha": LATER, "reply_comment_id": 5}
                )
            ],
            "only after exact-head proof",
        ),
        (
            lambda i: [
                proven_classification(i),
                proof(i),
                knowledge.Write(
                    "thread_resolved", {"finding_id": i, "remediated_head_sha": HEAD, "reply_comment_id": 5}
                ),
            ],
            "another head",
        ),
        (lambda i: [proven_classification(i, regression_tests=[])], "no proof"),
        (
            lambda i: [proven_classification(i, outcome="false-positive-claimed", family_id=None)],
            "only a mapping can be proven",
        ),
        (
            lambda i: [proven_classification(i, outcome="candidate-new-family", family_id=None, candidate_id="9" * 64)],
            "not recorded",
        ),
        (
            lambda i: [
                proven_classification(i),
                proof(i, authorization_id="hunter-issue-agent-authorization:" + "0" * 64),
            ],
            "another remediation",
        ),
        (
            lambda i: [proven_classification(i), proof(i, regression_tests=["tests/test_x.py::test_y"])],
            "proof tests differ",
        ),
        (lambda i: [remediation(i, 2)], "next attempt"),
        (lambda i: [remediation(i, 1), remediation(i, 2), remediation(i, 3)], "budget"),
        (lambda i: [knowledge.Write("recurrence", {"family_id": "DFF-049", "finding_id": i})], "unproven"),
        (
            lambda i: [
                knowledge.Write(
                    "finding_classified",
                    {
                        "finding_id": "0" * 64,
                        "outcome": "ambiguous",
                        "family_id": None,
                        "candidate_id": None,
                        "basis": "deterministic",
                        "regression_tests": [],
                        "authorization_id": None,
                    },
                )
            ],
            "unknown finding",
        ),
    ],
)
def test_every_out_of_order_or_unproven_write_is_refused(
    store: state.GitLedgerStore, writes: Any, message: str
) -> None:
    identity = seeded(store)
    with pytest.raises(knowledge.KnowledgeLedgerError, match=message):
        write(store, *writes(identity))


def test_a_proof_cannot_contradict_a_deterministic_mapping(store: state.GitLedgerStore) -> None:
    ingest(store, observation(message=observation()["message"] + " [family:DFF-049]"))
    identity = knowledge.finding_id(1, 561, 11)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="contradicts"):
        write(store, proven_classification(identity, family_id="DFF-028"))


def test_the_per_pr_remediation_budget(store: state.GitLedgerStore) -> None:
    ingest(
        store,
        *(
            observation(comment, message=f"Distinct problem number {chr(97 + comment)} here.")
            for comment in range(1, 4)
        ),
    )
    ids = [knowledge.finding_id(1, 561, comment) for comment in range(1, 4)]
    write(
        store,
        remediation(ids[0], 1),
        remediation(ids[0], 2),
        remediation(ids[1], 1),
        remediation(ids[1], 2),
        remediation(ids[2], 1),
    )
    ingest(store, observation(4, message="Yet another distinct problem here."))
    with pytest.raises(knowledge.KnowledgeLedgerError, match="PR budget"):
        write(store, remediation(knowledge.finding_id(1, 561, 4), 1))


def test_a_new_family_candidate_is_bound_to_its_invariant_and_findings(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    invariant = "A lifecycle is recorded complete only after the exact-head preflight succeeds."
    paths = ["src/hunter/automation/issue_agent_state.py"]
    good = knowledge.candidate_id(invariant, paths)
    candidate = {
        "candidate_id": good,
        "title": "completion-before-preflight",
        "invariant": invariant,
        "changed_paths": paths,
        "source_finding_ids": [identity],
        "regression_tests": [TEST],
    }
    with pytest.raises(knowledge.KnowledgeLedgerError, match="not derived"):
        write(store, knowledge.Write("family_candidate", {**candidate, "candidate_id": "1" * 64}))
    _, view, _ = write(
        store,
        knowledge.Write("family_candidate", candidate),
        proven_classification(identity, outcome="candidate-new-family", family_id=None, candidate_id=good),
    )
    assert view.findings[identity].classification["candidate_id"] == good


@pytest.mark.parametrize("role", ["bind", "finalize", "authorize"])
def test_a_role_outside_the_kind_allowlist_cannot_write(store: state.GitLedgerStore, role: str) -> None:
    evidence = knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="may not write"):
        write(store, knowledge.Write("finding_ingested", evidence), by={**BY, "role": role})


def test_an_untrusted_run_cannot_write(store: state.GitLedgerStore) -> None:
    evidence = knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="trusted run"):
        write(store, knowledge.Write("finding_ingested", evidence), by={**BY, "run_attempt": 2})


def test_tampered_or_foreign_records_fail_verification(store: state.GitLedgerStore, tmp_path: Path) -> None:
    seeded(store)
    _, entries = store.read_files(knowledge.KNOWLEDGE_LEDGER_REF, frozenset({"record.json"}))
    record = json.loads(entries[0][1]["record.json"])
    view = knowledge.KnowledgeView(1)
    tampers: list[tuple[dict[str, Any], str]] = [
        (
            {"evidence": {**record["evidence"], "claim": "forged claim text here"}},
            "signature|does not verify|fingerprint",
        ),
        ({"key": "0" * 64}, "signature|does not verify|key"),
        ({"record_seq": 3}, "signature|does not verify|sequence"),
    ]
    for tamper, message in tampers:
        with pytest.raises(state.LedgerCorruptError, match=message):
            knowledge.apply(knowledge.KnowledgeView(1), {**record, **tamper}, trust=TRUST, provenance=trusted)
    foreign = state.sign_record({k: v for k, v in record.items() if k != "signature"}, KEY)  # the Issue-ledger domain
    with pytest.raises(state.LedgerCorruptError, match="another ledger domain"):
        knowledge.apply(view, foreign, trust=TRUST, provenance=trusted)


# --- DPM overlay (L2): knowledge before implementation ---------------------------------------------------


def scope(paths: tuple[str, ...]) -> TaskScopeContract:
    return TaskScopeContract(
        task_id="t",
        branch_pattern="issue-1-*",
        base_ref="main",
        base_sha="0" * 40,
        allowed_paths=paths,
        prohibited_paths=(),
    )


def test_an_open_finding_reaches_the_dpm_context_of_a_task_touching_its_path(store: state.GitLedgerStore) -> None:
    view, _, _ = ingest(store, observation())
    overlay = knowledge.overlay_families(view)
    dpm = EngineeringContextAuthority(knowledge_overlay=overlay)
    context = dpm.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",)))
    ids = {entry["id"] for entry in context["applicable_defect_families"]}
    assert f"KF-{knowledge.finding_id(1, 561, 11)[:12]}" in ids
    unrelated = dpm.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("docs/",)))
    assert not any(str(entry["id"]).startswith("KF-") for entry in unrelated["applicable_defect_families"])
    plain = EngineeringContextAuthority().canonical_json(
        ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",))
    )
    assert dpm.canonical_json(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",))) != plain


def test_a_resolved_finding_leaves_the_overlay(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    _, view, _ = write(
        store,
        proven_classification(identity),
        proof(identity),
        knowledge.Write(
            "thread_resolved", {"finding_id": identity, "remediated_head_sha": LATER, "reply_comment_id": 5}
        ),
    )
    assert knowledge.overlay_families(view) == []


def test_the_overlay_cannot_impersonate_a_registry_family() -> None:
    with pytest.raises(EngineeringContextAuthorityError, match="KC-/KF-"):
        EngineeringContextAuthority(knowledge_overlay=[{"id": "DFF-049"}])


# --- a buggy trusted writer: validly signed records that break a derivation rule -------------------------


def forged(store: state.GitLedgerStore, kind: str, evidence: dict, **overrides: Any) -> None:
    _, view = knowledge.read(store, trust=TRUST, provenance=trusted)
    record = {
        "schema_version": knowledge.RECORD_SCHEMA_VERSION,
        "kind": kind,
        "key": knowledge.record_key(kind, evidence),
        "record_seq": view.next_seq,
        "prev_record_sha256": view.head_record_digest,
        "recorded_at": "2026-10-04T12:00:00Z",
        "recorded_by": BY,
        "repository_id": 1,
        "evidence": evidence,
    }
    record.update(overrides)
    knowledge.apply(
        view, state.sign_record(record, KEY, domain=knowledge.KNOWLEDGE_LEDGER_DOMAIN), trust=TRUST, provenance=trusted
    )


@pytest.mark.parametrize(
    ("change", "overrides", "message"),
    [
        ({"finding_id": "0" * 64}, {}, "finding id is not derived"),
        ({"fingerprint": "0" * 64}, {}, "fingerprint"),
        ({}, {"key": "0" * 64}, "key is not derived"),
        ({}, {"record_seq": 5}, "not contiguous"),
        ({}, {"prev_record_sha256": "0" * 64}, "does not chain"),
    ],
)
def test_a_signed_record_that_breaks_a_derivation_is_refused(
    store: state.GitLedgerStore, change: dict, overrides: dict, message: str
) -> None:
    evidence = {
        **knowledge.finding_from_observation(observation(), repository_id=1, trusted_reviewers=REVIEWERS),
        **change,
    }
    if "finding_id" in change:
        overrides = {"key": change["finding_id"], **overrides}
    with pytest.raises(knowledge.KnowledgeLedgerError, match=message):
        forged(store, "finding_ingested", evidence, **overrides)


def test_a_signed_duplicate_record_is_refused_even_with_a_valid_signature(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    evidence = dict(knowledge.read(store, trust=TRUST, provenance=trusted)[1].findings[identity].ingested)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="insert-only"):
        forged(store, "finding_ingested", evidence)


def test_a_recurrence_needs_a_proof_not_only_a_mapping(store: state.GitLedgerStore) -> None:
    ingest(store, observation(message=observation()["message"] + " [family:DFF-049]"))
    identity = knowledge.finding_id(1, 561, 11)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="unproven"):
        write(store, knowledge.Write("recurrence", {"family_id": "DFF-049", "finding_id": identity}))


def test_a_later_head_re_observation_alone_writes_nothing(store: state.GitLedgerStore) -> None:
    ingest(store, observation())
    view, written, _ = ingest(store, observation(reviewed_head_sha=LATER, source_event_head_sha=LATER))
    assert written == 0
    assert view.findings[knowledge.finding_id(1, 561, 11)].ingested["provenance"]["reviewed_head_sha"] == HEAD
