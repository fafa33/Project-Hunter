"""Stale completion-claim takeover must admit exactly one publisher.

A publisher that dies after winning the first create-only claim must not strand a
completion, but acting on the stale record itself would let every simultaneous
recoverer publish. Recovery therefore goes through the next create-only claim of
a bounded chain. These tests drive the production publication path against the
stateful GitHub model, whose ref creation is atomic.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import pytest
from test_collector_recovery_queue import head_for, stage

REPO = fake_gh.REPO
PR = 472
STALE = orchestrator.COMPLETION_CLAIM_STALE_SECONDS + 60


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    return github, local


def completions(github):
    return [
        s
        for s in github.statuses
        if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX) and s["state"] == "success"
    ]


def claim_refs(github):
    return sorted(ref for ref in github.refs if ref.startswith("tags/hunter-collector-completion/"))


def publish(run, proof):
    return orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)


def crash_on_status_write(monkeypatch):
    def dies(*_a, **_k):
        raise transport.GitHubRequestError("status write failed", category="transient", status_code=502)

    monkeypatch.setattr(orchestrator, "publish_collector_completion", dies)


def attempt_publish(run, proof):
    try:
        return publish(run, proof)
    except transport.GitHubRequestError:
        return "CRASHED"


def test_simultaneous_recoverers_of_a_stale_claim_publish_exactly_once(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    with monkeypatch.context() as m:
        crash_on_status_write(m)
        assert attempt_publish(run, proof) == "CRASHED"  # first claimant dies holding the claim
    assert len(claim_refs(github)) == 1 and not completions(github)
    github.age_completion_claims(STALE)

    contenders = 6
    gate = threading.Barrier(contenders)
    seen = threading.local()
    real = orchestrator.read_collector_completion

    def synchronized(*args, **kwargs):
        result = real(*args, **kwargs)
        if not getattr(seen, "first", False):  # every contender has read "absent" before any proceeds
            seen.first = True
            gate.wait(timeout=10)
        return result

    monkeypatch.setattr(orchestrator, "read_collector_completion", synchronized)
    with ThreadPoolExecutor(max_workers=contenders) as pool:
        outcomes = list(pool.map(lambda _i: attempt_publish(run, proof), range(contenders)))
    assert sum(o.startswith("PUBLISHED") for o in outcomes) == 1, outcomes
    assert len(completions(github)) == 1
    assert len(claim_refs(github)) == 2  # the original claim and exactly one takeover


def test_recovery_claim_contention_has_one_winner_and_losers_do_not_publish(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    cycle_args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    first, held = orchestrator.claim_completion_publication(*cycle_args)
    assert first is None and held is not None
    github.age_completion_claims(STALE)
    gate = threading.Barrier(5)
    real_create = orchestrator._create_claim

    def synchronized(repository, token, ref, record):
        gate.wait(timeout=10)  # all five reach the create-only takeover write together
        return real_create(repository, token, ref, record)

    orchestrator._create_claim, restore = synchronized, orchestrator._create_claim
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _i: orchestrator.claim_completion_publication(*cycle_args), range(5)))
    finally:
        orchestrator._create_claim = restore
    winners = [r for r in results if r[0] is None]
    assert len(winners) == 1 and winners[0][1]["takeover"] == 1
    assert all(r[0].startswith("SKIPPED") for r in results if r[0] is not None)


def test_fresh_claim_is_never_taken_over(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    cycle_args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    assert orchestrator.claim_completion_publication(*cycle_args)[0] is None
    assert orchestrator.claim_completion_publication(*cycle_args)[0].startswith("SKIPPED")
    assert len(claim_refs(github)) == 1


def test_repeated_publisher_crashes_never_duplicate_and_are_bounded(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    with monkeypatch.context() as m:
        crash_on_status_write(m)
        for _ in range(orchestrator.COMPLETION_MAX_TAKEOVERS + 1):  # the claim and every takeover die
            assert attempt_publish(run, proof) == "CRASHED"
            github.age_completion_claims(STALE)
    assert not completions(github)
    assert len(claim_refs(github)) == orchestrator.COMPLETION_MAX_TAKEOVERS + 1
    # Exhausted: fail closed, no further claim and no status, however many sweeps retry.
    for _ in range(3):
        assert "exhausted" in publish(run, proof)
    assert not completions(github)
    assert len(claim_refs(github)) == orchestrator.COMPLETION_MAX_TAKEOVERS + 1


def test_repeated_crashes_then_a_surviving_publisher_publishes_once(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    with monkeypatch.context() as m:
        crash_on_status_write(m)
        for _ in range(orchestrator.COMPLETION_MAX_TAKEOVERS):
            assert attempt_publish(run, proof) == "CRASHED"
            github.age_completion_claims(STALE)
    assert publish(run, proof).startswith("PUBLISHED")
    assert len(completions(github)) == 1


def test_replay_after_publication_never_publishes_again(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    assert publish(run, proof).startswith("PUBLISHED")
    github.age_completion_claims(STALE)
    for _ in range(3):
        assert publish(run, proof).startswith("SKIPPED")
    assert len(completions(github)) == 1
    assert len(claim_refs(github)) == 1


def test_a_stalled_publisher_superseded_by_a_takeover_writes_nothing(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    _refusal, stalled = orchestrator.claim_completion_publication(*args)  # claims, then stalls
    assert orchestrator.completion_claim_is_current(*args, stalled)
    github.age_completion_claims(STALE)
    assert publish(run, proof).startswith("PUBLISHED")  # the takeover winner publishes
    assert not orchestrator.completion_claim_is_current(*args, stalled)  # the stalled one is fenced
    assert len(completions(github)) == 1


def test_takeover_requires_the_completion_to_be_absent(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    assert orchestrator.claim_completion_publication(*args)[0] is None
    orchestrator.publish_collector_completion(REPO, "token", PR, head_for(PR), run)  # the "dead" one did publish
    github.age_completion_claims(STALE)
    assert orchestrator.claim_completion_publication(*args)[0].startswith("SKIPPED")
    assert len(claim_refs(github)) == 1


@pytest.mark.parametrize("needle", ["GET git/ref/tags/hunter-collector-completion/*", "POST git/refs", "POST git/tags"])
def test_unreadable_evidence_during_takeover_fails_closed(env, needle):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    assert orchestrator.claim_completion_publication(*args)[0] is None
    github.age_completion_claims(STALE)
    github.fail[needle] = transport.GitHubRequestError("down", category="transient", status_code=502)
    assert publish(run, proof).startswith("SKIPPED")
    github.fail.clear()
    assert not completions(github)
    assert len(claim_refs(github)) == 1


@pytest.mark.parametrize("claimed_at", ["not-a-time", ""])
def test_a_claim_with_an_unparseable_time_is_never_stale(env, claimed_at):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    assert orchestrator.claim_completion_publication(*args)[0] is None
    for tag in github.tags.values():
        record = json.loads(tag["message"])
        if record.get("kind") == "completion":
            record["claimed_at"] = claimed_at
            tag["message"] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert publish(run, proof).startswith("SKIPPED")
    assert not completions(github) and len(claim_refs(github)) == 1


def test_a_claim_for_another_run_is_ambiguous_and_fails_closed(env):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    args = (REPO, "token", PR, head_for(PR), orchestrator.BASE_GENERATION_ID, run)
    assert orchestrator.claim_completion_publication(*args)[0] is None
    for tag in github.tags.values():
        record = json.loads(tag["message"])
        if record.get("kind") == "completion":
            record["run_id"] = run + 1
            tag["message"] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    github.age_completion_claims(STALE)
    assert publish(run, proof).startswith("SKIPPED")
    assert not completions(github)


def test_a_publisher_whose_lease_expired_before_the_write_writes_nothing(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    real_fence = orchestrator.completion_claim_is_current

    def paused_past_the_lease(*args, **kwargs):
        github.age_completion_claims(orchestrator.COMPLETION_PUBLISH_LEASE_SECONDS + 5)  # a pause before the check
        return real_fence(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "completion_claim_is_current", paused_past_the_lease)
    assert publish(run, proof).startswith("SKIPPED")
    assert not completions(github)
    assert orchestrator.COMPLETION_PUBLISH_LEASE_SECONDS < orchestrator.COMPLETION_CLAIM_STALE_SECONDS


def test_documented_limitation_a_pause_between_fence_and_write_can_duplicate_an_identical_status(env, monkeypatch):
    """GitHub statuses have no conditional create, so this window is narrowed, not closed.

    The paused publisher passes its fence, stalls past the staleness bound, a takeover
    publishes, and the original then writes. The result is two statuses with the
    identical identity; readers see one completion for one run, so authority is
    unchanged. This test pins that the residual is exactly that and nothing more.
    """
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    real_write = orchestrator.publish_collector_completion
    state = {"nested": False}

    def stall_then_write(*args, **kwargs):
        if not state["nested"]:
            state["nested"] = True
            github.age_completion_claims(STALE)  # the original stalls past the staleness bound
            assert publish(run, proof).startswith("PUBLISHED")  # a takeover publishes meanwhile
        return real_write(*args, **kwargs)  # ...then the stalled original resumes and writes

    monkeypatch.setattr(orchestrator, "publish_collector_completion", stall_then_write)
    publish(run, proof)
    statuses = completions(github)
    assert len(statuses) == 2  # the residual is real: pinned, not hidden
    assert len({(s["context"], s["description"], s["target_url"]) for s in statuses}) == 1
    assert orchestrator.read_collector_completion(REPO, "token", PR, head_for(PR)) == ("present", run, None)
