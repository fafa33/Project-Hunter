"""ADR 0037 Slice 2: the signed, anchored Issue Agent state ledger (FMEA AT-13..17, AT-24/25/27, AT-44, AT-48)."""

from __future__ import annotations

import copy
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_state import (
    UNKNOWN,
    AnchorIntegrityError,
    AnchorPin,
    ArtifactFact,
    Facts,
    LedgerConflictError,
    LedgerCorruptError,
    LedgerError,
    TrustRoots,
)

KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()
TRUST = TrustRoots(state_keys={state.public_key_id(KEY.public_key()): KEY.public_key()}, repository_id=1)
REPO, ISSUE = 1, 520
AUTH = "a" * 64
CONTROL = "c" * 40
BASE = "b" * 40
HANDOFF = "d" * 64
TRUSTED_RUNS = {100, 101, 102, 103, 104, 105}


def trusted(recorded_by: Any, _record: Any) -> bool:
    return recorded_by["run_id"] in TRUSTED_RUNS and recorded_by["run_attempt"] == 1


def _by(role: str, run_id: int = 100, attempt: int = 1, head: str = CONTROL) -> dict[str, Any]:
    return {
        "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
        "job": role,
        "role": role,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
    }


def _artifact(artifact_id: int) -> dict[str, Any]:
    return {
        "run_id": 100,
        "artifact_id": artifact_id,
        "artifact_digest": "sha256:" + "e" * 64,
        "ciphertext_sha256": "f" * 64,
        "aad_sha256": "1" * 64,
        "recipient_key_id": "2" * 64,
    }


def authorized_evidence(auth: str = AUTH, run_id: int = 100) -> dict[str, Any]:
    return {
        "authorization_envelope_sha256": "3" * 64,
        "claims": {
            "owner_login": "fafa33",
            "label": "hunter-agent-execute",
            "issue_updated_at": "2026-10-04T10:00:00Z",
            "schema_version": "hunter-issue-agent-authorization-v1",
            "title_sha256": "4" * 64,
            "body_sha256": "5" * 64,
        },
        "task_scope": {
            "task_id": "issue-520-canary",
            "branch_pattern": "issue-520-*",
            "base_ref": "main",
            "base_sha": BASE,
            "allowed_paths": ["docs/ISSUE_AGENT_CANARY.md"],
            "prohibited_paths": [],
        },
        "task_scope_sha256": "6" * 64,
        "execution_branch": f"issue-{ISSUE}-{auth[:16]}",
        "base_sha": BASE,
        "control_sha": CONTROL,
        "authorize_run_id": run_id,
        "execution_id": state.execution_identity(
            authorization_id=auth, authorize_run_id=run_id, control_sha=CONTROL, handoff_sha256=HANDOFF
        ),
        "prompt_input_manifest_sha256": "7" * 64,
        "compiler_identity_sha256": "8" * 64,
        "deadline_published_at": "2026-10-04T16:00:00Z",
        "lineage": {
            "document_id": f"github-issue:fafa33/Project-Hunter#{ISSUE}",
            "build_record_id": "build-1",
            "envelope_id": "envelope-1",
            "prompt_artifact_id": "prompt-1",
            "prompt_sha256": "9" * 64,
            "handoff_sha256": HANDOFF,
            "dpm_context_sha256": "0" * 64,
            "source_handling_record_ids": ["sh-1", "sh-2"],
            "reconstruction": "EXACT_RECONSTRUCTION_UNAVAILABLE",
            "reconstruction_reason": "NO_CONFIDENTIAL_DURABLE_STORE",
        },
        "handoff_artifact": _artifact(11),
    }


class Chain:
    """Builds a correctly chained, signed ledger for one Issue."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.view = state.empty_view(REPO, ISSUE)

    def make(
        self,
        kind: str,
        st: str,
        evidence: dict[str, Any],
        by: dict[str, Any],
        auth: str = AUTH,
        key: Ed25519PrivateKey = KEY,
    ) -> dict[str, Any]:
        unsigned = {
            "schema_version": state.RECORD_SCHEMA_VERSION,
            "kind": kind,
            "record_seq": self.view.next_seq,
            "prev_record_sha256": self.view.head_record_digest,
            "recorded_at": "2026-10-04T10:00:00Z",
            "recorded_by": by,
            "repository_id": REPO,
            "issue_number": ISSUE,
            "authorization_id": auth,
            "state": st,
            "evidence": evidence,
        }
        return state.sign_record(unsigned, key)

    def add(self, record: dict[str, Any]) -> Chain:
        state.apply_record(self.view, record, trust=TRUST, provenance=trusted)
        self.records.append(record)
        return self

    def transition(self, st: str, evidence: dict[str, Any], role: str, auth: str = AUTH, run_id: int = 100) -> Chain:
        return self.add(self.make("transition", st, evidence, _by(role, run_id), auth))


def validated_evidence() -> dict[str, Any]:
    return {
        "receipt_sha256": "a1" * 32,
        "result_sha256": "b1" * 32,
        "tree_sha": "c1" * 20,
        "unsigned_commit_sha": "d1" * 20,
        "validation_definition": "e1" * 32,
        "toolchain_sha256": "f1" * 32,
        "validator_run_id": 100,
        "validation_attempts": 1,
    }


def published_evidence(chain: Chain, auth: str = AUTH) -> dict[str, Any]:
    bound = chain.view.authorizations[auth].evidence[state.AUTHORIZED]
    validated = chain.view.authorizations[auth].evidence[state.VALIDATED]
    identity = state.publication_identity(
        repository_id=REPO,
        issue_number=ISSUE,
        authorization_id=auth,
        base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"],
        execution_id=bound["execution_id"],
        result_sha256=validated["result_sha256"],
        tree_sha=validated["tree_sha"],
        unsigned_commit_sha=validated["unsigned_commit_sha"],
        control_sha=bound["control_sha"],
        writer_login="fafa33",
    )
    return {
        "writer_login": "fafa33",
        "publication_identity": identity,
        "head_sha": "9a" * 20,
        "commit_verified": True,
        "publish_attempts": 1,
        "deadline_completed_at": "2026-10-05T10:00:00Z",
    }


def result_bound_evidence() -> dict[str, Any]:
    return {
        "result_artifact": _artifact(22),
        "result_plaintext_sha256": "b1" * 32,
        "executor_job_id": 5,
        "executor_conclusion": "success",
        "executor_advisory_code": None,
    }


def full_chain(until: str = state.COMPLETED) -> Chain:
    chain = Chain().transition(state.AUTHORIZED, authorized_evidence(), "authorize")
    steps = [
        (state.RESULT_BOUND, lambda c: result_bound_evidence(), "bind"),
        (state.VALIDATED, lambda c: validated_evidence(), "record-validation"),
        (state.PUBLISHED, published_evidence, "finalize"),
        (
            state.COMPLETED,
            lambda c: {
                "pull_request_number": 561,
                "pull_request_node_id": "PR_kwDO",
                "pull_request_head_sha": "9a" * 20,
                "draft": True,
                "preflight_run_id": 77,
                "preflight_conclusion": "success",
            },
            "candidate-pr-record",
        ),
    ]
    for target, evidence, role in steps:
        if chain.view.authorizations[AUTH].state == until:
            break
        chain.transition(target, evidence(chain), role)
    return chain


def reject(chain: Chain, record: dict[str, Any], message: str) -> None:
    view = copy.deepcopy(chain.view)
    with pytest.raises(LedgerCorruptError, match=message):
        state.apply_record(view, record, trust=TRUST, provenance=trusted)


# --- chain validity -----------------------------------------------------------------------------------


def test_a_full_lifecycle_verifies_and_reconstructs_the_index() -> None:
    chain = full_chain()
    view = state.verify_chain(chain.records, repository_id=REPO, issue_number=ISSUE, trust=TRUST, provenance=trusted)
    assert view.authorizations[AUTH].state == state.COMPLETED
    assert view.active is None and view.claimed == [AUTH]
    assert view.index()["active_authorization_id"] is None


def test_an_unpinned_signer_cannot_write_a_transition() -> None:
    chain = full_chain(state.RESULT_BOUND)
    forged = chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation"), key=OTHER_KEY)
    reject(chain, forged, "not pinned")


def test_a_field_changed_after_signing_is_refused() -> None:
    chain = full_chain(state.RESULT_BOUND)
    record = chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation"))
    record["evidence"]["tree_sha"] = "0" * 40
    reject(chain, record, "signature")


def test_a_record_replayed_from_another_issue_is_refused() -> None:
    chain = full_chain(state.RESULT_BOUND)
    record = chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation"))
    unsigned = {k: v for k, v in record.items() if k != "signature"}
    other_issue = state.sign_record({**unsigned, "issue_number": 521}, KEY)
    reject(chain, other_issue, "different repository or Issue")


def test_an_earlier_record_replayed_into_the_chain_is_refused() -> None:
    chain = full_chain(state.VALIDATED)
    reject(chain, chain.records[1], "sequence")


def test_a_record_that_does_not_chain_to_its_predecessor_is_refused() -> None:
    chain = full_chain(state.RESULT_BOUND)
    record = chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation"))
    unsigned = {k: v for k, v in record.items() if k != "signature"}
    reject(chain, state.sign_record({**unsigned, "prev_record_sha256": "0" * 64}, KEY), "does not chain")


@pytest.mark.parametrize(
    ("until", "target", "role"),
    [
        (state.RESULT_BOUND, state.PUBLISHED, "finalize"),
        (state.RESULT_BOUND, state.COMPLETED, "candidate-pr-record"),  # the #558 P1 mutant
        (state.AUTHORIZED, state.VALIDATED, "record-validation"),
    ],
)
def test_skipping_a_state_is_an_illegal_transition(until: str, target: str, role: str) -> None:
    chain = full_chain(until)
    evidence = {
        state.PUBLISHED: published_evidence(full_chain(state.PUBLISHED)),
        state.COMPLETED: {
            "pull_request_number": 1,
            "pull_request_node_id": "PR_x",
            "pull_request_head_sha": "9a" * 20,
            "draft": True,
            "preflight_run_id": 77,
            "preflight_conclusion": "success",
        },
        state.VALIDATED: validated_evidence(),
    }[target]
    reject(chain, chain.make("transition", target, evidence, _by(role)), "illegal transition")


def test_completed_requires_the_observed_draft_pr_at_the_published_head() -> None:
    chain = full_chain(state.PUBLISHED)
    wrong_head = {
        "pull_request_number": 561,
        "pull_request_node_id": "PR_x",
        "pull_request_head_sha": "1" * 40,
        "draft": True,
        "preflight_run_id": 77,
        "preflight_conclusion": "success",
    }
    reject(chain, chain.make("transition", state.COMPLETED, wrong_head, _by("candidate-pr-record")), "published head")
    missing = {"pull_request_number": 561, "draft": True, "preflight_run_id": 77, "preflight_conclusion": "success"}
    reject(chain, chain.make("transition", state.COMPLETED, missing, _by("candidate-pr-record")), "schema")


def test_terminal_states_are_absorbing() -> None:
    chain = full_chain(state.AUTHORIZED).transition(
        state.FAILED, {"code": "EXECUTION_NOT_COMPLETED", "failed_from_state": state.AUTHORIZED}, "bind"
    )
    reject(chain, chain.make("transition", state.RESULT_BOUND, result_bound_evidence(), _by("bind")), "illegal")
    second_failure = {"code": "LIFECYCLE_DEADLINE_EXCEEDED", "failed_from_state": state.AUTHORIZED}
    reject(chain, chain.make("transition", state.FAILED, second_failure, _by("reconcile")), "illegal")


def test_one_active_authorization_per_issue_and_no_authorization_replay() -> None:
    chain = full_chain(state.AUTHORIZED)
    other = "b" * 64
    second = chain.make("transition", state.AUTHORIZED, authorized_evidence(other, 101), _by("authorize", 101), other)
    reject(chain, second, "second authorization")
    chain.transition(state.FAILED, {"code": "EXECUTION_NOT_STARTED", "failed_from_state": state.AUTHORIZED}, "bind")
    replay = chain.make("transition", state.AUTHORIZED, authorized_evidence(), _by("authorize"))
    reject(chain, replay, "replayed|illegal transition")
    fresh = chain.make("transition", state.AUTHORIZED, authorized_evidence(other, 101), _by("authorize", 101), other)
    chain.add(fresh)  # a fresh owner authorization after terminal is legal
    assert chain.view.active == other


def test_a_role_may_only_write_its_own_transitions() -> None:
    chain = full_chain(state.RESULT_BOUND)
    reject(chain, chain.make("transition", state.VALIDATED, validated_evidence(), _by("bind")), "may not write")


def test_a_record_from_an_untrusted_run_or_a_rerun_is_refused() -> None:
    chain = full_chain(state.RESULT_BOUND)
    reject(
        chain, chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation", 999)), "trusted"
    )
    reject(
        chain,
        chain.make("transition", state.VALIDATED, validated_evidence(), _by("record-validation", 100, 2)),
        "trusted",
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda e: e.update(execution_branch="issue-520-ffffffffffffffff"), "execution branch"),
        (lambda e: e.update(base_sha="1" * 40), "TaskScope base"),
        (lambda e: e.update(control_sha="1" * 40), "control commit|execution identity"),
        (lambda e: e.update(execution_id="1" * 64), "execution identity"),
    ],
)
def test_authorized_bindings_must_derive_from_the_signed_inputs(mutate: Any, message: str) -> None:
    evidence = authorized_evidence()
    mutate(evidence)
    reject(Chain(), Chain().make("transition", state.AUTHORIZED, evidence, _by("authorize")), message)


def test_authorized_must_be_written_by_the_bound_control_commit() -> None:
    record = Chain().make("transition", state.AUTHORIZED, authorized_evidence(), _by("authorize", head="1" * 40))
    reject(Chain(), record, "control commit")


def test_authorized_from_a_rerun_attempt_is_refused() -> None:
    record = Chain().make("transition", state.AUTHORIZED, authorized_evidence(), _by("authorize", 100, 2))
    reject(Chain(), record, "attempt 1|trusted")


def test_published_identity_must_derive_from_the_bound_evidence() -> None:
    chain = full_chain(state.VALIDATED)
    evidence = published_evidence(chain)
    evidence["publication_identity"] = "0" * 64
    reject(chain, chain.make("transition", state.PUBLISHED, evidence, _by("finalize")), "publication identity")


def test_validated_must_bind_the_bound_result_digest() -> None:
    chain = full_chain(state.RESULT_BOUND)
    evidence = {**validated_evidence(), "result_sha256": "0" * 64}
    reject(chain, chain.make("transition", state.VALIDATED, evidence, _by("record-validation")), "bound result")


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(note="free text"), id="unknown-field"),
        pytest.param(lambda r: r["evidence"].update(code="Some free text"), id="code-outside-vocabulary"),
        pytest.param(lambda r: r.update(record_seq=True), id="bool-as-int"),
        pytest.param(lambda r: r.update(recorded_at="2026-10-04 10:00"), id="malformed-timestamp"),
        pytest.param(lambda r: r["evidence"].update(failed_from_state="FAILED"), id="terminal-as-origin"),
    ],
)
def test_the_closed_schema_refuses_content_and_coercion(mutate: Any) -> None:
    chain = full_chain(state.AUTHORIZED)
    record = chain.make(
        "transition",
        state.FAILED,
        {"code": "EXECUTION_NOT_STARTED", "failed_from_state": state.AUTHORIZED},
        _by("bind"),
    )
    unsigned = {k: v for k, v in record.items() if k != "signature"}
    mutate(unsigned)
    reject(chain, state.sign_record(unsigned, KEY), "schema")


def test_secret_or_content_bearing_paths_are_refused_by_the_schema() -> None:
    evidence = authorized_evidence()
    evidence["task_scope"]["allowed_paths"] = ["../outside"]
    reject(Chain(), Chain().make("transition", state.AUTHORIZED, evidence, _by("authorize")), "schema")


def test_oversized_records_are_refused() -> None:
    evidence = authorized_evidence()
    evidence["lineage"]["source_handling_record_ids"] = [f"id-{n}-" + "x" * 190 for n in range(64)]
    evidence["task_scope"]["allowed_paths"] = [f"p{n}/" + "y" * 500 for n in range(256)]
    reject(Chain(), Chain().make("transition", state.AUTHORIZED, evidence, _by("authorize")), "size")


# --- resume protocol (model-free stages only, nonce-bound) ---------------------------------------------


def _resume(chain: Chain, stage: str, attempt: int, nonce: str) -> dict[str, Any]:
    current = chain.view.authorizations[AUTH].state
    evidence = {"stage": stage, "nonce": nonce, "attempt": attempt, "dispatched_at": "2026-10-04T11:00:00Z"}
    return chain.make("resume_requested", current, evidence, _by("reconcile"))


def test_a_validation_resume_binds_exactly_one_run_by_nonce() -> None:
    chain = full_chain(state.RESULT_BOUND)
    chain.add(_resume(chain, "validation", 1, "1" * 64))
    reject(chain, _resume(chain, "validation", 2, "2" * 64), "pending|attempt")
    wrong = chain.make("resume_bound", state.RESULT_BOUND, {"nonce": "9" * 64, "run_id": 101}, _by("reconcile", 101))
    reject(chain, wrong, "nonce")
    chain.add(chain.make("resume_bound", state.RESULT_BOUND, {"nonce": "1" * 64, "run_id": 101}, _by("reconcile", 101)))
    duplicate = chain.make(
        "resume_bound", state.RESULT_BOUND, {"nonce": "1" * 64, "run_id": 102}, _by("reconcile", 102)
    )
    reject(chain, duplicate, "unbound pending nonce")


def test_resume_attempts_are_capped_and_the_model_stage_never_resumes() -> None:
    chain = full_chain(state.RESULT_BOUND)
    reject(chain, _resume(chain, "validation", 2, "1" * 64), "attempt")
    authorized = full_chain(state.AUTHORIZED)
    reject(authorized, _resume(authorized, "validation", 1, "1" * 64), "outside its stage")


def test_a_stored_index_that_differs_from_the_chain_freezes() -> None:
    chain = Chain()
    record = chain.make("transition", state.AUTHORIZED, authorized_evidence(), _by("authorize"))
    honest = state.empty_view(REPO, ISSUE)
    state.apply_record(copy.deepcopy(honest), record, trust=TRUST, provenance=trusted)
    lying_index = {
        "schema_version": state.INDEX_SCHEMA_VERSION,
        "repository_id": REPO,
        "issue_number": ISSUE,
        "claimed_authorization_ids": [],
        "active_authorization_id": None,
        "pending_resume": None,
    }
    with pytest.raises(LedgerCorruptError, match="index"):
        state.apply_record(honest, record, trust=TRUST, provenance=trusted, index=lying_index)


# --- anchor integrity (AT-48, live-proven inputs from S0) ------------------------------------------------

PIN = AnchorPin(ruleset_id=24433842, updated_at="2026-10-03T23:15:23.241Z")
OK_RULESET = {
    "id": 24433842,
    "enforcement": "active",
    "updated_at": "2026-10-03T23:15:23.241Z",
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
}
OK_BRANCH_RULES = [{"type": "deletion", "ruleset_id": 24433842}, {"type": "non_fast_forward", "ruleset_id": 24433842}]


def test_an_intact_anchor_verifies() -> None:
    state.verify_anchor_integrity(PIN, OK_RULESET, OK_BRANCH_RULES)


@pytest.mark.parametrize(
    ("ruleset", "branch_rules"),
    [
        pytest.param({**OK_RULESET, "enforcement": "disabled"}, [], id="disabled"),
        pytest.param({**OK_RULESET, "enforcement": "evaluate"}, OK_BRANCH_RULES, id="evaluate-mode"),
        pytest.param({**OK_RULESET, "updated_at": "2026-10-04T09:00:00.000Z"}, OK_BRANCH_RULES, id="toggled"),
        pytest.param({**OK_RULESET, "id": 99}, OK_BRANCH_RULES, id="recreated"),
        pytest.param({**OK_RULESET, "rules": [{"type": "deletion"}]}, OK_BRANCH_RULES, id="rule-removed-stale-read"),
        pytest.param(OK_RULESET, [], id="branch-not-covered"),
        pytest.param(None, OK_BRANCH_RULES, id="missing"),
    ],
)
def test_any_anchor_weakening_freezes(ruleset: Any, branch_rules: Any) -> None:
    with pytest.raises(AnchorIntegrityError):
        state.verify_anchor_integrity(PIN, ruleset, branch_rules)


# --- git compare-and-swap store (AT-13, AT-14) -----------------------------------------------------------


@pytest.fixture
def remote(tmp_path: Path) -> str:
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(path)], check=True)
    return str(path)


def _store(remote: str, tmp_path: Path, name: str) -> state.GitLedgerStore:
    return state.GitLedgerStore(remote, workdir=tmp_path / name)


def test_append_and_read_round_trip_through_the_verifier(remote: str, tmp_path: Path) -> None:
    store = _store(remote, tmp_path, "w1")
    chain = full_chain()
    view = state.empty_view(REPO, ISSUE)
    head = None
    for record in chain.records:
        state.apply_record(view, record, trust=TRUST, provenance=trusted)
        head = store.append(ISSUE, head, record, view.index())
    read_head, entries = _store(remote, tmp_path, "r1").read(ISSUE)
    assert read_head == head and len(entries) == len(chain.records)
    verified = state.verify_chain(
        [e.record for e in entries],
        repository_id=REPO,
        issue_number=ISSUE,
        trust=TRUST,
        provenance=trusted,
        indexes=[e.index for e in entries],
    )
    assert verified.authorizations[AUTH].state == state.COMPLETED


def test_concurrent_writers_from_one_observed_head_have_exactly_one_winner(remote: str, tmp_path: Path) -> None:
    chain = full_chain(state.AUTHORIZED)
    seed = _store(remote, tmp_path, "seed")
    head = seed.append(ISSUE, None, chain.records[0], chain.view.index())
    outcomes: list[str] = []
    lock = threading.Lock()

    def writer(number: int) -> None:
        store = _store(remote, tmp_path, f"w{number}")
        local = copy.deepcopy(chain)
        code = sorted(state.FAILURE_CODES)[number]  # distinct bytes per writer: identical bytes are one write
        record = local.make(
            "transition", state.FAILED, {"code": code, "failed_from_state": state.AUTHORIZED}, _by("bind")
        )
        state.apply_record(local.view, record, trust=TRUST, provenance=trusted)
        try:
            store.append(ISSUE, head, record, local.view.index())
            result = "win"
        except (LedgerConflictError, LedgerError):
            result = "lose"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("win") == 1 and outcomes.count("lose") == 7


def test_byte_identical_concurrent_writes_are_one_idempotent_write(remote: str, tmp_path: Path) -> None:
    chain = full_chain(state.AUTHORIZED)
    head = _store(remote, tmp_path, "seed").append(ISSUE, None, chain.records[0], chain.view.index())
    record = chain.make(
        "transition",
        state.FAILED,
        {"code": "EXECUTION_NOT_STARTED", "failed_from_state": state.AUTHORIZED},
        _by("bind"),
    )
    local = copy.deepcopy(chain.view)
    state.apply_record(local, record, trust=TRUST, provenance=trusted)
    first = _store(remote, tmp_path, "a").append(ISSUE, head, record, local.index())
    again = _store(remote, tmp_path, "b").append(ISSUE, head, record, local.index())
    assert first == again == _store(remote, tmp_path, "r").remote_head(ISSUE)


def test_a_stale_writer_loses_the_compare_and_swap(remote: str, tmp_path: Path) -> None:
    chain = full_chain(state.RESULT_BOUND)
    store = _store(remote, tmp_path, "w")
    first = store.append(ISSUE, None, chain.records[0], {"x": 1})
    store.append(ISSUE, first, chain.records[1], {"x": 2})
    with pytest.raises(LedgerConflictError):
        _store(remote, tmp_path, "stale").append(ISSUE, first, chain.records[1], {"x": 3})


def test_a_lost_push_acknowledgement_is_resolved_by_read_back(
    remote: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(remote, tmp_path, "w")
    original = store._git

    def flaky(*args: Any, **kwargs: Any) -> bytes:
        output = original(*args, **kwargs)
        if args and args[0] == "push":
            raise LedgerError("connection reset after the server applied the push")
        return output

    monkeypatch.setattr(store, "_git", flaky)
    chain = full_chain(state.AUTHORIZED)
    head = store.append(ISSUE, None, chain.records[0], chain.view.index())
    assert _store(remote, tmp_path, "r").remote_head(ISSUE) == head


def test_a_tampered_ledger_layout_or_lineage_is_corrupt(remote: str, tmp_path: Path) -> None:
    store = _store(remote, tmp_path, "w")
    chain = full_chain(state.AUTHORIZED)
    store.append(ISSUE, None, chain.records[0], chain.view.index())
    work = tmp_path / "attacker"
    subprocess.run(
        ["git", "clone", "--quiet", "--branch", f"hunter-state/v1/issue-{ISSUE}", remote, str(work)], check=True
    )
    (work / "extra.txt").write_text("x")
    subprocess.run(["git", "-C", str(work), "add", "extra.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@a",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "push", "--quiet", "origin", f"HEAD:refs/heads/hunter-state/v1/issue-{ISSUE}"],
        check=True,
    )
    with pytest.raises(LedgerCorruptError, match="layout"):
        _store(remote, tmp_path, "r").read(ISSUE)


# --- advance (AT-24, AT-27; state machine spec section 6) -----------------------------------------------

DONE = dict(stage_run_active=False, now="2026-10-04T12:00:00Z")


def _view(until: str, **pending: Any) -> state.AuthorizationView:
    view = full_chain(until).view.authorizations[AUTH]
    for key, value in pending.items():
        setattr(view, key, value)
    return view


def _art(artifact_id: int = 22, name: str = f"hunter-ia-result-{AUTH}", expired: bool = False) -> ArtifactFact:
    return ArtifactFact(artifact_id, name, "sha256:" + "e" * 64, 100, expired)


@pytest.mark.parametrize(
    ("until", "facts", "expected"),
    [
        (state.AUTHORIZED, Facts(stage_run_active=UNKNOWN), ("noop", None, None)),
        (state.AUTHORIZED, Facts(stage_run_active=True), ("noop", None, None)),
        (state.AUTHORIZED, Facts(**DONE), ("noop", None, None)),
        (
            state.AUTHORIZED,
            Facts(**DONE, executor_conclusion="success", result_artifacts=(_art(),)),
            ("transition", state.RESULT_BOUND, None),
        ),
        (
            state.AUTHORIZED,
            Facts(**DONE, executor_conclusion="success", result_artifacts=(_art(), _art(23))),
            ("fail", None, "TRANSPORT_INTEGRITY_FAILED"),
        ),
        (
            state.AUTHORIZED,
            Facts(**DONE, executor_conclusion="skipped", result_artifacts=()),
            ("fail", None, "EXECUTION_NOT_STARTED"),
        ),
        (
            state.AUTHORIZED,
            Facts(**DONE, executor_conclusion="failure", result_artifacts=()),
            ("fail", None, "EXECUTION_NOT_COMPLETED"),
        ),
        (
            state.AUTHORIZED,
            Facts(**DONE, executor_conclusion="success", result_artifacts=(_art(expired=True),)),
            ("fail", None, "RESULT_TRANSPORT_EXPIRED"),
        ),
        (
            state.RESULT_BOUND,
            Facts(**DONE, receipt={"ok": 1}, remote_branch_head=None, open_draft_pr=None),
            ("transition", state.VALIDATED, None),
        ),
        (
            state.RESULT_BOUND,
            Facts(**DONE, receipt=None, result_artifacts=(_art(),), remote_branch_head=None, open_draft_pr=None),
            ("resume", None, None),
        ),
        (
            state.RESULT_BOUND,
            Facts(**DONE, receipt=UNKNOWN, remote_branch_head=None, open_draft_pr=None),
            ("noop", None, None),
        ),
        (
            state.RESULT_BOUND,
            Facts(
                **DONE,
                receipt=None,
                result_artifacts=(_art(expired=True),),
                remote_branch_head=None,
                open_draft_pr=None,
            ),
            ("fail", None, "RESULT_TRANSPORT_EXPIRED"),
        ),
        (
            state.RESULT_BOUND,
            Facts(**DONE, receipt={"ok": 1}, remote_branch_head="9a" * 20, open_draft_pr=None),
            ("freeze", None, "STATE_ROLLBACK_SUSPECTED"),
        ),
        (
            state.VALIDATED,
            Facts(**DONE, remote_branch_head="9a" * 20, remote_head_conforms=True, open_draft_pr=None),
            ("transition", state.PUBLISHED, None),
        ),
        (
            state.VALIDATED,
            Facts(**DONE, remote_branch_head="9a" * 20, remote_head_conforms=False, open_draft_pr=None),
            ("fail", None, "REMOTE_BRANCH_CONFLICT"),
        ),
        (
            state.VALIDATED,
            Facts(**DONE, remote_branch_head=None, result_artifacts=(_art(),), open_draft_pr=None),
            ("resume", None, None),
        ),
        (
            state.VALIDATED,
            Facts(
                stage_run_active=False,
                now="2026-10-04T17:00:00Z",
                remote_branch_head=None,
                result_artifacts=(_art(),),
                open_draft_pr=None,
            ),
            ("fail", None, "LIFECYCLE_DEADLINE_EXCEEDED"),
        ),
        (state.VALIDATED, Facts(**DONE, remote_branch_head=UNKNOWN, open_draft_pr=None), ("noop", None, None)),
        (
            state.VALIDATED,
            Facts(**DONE, remote_branch_head=None, open_draft_pr={"number": 1}),
            ("freeze", None, "STATE_ROLLBACK_SUSPECTED"),
        ),
        (
            state.PUBLISHED,
            Facts(**DONE, open_draft_pr={"number": 561}, preflight_conclusion="success"),
            ("transition", state.COMPLETED, None),
        ),
        (
            state.PUBLISHED,
            Facts(**DONE, open_draft_pr=None, preflight_conclusion="failure"),
            ("fail", None, "CANDIDATE_PREFLIGHT_FAILED"),
        ),
        (
            state.PUBLISHED,
            Facts(stage_run_active=False, now="2026-10-05T11:00:00Z", open_draft_pr=None, preflight_conclusion=None),
            ("fail", None, "CANDIDATE_PREFLIGHT_TIMEOUT"),
        ),
        (state.PUBLISHED, Facts(**DONE, open_draft_pr=UNKNOWN, preflight_conclusion=None), ("noop", None, None)),
        (state.COMPLETED, Facts(**DONE), ("noop", None, None)),
    ],
)
def test_advance_decision_table(until: str, facts: Facts, expected: tuple[str, str | None, str | None]) -> None:
    decision = state.advance(_view(until), facts)
    assert (decision.action, decision.target_state, decision.code) == expected


def test_completed_requires_a_successful_exact_head_preflight() -> None:
    chain = full_chain(state.PUBLISHED)
    failed = {
        "pull_request_number": 561,
        "pull_request_node_id": "PR_x",
        "pull_request_head_sha": "9a" * 20,
        "draft": True,
        "preflight_run_id": 77,
        "preflight_conclusion": "failure",
    }
    reject(chain, chain.make("transition", state.COMPLETED, failed, _by("candidate-pr-record")), "schema")
    view = chain.view.authorizations[AUTH]
    with_failed_preflight = Facts(**DONE, open_draft_pr={"number": 561}, preflight_conclusion="failure")
    assert (state.advance(view, with_failed_preflight).action, state.advance(view, with_failed_preflight).code) == (
        "fail",
        "CANDIDATE_PREFLIGHT_FAILED",
    )
    pending_preflight = Facts(**DONE, open_draft_pr={"number": 561}, preflight_conclusion=None)
    assert state.advance(view, pending_preflight).action == "noop"


def _pending(chain: Chain, stage: str = "validation", dispatched_at: str = "2026-10-04T11:00:00Z") -> Chain:
    current = chain.view.authorizations[AUTH].state
    attempt = (chain.view.authorizations[AUTH].resume_attempts or {}).get(stage, 0) + 1
    evidence = {"stage": stage, "nonce": f"{attempt}" * 64, "attempt": attempt, "dispatched_at": dispatched_at}
    return chain.add(chain.make("resume_requested", current, evidence, _by("reconcile")))


RESUMABLE = dict(
    receipt=None,
    result_artifacts=(ArtifactFact(22, "x", "sha256:" + "e" * 64, 1, False),),
    remote_branch_head=None,
    open_draft_pr=None,
)


@pytest.mark.parametrize(
    ("status", "now", "expected"),
    [
        (UNKNOWN, "2026-10-04T12:00:00Z", "noop"),
        ("active", "2026-10-04T12:00:00Z", "noop"),
        (None, "2026-10-04T11:05:00Z", "noop"),  # within the dispatch grace
        (None, "2026-10-04T12:00:00Z", "redispatch"),  # dispatch lost: same nonce, no attempt consumed
        ("concluded", "2026-10-04T12:00:00Z", "abandon_resume"),  # run ended without output
    ],
)
def test_a_pending_resume_never_stalls(status: Any, now: str, expected: str) -> None:
    view = _pending(full_chain(state.RESULT_BOUND)).view.authorizations[AUTH]
    facts = Facts(stage_run_active=False, now=now, resume_run_status=status, **RESUMABLE)
    assert state.advance(view, facts).action == expected


def test_an_abandoned_resume_consumes_its_attempt_and_the_cap_then_terminates() -> None:
    chain = _pending(full_chain(state.RESULT_BOUND))
    chain.add(
        chain.make(
            "resume_abandoned",
            state.RESULT_BOUND,
            {"nonce": "1" * 64, "reason": "run_concluded_without_output"},
            _by("reconcile"),
        )
    )
    view = chain.view.authorizations[AUTH]
    assert view.pending_resume is None and view.resume_attempts == {"validation": 1}
    decision = state.advance(view, Facts(stage_run_active=False, now="2026-10-04T12:00:00Z", **RESUMABLE))
    assert (decision.action, decision.code) == ("fail", "VALIDATION_UNAVAILABLE")
    publication = _pending(full_chain(state.VALIDATED), "publication")
    publication.add(
        publication.make(
            "resume_abandoned",
            state.VALIDATED,
            {"nonce": "1" * 64, "reason": "run_concluded_without_output"},
            _by("reconcile"),
        )
    )
    next_attempt = state.advance(
        publication.view.authorizations[AUTH], Facts(stage_run_active=False, now="2026-10-04T12:00:00Z", **RESUMABLE)
    )
    assert next_attempt.action == "resume"  # publication allows a second resume before its cap


def test_an_abandonment_must_name_the_pending_nonce() -> None:
    chain = _pending(full_chain(state.RESULT_BOUND))
    wrong = chain.make(
        "resume_abandoned",
        state.RESULT_BOUND,
        {"nonce": "9" * 64, "reason": "run_concluded_without_output"},
        _by("reconcile"),
    )
    reject(chain, wrong, "pending nonce")


def test_advance_caps_resumes_and_never_resumes_the_model() -> None:
    capped = _view(state.RESULT_BOUND, resume_attempts={"validation": 1})
    decision = state.advance(
        capped, Facts(**DONE, receipt=None, result_artifacts=(_art(),), remote_branch_head=None, open_draft_pr=None)
    )
    assert (decision.action, decision.code) == ("fail", "VALIDATION_UNAVAILABLE")
    for conclusion in ("failure", "cancelled", "timed_out", "success"):
        model = state.advance(
            _view(state.AUTHORIZED), Facts(**DONE, executor_conclusion=conclusion, result_artifacts=())
        )
        assert model.action != "resume"


def test_an_unknown_stage_run_status_is_never_treated_as_concluded() -> None:
    facts = Facts(stage_run_active=UNKNOWN, executor_conclusion="success", result_artifacts=(_art(),), now=DONE["now"])
    assert state.advance(_view(state.AUTHORIZED), facts).action == "noop"


def test_unknown_facts_never_produce_a_transition() -> None:
    for until in (state.AUTHORIZED, state.RESULT_BOUND, state.VALIDATED, state.PUBLISHED):
        decision = state.advance(_view(until), Facts(stage_run_active=False))
        assert decision.action == "noop", (until, decision)
