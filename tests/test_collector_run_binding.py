"""Adversarial tests for binding the collector capability to one GitHub run.

The dispatch nonce travels in a ``repository_dispatch`` payload, so any run that
obtains it is indistinguishable by payload, proof, title or status text. The only
unforgeable identity is the Actions run ID. These tests drive the real election,
the real collector start gate and the real publication boundary and assert that
the single-use capability can be consumed only by the run bound to it.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import fake_github_ownership as fake_gh
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
    return github, local


def reconcile(github, local, run_id):
    if run_id not in github.runs:
        github.add_orchestrator_run(run_id)
    local.run_id = run_id
    return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)


def intended(github, index=-1):
    payload = github.dispatches[index]["client_payload"]
    run_id = github.collector_runs()[index]["id"]
    return run_id, payload["dispatch_proof"], payload["recovery_epoch"]


def thief_run(github, copy_of, run_id=9001):
    """A later run that copied the payload of ``copy_of`` (same title, same proof)."""
    github.runs[run_id] = {**github.runs[copy_of], "id": run_id, "run_attempt": 1}
    return run_id


def start(github, run_id, proof, epoch, attempt=1):
    github.runs[run_id]["run_attempt"] = attempt
    collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, proof, run_id, epoch=epoch)


def bind_records(github):
    return [
        json.loads(github.tags[sha]["message"])
        for ref, sha in github.refs.items()
        if ref.startswith(orchestrator.RUN_BIND_REF_PREFIX)
    ]


def run_claims(github):
    return [ref for ref in github.refs if ref.startswith(orchestrator.RUN_CLAIM_REF_PREFIX)]


def test_dispatch_binds_the_capability_to_the_run_github_created(env):
    github, local = env
    reconcile(github, local, 101)
    run, _proof, _epoch = intended(github)
    (record,) = bind_records(github)
    assert record["run_id"] == run and record["epoch"] == 0 and record["orchestrator_run"] == 101


def test_early_competing_run_that_starts_first_cannot_consume_the_capability(env, monkeypatch):
    github, local = env
    # The orchestrator never lands its own binding (crash or listing lag).
    with monkeypatch.context() as no_bind:
        no_bind.setattr(orchestrator, "bind_dispatched_run", lambda *_a, **_k: None)
        reconcile(github, local, 101)
    run, proof, epoch = intended(github)
    assert not bind_records(github)
    thief = thief_run(github, run)
    # The competing run reaches the gate before the intended run is even started.
    with pytest.raises(ValueError, match="not bound to this run"):
        start(github, thief, proof, epoch)
    assert not run_claims(github), "the capability must stay unconsumed"
    # The intended run, started later, still owns the capability.
    start(github, run, proof, epoch)
    assert bind_records(github)[0]["run_id"] == run
    assert len(run_claims(github)) == 1


def test_replay_after_consumption_is_rejected_and_cannot_publish(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = intended(github)
    start(github, run, proof, epoch)
    thief = thief_run(github, run)
    with pytest.raises(ValueError, match="not bound to this run"):
        start(github, thief, proof, epoch)
    github.finish(101, "success")
    github.finish(thief, "success")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", thief, proof).startswith("SKIPPED")
    assert not [s for s in github.statuses if "Collector" in s["context"] and "Proof" not in s["context"]]
    github.finish(run, "success")
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")


def test_wrong_run_id_cannot_claim_even_with_a_valid_binding(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = intended(github)
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    for wrong in (run + 1, run - 1, 101, 0):
        with pytest.raises(orchestrator.CollectorAuthorityError, match="not bound"):
            orchestrator.claim_collector_run(REPO, "token", PR, HEAD, GEN, owner.epoch, owner.nonce_sha256, wrong, 1)
    assert not run_claims(github)


def test_claim_without_any_binding_fails_closed(env, monkeypatch):
    github, local = env
    reconcile(github, local, 101)
    run, _proof, _epoch = intended(github)
    github.refs = {ref: sha for ref, sha in github.refs.items() if not ref.startswith(orchestrator.RUN_BIND_REF_PREFIX)}
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    with pytest.raises(orchestrator.CollectorAuthorityError, match="not bound"):
        orchestrator.claim_collector_run(REPO, "token", PR, HEAD, GEN, owner.epoch, owner.nonce_sha256, run, 1)
    assert not run_claims(github)


def test_stale_epoch_run_cannot_use_its_binding_or_the_new_epochs(env):
    github, local = env
    reconcile(github, local, 101)
    old_run, old_proof, old_epoch = intended(github)
    github.finish(101, "success")
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)
    github.finish(old_run, "failure")
    reconcile(github, local, 102)
    new_run, new_proof, new_epoch = intended(github)
    assert new_epoch == "1"
    records = {r["epoch"]: r["run_id"] for r in bind_records(github)}
    assert records == {0: old_run, 1: new_run}
    # The old run is fenced by the newer epoch; the new capability is not usable by it.
    with pytest.raises(ValueError):
        start(github, old_run, old_proof, old_epoch)
    with pytest.raises(ValueError, match="not bound to this run"):
        start(github, old_run, new_proof, new_epoch)
    start(github, new_run, new_proof, new_epoch)


def test_a_late_run_of_an_earlier_epoch_is_never_eligible_for_the_next_binding(env):
    github, local = env
    reconcile(github, local, 101)
    old_run, _proof, _epoch = intended(github)
    github.finish(101, "success")
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)
    github.finish(old_run, "failure")
    # The old run's timestamp falls inside the new epoch's window (same-second skew).
    github.runs[old_run]["created_at"] = "2999-01-01T00:00:00Z"
    reconcile(github, local, 102)
    new_run = github.collector_runs()[-1]["id"]
    records = {r["epoch"]: r["run_id"] for r in bind_records(github)}
    assert records[0] == old_run and records[1] == new_run != old_run


def test_cross_generation_capability_has_no_binding(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = intended(github)
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, "other-gen", proof, run, epoch=epoch)
    assert {r["generation"] for r in bind_records(github)} == {GEN}
    assert not run_claims(github)


def test_concurrent_claims_admit_only_the_bound_run(env):
    github, local = env
    reconcile(github, local, 101)
    run, proof, epoch = intended(github)
    contenders = [run] + [thief_run(github, run, 9000 + i) for i in range(1, 6)]

    def attempt(run_id):
        try:
            start(github, run_id, proof, epoch)
            return run_id
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=len(contenders)) as pool:
        winners = [w for w in pool.map(attempt, contenders) if w is not None]
    assert winners == [run]
    assert len(run_claims(github)) == 1
    assert len(bind_records(github)) == 1


def test_decoy_dispatched_before_the_real_run_denies_service_but_never_authorizes(env):
    """Residual risk, fail closed: a decoy without the capability can win the binding.

    The decoy cannot start (it has no valid capability) and the bound-run mismatch
    blocks the intended run too, so no reviewer is invoked; recovery replaces the
    epoch only on authoritative evidence of failure.
    """
    github, local = env
    github.add_orchestrator_run(101)
    local.run_id = 101
    real_dispatch = github._dispatch

    def decoy_first(method, path, payload=None):
        if method == "POST" and path == "dispatches":
            github.next_run += 1
            github.runs[github.next_run] = {
                **fake_gh_run(github, payload),
                "id": github.next_run,
            }
        return real_dispatch(method, path, payload)

    github._dispatch = decoy_first
    orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)
    run, proof, epoch = intended(github)
    decoy = min(r["id"] for r in github.collector_runs())
    assert decoy < run and bind_records(github)[0]["run_id"] == decoy
    with pytest.raises(ValueError, match="not bound to this run"):
        start(github, run, proof, epoch)
    assert not run_claims(github)


def fake_gh_run(github, payload):
    client = payload["client_payload"]
    return {
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": fake_gh.TIP,
        "status": "in_progress",
        "conclusion": None,
        "created_at": fake_gh._now(),
        "run_attempt": 1,
        "display_title": orchestrator.collector_run_name(
            int(client["pr_number"]), client["head_sha"], client["generation_id"]
        ),
    }
