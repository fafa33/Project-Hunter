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
        {"reviewer": ""},
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


def test_sonar_finding_flows_through_signed_ledger_into_dpm_without_forged_authority(
    store: state.GitLedgerStore,
) -> None:
    sonar = {
        "source": "sonar",
        "provider": "sonar",
        "event_id": "sonar-issue-SONAR-123",
        "source_pr": 662,
        "reviewed_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "reviewer": "sonarqube-cloud",
        "path": "src/hunter/automation/issue_agent_state.py",
        "line": 7,
        "message": "[MAJOR] python:S123: Example regression [sonar_status=FIXED]",
        "availability": "available",
    }
    view, written, refusals = ingest(store, sonar)
    assert (written, refusals) == (1, [])
    identity = next(iter(view.findings))
    assert view.findings[identity].ingested["provenance"]["provider"] == "sonar"
    assert view.findings[identity].classification is None  # sensor cannot authorize a defect family
    _, again, refusals = ingest(store, {**sonar, "reviewed_head_sha": LATER})
    assert (again, refusals) == (0, [])
    _, restored = knowledge.read(store, trust=TRUST, provenance=trusted)
    overlay = knowledge.overlay_families(restored)
    context = EngineeringContextAuthority(knowledge_overlay=overlay).compile(
        ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",))
    )
    assert f"KF-{identity[:12]}" in {item["id"] for item in context["applicable_defect_families"]}


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
    _, written, refusals = ingest(store, observation(reviewer=""), observation(comment=12))
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


def test_untrusted_author_is_learned_without_deterministic_family_authority(store: state.GitLedgerStore) -> None:
    view, written, refusals = ingest(
        store,
        observation(comment=81, reviewer="external-reviewer", message="Bad guard. [family:DFF-049]"),
        observation(comment=82, reviewer="external-reviewer", message="Bad guard."),
    )
    assert written == 2 and not refusals
    assert all(item.classification is None for item in view.findings.values())


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


def test_a_confirmed_finding_missing_its_receipt_still_reaches_dpm_before_publication(
    store: state.GitLedgerStore,
) -> None:
    """AC5/AC6/AC8 (Issue #527, replaying the DFF-029/#524 class): a confirmed finding that cannot yet

    satisfy its Knowledge Receipt obligation (resolved, but no correction commit was ever cited) is
    still an open finding, so the existing DPM overlay already warns a later implementation task
    about it through the unchanged, mandatory SPM/DPM ingress -- the same canonical path PR #526
    showed could be bypassed when an implementation originated outside it. The receipt mechanism
    adds a merge-time check; it never weakens or replaces this pre-existing, unbypassable ingress.
    """
    confirmed_but_undocumented = observation(
        comment=91, obligation=True, receipt_eligible=False, message="Something unsafe here."
    )
    view, written, refusals = ingest_with_receipt(store, confirmed_but_undocumented)
    assert written == 1 and not refusals  # finding_ingested only; no receipt could be minted
    identity = knowledge.finding_id(1, 561, 91)
    assert view.findings[identity].receipt is None
    assert view.findings[identity].open  # still open: DFF-029's hosted-preflight gate also still applies
    overlay = knowledge.overlay_families(view)
    dpm = EngineeringContextAuthority(knowledge_overlay=overlay)
    context = dpm.compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",)))
    assert f"KF-{identity[:12]}" in {entry["id"] for entry in context["applicable_defect_families"]}


def test_a_proven_resolved_finding_remains_in_prevention_overlay(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    _, view, _ = write(
        store,
        proven_classification(identity),
        proof(identity),
        knowledge.Write(
            "thread_resolved", {"finding_id": identity, "remediated_head_sha": LATER, "reply_comment_id": 5}
        ),
    )
    overlay = knowledge.overlay_families(view)
    assert len(overlay) == 1
    assert overlay[0]["id"] == f"KF-{identity[:12]}"
    assert overlay[0]["title"].startswith("verified-prevention-")
    context = EngineeringContextAuthority(knowledge_overlay=overlay).compile(
        ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope(("src/hunter/automation/",))
    )
    assert overlay[0]["id"] in {entry["id"] for entry in context["applicable_defect_families"]}


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


def test_copilot_and_other_authenticated_reviewers_are_ingested(store: state.GitLedgerStore) -> None:
    view, written, refusals = ingest(
        store,
        observation(comment=71, reviewer="copilot-pull-request-reviewer[bot]"),
        observation(comment=72, reviewer="external-reviewer"),
    )
    assert written == 2
    assert not refusals
    assert len(view.findings) == 2


def historical_assessment(identity: str, **changes: Any) -> knowledge.Write:
    evidence = {
        "finding_id": identity,
        "pull_request_number": 561,
        "comment_id": 11,
        "reviewed_head_sha": HEAD,
        "remediation_commit_sha": LATER,
        "final_head_sha": LATER,
        "merge_commit_sha": "f" * 40,
        "hosted_preflight_run_id": 300,
        "regression_tests": [TEST],
        "disposition": "historical-fix-evidence",
        "source": "github-authenticated-historical-review",
    }
    evidence.update(changes)
    return knowledge.Write("historical_evidence_assessed", evidence)


def test_historical_assessment_is_signed_insert_only_and_not_exact_head_proof(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    item = historical_assessment(identity)
    _, view, written = write(store, item, by={**BY, "role": "knowledge-ingest"})
    assert written == 1
    assert view.findings[identity].historical_assessment == dict(item.evidence)
    assert view.findings[identity].proven is None
    assert view.findings[identity].resolved is None
    assert view.findings[identity].open
    _, _, written = write(store, item, by={**BY, "role": "knowledge-ingest"})
    assert written == 0
    with pytest.raises(knowledge.KnowledgeLedgerError, match="different evidence"):
        write(store, historical_assessment(identity, final_head_sha="0" * 40), by={**BY, "role": "knowledge-ingest"})


@pytest.mark.parametrize("change", [{"comment_id": 12}, {"reviewed_head_sha": BASE}, {"pull_request_number": 999}])
def test_historical_assessment_must_bind_original_finding(store: state.GitLedgerStore, change: dict) -> None:
    identity = seeded(store)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="historical assessment"):
        write(store, historical_assessment(identity, **change), by={**BY, "role": "knowledge-ingest"})


def test_historical_assessment_requires_authorized_writer(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="role may not write"):
        write(store, historical_assessment(identity))


def test_historical_assessment_does_not_authorize_resolution_or_recurrence(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    _, view, _ = write(store, historical_assessment(identity), by={**BY, "role": "knowledge-ingest"})
    assert knowledge.overlay_families(view)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="after exact-head proof"):
        write(
            store,
            knowledge.Write(
                "thread_resolved",
                {
                    "finding_id": identity,
                    "remediated_head_sha": LATER,
                    "reply_comment_id": 5,
                },
            ),
        )


def test_historical_assessment_binds_original_sha_and_legacy_signed_ingestion(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    assessed = historical_assessment(identity, reviewed_head_sha=LATER, ingested_head_sha=HEAD)
    _, view, written = write(store, assessed, by={**BY, "role": "knowledge-ingest"})
    assert written == 1
    assert view.findings[identity].historical_assessment["reviewed_head_sha"] == LATER
    assert view.findings[identity].historical_assessment["ingested_head_sha"] == HEAD
    assert view.findings[identity].proven is None
    assert view.findings[identity].resolved is None


def test_historical_legacy_binding_rejects_wrong_signed_ingestion_head(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="historical assessment"):
        write(
            store,
            historical_assessment(identity, reviewed_head_sha=LATER, ingested_head_sha="0" * 40),
            by={**BY, "role": "knowledge-ingest"},
        )


def _positive_e2e_fixture() -> dict[str, Any]:
    return {
        "issue_number": 577,
        "pull_request_number": 703,
        "head_sha": "8077437e0b0e27547ac7b7bd2fb1520dd3c9d849",
        "base_sha": "48f1ddfc306b576a1ede4c40c12588dc64c3ee9b",
        "merge_commit_sha": "32b2db0a84d411143ad99e51fa7b547447c7a44c",
        "lifecycle_run_id": 42510436813,
        "lifecycle_run_attempt": 1,
        "authoritative_job": "validate",
        "proof_digest": "a" * 64,
        "conclusion": "success",
        "admission_run_id": 42526727825,
        "source": "github-authenticated-canonical-proof",
    }


def test_positive_e2e_signed_record_replay_and_view(store: state.GitLedgerStore) -> None:
    evidence = _positive_e2e_fixture()
    by = {**BY, "role": "knowledge-ingest", "job": "knowledge-ingest"}
    item = knowledge.Write("verified_e2e_outcome", evidence)
    _, view, count = write(store, item, by=by)
    assert count == 1
    assert list(view.verified_e2e_outcomes.values()) == [evidence]
    _, replayed, count = write(store, item, by=by)
    assert count == 0 and replayed.next_seq == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "48f1ddfc306b576a1ede4c40c12588dc64c3ee9b"),
        ("lifecycle_run_attempt", 2),
        ("conclusion", "failure"),
        ("source", "untrusted-model"),
    ],
)
def test_positive_e2e_rejects_invalid_evidence(store: state.GitLedgerStore, field: str, value: Any) -> None:
    evidence = {**_positive_e2e_fixture(), field: value}
    with pytest.raises(state.LedgerCorruptError):
        write(store, knowledge.Write("verified_e2e_outcome", evidence), by={**BY, "role": "knowledge-ingest"})


def test_positive_e2e_rejects_unprivileged_writer(store: state.GitLedgerStore) -> None:
    with pytest.raises(state.LedgerCorruptError):
        write(store, knowledge.Write("verified_e2e_outcome", _positive_e2e_fixture()))


def test_a_ledger_with_no_positive_outcome_record_still_reads_as_before(store: state.GitLedgerStore) -> None:
    """Legacy signed-record compatibility: a chain written before this extension is unaffected by it."""
    view, written, refusals = ingest(store, observation())
    assert written == 1 and not refusals
    assert view.verified_e2e_outcomes == {}
    overlay_before = knowledge.overlay_families(view)
    _, replayed = knowledge.read(store, trust=TRUST, provenance=trusted)
    assert replayed.verified_e2e_outcomes == {}
    assert knowledge.overlay_families(replayed) == overlay_before


def test_positive_e2e_outcome_reaches_the_same_view_without_duplicating_the_dpm_overlay(
    store: state.GitLedgerStore,
) -> None:
    """SPM/DPM consumption (ADR 0039 L2): the positive record is readable on the same consumed ledger

    and never becomes a defect-shaped overlay entry, so an existing open finding's DPM overlay is
    byte-for-byte unaffected by its presence.
    """
    view, _, _ = ingest(store, observation())
    by = {**BY, "role": "knowledge-ingest", "job": "knowledge-ingest"}
    _, view, count = write(store, knowledge.Write("verified_e2e_outcome", _positive_e2e_fixture()), by=by)
    assert count == 1
    overlay = knowledge.overlay_families(view)
    assert len(overlay) == 1  # only the pre-existing open finding; the outcome adds nothing to it
    assert not any(
        entry["id"].startswith(("KC-", "KF-")) and "verified_e2e_outcome" in entry["id"] for entry in overlay
    )
    assert list(view.verified_e2e_outcomes.values()) == [_positive_e2e_fixture()]
    # Replaying the same write is still idempotent once a dissimilar kind already shares the chain.
    _, replayed, count = write(store, knowledge.Write("verified_e2e_outcome", _positive_e2e_fixture()), by=by)
    assert count == 0
    assert knowledge.overlay_families(replayed) == overlay


# --- fast-path Knowledge Receipt (ADR 0039 L11, Issue #527) ------------------------------------------------


def receipt_observation(comment: int = 11, *, family: str | None = None, **kw: Any) -> dict:
    value = observation(
        comment,
        receipt_eligible=True,
        receipt_invariant="A confirmed invariant description of the real defect.",
        receipt_fix_reference="1" * 40,
        receipt_affected_path="src/hunter/automation/issue_agent_state.py",
        receipt_regression_evidence=[TEST],
        receipt_family_id=family,
    )
    value.update(kw)
    return value


def ingest_with_receipt(store: state.GitLedgerStore, *observations: dict) -> tuple:
    """The real ``cmd_knowledge_ingest`` wiring: ingestion and receipt writes in one signed batch."""
    _, view = knowledge.read(store, trust=TRUST, provenance=trusted)
    writes, refusals = knowledge.ingestion_writes(
        view, observations, repository_id=1, trusted_reviewers=REVIEWERS, applicability=applicability
    )
    receipt_ws, receipt_refusals = knowledge.receipt_writes(
        view, observations, repository_id=1, trusted_reviewers=REVIEWERS, applicability=applicability
    )
    _, view, written = write(store, *writes, *receipt_ws, by={**BY, "role": "knowledge-ingest"})
    return view, written, refusals + receipt_refusals


def receipt_of(view: knowledge.KnowledgeView, comment: int) -> Any:
    return view.findings[knowledge.finding_id(1, 561, comment)].receipt


def receipt_evidence(identity: str, fingerprint: str, **change: Any) -> dict:
    evidence = {
        "finding_id": identity,
        "fingerprint": fingerprint,
        "invariant": "An invariant long enough to pass the bounded text pattern check here.",
        "affected_paths": ["src/hunter/automation/issue_agent_state.py"],
        "correction_commit": "1" * 40,
        "regression_evidence": [TEST],
        "outcome": "pending-adjudication",
        "family_id": None,
    }
    evidence.update(change)
    return evidence


def test_a_confirmed_finding_with_complete_evidence_receives_a_pending_adjudication_receipt_before_resolution(
    store: state.GitLedgerStore,
) -> None:
    view, written, refusals = ingest_with_receipt(store, receipt_observation())
    assert written == 2 and not refusals  # finding_ingested + knowledge_receipt; no [family] tag in the message text
    receipt = receipt_of(view, 11)
    assert receipt is not None
    assert receipt["outcome"] == "pending-adjudication"
    assert receipt["family_id"] is None
    assert receipt["correction_commit"] == "1" * 40
    assert receipt["affected_paths"] == ["src/hunter/automation/issue_agent_state.py"]
    assert receipt["regression_evidence"] == [TEST]
    # The mandated lifecycle writes the receipt before the thread is ever resolved.
    assert view.findings[knowledge.finding_id(1, 561, 11)].resolved is None


def test_a_confirmed_finding_whose_tag_matches_an_applicable_family_receives_a_matched_family_receipt(
    store: state.GitLedgerStore,
) -> None:
    view, _, refusals = ingest_with_receipt(store, receipt_observation(family="DFF-049"))
    assert not refusals
    receipt = receipt_of(view, 11)
    assert receipt["outcome"] == "matched-family"
    assert receipt["family_id"] == "DFF-049"


def test_a_confirmed_finding_whose_family_tag_is_inapplicable_falls_back_to_pending_adjudication(
    store: state.GitLedgerStore,
) -> None:
    view, _, refusals = ingest_with_receipt(
        store,
        receipt_observation(
            family="DFF-049", path="scripts/unrelated.py", receipt_affected_path="scripts/unrelated.py"
        ),
    )
    assert not refusals
    receipt = receipt_of(view, 11)
    assert receipt["outcome"] == "pending-adjudication"
    assert receipt["family_id"] is None


def test_a_matched_family_receipt_also_classifies_the_finding_for_dpm(store: state.GitLedgerStore) -> None:
    """Issue #586 requirement 3 ("feed prevention into canonical DPM/SPM agent context"): a family

    an owner names only in the disposition reply -- not in the original finding comment, the
    realistic case since the reviewing bot rarely knows the family -- must still reach DPM/SPM
    the same way a tag-in-message match already does, not stay invisible to classification.
    """
    view, written, refusals = ingest_with_receipt(store, receipt_observation(family="DFF-049"))
    assert not refusals
    identity = knowledge.finding_id(1, 561, 11)
    assert written == 3  # finding_ingested + knowledge_receipt + the new finding_classified
    classification = view.findings[identity].classification
    assert classification is not None
    assert classification["outcome"] == "matched" and classification["family_id"] == "DFF-049"
    overlay = knowledge.overlay_families(view)
    assert any(entry["id"] == f"KF-{identity[:12]}" and "DFF-049" in entry["invariant"] for entry in overlay)


def test_a_pending_adjudication_receipt_never_classifies_the_finding(store: state.GitLedgerStore) -> None:
    view, written, refusals = ingest_with_receipt(store, receipt_observation())  # no family tag at all
    assert not refusals
    identity = knowledge.finding_id(1, 561, 11)
    assert written == 2  # finding_ingested + knowledge_receipt only
    assert view.findings[identity].classification is None


def test_a_matched_family_receipt_never_double_classifies_when_the_message_already_carries_the_tag(
    store: state.GitLedgerStore,
) -> None:
    """ingestion_writes already owns classification when the *original* comment carries the tag;

    receipt_writes must never add a second, potentially conflicting finding_classified record.
    """
    tagged = receipt_observation(family="DFF-049", message=observation()["message"] + " [family:DFF-049]")
    view, written, refusals = ingest_with_receipt(store, tagged)
    assert not refusals
    identity = knowledge.finding_id(1, 561, 11)
    # finding_ingested + the one finding_classified (from ingestion_writes's own tag match) +
    # knowledge_receipt -- never a second finding_classified for the same finding.
    assert written == 3
    assert view.findings[identity].classification["family_id"] == "DFF-049"


def test_a_matched_family_receipt_never_reclassifies_an_already_classified_finding(
    store: state.GitLedgerStore,
) -> None:
    """A finding already deterministically classified by an earlier pass must never be

    reclassified by a later receipt, even when the receipt's own family link would otherwise
    qualify -- first classification wins, exactly like ingestion_writes's own rule.
    """
    tagged_message = observation()["message"] + " [family:DFF-049]"
    view1, _, _ = ingest_with_receipt(store, observation(message=tagged_message))
    identity = knowledge.finding_id(1, 561, 11)
    assert view1.findings[identity].classification["family_id"] == "DFF-049"
    # A later pass: the owner's reply now also carries a matching family tag. The already-existing
    # classification must be left exactly as it is -- no second write is even attempted, same path
    # and message (and therefore fingerprint) as the original ingestion.
    view2, written2, refusals2 = ingest_with_receipt(
        store, receipt_observation(family="DFF-049", message=tagged_message)
    )
    assert not refusals2
    assert written2 == 1  # only the new knowledge_receipt; no finding_classified attempted
    assert view2.findings[identity].classification["family_id"] == "DFF-049"


def test_a_finding_without_complete_receipt_evidence_receives_no_receipt(store: state.GitLedgerStore) -> None:
    view, written, refusals = ingest_with_receipt(store, observation())
    assert written == 1 and not refusals  # only finding_ingested; receipt_eligible is absent
    assert receipt_of(view, 11) is None


def test_a_receipt_can_be_written_on_a_later_pass_after_the_finding_was_already_ingested(
    store: state.GitLedgerStore,
) -> None:
    """The mandated lifecycle allows the owner's disposition reply to arrive after the finding was first observed.

    A finding already ingested on an earlier pass (before any disposition reply existed) must still become
    receipt-eligible on a later pass once the reply supplies complete evidence -- ingestion and receipt
    emission are independent passes over the same observations, not a single ingest-or-never gate.
    """
    view1, written1, refusals1 = ingest_with_receipt(store, observation())
    assert written1 == 1 and not refusals1
    assert receipt_of(view1, 11) is None
    view2, written2, refusals2 = ingest_with_receipt(store, receipt_observation())
    assert written2 == 1 and not refusals2  # only the new knowledge_receipt; finding_ingested already exists
    assert receipt_of(view2, 11) is not None


def test_a_finding_receives_at_most_one_receipt_insert_only(store: state.GitLedgerStore) -> None:
    view, _, _ = ingest_with_receipt(store, receipt_observation())
    view2, written2, refusals2 = ingest_with_receipt(store, receipt_observation())
    assert written2 == 0 and not refusals2
    assert receipt_of(view2, 11) == receipt_of(view, 11)


# --- the Knowledge Receipt transport verdict (the status hunter_merge_readiness_v2 consumes) --------------


def test_the_verdict_is_success_with_no_verified_applicable_obligation(store: state.GitLedgerStore) -> None:
    """Legacy PRs and PRs with no confirmed-and-resolved finding are success -- nothing to block."""
    view, _, _ = ingest(store, observation())  # ingested, but no owner disposition at all
    verdict_state, detail = knowledge.receipt_verdict(
        view, [observation()], repository_id=1, trusted_reviewers=REVIEWERS
    )
    assert verdict_state == "success"
    assert "no verified applicable receipt obligation" in detail


def resolved_confirmed_observation(comment: int = 11, **kw: Any) -> dict:
    """A thread that entered the receipt-required lifecycle: resolved, owner-confirmed, full evidence."""
    value = receipt_observation(comment)
    value["obligation"] = True
    value.update(kw)
    return value


def test_the_verdict_is_success_once_the_obligation_has_a_receipt(store: state.GitLedgerStore) -> None:
    obs = resolved_confirmed_observation()
    view, written, refusals = ingest_with_receipt(store, obs)
    assert written == 2 and not refusals
    verdict_state, detail = knowledge.receipt_verdict(view, [obs], repository_id=1, trusted_reviewers=REVIEWERS)
    assert verdict_state == "success"
    assert "1 receipt(s) verified" in detail


def test_the_verdict_fails_closed_when_the_obligation_has_no_receipt(store: state.GitLedgerStore) -> None:
    """A thread resolved as confirmed, but whose receipt could never be minted (incomplete evidence)."""
    obs = resolved_confirmed_observation(receipt_eligible=False)
    view, written, refusals = ingest_with_receipt(store, obs)
    assert written == 1 and not refusals  # finding_ingested only; no receipt could be minted
    verdict_state, detail = knowledge.receipt_verdict(view, [obs], repository_id=1, trusted_reviewers=REVIEWERS)
    assert verdict_state == "failure"
    assert "1 of 1" in detail


def test_an_interrupted_transport_publish_is_resumed_by_another_executor_from_durable_state(
    store: state.GitLedgerStore,
) -> None:
    """AC9 (Issue #527): the ledger append (the durable fact) and the status publish (the transport

    of that fact) are independent steps. Simulate "executor A" writing the signed receipt and then
    becoming unavailable before it could publish the status -- then prove "executor B", an entirely
    separate process with no shared in-memory state, re-derives byte-identical transport evidence
    from a cold read of the same durable ledger, and the originating PR's signed records are
    unaffected by the interruption (no duplicate, no corruption, no re-authorization needed).
    """
    obs = resolved_confirmed_observation()
    # Executor A: the signed ledger append succeeds. It then becomes unavailable (crash, quota,
    # timeout, billing failure -- the exact class AC9 names) before publishing the status.
    view_a, written, refusals = ingest_with_receipt(store, obs)
    assert written == 2 and not refusals
    identity = knowledge.finding_id(1, 561, 11)
    receipt_before = view_a.findings[identity].receipt
    assert receipt_before is not None

    # Executor B: a fully independent process, with no memory of executor A's run, reads the same
    # durable signed ledger cold and resumes the only outstanding work -- the transport verdict.
    _, view_b = knowledge.read(store, trust=TRUST, provenance=trusted)
    assert view_b.findings[identity].receipt == receipt_before  # the originating write is unaffected
    verdict_b = knowledge.receipt_verdict(view_b, [obs], repository_id=1, trusted_reviewers=REVIEWERS)
    verdict_a = knowledge.receipt_verdict(view_a, [obs], repository_id=1, trusted_reviewers=REVIEWERS)
    assert verdict_b == verdict_a == ("success", "1 receipt(s) verified")

    # A third pass over the exact same observation is still idempotent: no duplicate, no new write.
    view_c, written_c, refusals_c = ingest_with_receipt(store, obs)
    assert written_c == 0 and not refusals_c
    assert view_c.findings[identity].receipt == receipt_before


def test_the_verdict_counts_every_missing_obligation_among_several(store: state.GitLedgerStore) -> None:
    satisfied = resolved_confirmed_observation(11)
    missing = resolved_confirmed_observation(12, receipt_eligible=False)
    view, _, _ = ingest_with_receipt(store, satisfied, missing)
    verdict_state, detail = knowledge.receipt_verdict(
        view, [satisfied, missing], repository_id=1, trusted_reviewers=REVIEWERS
    )
    assert verdict_state == "failure"
    assert "1 of 2" in detail


def test_receipt_writes_refuses_an_abbreviated_sha_instead_of_building_an_invalid_write(
    store: state.GitLedgerStore,
) -> None:
    """Copilot F7 (PR #737): even if a caller's eligibility gate is wrong or absent,

    receipt_writes must never build a Write that append() would then reject, aborting the rest of
    this batch's otherwise-valid ingestion -- it must refuse the one bad observation and continue.
    """
    bad = receipt_observation(11, receipt_fix_reference="1" * 7)
    good = receipt_observation(12)
    _, view = knowledge.read(store, trust=TRUST, provenance=trusted)
    writes, refusals = knowledge.receipt_writes(
        view, [bad, good], repository_id=1, trusted_reviewers=REVIEWERS, applicability=applicability
    )
    assert [write.kind for write in writes] == ["knowledge_receipt"]
    assert writes[0].evidence["finding_id"] == knowledge.finding_id(1, 561, 12)
    assert len(refusals) == 1 and "schema" in refusals[0] and "correction_commit" in refusals[0]


def test_receipt_writes_refuses_a_short_invariant_instead_of_building_an_invalid_write(
    store: state.GitLedgerStore,
) -> None:
    bad = receipt_observation(11, receipt_invariant="too short")
    _, view = knowledge.read(store, trust=TRUST, provenance=trusted)
    writes, refusals = knowledge.receipt_writes(
        view, [bad], repository_id=1, trusted_reviewers=REVIEWERS, applicability=applicability
    )
    assert writes == []
    assert len(refusals) == 1 and "invariant" in refusals[0]


def test_an_obligation_refused_by_finding_identity_is_never_treated_as_no_obligation(
    store: state.GitLedgerStore,
) -> None:
    """Copilot F8 (PR #737): a confirmed, resolved finding whose path/provenance is malformed is

    refused by finding_from_observation -- but that must count as a permanently unsatisfiable
    obligation, never silently drop out of the obligation set and let the verdict read as success.
    """
    malformed = resolved_confirmed_observation(path=None)  # finding_from_observation requires a path
    view, _, _ = ingest_with_receipt(store, resolved_confirmed_observation(13))  # an unrelated satisfied one
    verdict_state, detail = knowledge.receipt_verdict(view, [malformed], repository_id=1, trusted_reviewers=REVIEWERS)
    assert verdict_state == "failure"
    assert "malformed evidence" in detail


def test_an_obligation_refused_alongside_a_satisfied_one_still_fails_closed(store: state.GitLedgerStore) -> None:
    satisfied = resolved_confirmed_observation(11)
    malformed = resolved_confirmed_observation(12, path=None)
    view, _, _ = ingest_with_receipt(store, satisfied)
    verdict_state, _detail = knowledge.receipt_verdict(
        view, [satisfied, malformed], repository_id=1, trusted_reviewers=REVIEWERS
    )
    assert verdict_state == "failure"  # the one satisfied obligation cannot offset the refused one


def test_a_knowledge_receipt_does_not_alter_the_dpm_overlay(store: state.GitLedgerStore) -> None:
    """The receipt is finding-lifecycle evidence, not prevention knowledge; the DPM overlay is unaffected by it."""
    view, _, _ = ingest(store, observation())
    overlay_before = knowledge.overlay_families(view)
    view, written, refusals = ingest_with_receipt(store, receipt_observation())
    assert written == 1 and not refusals  # only the new knowledge_receipt; finding_ingested already exists
    assert knowledge.overlay_families(view) == overlay_before


def test_a_receipt_requires_an_existing_finding(store: state.GitLedgerStore) -> None:
    with pytest.raises(knowledge.KnowledgeLedgerError, match="unknown finding"):
        write(
            store,
            knowledge.Write("knowledge_receipt", receipt_evidence("0" * 64, "1" * 64)),
            by={**BY, "role": "knowledge-ingest"},
        )


def test_receipt_fingerprint_must_match_its_finding(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    with pytest.raises(knowledge.KnowledgeLedgerError, match="fingerprint mismatch"):
        write(
            store,
            knowledge.Write("knowledge_receipt", receipt_evidence(identity, "0" * 64)),
            by={**BY, "role": "knowledge-ingest"},
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"affected_paths": []}, "at least one affected path"),
        ({"regression_evidence": []}, "regression evidence"),
        ({"outcome": "matched-family", "family_id": None}, "family id iff matched"),
        ({"outcome": "pending-adjudication", "family_id": "DFF-049"}, "family id iff matched"),
    ],
)
def test_a_receipt_must_be_internally_consistent(store: state.GitLedgerStore, change: dict, message: str) -> None:
    identity = seeded(store)
    fingerprint = knowledge.read(store, trust=TRUST, provenance=trusted)[1].findings[identity].ingested["fingerprint"]
    with pytest.raises(knowledge.KnowledgeLedgerError, match=message):
        write(
            store,
            knowledge.Write("knowledge_receipt", receipt_evidence(identity, fingerprint, **change)),
            by={**BY, "role": "knowledge-ingest"},
        )


def test_knowledge_receipt_requires_the_knowledge_ingest_role(store: state.GitLedgerStore) -> None:
    identity = seeded(store)
    fingerprint = knowledge.read(store, trust=TRUST, provenance=trusted)[1].findings[identity].ingested["fingerprint"]
    with pytest.raises(knowledge.KnowledgeLedgerError, match="may not write"):
        write(store, knowledge.Write("knowledge_receipt", receipt_evidence(identity, fingerprint)))
