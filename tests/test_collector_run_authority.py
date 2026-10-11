"""Hostile tests for collector-run authority as recovery, reviewer and exhaustion evidence.

A completed successful run with the right title, workflow and time is not an
authorized collector. These drive the one canonical authority check
(``collector_run_authority``) through each privileged consumer -- recovery
(``owner_attempt_failed``), the exhaustion receipt loader (``load_exhaustion``)
and governance trigger acceptance (``trusted_collector_run``) -- against the
stateful GitHub model.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_governance_review_v2 as governance
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
PR = 472
GEN = orchestrator.BASE_GENERATION_ID
REPO = fake_gh.REPO
POOL = {"agents": ()}


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    monkeypatch.setattr(collector, "BIND_INTERVAL_SECONDS", 0)
    for module in (governance, collector.governance):
        monkeypatch.setattr(module, "request_json", github.request_json)
    monkeypatch.setattr(collector.orchestration, "request_json", github.request_json)
    return github, local


def reconcile(github, local, run_id):
    if run_id not in github.runs:
        github.add_orchestrator_run(run_id)
    local.run_id = run_id
    return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)


def cap(github, index=-1):
    payload = github.dispatches[index]["client_payload"]
    return github.collector_runs()[index]["id"], payload["dispatch_proof"], payload["recovery_epoch"]


def start(github, run_id, proof, epoch, attempt=1):
    github.runs[run_id]["run_attempt"] = attempt
    collector.verify_trusted_dispatch_before_work(REPO, "token", PR, HEAD, GEN, proof, run_id, epoch=epoch)


def authorized(github, local):
    """An orchestrator dispatched a collector that passed the gate and succeeded."""
    reconcile(github, local, 101)
    run, proof, epoch = cap(github)
    start(github, run, proof, epoch)
    github.finish(run, "success")
    return run, proof, epoch


def thief(github, copy_of, run_id=9001, **overrides):
    github.runs[run_id] = {**github.runs[copy_of], "id": run_id, "run_attempt": 1, **overrides}
    github.finish(run_id, "success")
    return run_id


def authority(github, run_id, **kwargs):
    return orchestrator.collector_run_authority(REPO, "token", dict(github.runs[run_id]), **kwargs)


def refs(github, prefix):
    return [ref for ref in github.refs if ref.startswith(prefix)]


def drop(github, prefix):
    github.refs = {ref: sha for ref, sha in github.refs.items() if not ref.startswith(prefix)}


def consumers(github, monkeypatch, run_id, *, head=HEAD):
    """Every privileged consumer of one run, as (name, accepted?) with the receipt never read."""
    results = {}
    monkeypatch.setattr(collector, "download_artifact", lambda *_a: pytest.fail("receipt consumed before authority"))
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])  # authority passed => receipt lookup reached
    monkeypatch.setattr(
        collector,
        "GitHubBackend",
        lambda *_a: type("B", (), {"head": lambda self: HEAD})(),
    )
    try:
        collector.load_exhaustion(REPO, "token", PR, head, POOL, run_id, "alternate")
    except ValueError as exc:
        results["load_exhaustion"] = "artifact" in str(exc)  # reached the receipt only if authority passed
    trigger = {
        "collector_run_id": run_id,
        "collector_run_attempt": github.runs[run_id]["run_attempt"],
        "head_sha": head,
    }
    results["trusted_collector_run"] = governance.trusted_collector_run(REPO, "token", trigger)
    return results


# --- the legitimate collector ------------------------------------------------------
def test_the_current_authorized_collector_is_accepted_by_every_consumer(env, monkeypatch):
    github, local = env
    run, _proof, _epoch = authorized(github, local)
    owner = authority(github, run, pr_number=PR, head_sha=HEAD, generation=GEN)
    assert owner.epoch == 0 and owner.orchestrator_run_id == 101
    assert consumers(github, monkeypatch, run) == {"load_exhaustion": True, "trusted_collector_run": True}
    assert orchestrator.collector_run_is_current(REPO, "token", PR, HEAD, github.runs[run])


# --- hostile runs ------------------------------------------------------------------
def test_legacy_successful_runs_are_refused(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    for event in ("workflow_dispatch", "pull_request_target"):
        legacy = thief(github, run, 9100 + len(event), event=event)
        with pytest.raises(orchestrator.CollectorAuthorityError):
            authority(github, legacy)
        assert not any(consumers(github, monkeypatch, legacy).values())


def test_spoofed_repository_dispatch_with_a_copied_payload_is_refused(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    spoof = thief(github, run)
    with pytest.raises(orchestrator.CollectorAuthorityError, match="not bound|run claim"):
        authority(github, spoof)
    assert not any(consumers(github, monkeypatch, spoof).values())
    assert not orchestrator.collector_run_is_current(REPO, "token", PR, HEAD, github.runs[spoof])


def test_a_missing_binding_is_refused(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    drop(github, orchestrator.RUN_BIND_REF_PREFIX)
    with pytest.raises(orchestrator.CollectorAuthorityError, match="not bound"):
        authority(github, run)
    assert not any(consumers(github, monkeypatch, run).values())


def test_a_missing_run_claim_is_refused(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    drop(github, orchestrator.RUN_CLAIM_REF_PREFIX)
    with pytest.raises(orchestrator.CollectorAuthorityError, match="run claim"):
        authority(github, run)
    assert not any(consumers(github, monkeypatch, run).values())
    assert not orchestrator.collector_run_is_current(REPO, "token", PR, HEAD, github.runs[run])


def test_a_wrong_run_attempt_is_refused(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    github.runs[run]["run_attempt"] = 2  # a rerun that never passed the start gate
    with pytest.raises(orchestrator.CollectorAuthorityError, match="run claim"):
        authority(github, run)
    assert not any(consumers(github, monkeypatch, run).values())


def test_a_stale_epoch_is_refused_and_the_new_epoch_accepted(env, monkeypatch):
    github, local = env
    reconcile(github, local, 101)
    old, old_proof, old_epoch = cap(github)
    start(github, old, old_proof, old_epoch)
    github.finish(101, "success")
    github.finish(old, "failure")
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)
    reconcile(github, local, 102)
    github.runs[old]["conclusion"] = "success"  # the fenced run is re-run and finishes green
    new, proof, epoch = cap(github)
    assert epoch == "1"
    start(github, new, proof, epoch)
    github.finish(new, "success")
    with pytest.raises(orchestrator.CollectorAuthorityError):
        authority(github, old)
    assert not any(consumers(github, monkeypatch, old).values())
    assert authority(github, new).epoch == 1


def test_a_wrong_pr_head_or_generation_is_refused(env):
    github, local = env
    run, _p, _e = authorized(github, local)
    for kwargs in ({"pr_number": PR + 1}, {"head_sha": "b" * 40}, {"generation": "other-gen"}):
        with pytest.raises(orchestrator.CollectorAuthorityError, match="another PR, head or generation"):
            authority(github, run, **kwargs)
    github.runs[run]["display_title"] = orchestrator.collector_run_name(PR, HEAD, "other-gen")
    with pytest.raises(orchestrator.CollectorAuthorityError):
        authority(github, run)  # a title naming a generation with no owner/claim is not authority


def test_a_cycle_with_no_owner_has_no_legacy_fallback(env, monkeypatch):
    github, local = env
    run, _p, _e = authorized(github, local)
    drop(github, orchestrator.OWNER_REF_PREFIX)
    with pytest.raises(orchestrator.CollectorAuthorityError, match="no elected owner"):
        authority(github, run)
    assert not orchestrator.collector_run_is_current(REPO, "token", PR, HEAD, github.runs[run])
    assert not any(consumers(github, monkeypatch, run).values())


def test_replay_of_the_authorized_run_by_another_run_id_is_refused(env, monkeypatch):
    github, local = env
    run, proof, epoch = authorized(github, local)
    replay = thief(github, run, 9002)
    with pytest.raises(ValueError):
        start(github, replay, proof, epoch)
    assert not any(consumers(github, monkeypatch, replay).values())
    assert authority(github, run).epoch == 0  # and the original is undisturbed


@pytest.mark.parametrize("needle", ["GET git/ref/*", "GET actions/runs/101", "GET commits/*"])
def test_unavailable_authority_evidence_fails_closed(env, monkeypatch, needle):
    github, local = env
    run, _p, _e = authorized(github, local)
    github.fail[needle] = transport.GitHubRequestError("down", category="transient", status_code=502)
    with pytest.raises((transport.GitHubRequestError, orchestrator.CollectorAuthorityError, ValueError)):
        authority(github, run)
    trigger = {"collector_run_id": run, "collector_run_attempt": 1, "head_sha": HEAD}
    assert governance.trusted_collector_run(REPO, "token", trigger) is False


# --- recovery (owner_attempt_failed) -------------------------------------------------
def recover_ready(github):
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)


def test_a_spoofed_success_cannot_suppress_legitimate_recovery(env):
    github, local = env
    reconcile(github, local, 101)
    real, _p, _e = cap(github)
    github.finish(101, "success")
    github.finish(real, "failure")
    thief(github, real)  # same title, workflow and a fresh timestamp, completed green
    recover_ready(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0, 1] and len(github.dispatches) == 2


def test_an_authorized_success_still_prevents_unsafe_re_election(env):
    github, local = env
    authorized(github, local)
    github.finish(101, "success")
    recover_ready(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0] and len(github.dispatches) == 1


def test_a_stale_owner_success_does_not_protect_the_new_epoch(env):
    github, local = env
    old, _p, _e = authorized(github, local)  # epoch 0 succeeded...
    owner0 = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[0]
    github.runs[old]["status"], github.runs[old]["conclusion"] = "completed", "success"
    github.finish(101, "success")
    # ...but is evaluated against a different owner (as a later epoch would be).
    other = orchestrator.CollectorOwner(1, 777, "f" * 64, owner0.claimed_at)
    github.add_orchestrator_run(777, "completed", "success")
    assert not orchestrator._run_holds_owner_authority(REPO, "token", PR, HEAD, GEN, other, github.runs[old])


def test_unreadable_authority_evidence_never_authorizes_recovery(env):
    github, local = env
    reconcile(github, local, 101)
    real, _p, _e = cap(github)
    github.finish(101, "success")
    github.finish(real, "failure")
    thief(github, real)
    recover_ready(github)
    github.fail["GET git/ref/tags/hunter-collector-bind/*"] = transport.GitHubRequestError(
        "down", category="transient", status_code=502
    )
    try:
        reconcile(github, local, 102)
    except (transport.GitHubRequestError, ValueError):
        pass  # failing closed by raising or by standing down are both acceptable
    assert github.owner_epochs() == [0] and len(github.dispatches) == 1


def test_concurrent_recovery_elects_exactly_one_owner_despite_a_spoofed_success(env, monkeypatch):
    github, local = env
    reconcile(github, local, 101)
    real, _p, _e = cap(github)
    github.finish(101, "success")
    github.finish(real, "failure")
    thief(github, real)
    recover_ready(github)
    github.add_orchestrator_run(102)
    github.add_orchestrator_run(103)
    start_gate = threading.Barrier(2, timeout=30)
    first_read = threading.local()
    real_read_cycle = orchestrator.read_cycle

    def read_cycle_once_both_have_started(*args, **kwargs):
        result = real_read_cycle(*args, **kwargs)
        if not getattr(first_read, "done", False):
            first_read.done = True
            start_gate.wait()
        return result

    monkeypatch.setattr(orchestrator, "read_cycle", read_cycle_once_both_have_started)
    gate = threading.Barrier(2, timeout=30)
    original = orchestrator.owner_attempt_failed

    def concurrent_recovery(*args):
        failed = original(*args)
        gate.wait()
        return failed

    monkeypatch.setattr(orchestrator, "owner_attempt_failed", concurrent_recovery)

    def contender(run_id):
        local.run_id = run_id
        return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(contender, (102, 103)))
    assert github.owner_epochs() == [0, 1]
    assert len(github.dispatches) == 2
