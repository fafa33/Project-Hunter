"""Safe migration from the ``workflow_dispatch`` collector to ``repository_dispatch``.

Pre-migration collectors can still be executing when the new orchestrator takes
over. They are liveness evidence only: they hold no ownership claim, so they can
neither be elected, nor publish a completion, nor cause a reviewer to be invoked a
second time. All of this is driven through the production paths against the
stateful GitHub model.
"""

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

PR = 472
HEAD = "a" * 40
GEN = orchestrator.BASE_GENERATION_ID
REPO = fake_gh.REPO


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    return github, local


def add_legacy(
    github,
    *,
    status="in_progress",
    conclusion=None,
    created_at=None,
    branch="main",
    pr=PR,
    head=HEAD,
    event="workflow_dispatch",
):
    github.next_run += 1
    github.runs[github.next_run] = {
        "id": github.next_run,
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": event,
        "head_branch": branch,
        "head_sha": fake_gh.TIP,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at or fake_gh._now(),
        "run_attempt": 1,
        "display_title": orchestrator.collector_run_name(pr, head, GEN),
    }
    return github.next_run


def reconcile(github, local, run_id):
    github.add_orchestrator_run(run_id) if run_id not in github.runs else None
    local.run_id = run_id
    return orchestrator.ensure_collector(REPO, "token", PR, HEAD, GEN)


def old_enough(github):
    github.age_claims(orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS + 600)


def hours_ago(hours):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_active_legacy_collector_blocks_electing_a_second_owner(env):
    github, local = env
    add_legacy(github, status="in_progress")
    reconcile(github, local, 101)
    assert not github.dispatches and github.owner_epochs() == []
    queued = add_legacy(github, status="queued")  # a queued legacy run is just as live
    github.runs.pop(next(rid for rid, r in github.runs.items() if r["status"] == "in_progress" and rid != queued))
    reconcile(github, local, 102)
    assert not github.dispatches and github.owner_epochs() == []


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
def test_completed_legacy_collector_neither_settles_nor_blocks_the_new_contract(env, conclusion):
    github, local = env
    add_legacy(github, status="completed", conclusion=conclusion)
    reconcile(github, local, 101)
    assert len(github.dispatches) == 1 and github.owner_epochs() == [0]


def test_legacy_collector_finishing_during_recovery_releases_recovery_and_nothing_else(env):
    github, local = env
    github.fail["POST dispatches"] = fake_gh.Crash()
    with pytest.raises(fake_gh.Crash):
        reconcile(github, local, 101)  # epoch 0 owner claimed, then died before dispatching
    github.fail.clear()
    github.finish(101)
    legacy = add_legacy(github, status="in_progress")
    old_enough(github)
    reconcile(github, local, 102)
    assert github.owner_epochs() == [0] and not github.dispatches  # a live legacy run holds recovery off
    github.finish(legacy, "success")  # ...and it ends by itself
    old_enough(github)
    reconcile(github, local, 103)
    assert github.owner_epochs() == [0, 1] and len(github.dispatches) == 1


def test_recovery_itself_sees_a_draining_legacy_collector(env):
    """The election's own evidence check, not only the pre-check, treats a live legacy run as active."""
    github, local = env
    github.fail["POST dispatches"] = fake_gh.Crash()
    with pytest.raises(fake_gh.Crash):
        reconcile(github, local, 101)
    github.fail.clear()
    github.finish(101)
    add_legacy(github, status="in_progress")
    owner = orchestrator.read_owner_chain(REPO, "token", PR, HEAD, GEN)[-1]
    assert orchestrator.owner_attempt_failed(REPO, "token", PR, HEAD, GEN, owner) is False


@pytest.mark.parametrize(
    "stale",
    [
        {"created_at": "2020-01-01T00:00:00Z"},  # wedged "active" far beyond any job timeout
        {"branch": "attacker"},  # not the trusted default-branch definition
        {"pr": 999, "head": "b" * 40},  # a different cycle
        {"event": "push"},  # an active run of some other event is not a legacy collector
    ],
)
def test_untrusted_or_stale_legacy_runs_cannot_block_the_new_election(env, stale):
    github, local = env
    add_legacy(github, **stale)
    reconcile(github, local, 101)
    assert len(github.dispatches) == 1 and github.owner_epochs() == [0]


def test_a_legacy_run_blocks_only_for_the_drain_bound(env):
    github, local = env
    add_legacy(github, created_at=hours_ago(0.5))
    reconcile(github, local, 101)
    assert not github.dispatches  # inside the bound: draining
    github.runs[max(github.runs)]["created_at"] = hours_ago(2)
    old_enough(github)
    reconcile(github, local, 102)
    assert len(github.dispatches) == 1  # past the bound it can no longer block


def test_unreadable_legacy_listing_fails_closed(env):
    github, local = env
    github.fail["GET actions/workflows/hunter-reviewer-collector.yml/runs?event=workflow_dispatch*"] = (
        transport.GitHubRequestError("down", category="transient", status_code=500)
    )
    with pytest.raises(RuntimeError, match="refusing to risk a duplicate dispatch"):
        reconcile(github, local, 101)
    assert not github.dispatches and github.owner_epochs() == []


def test_a_legacy_collector_can_never_publish_a_completion(env):
    github, local = env
    legacy = add_legacy(github, status="completed", conclusion="success")
    # A forged trusted-looking status naming the legacy run is not accepted...
    orchestrator.publish_collector_completion(REPO, "token", PR, HEAD, legacy)
    assert orchestrator.read_collector_completion(REPO, "token", PR, HEAD)[0] == "absent"
    # ...and the trusted publisher refuses to attest it, with or without a capability.
    for proof in ("", "c" * 64):
        with pytest.raises(ValueError, match="trusted successful default-branch provenance"):
            orchestrator.publish_trusted_collector_completion(REPO, "token", legacy, proof)


def test_a_legacy_run_cannot_pass_the_new_start_gate(env):
    github, local = env
    reconcile(github, local, 101)
    payload = github.dispatches[-1]["client_payload"]
    legacy = add_legacy(github, status="in_progress")
    with pytest.raises(ValueError, match="not trusted default-branch repository dispatch"):
        collector.verify_trusted_dispatch_before_work(
            REPO, "token", PR, HEAD, GEN, payload["dispatch_proof"], legacy, epoch=payload["recovery_epoch"]
        )


def test_legacy_and_new_collectors_share_one_reviewer_invocation_identity():
    """A second collector adopts the first one's reviewer trigger instead of asking again.

    The invocation key is derived from head, claims and generation and deliberately
    excludes the collector run, so a draining legacy run and a newly elected owner
    cannot cause a reviewer to be invoked twice for the same cycle.
    """
    claims = "d" * 64
    legacy = collector.GitHubBackend(REPO, "", PR, HEAD, claims, 111, 1, GEN)
    elected = collector.GitHubBackend(REPO, "", PR, HEAD, claims, 222, 2, GEN)
    agent = {"id": "codex"}
    assert legacy.invocation_marker(agent, 1) == elected.invocation_marker(agent, 1)
    other_generation = collector.GitHubBackend(REPO, "", PR, HEAD, claims, 222, 2, "0" * 15 + "1")
    assert legacy.invocation_marker(agent, 1) != other_generation.invocation_marker(agent, 1)
    assert collector.invocation_key(HEAD, claims, "codex", 1) == collector.invocation_key(HEAD, claims, "codex", 1, GEN)
