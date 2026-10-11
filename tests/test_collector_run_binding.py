"""Adversarial tests for binding the collector capability to the run that holds it.

A lookalike ``repository_dispatch`` run (matching title, workflow, time or an
earlier listing position) must never establish or block the binding. The binding
belongs to the earliest eligible run that proves possession of the epoch's
capability with an HMAC keyed by the dispatch nonce, published under a ref name
that contains the MAC itself. These drive the real election, the real collector
start gate, the real recovery decision and the real publication boundary against
the stateful GitHub model.
"""

import json
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
    monkeypatch.setattr(collector, "BIND_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(collector, "BIND_ATTEMPTS", 3)
    return github, local


def reconcile(github, local, run_id):
    if run_id not in github.runs:
        github.add_orchestrator_run(run_id)
    local.run_id = run_id
    return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)


def genuine(github):
    payload = github.dispatches[-1]["client_payload"]
    run_id = max(r["id"] for r in github.collector_runs() if r.get("genuine"))
    return run_id, payload["dispatch_proof"], payload["recovery_epoch"]


def tag_genuine_runs(github):
    """Mark every run the fake created from a real dispatch (the orchestrator's own)."""
    real = github._dispatch

    def dispatch(method, path, payload=None):
        result = real(method, path, payload)
        if method == "POST" and path == "dispatches":
            github.runs[github.next_run]["genuine"] = True
        return result

    github._dispatch = dispatch


def lookalike(github, *, status="completed", conclusion="failure", created_at=None, run_id=None):
    """A run with the cycle's exact title/workflow/branch, dispatched without the capability."""
    github.next_run += 1
    rid = run_id or github.next_run
    github.runs[rid] = {
        "id": rid,
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": fake_gh.TIP,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "created_at": created_at or fake_gh._now(),
        "run_attempt": 1,
        "display_title": orchestrator.collector_run_name(PR, HEAD, GEN),
    }
    return rid


def start(github, run_id, proof, epoch, attempt=1):
    github.runs[run_id]["run_attempt"] = attempt
    collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, proof, run_id, epoch=epoch)


def binds(github):
    return {
        json.loads(github.tags[sha]["message"])["epoch"]: json.loads(github.tags[sha]["message"])["run_id"]
        for ref, sha in github.refs.items()
        if ref.startswith(orchestrator.RUN_BIND_REF_PREFIX)
    }


def run_claims(github):
    return [ref for ref in github.refs if ref.startswith(orchestrator.RUN_CLAIM_REF_PREFIX)]


def dispatch_with_decoys_first(github, local, count=1, **decoy):
    """Decoys land after the trusted proof status but before the genuine run (lower ids)."""
    tag_genuine_runs(github)
    real = github._dispatch
    made = []

    def dispatch(method, path, payload=None):
        if method == "POST" and path == "dispatches":
            for _ in range(count):
                made.append(lookalike(github, **decoy))
        return real(method, path, payload)

    github._dispatch = dispatch
    reconcile(github, local, 101)
    return made


# 1 -------------------------------------------------------------------------------
def test_a_decoy_that_arrives_before_the_legitimate_run_cannot_win_the_binding(env):
    github, local = env
    (decoy,) = dispatch_with_decoys_first(github, local)
    run, proof, epoch = genuine(github)
    assert decoy < run
    start(github, run, proof, epoch)
    assert binds(github) == {0: run}
    assert len(run_claims(github)) == 1


def test_a_decoy_dispatched_before_the_proof_was_issued_is_never_a_contender(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, proof, epoch = genuine(github)
    # A still-running lookalike with a lower id that predates the proof can never hold the nonce.
    old = lookalike(github, status="in_progress", created_at="2000-01-01T00:00:00Z", run_id=run - 1000)
    assert old < run
    start(github, run, proof, epoch)  # no wait on a run that predates the capability
    assert binds(github) == {0: run}


# 2 -------------------------------------------------------------------------------
def test_a_decoy_that_arrives_after_the_legitimate_run_is_irrelevant(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, proof, epoch = genuine(github)
    decoy = lookalike(github, status="in_progress")
    assert decoy > run
    start(github, run, proof, epoch)
    assert binds(github) == {0: run}
    with pytest.raises(ValueError):
        start(github, decoy, "f" * 64, epoch)


# 3 -------------------------------------------------------------------------------
def test_concurrent_claimants_with_the_copied_capability_admit_only_the_genuine_run(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, proof, epoch = genuine(github)
    thieves = [lookalike(github, status="in_progress") for _ in range(5)]  # copied the payload, later ids

    def attempt(run_id):
        try:
            start(github, run_id, proof, epoch)
            return run_id
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        winners = [w for w in pool.map(attempt, [run, *thieves]) if w is not None]
    assert winners == [run]
    assert binds(github) == {0: run}
    assert len(run_claims(github)) == 1


def test_a_thief_that_starts_before_the_genuine_run_waits_and_then_loses(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, proof, epoch = genuine(github)
    github.runs[run]["status"] = "queued"  # the genuine run has not started yet
    thief = lookalike(github, status="in_progress")
    with pytest.raises(ValueError, match="could not be bound"):
        start(github, thief, proof, epoch)  # it may not take the capability from an earlier run
    assert not binds(github) and not run_claims(github)
    github.runs[run]["status"] = "in_progress"
    start(github, run, proof, epoch)
    assert binds(github) == {0: run}
    with pytest.raises(ValueError):
        start(github, thief, proof, epoch)


# 4 / 8 ---------------------------------------------------------------------------
def test_a_running_decoy_only_delays_and_the_legitimate_run_eventually_starts(env):
    github, local = env
    (decoy,) = dispatch_with_decoys_first(github, local, status="in_progress")
    run, proof, epoch = genuine(github)
    with pytest.raises(ValueError, match="could not be bound"):
        start(github, run, proof, epoch)  # bounded wait, fail closed, nothing consumed
    assert not binds(github) and not run_claims(github)
    github.finish(decoy, "failure")  # the decoy runs the trusted gate and fails it
    start(github, run, proof, epoch)
    assert binds(github) == {0: run}


def test_forged_possession_records_from_a_party_without_the_capability_are_ignored(env):
    github, local = env
    (decoy,) = dispatch_with_decoys_first(github, local, status="in_progress")
    run, proof, epoch = genuine(github)
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    # A party with tag-write access forges a possession record for the decoy...
    forged = orchestrator._possession_prefix(PR, HEAD, GEN, owner.epoch, decoy) + "0" * 16
    orchestrator._create_claim(
        REPO, "token", forged, {"v": 1, "kind": "possession", "epoch": 0, "run_id": decoy, "mac": "0" * 64}
    )
    # ...and squats on a name in the genuine run's possession namespace.
    squat = orchestrator._possession_prefix(PR, HEAD, GEN, owner.epoch, run) + "1" * 16
    orchestrator._create_claim(
        REPO, "token", squat, {"v": 1, "kind": "possession", "epoch": 0, "run_id": run, "mac": "1" * 64}
    )
    assert not orchestrator.run_holds_possession(REPO, "token", PR, HEAD, GEN, owner, decoy, proof)
    github.finish(decoy, "failure")
    start(github, run, proof, epoch)
    assert binds(github) == {0: run}
    assert orchestrator.run_holds_possession(REPO, "token", PR, HEAD, GEN, owner, run, proof)


# 5 / 6 ---------------------------------------------------------------------------
def test_an_old_capability_and_wrong_epoch_or_generation_are_rejected(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    old_run, old_proof, old_epoch = genuine(github)
    github.finish(101, "success")
    start(github, old_run, old_proof, old_epoch)
    github.finish(old_run, "failure")  # a claimed run that failed: genuine recovery evidence
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)
    reconcile(github, local, 102)
    new_run, new_proof, new_epoch = genuine(github)
    assert new_epoch == "1"
    with pytest.raises(ValueError):
        start(github, new_run, old_proof, old_epoch)  # replay of the old capability
    with pytest.raises(ValueError):
        start(github, new_run, new_proof, old_epoch)  # wrong epoch
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(
            REPO, "token", PR, HEAD, "other-gen", new_proof, new_run, epoch=new_epoch
        )
    start(github, new_run, new_proof, new_epoch)
    assert binds(github) == {0: old_run, 1: new_run}


# 7 -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "needle", ["GET git/matching-refs/*", "GET git/ref/tags/hunter-collector-bind/*", "POST git/refs"]
)
def test_missing_or_unreadable_binding_evidence_fails_closed(env, needle):
    github, local = env
    dispatch_with_decoys_first(github, local)  # an earlier contender whose possession must be read
    run, proof, epoch = genuine(github)
    github.fail[needle] = transport.GitHubRequestError("down", category="transient", status_code=502)
    with pytest.raises((transport.GitHubRequestError, ValueError)):
        start(github, run, proof, epoch)
    github.fail.clear()
    assert not run_claims(github)


def test_a_claimed_run_that_failed_is_still_recovery_evidence(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, proof, epoch = genuine(github)
    start(github, run, proof, epoch)
    github.finish(101, "success")
    github.finish(run, "failure")
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    assert orchestrator.owner_attempt_failed(REPO, "token", PR, HEAD, GEN, owner) is True


def test_a_claim_without_a_binding_fails_closed(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, _proof, _epoch = genuine(github)
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    with pytest.raises(orchestrator.CollectorAuthorityError, match="not bound"):
        orchestrator.claim_collector_run(REPO, "token", PR, HEAD, GEN, owner.epoch, owner.nonce_sha256, run, 1)
    assert not run_claims(github)


# 9 -------------------------------------------------------------------------------
def test_unauthenticated_decoys_cannot_consume_recovery_epochs(env):
    github, local = env
    tag_genuine_runs(github)
    reconcile(github, local, 101)
    run, _proof, _epoch = genuine(github)
    github.finish(101, "success")
    github.runs[run]["status"] = "queued"  # the genuine collector is slow to start
    for _ in range(5):  # a stream of failed lookalikes
        lookalike(github)
    github.runs[run]["status"] = "completed"
    github.runs[run]["conclusion"] = "cancelled"  # ...and, say, it was cancelled before its gate
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    # The recovery decision itself: failed lookalikes are not evidence of a failed attempt.
    assert orchestrator.owner_attempt_failed(REPO, "token", PR, HEAD, GEN, owner) is False
    for orchestrator_run in (102, 103, 104):
        reconcile(github, local, orchestrator_run)
    assert github.owner_epochs() == [0], "failed lookalikes are not evidence of a failed attempt"
    # Only the liveness grace (authoritative absence of a claimed run) releases the epoch.
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)
    reconcile(github, local, 105)
    assert github.owner_epochs() == [0, 1]


def test_documented_limitation_a_leaked_nonce_lets_a_tag_writer_deny_service_but_never_authorize(env):
    """The dispatch nonce is not secret: it is in the collector step env (public run
    logs) and in the public dispatch-proof artifact. An actor who also holds
    contents:write can then forge a valid possession record for a lookalike that was
    dispatched between proof issuance and the genuine run. This pins the residual:
    the genuine run is refused and nothing is consumed (denial of service), and the
    lookalike still gains no authority. Closing it needs an arbitration key that
    candidate code cannot read (an owner setting), not more code here.
    """
    github, local = env
    (decoy,) = dispatch_with_decoys_first(github, local, status="in_progress")
    run, proof, epoch = genuine(github)
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    orchestrator.publish_possession(REPO, "token", PR, HEAD, GEN, owner, decoy, proof)  # nonce read from public logs
    with pytest.raises(ValueError, match="earlier run holds"):
        start(github, run, proof, epoch)
    assert not binds(github) and not run_claims(github)
    with pytest.raises(orchestrator.CollectorAuthorityError):
        orchestrator.collector_run_authority(REPO, "token", dict(github.runs[decoy]))
