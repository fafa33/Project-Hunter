"""Missed-completion recovery works from durable state, not from run-history position.

These drive the production path (``outstanding_collector_runs`` /
``publish_trusted_collector_completion`` / the CLI) against the stateful GitHub
model, with real owner and run-claim refs. Every sweep below is a fresh call with no
in-memory carry-over, so progress that survives across calls is durable by
construction.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

GEN = orchestrator.BASE_GENERATION_ID
REPO = fake_gh.REPO


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    return github, local


def head_for(pr):
    return f"{pr:040x}"


def stage(github, local, pr, *, orchestrator_run, finish=True):
    """A collector that ran under the elected owner and finished, completion unpublished."""
    head = head_for(pr)
    github.open_prs[pr] = head
    github.add_orchestrator_run(orchestrator_run)
    local.run_id = orchestrator_run
    orchestrator.ensure_collector(REPO, "token", pr, head, GEN)
    payload = github.dispatches[-1]["client_payload"]
    run = github.collector_runs()[-1]["id"]
    proof, epoch = payload["dispatch_proof"], payload["recovery_epoch"]
    collector.verify_trusted_dispatch_before_work(REPO, "token", pr, head, GEN, proof, run, epoch=epoch)
    if finish:
        github.finish(orchestrator_run, "success")
        github.finish(run, "success")
    return run, proof


def flood(github, count, *, created="2099-01-01T00:00:00Z"):
    """Completed collector runs of unrelated PRs, all newer than the staged ones."""
    template = github.collector_runs()[0]
    for index in range(count):
        github.next_run += 1
        github.runs[github.next_run] = {
            **template,
            "id": github.next_run,
            "status": "completed",
            "conclusion": "success",
            "created_at": created,
            "display_title": orchestrator.collector_run_name(900_000 + index, head_for(900_000 + index), GEN),
        }


def sweep(**kwargs):
    kwargs.setdefault("limit", 50)
    return [item.run_id for item in orchestrator.outstanding_collector_runs(REPO, "token", **kwargs)]


def test_run_behind_more_than_300_completed_runs_is_still_found(env):
    github, local = env
    run, _ = stage(github, local, 472, orchestrator_run=101)
    flood(github, 400)
    # The scenario genuinely exceeds the old window: three pages of 100 newest runs miss it.
    newest = sorted(github.collector_runs(), key=lambda r: (r["created_at"], r["id"]), reverse=True)
    assert run not in [r["id"] for r in newest[:300]]
    assert sweep() == [run]


def test_continuous_arrival_of_newer_runs_never_hides_an_outstanding_run(env):
    github, local = env
    run, _ = stage(github, local, 472, orchestrator_run=101)
    for round_number in range(4):
        flood(github, 120, created=f"2099-01-0{round_number + 1}T00:00:00Z")
        assert sweep() == [run]


def test_progress_is_durable_fair_and_a_processed_run_leaves_the_queue(env):
    github, local = env
    staged = {pr: stage(github, local, pr, orchestrator_run=100 + pr) for pr in range(472, 479)}
    runs = {pr: run for pr, (run, _) in staged.items()}
    # Fresh calls, no carried state: the same durable state gives the same answer.
    first_sweep = sweep(limit=2, rotation=0)
    second_sweep = sweep(limit=2, rotation=0)
    assert second_sweep == first_sweep
    assert first_sweep  # non-vacuous: there is real outstanding work to repeat
    # Bounded per sweep, yet rotation reaches every outstanding run.
    seen = set()
    for rotation in range(len(runs)):
        batch = sweep(limit=2, rotation=rotation)
        assert len(batch) <= 2
        seen.update(batch)
    assert seen == set(runs.values())
    # Publishing one run removes exactly that run from the queue, durably.
    done_pr = 475
    run, proof = staged[done_pr]
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")
    assert set(sweep()) == set(runs.values()) - {run}


def test_concurrent_sweeps_publish_one_authoritative_completion(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    gate = threading.Barrier(2, timeout=30)
    original_read = orchestrator.read_collector_completion

    def synchronized_precheck(*args):
        result = original_read(*args)
        if result[0] == "absent":
            gate.wait()
        return result

    with monkeypatch.context() as race, ThreadPoolExecutor(max_workers=2) as pool:
        race.setattr(orchestrator, "read_collector_completion", synchronized_precheck)
        outcomes = list(
            pool.map(lambda _i: orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof), range(2))
        )
    completions = [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]
    # Both sweeps raced past the pre-check, but the evidence is one identity and one consumer result.
    assert all(o.startswith(("PUBLISHED", "SKIPPED")) for o in outcomes)
    assert len(completions) == 1
    assert completions[0]["description"] == f"collector_run_id={run}"
    assert orchestrator.read_collector_completion(REPO, "token", 472, head_for(472)) == ("present", run, None)
    # Any later sweep publishes nothing.
    again = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert again == "SKIPPED: collector completion already published"
    assert len([s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]) == len(
        completions
    )


def test_deleted_or_expired_runs_are_skipped_not_fatal(env):
    github, local = env
    kept = [stage(github, local, pr, orchestrator_run=100 + pr)[0] for pr in (472, 473)]
    gone, _ = stage(github, local, 474, orchestrator_run=574)
    github.delete_run(gone)
    assert sorted(sweep()) == sorted(kept)


def test_still_running_collectors_are_not_outstanding(env):
    github, local = env
    stage(github, local, 472, orchestrator_run=101, finish=False)
    assert sweep() == []


def test_failed_collector_is_not_outstanding(env):
    github, local = env
    run, _ = stage(github, local, 472, orchestrator_run=101)
    github.finish(run, "failure")
    assert sweep() == []


def test_partial_pagination_still_sweeps_the_pages_read_and_a_dead_first_page_fails(env):
    github, local = env
    first_page, second_page = 50, 472  # 150 filler PRs sit between them: 50 on page 1, 472 on page 2
    run_one, _ = stage(github, local, first_page, orchestrator_run=150)
    run_two, _ = stage(github, local, second_page, orchestrator_run=572)
    for number in range(100, 250):
        github.open_prs[number] = head_for(number)
    listing = orchestrator.list_open_pull_requests(REPO, "token")
    assert [n for n, _ in listing].index(second_page) >= 100  # really on page 2
    assert sorted(sweep()) == sorted([run_one, run_two])

    github.fail["GET pulls?state=open&per_page=100&page=2*"] = transport.GitHubRequestError(
        "down", category="transient", status_code=502
    )
    calls_before = len(github.calls)
    assert sweep() == [run_one]  # the page read is still swept; page 2 is retried next sweep
    assert ("GET", "pulls?state=open&per_page=100&page=2") in github.calls[calls_before:]

    github.fail.clear()
    github.fail["GET pulls?state=open&per_page=100&page=1*"] = transport.GitHubRequestError(
        "down", category="transient", status_code=502
    )
    with pytest.raises(transport.GitHubRequestError):
        sweep()


def test_one_pull_request_failing_does_not_block_the_others(env, monkeypatch):
    github, local = env
    runs = {pr: stage(github, local, pr, orchestrator_run=100 + pr)[0] for pr in (472, 473, 474)}
    real = orchestrator.read_cycle

    def flaky(repository, token, pr, head):
        if pr == 473:
            raise transport.GitHubRequestError("down", category="transient", status_code=500)
        return real(repository, token, pr, head)

    monkeypatch.setattr(orchestrator, "read_cycle", flaky)
    assert sorted(sweep()) == sorted([runs[472], runs[474]])


def test_unreadable_owner_evidence_is_never_treated_as_outstanding_work(env):
    github, local = env
    stage(github, local, 472, orchestrator_run=101)
    github.fail["GET git/ref/*"] = transport.GitHubRequestError("down", category="transient", status_code=500)
    assert sweep() == []


def test_sweep_is_bounded_by_time_and_count(env):
    github, local = env
    for pr in range(472, 478):
        stage(github, local, pr, orchestrator_run=100 + pr)
    assert sweep(limit=3) and len(sweep(limit=3)) == 3
    calls_before = len(github.calls)
    assert sweep(budget_seconds=0) == []
    assert len(github.calls) - calls_before <= 2  # only the open-PR listing, no per-PR work


def test_cli_prints_one_line_per_outstanding_run(env, monkeypatch, capsys):
    github, local = env
    run, _ = stage(github, local, 472, orchestrator_run=101)
    monkeypatch.setattr(
        "sys.argv",
        ["hunter_review_orchestrator.py", "outstanding-collector-runs", "--repository", REPO, "--limit", "5"],
    )
    assert orchestrator.main() == 0
    line = capsys.readouterr().out.strip()
    assert line == f"{run}\t{orchestrator.collector_run_name(472, head_for(472), GEN)}"


def test_a_replaced_owners_run_is_not_published_by_recovery(env):
    """Recovery only queues work; publication still requires the current epoch."""
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    record = {
        "v": 1,
        "kind": "owner",
        "pr": 472,
        "head": head_for(472),
        "generation": GEN,
        "epoch": 1,
        "orchestrator_run": 102,
        "nonce_sha256": "9" * 64,
        "claimed_at": "2026-10-10T10:00:05Z",
    }
    orchestrator._create_claim(REPO, "token", orchestrator.collector_owner_ref(472, head_for(472), GEN, 1), record)
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("SKIPPED")
    assert not [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]


def test_open_prs_beyond_the_first_window_are_reached_by_rotation(env):
    github, local = env
    far = 700  # PR numbers sort ascending, so this one sits on page 7: outside the first 5-page window
    run, _ = stage(github, local, far, orchestrator_run=101)
    for number in range(1, 650):
        github.open_prs[number] = head_for(number)
    assert sweep(rotation=0) == []  # window 0 (pages 1-5) never sees it
    assert sweep(rotation=1) == [run]  # window 1 (pages 6-10) does
    assert sweep(rotation=2) == []  # a window past the end wraps to the first one, still bounded
    assert orchestrator.list_open_pull_requests(REPO, "token", 7)  # far past the end wraps, never empty


def test_a_pr_retargeted_away_from_main_is_not_a_recovery_candidate_or_published(env):
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    github.pr_bases[472] = "release"
    assert sweep() == []
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("SKIPPED") and "base branch" in outcome
    assert not [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]


def test_unusable_receipt_never_receives_a_trusted_completion(env, monkeypatch, tmp_path, capsys):
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    github.runs[run]["run_attempt"] = 1
    receipt_dir = tmp_path / "collector-receipt"
    receipt_dir.mkdir()
    (receipt_dir / "collector-dispatch-proof.txt").write_text(proof)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["x", "trusted-collector-complete", "--repository", REPO, "--run-id", str(run)])
    for body in (None, "{not json", '{"run_attempt": 9}'):
        if body is not None:
            (receipt_dir / "reviewer-results.json").write_text(body)
        assert orchestrator.main() == 0
        assert "receipt unusable" in capsys.readouterr().out
        assert sweep() == [run]  # still outstanding: retried by the next sweep
    (receipt_dir / "reviewer-results.json").write_text('{"run_attempt": 1}')
    orchestrator.main()
    assert "PUBLISHED" in capsys.readouterr().out
    assert sweep() == []


def test_unreadable_completion_history_is_unavailable_evidence_not_absence(env, monkeypatch):
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    real = orchestrator.read_collector_completion
    monkeypatch.setattr(
        orchestrator,
        "read_collector_completion",
        lambda *a, **k: ("absent", None, "collector completion evidence unavailable: HTTP 502"),
    )
    outcome = orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    assert outcome.startswith("SKIPPED") and "unavailable" in outcome
    assert not [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]
    monkeypatch.setattr(orchestrator, "read_collector_completion", real)  # evidence readable again: it publishes
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")


def test_a_publisher_that_dies_after_claiming_does_not_strand_the_completion(env, monkeypatch):
    """The claim makes publication single-writer, so a crash between claim and status must be recoverable."""
    github, local = env
    run, proof = stage(github, local, 472, orchestrator_run=101)
    real = orchestrator.publish_collector_completion

    def dies(*args, **kwargs):
        raise transport.GitHubRequestError("status write failed", category="transient", status_code=502)

    monkeypatch.setattr(orchestrator, "publish_collector_completion", dies)
    with pytest.raises(transport.GitHubRequestError):
        orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof)
    monkeypatch.setattr(orchestrator, "publish_collector_completion", real)
    # Inside the grace window another sweep must not publish (the first publisher may still be writing)...
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("SKIPPED")
    assert not [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]
    # ...but once the claim is stale and no status exists, the run is not stranded forever.
    github.age_completion_claims(orchestrator.COMPLETION_CLAIM_STALE_SECONDS + 60)
    assert orchestrator.publish_trusted_collector_completion(REPO, "token", run, proof).startswith("PUBLISHED")
    assert orchestrator.read_collector_completion(REPO, "token", 472, head_for(472)) == ("present", run, None)
    assert sweep() == []


def test_a_run_whose_completion_evidence_is_unreadable_does_not_consume_a_batch_slot(env, monkeypatch):
    github, local = env
    runs = {pr: stage(github, local, pr, orchestrator_run=100 + pr)[0] for pr in range(472, 478)}
    real = orchestrator.read_collector_completion
    unreadable = {472, 473, 474}  # as many unreadable runs as the batch limit would let them crowd out the rest

    def flaky(repository, token, pr, head):
        if pr in unreadable:
            return "absent", None, "collector completion evidence unavailable: HTTP 502"
        return real(repository, token, pr, head)

    monkeypatch.setattr(orchestrator, "read_collector_completion", flaky)
    batch = sweep(limit=3)
    # Unreadable evidence is retried on a later sweep and never takes a slot from readable work.
    assert sorted(batch) == sorted(runs[pr] for pr in (475, 476, 477))
    unreadable.clear()
    assert sorted(sweep(limit=10)) == sorted(runs.values())
