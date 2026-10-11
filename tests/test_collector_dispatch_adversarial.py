"""Adversarial ownership, recovery and fencing tests for the review collector.

These drive the real election (``ensure_collector`` -> ``elect_collector_owner`` ->
``dispatch_collector``), the real collector start gate and the real trusted
completion boundary against a stateful GitHub model whose ``git/refs`` creation is
create-only and atomic. Nothing here substitutes a helper for the election.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
PR = 472
GEN = orchestrator.BASE_GENERATION_ID
REPO = fake_gh.REPO


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    return github, local


def reconcile(github, local, run_id, *, register=True):
    if register and run_id not in github.runs:
        github.add_orchestrator_run(run_id)
    local.run_id = run_id
    return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)


def capability(github, index=-1):
    payload = github.dispatches[index]["client_payload"]
    run_id = [r for r in github.collector_runs()][index]["id"]
    return run_id, payload["dispatch_proof"], payload["recovery_epoch"]


def start_collector(github, run_id, proof, epoch, attempt=1):
    github.runs[run_id]["run_attempt"] = attempt
    collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, proof, run_id, epoch=epoch)


def fail_collector(github, run_id):
    github.finish(run_id, "failure")


def recover_ready(github):
    """Age the claim and the pending cycle past the liveness grace."""
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)


# 1 ---------------------------------------------------------------------------
def test_simultaneous_reconciles_with_different_nonces_authorize_exactly_one_dispatch(env, monkeypatch):
    github, local = env
    github.add_orchestrator_run(101)
    github.add_orchestrator_run(102)
    github.ref_gate = threading.Barrier(2)  # both contenders reach the ref write together
    nonces = iter(["1" * 64, "2" * 64])
    nonce_lock = threading.Lock()

    def distinct_nonce(_n):
        with nonce_lock:
            return next(nonces)

    monkeypatch.setattr(orchestrator.secrets, "token_hex", distinct_nonce)

    def contender(run_id):
        local.run_id = run_id
        return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, (101, 102)))

    assert len(github.dispatches) == 1
    assert len(github.proofs()) == 1
    assert github.owner_epochs() == [0]
    assert len(github.collector_runs()) == 1
    # The loser published no independent record: the one cycle is the winner's.
    winner = github.cycle.trigger_id
    assert winner in {101, 102}
    assert {r.trigger_id for r in results if r is not None} <= {winner}
    assert github.cycle_publications == 1


def test_same_contender_reconciling_twice_never_dispatches_twice(env):
    github, local = env
    reconcile(github, local, 101)
    reconcile(github, local, 101)
    assert len(github.dispatches) == 1


# 2 ---------------------------------------------------------------------------
def test_winner_crash_before_proof_publication_is_recovered_once(env):
    github, local = env
    github.fail["POST statuses/" + HEAD] = fake_gh.Crash()
    with pytest.raises(fake_gh.Crash):
        reconcile(github, local, 101)
    assert github.owner_epochs() == [0] and not github.proofs() and not github.dispatches
    github.fail.clear()
    # The owner is still alive: no replacement, however old the claim looks.
    recover_ready(github)
    reconcile(github, local, 102)
    assert not github.dispatches
    github.finish(101)  # process death observed by the Actions API
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0, 1]
    assert len(github.dispatches) == 1
    assert github.dispatches[0]["client_payload"]["recovery_epoch"] == "1"


# 3 ---------------------------------------------------------------------------
def test_winner_crash_after_proof_before_dispatch_is_recovered_once(env):
    github, local = env
    github.fail["POST dispatches"] = fake_gh.Crash()
    with pytest.raises(fake_gh.Crash):
        reconcile(github, local, 101)
    assert len(github.proofs()) == 1 and not github.dispatches
    github.fail.clear()
    github.finish(101)
    recover_ready(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0, 1]
    assert len(github.dispatches) == 1
    # The dead epoch's proof stays on the head but is fenced: it names epoch 0.
    assert len(github.proofs()) == 2


# 4 ---------------------------------------------------------------------------
def test_ambiguous_dispatch_failure_neither_retries_nor_deadlocks(env):
    github, local = env
    original = github._dispatch

    def accepted_then_timeout(method, path, payload):
        result = original(method, path, payload)
        if method == "POST" and path == "dispatches":
            raise transport.GitHubRequestError("timed out after accept", category="transport")
        return result

    github._dispatch = accepted_then_timeout
    with pytest.raises(transport.GitHubRequestError):
        reconcile(github, local, 101)
    github._dispatch = original
    assert len(github.dispatches) == 1
    github.finish(101)
    recover_ready(github)
    # GitHub did accept it: a live collector exists, so nothing else may start.
    reconcile(github, local, 102)
    assert len(github.dispatches) == 1 and github.owner_epochs() == [0]


def test_ambiguous_dispatch_that_was_lost_is_replaced_only_after_owner_ends(env):
    github, local = env
    github.fail["POST dispatches"] = transport.GitHubRequestError("500", category="transient", status_code=500)
    with pytest.raises(transport.GitHubRequestError):
        reconcile(github, local, 101)
    github.fail.clear()
    recover_ready(github)
    reconcile(github, local, 102)
    assert not github.dispatches  # owner 101 not yet proven ended
    github.finish(101)
    reconcile(github, local, 102)
    assert len(github.dispatches) == 1 and github.owner_epochs() == [0, 1]


# 5 ---------------------------------------------------------------------------
def test_live_owner_with_a_running_collector_cannot_be_replaced(env):
    github, local = env
    reconcile(github, local, 101)
    github.finish(101, "success")
    recover_ready(github)  # old, but its collector is still running
    reconcile(github, local, 102)
    assert len(github.dispatches) == 1 and github.owner_epochs() == [0]


def test_timeout_alone_never_replaces_a_live_owner_run(env):
    github, local = env
    github.fail["POST dispatches"] = fake_gh.Crash()
    with pytest.raises(fake_gh.Crash):
        reconcile(github, local, 101)
    github.fail.clear()
    recover_ready(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0] and not github.dispatches


# 6 ---------------------------------------------------------------------------
def test_proven_failed_owner_is_replaced_exactly_once(env):
    github, local = env
    reconcile(github, local, 101)
    first_run, _, _ = capability(github)
    github.finish(101, "success")
    fail_collector(github, first_run)
    recover_ready(github)
    reconcile(github, local, 102)
    reconcile(github, local, 102)
    reconcile(github, local, 103)
    assert github.owner_epochs() == [0, 1]
    assert len(github.dispatches) == 2


# 7 ---------------------------------------------------------------------------
def test_two_simultaneous_recovery_contenders_elect_exactly_one_replacement(env, monkeypatch):
    github, local = env
    reconcile(github, local, 101)
    run, _, _ = capability(github)
    github.finish(101, "success")
    fail_collector(github, run)
    recover_ready(github)
    github.add_orchestrator_run(102)
    github.add_orchestrator_run(103)
    github.ref_gate = threading.Barrier(2)

    def contender(run_id):
        local.run_id = run_id
        return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(contender, (102, 103)))

    assert github.owner_epochs() == [0, 1]
    assert len(github.dispatches) == 2
    assert github.dispatches[1]["client_payload"]["recovery_epoch"] == "1"
    assert len(github.proofs()) == 2


# 8 ---------------------------------------------------------------------------
def test_old_owner_resuming_after_replacement_is_rejected_at_start_and_publication(env):
    github, local = env
    reconcile(github, local, 101)
    old_run, old_proof, old_epoch = capability(github)
    start_collector(github, old_run, old_proof, old_epoch)  # it did start once
    github.finish(101, "success")
    recover_ready(github)
    fail_collector(github, old_run)
    reconcile(github, local, 102)
    new_run, new_proof, new_epoch = capability(github)
    assert new_epoch == "1"

    # The stale run is re-run after the replacement: fenced before any work.
    github.attempts[(old_run, 1)] = {"run_attempt": 1, "status": "completed", "conclusion": "failure"}
    with pytest.raises(ValueError):
        start_collector(github, old_run, old_proof, old_epoch, attempt=2)

    # Even a stale success cannot be published once its epoch is replaced.
    github.runs[old_run]["status"] = "completed"
    github.runs[old_run]["conclusion"] = "success"
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", old_run, old_proof)
    assert outcome.startswith("SKIPPED")
    assert not [s for s in github.statuses if "Collector" in s["context"] and "Proof" not in s["context"]]

    # The replacement is the sole valid owner.
    start_collector(github, new_run, new_proof, new_epoch)


# 9 ---------------------------------------------------------------------------
def test_replayed_proof_on_another_run_is_rejected(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch)
    # An attacker with dispatch rights replays the readable capability.
    github.dispatches.append(github.dispatches[0])
    github.runs[9001] = {**github.runs[run], "id": 9001, "run_attempt": 1}
    with pytest.raises(ValueError, match="already consumed"):
        start_collector(github, 9001, proof, epoch)
    # The same run attempt cannot start twice either.
    with pytest.raises(ValueError):
        start_collector(github, run, proof, epoch)


def test_rerun_of_a_failed_attempt_uses_the_attempt_endpoint_and_is_bounded(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch, attempt=1)
    # Attempt 1 has not ended unsuccessfully yet: no attempt 2.
    github.attempts[(run, 1)] = {"run_attempt": 1, "status": "in_progress", "conclusion": None}
    with pytest.raises(ValueError):
        start_collector(github, run, proof, epoch, attempt=2)
    github.attempts[(run, 1)] = {"run_attempt": 1, "status": "completed", "conclusion": "failure"}
    start_collector(github, run, proof, epoch, attempt=2)
    assert ("GET", f"actions/runs/{run}/attempts/1") in github.calls
    assert not any(path.endswith("/attempts") for _, path in github.calls)
    github.attempts[(run, 2)] = {"run_attempt": 2, "status": "completed", "conclusion": "failure"}
    start_collector(github, run, proof, epoch, attempt=3)
    github.attempts[(run, 3)] = {"run_attempt": 3, "status": "completed", "conclusion": "failure"}
    with pytest.raises(ValueError):
        start_collector(github, run, proof, epoch, attempt=4)


def test_cross_generation_and_cross_head_capabilities_are_rejected(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, "other-gen", proof, run, epoch=epoch)
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(REPO, "token", PR, "b" * 40, GEN, proof, run, epoch=epoch)
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, "c" * 64, run, epoch=epoch)
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, proof, run, epoch="1")


# 10 --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "needle",
    ["GET git/ref/*", "POST git/refs", "POST git/tags", "GET commits/main", "POST statuses/*", "POST dispatches"],
)
@pytest.mark.parametrize("status,category", [(500, "transient"), (403, "permanent"), (None, "transport")])
def test_unavailable_github_evidence_fails_closed(env, needle, status, category):
    github, local = env
    github.fail[needle] = transport.GitHubRequestError("down", category=category, status_code=status)
    with pytest.raises((transport.GitHubRequestError, ValueError, RuntimeError)):
        reconcile(github, local, 101)
    # Nothing is dispatched on unverifiable evidence; only a dispatch API failure
    # can follow a successful (single) election.
    assert len(github.dispatches) == 0
    assert len(github.owner_epochs()) <= 1


def test_unreadable_owner_run_evidence_never_authorizes_recovery(env):
    github, local = env
    reconcile(github, local, 101)
    run, _, _ = capability(github)
    github.finish(101, "success")
    fail_collector(github, run)
    recover_ready(github)
    github.fail["GET actions/runs/101"] = transport.GitHubRequestError("500", category="transient", status_code=500)
    with pytest.raises(transport.GitHubRequestError):
        reconcile(github, local, 102)
    assert github.owner_epochs() == [0] and len(github.dispatches) == 1


def test_malformed_claim_is_not_treated_as_unowned(env):
    github, local = env
    reconcile(github, local, 101)
    fail_collector(github, capability(github)[0])
    github.finish(101)
    recover_ready(github)
    ref = next(iter(github.refs))
    github.tags[github.refs[ref]]["message"] = "not-json"
    with pytest.raises(ValueError):
        reconcile(github, local, 102)
    assert len(github.dispatches) == 1


# 11 --------------------------------------------------------------------------
def test_successful_completion_blocks_recovery(env):
    github, local = env
    reconcile(github, local, 101)
    run, _, _ = capability(github)
    github.finish(101, "success")
    github.finish(run, "success")
    recover_ready(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0] and len(github.dispatches) == 1


def test_recovery_between_success_check_and_publication_fences_the_publication(env, monkeypatch):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    real_read = orchestrator.read_owner_chain
    calls = {"n": 0}

    def replaced_after_first_check(*args):
        chain = real_read(*args)
        calls["n"] += 1
        if calls["n"] == 1:
            # A recovery lands right after the first authority check.
            record = {
                "v": 1,
                "kind": "owner",
                "pr": PR,
                "head": HEAD,
                "generation": GEN,
                "epoch": 1,
                "orchestrator_run": 102,
                "nonce_sha256": "9" * 64,
                "claimed_at": "2026-10-10T10:00:05Z",
            }
            orchestrator._create_claim(REPO, "token", orchestrator.collector_owner_ref(PR, HEAD, GEN, 1), record)
        return chain

    monkeypatch.setattr(orchestrator, "read_owner_chain", replaced_after_first_check)
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("SKIPPED")
    assert not [s for s in github.statuses if "Collector" in s["context"] and "Proof" not in s["context"]]


def test_valid_current_owner_completion_is_published(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("PUBLISHED")


def test_completion_without_a_run_claim_is_rejected(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, _ = capability(github)
    github.finish(101, "success")
    github.finish(run, "success")  # never passed the start gate: no run claim
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("SKIPPED")


# 12 --------------------------------------------------------------------------
def test_recovery_is_bounded_and_never_loops_dispatching(env):
    github, local = env
    for round_number in range(1, 12):
        orchestrator_run = 100 + round_number
        reconcile(github, local, orchestrator_run)
        for run in github.collector_runs():
            if run["status"] != "completed":
                fail_collector(github, run["id"])
        github.finish(orchestrator_run, "success")
        recover_ready(github)
    assert len(github.dispatches) == orchestrator.MAX_COLLECTOR_DISPATCHES
    assert github.owner_epochs() == list(range(orchestrator.MAX_COLLECTOR_DISPATCHES))
    assert len(github.proofs()) == orchestrator.MAX_COLLECTOR_DISPATCHES


# Authority shape -----------------------------------------------------------------
def test_owner_ref_is_independent_of_the_dispatch_nonce():
    ref = orchestrator.collector_owner_ref(PR, HEAD, GEN, 0)
    assert ref == orchestrator.collector_owner_ref(PR, HEAD, GEN, 0)
    assert ref != orchestrator.collector_owner_ref(PR, HEAD, GEN, 1)
    assert ref != orchestrator.collector_owner_ref(PR, "b" * 40, GEN, 0)
    assert ref != orchestrator.collector_owner_ref(PR, HEAD, "other" * 12, 0)
    assert ref != orchestrator.collector_owner_ref(PR + 1, HEAD, GEN, 0)


def test_status_without_an_owner_claim_is_not_ownership(env):
    """A forged trusted-looking status alone cannot authorize a collector."""
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    github.refs.clear()
    with pytest.raises(ValueError):
        start_collector(github, run, proof, epoch)


def test_completion_with_a_wrong_capability_is_skipped(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, "c" * 64).startswith("SKIPPED")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, "").startswith("SKIPPED")


@pytest.mark.parametrize("override", [{"head_branch": "attacker"}, {"conclusion": "failure"}, {}])
def test_untrusted_lookalike_run_neither_starts_nor_blocks_the_legitimate_collector(env, override):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    lookalike = {**github.runs[run], "id": 7777, "created_at": "2026-10-10T10:00:00Z", **override}
    lookalike["status"] = "completed"
    github.runs[7777] = lookalike
    with pytest.raises(ValueError):
        start_collector(github, 7777, "e" * 64, epoch)
    if override.get("head_branch"):
        with pytest.raises(ValueError):
            start_collector(github, 7777, proof, epoch)
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")


def test_stale_status_read_never_lets_a_non_owner_republish_the_cycle(env, monkeypatch):
    """A second reconcile that sees the live collector but a stale (absent) status
    read must not overwrite the cycle's trigger with its own run: the owner ref is
    the single authority and the start gate and completion both compare against it."""
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    publications = github.cycle_publications
    with monkeypatch.context() as stale:
        stale.setattr(orchestrator, "read_cycle", lambda *_a: ("absent", None, None))  # stale status read
        reconcile(github, local, 102)
    assert github.cycle_publications == publications
    assert github.cycle.trigger_id == 101
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")


def _finished_current_owner(github, local):
    reconcile(github, local, 101)
    run, proof, epoch = capability(github)
    start_collector(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "success")
    return run, proof


def test_completion_for_another_head_or_generation_is_never_published(env):
    github, local = env
    run, proof = _finished_current_owner(github, local)
    title = github.runs[run]["display_title"]
    github.runs[run]["display_title"] = title.replace(HEAD, "b" * 40)  # cross-head
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("SKIPPED")
    github.runs[run]["display_title"] = title + " GEN other-generation"  # cross-generation
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("SKIPPED")
    assert not [s for s in github.statuses if "Proof" not in s["context"]]


@pytest.mark.parametrize("needle", ["GET git/ref/*", "GET git/tags/*", "GET actions/runs/101"])
def test_completion_fails_closed_when_authority_is_unreadable(env, needle):
    github, local = env
    run, proof = _finished_current_owner(github, local)
    github.fail[needle] = transport.GitHubRequestError("down", category="transient", status_code=500)
    with pytest.raises((transport.GitHubRequestError, ValueError)):
        orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert not [s for s in github.statuses if "Proof" not in s["context"]]


def test_collector_workflow_binds_the_elected_capability_and_has_no_status_grant():
    import pathlib

    import yaml

    path = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/hunter-reviewer-collector.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(workflow[True]) == {"repository_dispatch"}
    permissions = workflow["permissions"]
    assert "statuses" not in permissions and "checks" not in permissions
    assert permissions["contents"] == "write"  # create-only claim refs
    env = {}
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            env.update(step.get("env") or {})
    assert env["COLLECTOR_DISPATCH_PROOF"] == "${{ github.event.client_payload.dispatch_proof }}"
    assert env["COLLECTOR_RECOVERY_EPOCH"] == "${{ github.event.client_payload.recovery_epoch }}"
