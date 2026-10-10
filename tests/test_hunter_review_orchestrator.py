from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
from dataclasses import replace

import hunter_github_transport as transport
import hunter_pre_ready_review as pre_ready
import hunter_review_orchestrator as orchestrator
import pytest
import yaml

HEAD = "a" * 40
REPOSITORY_ROOT = pathlib.Path(orchestrator.__file__).resolve().parents[1]


def _five_minutes_ago():
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_cycle(**overrides):
    values = {
        "pr_number": 472,
        "head_sha": HEAD,
        "state": "WAITING_FOR_REVIEWER",
        "provider_id": "",
        "trigger_id": None,
        "started_at": "2026-09-15T00:00:00Z",
        "config_digest": "d" * 64,
    }
    values.update(overrides)
    return orchestrator.ReviewCycle(**values)


def test_cycle_for_old_head_is_superseded():
    cycle = make_cycle(head_sha="a" * 40)
    assert orchestrator.classify_cycle(cycle, current_head="b" * 40) == "SUPERSEDED"


def test_waiting_cycle_is_pending_not_failure():
    cycle = make_cycle(state="WAITING_FOR_REVIEWER")
    assert orchestrator.governance_projection(cycle) == ("pending", "WAITING_FOR_REVIEWER")


def test_read_cycle_accepts_only_current_head_trusted_workflow_status(monkeypatch):
    def request(_repository, _token, _method, path, _payload=None):
        if path == "pulls/472":
            return {"state": "open", "head": {"sha": HEAD}}
        if path == f"commits/{HEAD}/statuses?per_page=100":
            # The status LIST endpoint, which is the one that carries the publisher.
            return [
                {
                    "context": "Hunter Review Orchestration / PR #472",
                    "description": f"WAITING_FOR_REVIEWER|local|0|{'d' * 64}",
                    "target_url": "https://github.com/owner/repo/actions/runs/123",
                    "created_at": "2026-09-15T00:00:00Z",
                    "creator": {"login": "github-actions[bot]"},
                }
            ]
        if path == "":
            return {"default_branch": "main"}
        if path == "actions/runs/123":
            return {
                "id": 123,
                "head_branch": "main",
                "path": ".github/workflows/hunter-governance-review.yml",
            }
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    state, cycle, error = orchestrator.read_cycle("owner/repo", "token", 472, HEAD)

    assert error is None
    assert state == "present"
    assert cycle is not None
    assert cycle.state == "WAITING_FOR_REVIEWER"
    assert cycle.head_sha == HEAD


def test_ready_review_request_dispatches_collector_once(monkeypatch):
    stored = {"cycle": None, "dispatches": 0}

    def read_cycle(*_args):
        cycle = stored["cycle"]
        return ("present", cycle, None) if cycle is not None else ("absent", None, None)

    monkeypatch.setattr(orchestrator, "read_cycle", read_cycle)
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 123, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: stored.update(cycle=cycle), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )

    first = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    second = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert first == second
    assert stored["dispatches"] == 1


def _absent_cycle_with_recorded_dispatches(monkeypatch) -> dict:
    """Stub an unrecorded exact head whose dispatches append real in-progress
    collector runs to the run listing ``ensure_collector`` reads back."""

    stored: dict = {"dispatches": 0}
    runs: list[dict] = []

    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, **_kwargs: None, raising=False)

    def dispatch_collector(_repository, _token, pr_number, head_sha, generation_id=orchestrator.BASE_GENERATION_ID):
        stored["dispatches"] += 1
        runs.append(_collector_run("in_progress", pr_number=pr_number, head_sha=head_sha, run_id=stored["dispatches"]))

    monkeypatch.setattr(orchestrator, "dispatch_collector", dispatch_collector, raising=False)

    def request_json(_repository, _token, _method, path, _payload=None):
        assert path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs")
        return {"workflow_runs": runs}

    monkeypatch.setattr(orchestrator, "request_json", request_json)

    return stored


def test_a_second_call_sees_the_correlated_collector_and_does_not_redispatch(monkeypatch):
    """PR #535 live evidence (runs 36340127965 and 36340195915).

    GitHub's combined-status read (what ``read_cycle`` uses) is not guaranteed
    read-your-write consistent: a status one reconcile execution just posted
    can still be reported "absent" to a later, near-simultaneous execution of
    the same event. Both report "absent" on every call here, so only the
    collector *run* listing (a separate, directly-queried endpoint) can tell the
    second caller that this exact identity has already been dispatched.

    Scope, deliberately narrow: these two calls are serial, so the first one's
    dispatch is already visible to the second one's liveness read. This proves
    the correlated-run case converges on one dispatch. It does NOT prove two
    genuinely simultaneous reconciles cannot both dispatch -- see
    ``test_two_genuinely_simultaneous_dispatches_can_both_miss_the_correlated_run``.
    """

    stored = _absent_cycle_with_recorded_dispatches(monkeypatch)

    first = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    second = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 1
    assert first.state == "WAITING_FOR_REVIEWER"
    assert second.state == "WAITING_FOR_REVIEWER"


def test_two_genuinely_simultaneous_dispatches_can_both_miss_the_correlated_run(monkeypatch):
    """Documents the residual window the previous test's name overclaimed.

    Two reconciles that both read the cycle status as absent *and* both read
    collector liveness before either has dispatched will each dispatch. Nothing
    in the current design closes that window: GitHub's status API has no
    compare-and-set, so publishing the durable record is not atomic with acting
    on it. The bounded recovery path in ``collector_needs_dispatch`` is what
    keeps the consequence bounded -- a redundant collector, never a false clear --
    so this is recorded as a known limitation rather than asserted away.

    Closing it needs an atomic claim (a lock/lease on the exact identity), which
    is a trust-boundary change for the owner to authorize rather than something
    to slip in while reconciling a conflict.
    """

    stored = _absent_cycle_with_recorded_dispatches(monkeypatch)
    stored["reads"] = []

    def collector_liveness(_repository, _token, _pr, _head, _generation_id=orchestrator.BASE_GENERATION_ID):
        # Read liveness before honouring any dispatch, so neither call can see
        # the other's run -- the true simultaneous case.
        stored["reads"].append(stored["dispatches"])
        return ("missing", 0)

    monkeypatch.setattr(orchestrator, "collector_liveness", collector_liveness, raising=False)

    first = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    second = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    # Both calls observed a missing correlated run: neither saw the other's.
    assert stored["reads"] == [0, 1]
    assert stored["dispatches"] == 2
    # Both still publish the same durable, non-authoritative pending state.
    assert first.state == "WAITING_FOR_REVIEWER"
    assert second.state == "WAITING_FOR_REVIEWER"


def _seconds_ago(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _budget() -> int:
    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    return pre_ready.reviewer_chain_worst_case_seconds(pool)


def test_an_active_correlated_collector_is_not_timed_out_on_the_nominal_deadline(monkeypatch):
    """Queue delay must not finalize a collector that is genuinely running.

    ``started_at`` is written before dispatch, so a cycle can pass the nominal
    opportunity budget while its correlated collector is still queued or in
    progress -- the collector's own job lifetime is counted from when it starts,
    not from when it was requested. Publishing REVIEW_TIMED_OUT there ends the
    opportunity while a live collector can still publish a competing result for
    the same exact head.
    """

    budget = _budget()
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(budget + 60))
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("queued")])

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert result.state == "WAITING_FOR_REVIEWER"
    assert result is not None
    # Regression 3: the skipped path must publish nothing at all, so no
    # competing terminal result can exist for this head and generation.
    assert stored["published"] == []
    assert stored["dispatches"] == 0


def test_an_in_progress_correlated_collector_is_also_withheld_from_the_nominal_deadline(monkeypatch):
    """Queued and in_progress are both ``active``; neither may be finalized early."""

    budget = _budget()
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(budget + 60))
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("in_progress")])

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []
    assert stored["dispatches"] == 0


def test_a_dead_or_missing_collector_still_times_out_legitimately(monkeypatch):
    """Regression 2: only a genuinely active collector buys the extra window."""

    budget = _budget()
    started = _seconds_ago(budget + 60)

    dead = make_cycle(trigger_id=123, started_at=started)
    stored = _ensure_harness(monkeypatch, dead, [_collector_run("completed", conclusion="failure")])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"
    assert stored["published"][-1].state == "REVIEW_TIMED_OUT"

    absent = make_cycle(trigger_id=123, started_at=started)
    stored = _ensure_harness(monkeypatch, absent, [])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"
    assert stored["published"][-1].state == "REVIEW_TIMED_OUT"


def test_an_active_collector_cannot_hold_the_cycle_open_past_the_bounded_grace(monkeypatch):
    """The extra window is bounded, so an active collector cannot wait forever.

    Beyond twice the canonical budget the timeout is legitimate regardless of
    liveness. That bound is evaluated before any liveness evidence is read, which
    is what makes it hold even when that evidence cannot be read at all.
    """

    budget = _budget()
    grace = budget * orchestrator.ACTIVE_COLLECTOR_GRACE_MULTIPLIER
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(grace + 60))
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("in_progress")])

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert result.state == "REVIEW_TIMED_OUT"
    assert stored["published"][-1].state == "REVIEW_TIMED_OUT"
    assert stored["dispatches"] == 0


def test_unreadable_liveness_cannot_finalize_a_timeout_before_the_bounded_grace(monkeypatch):
    """Unreadable evidence is not evidence that no collector is running.

    It must not finalize early -- but the bound still applies, so this cannot
    become a permanent wait either.
    """

    import hunter_github_transport as transport

    budget = _budget()
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(budget + 60))
    stored = _ensure_harness(monkeypatch, cycle, [])

    def unavailable(*_args, **_kwargs):
        raise transport.GitHubRequestError("rate limited", category="transient", status_code=429)

    monkeypatch.setattr(orchestrator, "collector_liveness", unavailable, raising=False)

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []

    # ... and the same unreadable evidence past the bound still finalizes.
    grace = budget * orchestrator.ACTIVE_COLLECTOR_GRACE_MULTIPLIER
    stale = make_cycle(trigger_id=123, started_at=_seconds_ago(grace + 60))
    stored = _ensure_harness(monkeypatch, stale, [])
    monkeypatch.setattr(orchestrator, "collector_liveness", unavailable, raising=False)
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"


def test_the_timeout_bound_never_consults_another_head_or_generation(monkeypatch):
    """Exact-head and generation binding must not be relaxed by the grace.

    The liveness read is made for the recorded cycle's own identity, and the
    published terminal cycle preserves that identity exactly.
    """

    seen: list[tuple] = []

    def capture(repository, token, pr_number, head_sha, generation_id=orchestrator.BASE_GENERATION_ID):
        seen.append((pr_number, head_sha, generation_id))
        return "active", 1

    budget = _budget()
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(budget + 60), generation_id="gen-7")
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("in_progress")])
    monkeypatch.setattr(orchestrator, "collector_liveness", capture, raising=False)

    # The same generation must be presented, so the idempotent branch is taken
    # rather than the remediation-admissibility branch.
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, "gen-7")

    assert seen == [(472, HEAD, "gen-7")]
    assert result.state == "WAITING_FOR_REVIEWER"
    assert result.generation_id == "gen-7"
    assert result.head_sha == HEAD
    assert stored["published"] == []


def test_review_opportunity_timeout_finalizes_pending_cycle_without_redispatch(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("in_progress")])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"
    assert stored["dispatches"] == 0
    assert stored["published"][-1].state == "REVIEW_TIMED_OUT"
    assert orchestrator.governance_projection(result) == ("success", "REVIEW_TIMED_OUT")


def test_review_timeout_is_terminal_and_idempotent(monkeypatch):
    cycle = make_cycle(state="REVIEW_TIMED_OUT", trigger_id=123, started_at="2020-01-01T00:00:00Z")
    stored = _ensure_harness(monkeypatch, cycle, [])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result == cycle
    assert stored["dispatches"] == 0
    assert stored["published"] == []


def test_collector_workflow_lifetime_covers_the_reviewer_chain_budget():
    """PR #535 live evidence: the collector's declared lifetime must not silently

    contradict the reviewer budgets it is supposed to run to completion. A
    workflow ``timeout-minutes`` shorter than the worst case every enabled
    reviewer can spend (as ``docs/CODE_WRITE_POLICY.json`` itself configures
    it) cancels the job mid-invocation and turns a reviewer still within its
    own configured budget into a false "unavailable". This is the single
    coherence check both magic numbers -- the workflow's static YAML timeout
    and the pool's per-agent budgets -- are validated against, rather than two
    independently hand-set figures that can drift apart again.
    """

    document = yaml.safe_load(
        pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-reviewer-collector.yml").read_text()
    )
    workflow_seconds = int(document["jobs"]["collect"]["timeout-minutes"]) * 60

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    required_seconds = pre_ready.reviewer_chain_worst_case_seconds(pool)

    assert workflow_seconds >= required_seconds


def test_independent_review_opportunity_matches_the_same_canonical_budget():
    """The orchestrator's own pending-cycle timeout must derive from the same

    source as the collector workflow's lifetime, not carry its own
    disconnected constant that can silently fall out of step with it.
    """

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    assert orchestrator.independent_review_opportunity_seconds() == pre_ready.reviewer_chain_worst_case_seconds(pool)


def test_the_derived_budget_is_still_readable_as_a_module_attribute():
    """The budget is a derivation, but trusted default-branch readers still reach
    it as a module attribute.

    The trusted orchestrator replay harness imports this *candidate's* module
    while its own scenario logic is still the default branch's version, and that
    version reads the budget through `INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS`.
    Dropping the name broke the candidate-facing half of that harness before the
    candidate can ever prove itself, which is the same bootstrap limitation the
    Review Opportunity migration identity exists to work around. Both access forms
    must therefore keep resolving, to the same derived value.
    """

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    derived = pre_ready.reviewer_chain_worst_case_seconds(pool)

    assert orchestrator.INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS == derived
    assert orchestrator.independent_review_opportunity_seconds() == derived

    with pytest.raises(AttributeError):
        _unknown = orchestrator.NOT_A_REAL_ORCHESTRATOR_ATTRIBUTE


def test_a_missing_run_id_refuses_before_any_collector_is_dispatched(monkeypatch):
    """A dispatch this run cannot name would be re-dispatched on the next pass.

    `trigger_id` is how a later pass recognises that a collector is already
    running for this exact HEAD. Dispatching first and only then discovering the
    run identity is unavailable would leave that collector recorded without one,
    which the next pass reads as "never dispatched" -- a second invocation
    against the same immutable head.
    """

    stored = {"cycle": None, "dispatches": 0}
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: None, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: stored.update(cycle=cycle), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="GITHUB_RUN_ID"):
        orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 0
    assert stored["cycle"] is None


def _ensure_harness(monkeypatch, cycle, runs):
    """Drive ensure_collector against a recorded cycle and a collector run listing."""

    stored = {"dispatches": 0, "published": []}
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    monkeypatch.setattr(
        orchestrator, "publish_cycle", lambda *_args, cycle: stored["published"].append(cycle), raising=False
    )
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )

    def request_json(_repository, _token, _method, path, _payload=None):
        if not path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs"):
            pytest.fail(f"unexpected request {path}")
        return {"workflow_runs": runs}

    monkeypatch.setattr(orchestrator, "request_json", request_json)
    return stored


def _collector_run(status, conclusion=None, pr_number=472, head_sha=HEAD, run_id=1):
    return {
        "id": run_id,
        "display_title": orchestrator.collector_run_name(pr_number, head_sha),
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "status": status,
        "conclusion": conclusion,
    }


def test_a_failed_collector_is_redispatched_rather_than_parking_the_cycle(monkeypatch):
    """A recorded trigger id is not proof that the collector ran to completion."""

    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("completed", "failure")])

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 1
    assert result.trigger_id == 999


def test_a_missing_collector_run_is_redispatched(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    stored = _ensure_harness(monkeypatch, cycle, [])

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 1


def test_a_live_or_successful_collector_is_never_duplicated(monkeypatch):
    for run in (
        _collector_run("in_progress"),
        _collector_run("queued"),
        _collector_run("completed", "success"),
    ):
        cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
        stored = _ensure_harness(monkeypatch, cycle, [run])

        result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

        assert stored["dispatches"] == 0
        assert result == cycle


def test_redispatch_stops_at_the_bounded_budget(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    runs = [
        _collector_run("completed", "failure", run_id=index)
        for index in range(1, orchestrator.MAX_COLLECTOR_DISPATCHES + 1)
    ]
    stored = _ensure_harness(monkeypatch, cycle, runs)

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 0
    assert result == cycle


def test_a_freshly_dispatched_cycle_is_not_redispatched_before_runs_are_listed(monkeypatch):
    from datetime import UTC, datetime

    cycle = make_cycle(trigger_id=123, started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    stored = _ensure_harness(monkeypatch, cycle, [])

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 0


def test_unreadable_collector_liveness_evidence_never_redispatches(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())

    def unavailable(*_args, **_kwargs):
        raise transport.GitHubRequestError("rate limited", category="transient", status_code=429)

    monkeypatch.setattr(orchestrator, "request_json", unavailable)

    assert orchestrator.collector_needs_dispatch("owner/repo", "token", cycle) is False


# ---------------------------------------------------------------------------
# Production defect: two reconciles landing close together each observed the
# commit-status cycle as "absent" (a stale/racy read of that one idempotency
# record) and each independently dispatched a collector for the same PR, exact
# head, and generation. `ensure_collector` must suppress a duplicate dispatch
# whenever a correlated collector run already exists, independent of whatever
# `read_cycle` itself answered -- never relying on the commit-status cycle as
# the only idempotency boundary.
# ---------------------------------------------------------------------------


def test_absent_cycle_read_does_not_duplicate_an_already_active_correlated_collector(monkeypatch):
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    published = []
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: published.append(cycle), raising=False)
    dispatches = []
    monkeypatch.setattr(orchestrator, "dispatch_collector", lambda *_args: dispatches.append(_args), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("active", 1), raising=False)

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert dispatches == []
    # A correlated collector run already active for this exact identity is
    # authoritative even when the commit-status read raced and reported the
    # cycle absent, so no second dispatch is issued -- but the durable cycle
    # record is still published, because that record is what makes a dispatch
    # that is already in flight recoverable rather than duplicable.
    assert result.trigger_id == 999
    assert result.state == "WAITING_FOR_REVIEWER"
    assert published


def test_absent_cycle_read_does_not_duplicate_an_already_successful_correlated_collector(monkeypatch):
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: None, raising=False)
    dispatches = []
    monkeypatch.setattr(orchestrator, "dispatch_collector", lambda *_args: dispatches.append(_args), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("completed", 1), raising=False)

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert dispatches == []


def test_absent_cycle_read_still_dispatches_when_no_correlated_collector_exists(monkeypatch):
    """Bounded recovery for a genuinely missing/dead collector still works."""

    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: None, raising=False)
    dispatches = []
    monkeypatch.setattr(orchestrator, "dispatch_collector", lambda *_args: dispatches.append(_args), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert len(dispatches) == 1


def test_unreadable_collector_correlation_evidence_refuses_to_risk_a_duplicate_dispatch(monkeypatch):
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    dispatches = []
    monkeypatch.setattr(orchestrator, "dispatch_collector", lambda *_args: dispatches.append(_args), raising=False)

    def unavailable(*_args, **_kwargs):
        raise transport.GitHubRequestError("rate limited", category="transient", status_code=429)

    monkeypatch.setattr(orchestrator, "collector_liveness", unavailable, raising=False)

    # Unreadable correlation evidence is not evidence that no collector exists,
    # so it must fail closed rather than authorise a dispatch it cannot rule out
    # as a duplicate.
    with pytest.raises(RuntimeError, match="correlation evidence unavailable"):
        orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert dispatches == []


def test_collector_liveness_ignores_runs_for_another_candidate(monkeypatch):
    """Correlation is the dispatch identity, not the workflow or the timestamp."""

    runs = [
        _collector_run("in_progress", pr_number=471),
        _collector_run("in_progress", head_sha="b" * 40),
        {**_collector_run("in_progress"), "path": ".github/workflows/ci.yml"},
    ]
    monkeypatch.setattr(orchestrator, "request_json", lambda *_args, **_kwargs: {"workflow_runs": runs}, raising=False)

    assert orchestrator.collector_runs("owner/repo", "token", 472, HEAD) == []
    assert orchestrator.collector_liveness("owner/repo", "token", 472, HEAD) == ("missing", 0)


def _render_run_name(template: str, generation_id: str) -> str:
    """Render the workflow run-name template the way Actions would.

    The generation expression is `a && b || c`, which yields `b` when the input
    is a non-empty string and `c` when it is empty or absent.
    """

    suffix = f" GEN {generation_id}" if generation_id else ""
    return (
        template.replace("${{ github.event.client_payload.pr_number }}", "472")
        .replace("${{ github.event.client_payload.head_sha }}", HEAD)
        .replace(
            "${{ github.event.client_payload.generation_id && format(' GEN {0}', github.event.client_payload.generation_id) || '' }}",
            suffix,
        )
    )


def test_collector_workflow_renders_the_correlated_run_name():
    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-reviewer-collector.yml").read_text(encoding="utf-8")
    template = yaml.safe_load(text)["run-name"]

    for generation_id in (orchestrator.BASE_GENERATION_ID, "0123456789abcdef"):
        assert _render_run_name(template, generation_id) == orchestrator.collector_run_name(472, HEAD, generation_id)


def test_dispatch_collector_sends_only_exact_identity(monkeypatch):
    seen = []
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda repository, token, method, path, payload=None: seen.append((method, path, payload)),
    )

    orchestrator.dispatch_collector("owner/repo", "token", 472, HEAD)

    assert len(seen) == 2
    method, path, status = seen[0]
    assert method == "POST" and path == f"statuses/{HEAD}"
    assert status["context"] == "Hunter Collector Dispatch Proof / PR #472"
    method, path, dispatch = seen[1]
    assert method == "POST" and path == "dispatches"
    assert dispatch["event_type"] == "hunter-reviewer-collect"
    assert dispatch["client_payload"]["pr_number"] == "472"
    assert dispatch["client_payload"]["head_sha"] == HEAD
    assert dispatch["client_payload"]["generation_id"] == ""
    assert (
        status["description"]
        == "|" + orchestrator.hashlib.sha256(dispatch["client_payload"]["dispatch_proof"].encode()).hexdigest()
    )


def _workflow_documents():
    directory = pathlib.Path(REPOSITORY_ROOT, ".github/workflows")
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _permission_blocks(document):
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        return ["missing-workflow-permissions"]
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    return [block for block in blocks if isinstance(block, dict)]


#: Permission levels that can drive another workflow run.
WORKFLOW_DRIVING = frozenset({"write", "admin"})


def _actions_levels(document):
    """Yield the effective `actions` level of every permission block in `document`.

    A workflow may declare permissions as a mapping or as one of the blanket
    strings, and `write-all` grants `actions: write` just as surely as spelling
    the scope out does. Reading only mappings would let the shorter spelling pass
    a guard that the longer one fails.
    """
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        yield "missing-workflow-permissions"
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    for block in blocks:
        if isinstance(block, str):
            yield "write" if block.strip() == "write-all" else "read"
        elif isinstance(block, dict) and block.get("actions") is not None:
            yield str(block["actions"]).strip()


def _triggers(document):
    # PyYAML resolves a bare `on:` key to True, so read both spellings.
    raw = document.get("on", document.get(True))
    if isinstance(raw, dict):
        return set(raw)
    if isinstance(raw, list):
        return set(raw)
    return {str(raw)} if raw else set()


def test_no_pull_request_reachable_workflow_can_drive_other_workflows():
    """A `pull_request` run executes the candidate's own copy of the workflow file.

    `actions: write` there would let candidate-authored steps dispatch, re-run or
    cancel trusted workflows with the repository token, so the permission must be
    unreachable from candidate-controlled execution.
    """

    privileged = [
        path.name
        for path, document in _workflow_documents()
        if isinstance(document, dict)
        and "pull_request" in _triggers(document)
        and any(level in WORKFLOW_DRIVING | {"missing-workflow-permissions"} for level in _actions_levels(document))
    ]
    assert privileged == []


@pytest.mark.parametrize(
    ("source", "candidate_controlled"),
    [
        ("on:\n  pull_request:\n    branches: [main]\n", True),
        ("on: pull_request\n", True),
        ("on: [push, pull_request]\n", True),
        ('"on":\n  pull_request: null\n', True),
        # `pull_request_target` runs the base-branch copy of the workflow file,
        # so its permissions are not candidate-reachable and are not flagged.
        ("on:\n  pull_request_target:\n    types: [opened]\n", False),
        ("on:\n  workflow_run:\n    workflows: [Other]\n", False),
        ("on:\n  push:\n    branches: [main]\n", False),
        ("on: workflow_dispatch\n", False),
    ],
)
def test_candidate_controlled_triggers_are_read_from_every_declaration_shape(source, candidate_controlled):
    assert ("pull_request" in _triggers(yaml.safe_load(source))) is candidate_controlled


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ({"actions": "write"}, ["write"]),
        ({"actions": "read"}, ["read"]),
        ({"actions": " write "}, ["write"]),
        ({"contents": "read"}, []),
        ({}, []),
        (None, ["missing-workflow-permissions"]),
        # Blanket declarations grant every scope, `actions` included.
        ("write-all", ["write"]),
        ("read-all", ["read"]),
    ],
)
def test_the_actions_level_is_read_from_every_permission_shape(permissions, expected):
    assert list(_actions_levels({"permissions": permissions})) == expected


def test_a_job_cannot_re_grant_what_the_workflow_block_gave_up():
    """A read-only workflow block does not excuse a job that takes the scope back."""
    document = {
        "permissions": {"actions": "read"},
        "jobs": {"privileged": {"permissions": {"actions": "write"}}},
    }
    assert any(level in WORKFLOW_DRIVING for level in _actions_levels(document))


def test_the_guard_still_describes_a_repository_that_grants_the_permission_somewhere():
    """The guard above is only meaningful while `actions: write` exists at all."""
    privileged = [
        path.name
        for path, document in _workflow_documents()
        if isinstance(document, dict) and any(level in WORKFLOW_DRIVING for level in _actions_levels(document))
    ]
    assert privileged, "no workflow declares actions:write; this guard no longer describes the repository"


def test_trusted_orchestration_runs_only_from_workflows_without_pull_request_triggers():
    dispatching = [
        (path.name, _triggers(document))
        for path, document in _workflow_documents()
        if isinstance(document, dict)
        and re.search(r"python scripts/hunter_review_orchestrator\.py (?:ensure|collector-complete)", path.read_text())
    ]
    assert dispatching, "no workflow runs the trusted review orchestrator"
    for name, triggers in dispatching:
        assert "pull_request" not in triggers, name
    assert "hunter-governance-reconcile.yml" in {name for name, _ in dispatching}


def test_reconcile_workflow_ensures_the_review_cycle_with_dispatch_permission():
    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml")
    text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert any(str(block.get("actions", "")).strip() == "write" for block in _permission_blocks(document))
    assert "hunter_review_orchestrator.py ensure" in text


def test_governance_review_workflow_keeps_only_read_access_to_actions():
    """It still reads collector run evidence; it may no longer drive workflows."""

    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-review.yml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "pull_request" not in _triggers(document)
    assert "pull_request_target" in _triggers(document)
    assert "workflow_dispatch" not in _triggers(document)
    assert document["permissions"]["statuses"] == "write"
    blocks = _permission_blocks(document)
    assert blocks and all(str(block.get("actions", "read")).strip() == "read" for block in blocks)
    assert "python scripts/hunter_review_orchestrator.py" not in path.read_text(encoding="utf-8")


def _pull_request_target_types(workflow_name):
    document = yaml.safe_load(
        pathlib.Path(REPOSITORY_ROOT, ".github/workflows", workflow_name).read_text(encoding="utf-8")
    )
    triggers = document.get("on", document.get(True))
    return triggers, triggers.get("pull_request_target") or {}


def test_reconcile_wakes_when_a_pr_becomes_ready_for_review():
    """Issue #534: Draft -> Ready must promptly reach trusted reconciliation.

    ``Hunter / Merge Readiness`` reacts to ``ready_for_review`` immediately
    (see ``hunter-merge-readiness.yml``); this workflow -- the only one that
    runs the privileged orchestrator -- must react to the same transition so
    a Ready PR is never left waiting on the next unrelated event or the
    30-minute schedule for its first orchestration cycle to exist.
    ``pull_request_target`` (not ``pull_request``) is required here: it is
    resolved from the base branch's copy of this file, so it is not
    candidate-controlled -- already proven generically by
    ``test_candidate_controlled_triggers_are_read_from_every_declaration_shape``
    above for exactly this trigger shape.
    """
    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    triggers = document.get("on", document.get(True))
    pull_request_target = triggers.get("pull_request_target")
    assert pull_request_target is not None, "reconcile has no pull_request_target trigger"
    assert pull_request_target.get("branches") == ["main"]
    assert "ready_for_review" in pull_request_target.get("types", [])
    # Drafting a PR is a withdrawal of review, not a request for it, and the
    # transition back to Ready is what carries the request; this trigger must
    # never also wake on the transition that removes it.
    assert "converted_to_draft" not in pull_request_target.get("types", [])

    text = path.read_text(encoding="utf-8")
    assert '"${event_name}" == "pull_request_target"' in text
    checkout = document["jobs"]["reconcile"]["steps"][0]
    assert checkout["with"]["ref"] == "main"


def test_reconcile_wakes_when_a_pr_head_is_pushed():
    """Issue #534 recurrence, PR #535 exact HEAD 51108333821ab42d1ab7aa0b812a72d890f73795.

    ``synchronize`` is the lifecycle event that *changes a PR's exact HEAD*,
    and Merge Readiness reacts to it immediately. Without the same trigger
    here, a pushed head has no trusted event-driven path to the orchestrator
    that must produce its exact-head cycle, and readiness correctly reports
    MALFORMED_REVIEW: WAITING_FOR_REVIEWER against a prerequisite nobody was
    asked to create. The `workflow_run` reconciliations that eventually ran
    (36393517015, 36393716517) are recovery: they depend on an unrelated
    workflow completing first, so they cannot be the primary mechanism.
    """
    _triggers_block, pull_request_target = _pull_request_target_types("hunter-governance-reconcile.yml")
    assert pull_request_target.get("branches") == ["main"]
    assert "synchronize" in pull_request_target.get("types", [])


def test_reconcile_wakes_on_every_lifecycle_event_that_needs_or_moves_the_exact_head():
    """DFF-045, generalised: the prerequisite must keep pace with the gate.

    Whenever Merge Readiness reacts immediately to a transition that makes
    review required or moves the exact HEAD it is judged against, the trusted
    workflow that owns the orchestration prerequisite must react to the same
    transition directly. This asserts the two triggers cannot drift apart
    again for a third event variant.
    """
    _readiness_triggers, readiness = _pull_request_target_types("hunter-merge-readiness.yml")
    _reconcile_triggers, reconcile = _pull_request_target_types("hunter-governance-reconcile.yml")

    for event_type in ("ready_for_review", "synchronize"):
        assert event_type in readiness.get("types", []), f"Merge Readiness no longer reacts to {event_type}"
        assert event_type in reconcile.get(
            "types", []
        ), f"{event_type} wakes Merge Readiness but not the trusted orchestration prerequisite"
    assert reconcile.get("branches") == readiness.get("branches") == ["main"]


def test_a_pushed_head_reaches_orchestration_as_the_exact_current_head(monkeypatch):
    """The target is trusted state, never the event payload or a stale head.

    ``ensure_current`` re-derives the current HEAD from the pull-request API
    on every call, so the reconcile step can pass only ``--pr`` and a pushed
    head is still reconciled against the head GitHub reports -- including a
    head pushed again between the event and the run.
    """
    pushed = "b" * 40
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "draft": False, "head": {"sha": pushed}},
    )
    monkeypatch.setattr(
        orchestrator,
        "review_request_state",
        lambda *_args: orchestrator.ReviewRequestReadiness(True, "d" * 64, "", "success"),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "current_remediation_generation", lambda *_args: orchestrator.BASE_GENERATION_ID)
    targets = []
    monkeypatch.setattr(
        orchestrator,
        "ensure_collector",
        lambda _repo, _token, pr_number, head_sha, *args: targets.append((pr_number, head_sha)),
        raising=False,
    )

    orchestrator.ensure_current("owner/repo", "token", 535)

    assert targets == [(535, pushed)]

    # The privileged step itself must not be able to name a head at all.
    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"python scripts/hunter_review_orchestrator\.py ensure\s*\\\n\s*--pr \"\$\{pr_number\}\" \\\n\s*"
        r"--repository \"\$\{GITHUB_REPOSITORY\}\"",
        text,
    ), "the ensure call must pass only the PR, so the orchestrator re-derives the exact head"
    assert "--head" not in text


def _assert_draft_pr_starts_no_review(monkeypatch, pr_number):
    """A Draft PR short-circuits before the review-request lookup and before
    any collector dispatch."""

    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "draft": True, "head": {"sha": HEAD}},
    )
    prerequisite_calls = []
    monkeypatch.setattr(
        orchestrator,
        "review_request_state",
        lambda *_args: prerequisite_calls.append(True)
        or orchestrator.ReviewRequestReadiness(True, "d" * 64, "", "success"),
        raising=False,
    )
    dispatch_calls = []
    monkeypatch.setattr(orchestrator, "ensure_collector", lambda *_args: dispatch_calls.append(True), raising=False)

    assert orchestrator.ensure_current("owner/repo", "token", pr_number) is None
    assert prerequisite_calls == []
    assert dispatch_calls == []


def test_a_pushed_head_on_a_draft_pr_dispatches_nothing(monkeypatch):
    """``synchronize`` fires for Draft PRs; that must not start a review cycle.

    Unlike ``ready_for_review``, which GitHub only fires on the transition out
    of Draft, ``synchronize`` fires for every push to a Draft branch. The
    trigger therefore cannot be the Draft guard: the guard is the orchestrator's
    own draft short-circuit, which runs before the review-request lookup and
    before any dispatch.
    """
    _assert_draft_pr_starts_no_review(monkeypatch, 535)


def test_unrelated_pull_request_events_do_not_dispatch_reviewer_orchestration():
    """The trigger set is exactly the review-relevant lifecycle events.

    Waking privileged orchestration on events that cannot change a PR's exact
    HEAD or its review-required state spends `actions: write` budget and can
    mint cycles for PRs that have no review request to satisfy.
    """
    triggers, pull_request_target = _pull_request_target_types("hunter-governance-reconcile.yml")
    assert set(pull_request_target.get("types", [])) == {"ready_for_review", "synchronize"}
    for unrelated in ("closed", "reopened", "labeled", "unlabeled", "edited", "assigned", "converted_to_draft"):
        assert unrelated not in pull_request_target.get("types", []), unrelated
    # Nothing else may route an arbitrary pull request event into this job.
    for other in ("pull_request", "issue_comment", "pull_request_review_thread", "check_run", "check_suite"):
        assert other not in triggers, other


def test_synchronize_reconciliation_never_executes_candidate_code():
    """The new trigger must stay on the trusted default-branch path.

    `pull_request_target` resolves this file from the base branch and the job
    checks out `main` with `persist-credentials: false`, so the privileged
    `actions: write` token is never used to run anything a PR controls.
    """
    document = yaml.safe_load(
        pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml").read_text(encoding="utf-8")
    )
    assert "pull_request" not in _triggers(document)

    steps = document["jobs"]["reconcile"]["steps"]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")]
    assert checkouts
    for step in checkouts:
        assert step["with"]["ref"] == "main"
        assert step["with"]["persist-credentials"] is False

    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml").read_text(
        encoding="utf-8"
    )
    assert "github.event.pull_request.head.sha" not in text
    assert "actions/checkout@${{" not in text


def test_orchestration_bootstraps_only_from_the_trusted_default_branch_checkout():
    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml").read_text(
        encoding="utf-8"
    )
    assert "if [ ! -f scripts/hunter_review_orchestrator.py ]; then" in text
    assert "Bootstrap phase" in text


def test_default_branch_without_orchestrator_publishes_bootstrap_pending_without_dispatching_authority():
    """A bootstrap PR cannot invoke a controller that trusted main does not contain.

    The migration state itself is decided by the trusted default-branch bridge --
    see ``tests/test_pr473_trusted_bridge_bootstrap.py`` -- because only that tree
    can tell whether the controller has landed, and only that tree can adopt an
    exact-head review once one exists. What this lane owns is the guarantee that
    it hands the decision to that bridge and does nothing else: it runs the
    trusted copy out of ``engine/``, never a candidate copy of the orchestrator,
    and reaches no dispatch or comment surface on the way.
    """

    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-review.yml")
    workflow = path.read_text(encoding="utf-8")
    document = yaml.safe_load(workflow)

    runs = [
        step["run"] for job in document["jobs"].values() for step in job["steps"] if isinstance(step.get("run"), str)
    ]
    assert any('python "${BRIDGE}" governance' in run for run in runs)

    for run in runs:
        assert "python scripts/hunter_review_orchestrator.py" not in run
        assert "actions/workflows/" not in run
        assert "issues/${PR_NUMBER}/comments" not in run
        assert "${GITHUB_WORKSPACE}/engine/scripts/" in run or "BRIDGE" not in run

    checkouts = [
        step
        for job in document["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert checkouts, "the lane must check out the trusted tree it executes"
    for step in checkouts:
        with_block = step.get("with") or {}
        assert with_block.get("ref") == "${{ github.event.repository.default_branch }}"
        assert with_block.get("path") == "engine"


def test_review_prerequisites_accept_canonical_review_request_object(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args, **_kwargs: {},
    )
    import hunter_governance_review_v2 as governance

    monkeypatch.setattr(governance, "read_trusted_upgrade_status", lambda *_args: ("success", ""))
    document = {
        "review_request": {"schema": "hunter.review-request.v1", "claims_id": "d" * 64},
        "claims": {},
    }
    monkeypatch.setattr(governance, "read_head_pre_ready_review", lambda *_args: ("present", document, None))
    monkeypatch.setattr(governance, "valid_current_review_request", lambda *_args: (True, "current"))

    assert orchestrator.review_prerequisites_ready("owner/repo", "token", 472, HEAD) is True


def test_review_prerequisites_reject_stale_request_before_hosted_review_dispatch(monkeypatch):
    """An inherited request must not spend reviewer capacity on another candidate's claims."""

    monkeypatch.setattr(orchestrator, "request_json", lambda *_args, **_kwargs: {})
    import hunter_governance_review_v2 as governance

    monkeypatch.setattr(governance, "read_trusted_upgrade_status", lambda *_args: ("success", ""))
    monkeypatch.setattr(
        governance,
        "valid_current_review_request",
        lambda *_args: (False, "STALE_REVIEW: review request describes an older base"),
        raising=False,
    )
    document = {
        "review_request": {"schema": "hunter.review-request.v1", "claims_id": "d" * 64},
        "claims": {},
    }
    monkeypatch.setattr(governance, "read_head_pre_ready_review", lambda *_args: ("present", document, None))

    assert orchestrator.review_prerequisites_ready("owner/repo", "token", 472, HEAD) is False


def test_current_pr_waits_for_trusted_review_prerequisites(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "head": {"sha": HEAD}},
    )
    monkeypatch.setattr(
        orchestrator,
        "review_request_state",
        lambda *_args: orchestrator.ReviewRequestReadiness(False, "", "still running", "pending"),
        raising=False,
    )
    calls = []
    monkeypatch.setattr(orchestrator, "ensure_collector", lambda *_args: calls.append(True), raising=False)

    result = orchestrator.ensure_current("owner/repo", "token", 472)

    assert result is None
    assert calls == []


def test_current_pr_never_dispatches_for_a_draft_pr(monkeypatch):
    """Issue #534, requirement 5: a Draft PR must never start candidate review.

    This is checked directly on the PR's own state rather than left to the
    indirect fact that a Draft head typically has no pre-ready review request
    yet: the draft check runs, and short-circuits, before that lookup.
    """
    _assert_draft_pr_starts_no_review(monkeypatch, 472)


def _readiness_harness(monkeypatch, *, prerequisite_state, request_valid, request_state="present"):
    """Wire the exact-head prerequisites the reconcile transition reads.

    `prerequisite_state` is the trusted preflight/admission state, returned
    rather than made unreachable: the orchestrator consults it for every pull
    request outside the bounded Review Opportunity migration identity, so a
    test that suppressed it would prove nothing about the real path.
    """

    head = HEAD
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "head": {"sha": head}},
        raising=False,
    )
    document = {"review_request": {"schema": "hunter.review-request.v1", "claims_id": "c" * 64}}

    import hunter_governance_review_v2 as governance

    monkeypatch.setattr(
        governance, "read_trusted_upgrade_status", lambda *_args: (prerequisite_state, "prereq detail"), raising=False
    )
    if request_state == "present":
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: ("present", document, None), raising=False
        )
    else:
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: (request_state, None, "absent"), raising=False
        )
    monkeypatch.setattr(
        governance,
        "valid_current_review_request",
        lambda *_args: (request_valid, "stale finding F-8 has no exact-head correction evidence"),
        raising=False,
    )
    return document


def _collector_harness(monkeypatch):
    stored = {"cycle": None, "dispatches": 0}
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_args: ("present", stored["cycle"], None) if stored["cycle"] else ("absent", None, None),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 555, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: stored.update(cycle=cycle), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "current_remediation_generation", lambda *_args: "gen-1", raising=False)
    return stored


def test_an_exact_head_codex_clear_dispatches_no_redundant_collector(monkeypatch):
    """An authenticated Codex clear of this exact head is already authority.

    Reconcile must not post another bot ``@codex review`` for unchanged content,
    and a head with no such clear still dispatches exactly once.
    """

    stored = _collector_harness(monkeypatch)
    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=True)

    monkeypatch.setattr(orchestrator, "exact_head_codex_clear_exists", lambda *_args: True)
    assert orchestrator.ensure_current("owner/repo", "token", 472) is None
    assert stored["dispatches"] == 0
    assert stored["cycle"] is None

    monkeypatch.setattr(orchestrator, "exact_head_codex_clear_exists", lambda *_args: False)
    assert orchestrator.ensure_current("owner/repo", "token", 472) is not None
    assert stored["dispatches"] == 1


def test_blocked_review_request_reports_the_reason_and_never_dispatches(monkeypatch):
    """PR #540: trusted preflight already passed but the exact-head pre-ready
    review request is not valid for this head. A reconcile must not report
    success here, and it must not spend reviewer capacity either."""
    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=False)
    stored = _collector_harness(monkeypatch)

    with pytest.raises(orchestrator.ReviewRequestBlocked) as blocked:
        orchestrator.ensure_current("owner/repo", "token", 472)

    assert "stale finding F-8" in str(blocked.value)
    assert "needs a pre-ready review request committed for it" in str(blocked.value)
    assert stored["dispatches"] == 0
    assert stored["cycle"] is None


def test_blocked_review_request_makes_the_reconcile_exit_non_zero(monkeypatch):
    """The reconcile step only fails visibly when the orchestrator exits
    non-zero, so a blocked head must not exit 0."""

    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=False)
    _collector_harness(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "ensure", "--repository", "owner/repo", "--pr", "472"])

    assert orchestrator.main() == 1


def test_new_head_starts_orchestration_once_after_its_prerequisite_completes(monkeypatch):
    """The #540 defect shape, end to end.

    A candidate gets a new head. The first reconcile sees the trusted
    prerequisite still running, so it does nothing and stays retryable. The
    prerequisite then succeeds and the pre-ready review request becomes valid
    for that same exact head, so the next reconcile publishes the exact-head
    cycle and dispatches the collector exactly once. Further reconciles of the
    same head stay idempotent.
    """

    stored = _collector_harness(monkeypatch)
    # 1 + 2: same exact head, request not yet committed -> no dispatch, no cycle.
    _readiness_harness(monkeypatch, prerequisite_state="pending", request_valid=False, request_state="absent")
    assert orchestrator.ensure_current("owner/repo", "token", 472) is None
    assert stored["dispatches"] == 0
    assert stored["cycle"] is None

    # 3 + 4 + 5 + 6: the request is now committed and binds this exact head.
    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=True)
    cycle = orchestrator.ensure_current("owner/repo", "token", 472)

    # 7: repeated reconciliation of the same exact head is idempotent.
    again = orchestrator.ensure_current("owner/repo", "token", 472)

    assert cycle is not None
    assert cycle.head_sha == HEAD
    assert cycle.state == "WAITING_FOR_REVIEWER"
    assert again is cycle
    assert stored["dispatches"] == 1
    assert stored["cycle"] is cycle


def test_incomplete_prerequisite_never_dispatches_and_stays_retryable(monkeypatch):
    """Untrusted or unfinished prerequisites still cannot dispatch, and they
    keep the benign pending exit so the ordinary dependency wait is not turned
    into a red reconcile."""

    _readiness_harness(monkeypatch, prerequisite_state="pending", request_valid=False, request_state="absent")
    stored = _collector_harness(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "ensure", "--repository", "owner/repo", "--pr", "472"])

    assert orchestrator.ensure_current("owner/repo", "token", 472) is None
    assert orchestrator.main() == 0
    assert stored["dispatches"] == 0


def test_superseded_head_is_never_dispatched_against(monkeypatch):
    """A recorded cycle for a superseded head must not be mistaken for the
    current head's idempotency record, and reviewer capacity is only ever
    spent on the head the pull request actually points at."""

    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=True)
    stored = _collector_harness(monkeypatch)
    stored["cycle"] = make_cycle(head_sha="b" * 40)

    result = orchestrator.ensure_current("owner/repo", "token", 472)

    # The stale cycle is not resumed: the single dispatch belongs to the head
    # the pull request actually points at, never to the superseded one.
    assert stored["dispatches"] == 1
    assert stored["cycle"].head_sha == HEAD
    assert stored["cycle"].head_sha != "b" * 40
    assert result.head_sha == HEAD
    # And that new record is now the idempotency record for this head.
    again = orchestrator.ensure_current("owner/repo", "token", 472)
    assert again is stored["cycle"]
    assert stored["dispatches"] == 1


def test_readiness_reports_which_blocker_applies(monkeypatch):
    """The reason survives to the caller, so the blocker is actionable rather
    than a bare False."""

    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=False)
    blocked = orchestrator.review_request_state("owner/repo", "token", 472, HEAD)
    assert blocked.ready is False
    assert blocked.prerequisite_state == "success"
    assert "stale finding F-8" in blocked.reason

    _readiness_harness(monkeypatch, prerequisite_state="pending", request_valid=False)
    pending = orchestrator.review_request_state("owner/repo", "token", 472, HEAD)
    assert pending.ready is False
    assert pending.prerequisite_state == "pending"
    assert "prereq detail" in pending.reason


def test_offline_mac_skips_local_without_red(monkeypatch):
    monkeypatch.setattr(orchestrator, "runner_state", lambda *_args, **_kwargs: "offline", raising=False)
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "FAILOVER_IN_PROGRESS"
    assert decision.next_provider == "codex"
    assert decision.reason == "offline"


def test_missing_mac_runner_skips_local_without_red(monkeypatch):
    monkeypatch.setattr(orchestrator, "runner_state", lambda *_args, **_kwargs: "missing", raising=False)
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "FAILOVER_IN_PROGRESS"
    assert decision.next_provider == "codex"


def test_failover_target_is_the_pools_hosted_authority_not_a_retired_provider():
    """The next hop must be the pool's authority reviewer, never a name that left it."""

    import hunter_pre_ready_review as pre_ready

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    assert "opencode" not in {str(agent["id"]) for agent in pool["agents"]}
    assert orchestrator.next_authority_provider() == "codex"
    # Disabled triage-only reviewers are not part of the active pool. Until
    # trusted health admission exists, authority progression starts at Codex.
    assert orchestrator.first_pool_provider() == "codex"
    assert orchestrator.next_authority_provider(after="codex") == "copilot"
    assert orchestrator.next_authority_provider(after="gemini") == "groq"
    assert orchestrator.next_authority_provider(after="groq") == str(pool["last_resort"]) == "hunter-guard"


def test_unreadable_runner_probe_neither_crashes_nor_skips_the_reviewer(monkeypatch):
    """GITHUB_TOKEN cannot read repository runners; 403 is not evidence of absence."""

    def forbidden(*_args, **_kwargs):
        raise transport.GitHubRequestError("no Administration:read", category="permanent", status_code=403)

    monkeypatch.setattr(orchestrator, "request_json", forbidden)

    assert orchestrator.runner_state("owner/repo", "token") == "unknown"
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "REVIEW_IN_PROGRESS"
    assert decision.next_provider == "codex"
    assert decision.reason == "unknown"


def test_non_permission_runner_failures_still_propagate(monkeypatch):
    def broken(*_args, **_kwargs):
        raise transport.GitHubRequestError("server error", category="transient", status_code=500)

    monkeypatch.setattr(orchestrator, "request_json", broken)

    with pytest.raises(transport.GitHubRequestError):
        orchestrator.runner_state("owner/repo", "token")


def test_online_mac_that_never_starts_does_not_stall_pr():
    class FakeJobs:
        def __init__(self):
            self.clock = 0.0

        def now(self):
            return self.clock

        def sleep(self, seconds):
            self.clock += seconds

        def started(self):
            return False

    decision = orchestrator.wait_for_local_ack(FakeJobs(), timeout=30)
    assert decision.state == "FAILOVER_IN_PROGRESS"
    assert decision.reason == "unresponsive"
    assert decision.next_provider == "codex"


def test_collector_completion_status_binds_trusted_run_to_exact_head(monkeypatch):
    seen = []
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 777)
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda repository, token, method, path, payload=None: seen.append((method, path, payload)) or {},
    )
    orchestrator.publish_collector_completion("owner/repo", "token", 472, HEAD, 777)
    assert seen == [
        (
            "POST",
            f"statuses/{HEAD}",
            {
                "state": "success",
                "context": "Hunter Reviewer Collector / PR #472",
                "description": "collector_run_id=777",
                "target_url": "https://github.com/owner/repo/actions/runs/777",
            },
        )
    ]


def test_read_collector_completion_rejects_untrusted_or_wrong_head_run(monkeypatch):
    def request(_repository, _token, _method, path, _payload=None):
        if path == f"commits/{HEAD}/statuses?per_page=100":
            return [
                {
                    "state": "success",
                    "context": "Hunter Reviewer Collector / PR #472",
                    "description": "collector_run_id=777",
                    "target_url": "https://github.com/owner/repo/actions/runs/777",
                    "creator": {"login": "github-actions[bot]"},
                }
            ]
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": "c" * 40}
        if path == "actions/runs/777":
            return {
                "id": 777,
                "head_branch": "main",
                "head_sha": "b" * 40,
                "path": ".github/workflows/hunter-reviewer-collector.yml",
                "event": "repository_dispatch",
                "status": "completed",
                "conclusion": "success",
            }
        if path == f"compare/{'b' * 40}...{'c' * 40}":
            return {"status": "diverged"}
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    state, run_id, reason = orchestrator.read_collector_completion("owner/repo", "token", 472, HEAD)
    assert state == "absent"
    assert run_id is None
    assert reason is None


def test_reconcile_runs_when_reviewer_collector_completes():
    root = orchestrator.__file__ and orchestrator.__file__.rsplit("/scripts/", 1)[0]
    text = open(f"{root}/.github/workflows/hunter-governance-reconcile.yml", encoding="utf-8").read()
    assert "Hunter Reviewer Collector" in text


# ---------------------------------------------------------------------------
# Production defect: a completed Hunter Reviewer Collector run always executes
# from the trusted default branch, so its own workflow_run.head_sha is main's
# SHA, never the candidate's. Reconcile's PR derivation must never search open
# PRs by that head_sha for this specific trigger; the candidate PR/head is
# instead the trusted identity the collector's own run-name already carries.
# ---------------------------------------------------------------------------

RECONCILE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "hunter-governance-reconcile.yml"
COLLECTOR_WORKFLOW_FILE = REPOSITORY_ROOT / ".github" / "workflows" / "hunter-reviewer-collector.yml"
_COLLECTOR_BRANCH_MARKER = (
    'elif [[ "${event_name}" == "workflow_run" '
    '&& "${EVENT_WORKFLOW_RUN_NAME}" == "Hunter Reviewer Collector" ]]; then'
)


def _reconcile_run_script() -> str:
    document = yaml.safe_load(RECONCILE_WORKFLOW.read_text(encoding="utf-8"))
    for step in document["jobs"]["reconcile"]["steps"]:
        if step.get("name") == "Refresh lightweight governance status":
            return str(step["run"])
    raise AssertionError("reconcile governance-refresh step not found")


def _collector_branch_body() -> str:
    script = _reconcile_run_script()
    assert _COLLECTOR_BRANCH_MARKER in script
    return script.split(_COLLECTOR_BRANCH_MARKER, 1)[1].split("elif", 1)[0]


def _extract_collector_pr_numbers(title: str) -> str:
    """Run the exact committed shell text that derives pr_numbers for a
    completed 'Hunter Reviewer Collector' run, against a real bash process --
    exercising the real committed script rather than a reimplementation."""

    completed = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{_collector_branch_body()}\nprintf "%s" "$pr_numbers"'],
        env={"EVENT_WORKFLOW_RUN_TITLE": title, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_collector_completion_pr_derivation_never_uses_workflow_run_head_sha():
    """Defect B: main's own SHA must never be mistaken for the candidate HEAD."""

    assert "EVENT_WORKFLOW_RUN_HEAD_SHA" not in _collector_branch_body()


def test_collector_completion_pr_derivation_extracts_the_correlated_pr_number():
    title = f"Hunter Reviewer Collector PR 541 HEAD {'e' * 40}"
    assert _extract_collector_pr_numbers(title) == "541"


def test_collector_completion_pr_derivation_extracts_the_pr_number_with_a_remediation_generation_suffix():
    title = f"Hunter Reviewer Collector PR 541 HEAD {'e' * 40} GEN {'a' * 16}"
    assert _extract_collector_pr_numbers(title) == "541"


def test_collector_completion_pr_derivation_yields_nothing_for_malformed_or_foreign_titles():
    """Malformed/mismatched run correlation must never select a PR or head."""

    assert _extract_collector_pr_numbers("some unrelated bot posted this title") == ""
    assert _extract_collector_pr_numbers("Hunter Reviewer CollectorPR541 HEAD deadbeef") == ""
    assert _extract_collector_pr_numbers("Hunter Reviewer Collector PR HEAD " + "e" * 40) == ""


def test_collector_completion_is_published_only_by_trusted_reconcile():
    document = yaml.safe_load(COLLECTOR_WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert document["permissions"].get("statuses") != "write"
    assert all(step.get("name") != "Publish collector completion" for step in document["jobs"]["collect"]["steps"])
    trusted = yaml.safe_load(
        (REPOSITORY_ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text(encoding="utf-8")
    )
    assert "workflow_dispatch" not in _triggers(trusted)
    steps = trusted["jobs"]["reconcile"]["steps"]
    publisher = next(step for step in steps if step.get("name") == "Publish trusted collector completion")
    assert "trusted-collector-complete" in str(publisher["run"])
    assert "github.event.workflow_run.id" in str(publisher["env"])


@pytest.mark.parametrize(
    "override", ["wrong_branch", "wrong_path", "failed", "wrong_event", "wrong_title", "wrong_head"]
)
def test_trusted_collector_completion_rejects_untrusted_run(monkeypatch, override):
    run = {
        "id": 777,
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "display_title": f"Hunter Reviewer Collector PR 472 HEAD {HEAD}",
        "head_sha": HEAD,
        "created_at": "2026-10-09T19:01:00Z",
    }
    pr = {"state": "open", "head": {"sha": HEAD}}
    if override == "wrong_branch":
        run["head_branch"] = "candidate"
    elif override == "wrong_path":
        run["path"] = ".github/workflows/evil.yml"
    elif override == "failed":
        run["conclusion"] = "failure"
    elif override == "wrong_event":
        run["event"] = "pull_request"
    elif override == "wrong_title":
        run["display_title"] = "spoofed"
    elif override == "wrong_head":
        pr["head"]["sha"] = "f" * 40
    published = []

    def request(_repository, _token, _method, path, _payload=None):
        if path == "actions/runs/777":
            return run
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": HEAD}
        if path == "pulls/472":
            return pr
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    monkeypatch.setattr(orchestrator, "publish_collector_completion", lambda *args: published.append(args))
    if override == "wrong_head":
        assert "SKIPPED" in orchestrator.publish_trusted_collector_completion("owner/repo", "token", 777)
    else:
        with pytest.raises(ValueError):
            orchestrator.publish_trusted_collector_completion("owner/repo", "token", 777)
    assert not published


def _trusted_collector_fixture(run, *, first_run=None):
    """Shared trusted API responses for positive and adversarial dispatch tests."""
    origin = {
        "id": 123,
        "head_branch": "main",
        "path": ".github/workflows/hunter-governance-reconcile.yml",
        "event": "schedule",
    }
    status = {
        "context": "Hunter Collector Dispatch Proof / PR #472",
        "description": "|" + orchestrator.hashlib.sha256(("a" * 64).encode()).hexdigest(),
        "creator": {"login": "github-actions[bot]"},
        "target_url": "https://github.com/owner/repo/actions/runs/123",
        "created_at": "2026-10-09T19:00:00Z",
    }
    cycle = orchestrator.ReviewCycle(
        472,
        HEAD,
        "WAITING_FOR_REVIEWER",
        "",
        123,
        "2026-10-09T19:00:00Z",
        "digest",
        orchestrator.BASE_GENERATION_ID,
    )
    responses = {
        f"actions/runs/{run['id']}": run,
        "actions/runs/123": origin,
        "": {"default_branch": "main"},
        "commits/main": {"sha": HEAD},
        "pulls/472": {"state": "open", "head": {"sha": HEAD}},
        f"commits/{HEAD}/statuses?per_page=100": [status],
    }

    runs = [first_run, run] if first_run is not None else [run]
    for item in runs:
        item.setdefault("path", orchestrator.COLLECTOR_WORKFLOW_PATH)

    def request(_repo, _token, _method, path, _payload=None):
        if path == (
            f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs?" "event=repository_dispatch&per_page=100&page=1"
        ):
            return {"workflow_runs": runs}
        return responses[path]

    return request, cycle, runs


@pytest.mark.parametrize("capability,accepted", [("a", True), ("b", False)])
def test_trusted_collector_completion_dispatch_capability(monkeypatch, capability, accepted):
    run = {
        "id": 777,
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
        "display_title": f"Hunter Reviewer Collector PR 472 HEAD {HEAD}",
        "created_at": "2026-10-09T19:01:00Z",
    }
    request, cycle, runs = _trusted_collector_fixture(run)
    published = []
    monkeypatch.setattr(orchestrator, "request_json", request)
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "collector_runs", lambda *_: runs)
    monkeypatch.setattr(orchestrator, "publish_collector_completion", lambda *args: published.append(args))
    result = orchestrator.publish_trusted_collector_completion("owner/repo", "token", 777, capability * 64)
    assert result.startswith("PUBLISHED:") if accepted else "SKIPPED" in result
    assert bool(published) == accepted
    if accepted:
        assert published == [("owner/repo", "token", 472, HEAD, 777)]


def test_trusted_completion_rejects_replayed_proof_on_another_run(monkeypatch):
    original = {
        "id": 777,
        "created_at": "2026-10-09T19:01:00Z",
        "display_title": f"Hunter Reviewer Collector PR 472 HEAD {HEAD}",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
    }
    replay = dict(
        original,
        id=778,
        created_at="2026-10-09T19:02:00Z",
        path=orchestrator.COLLECTOR_WORKFLOW_PATH,
        event="repository_dispatch",
        head_branch="main",
        head_sha=HEAD,
        status="completed",
        conclusion="success",
    )
    request, cycle, runs = _trusted_collector_fixture(replay, first_run=original)
    published = []
    monkeypatch.setattr(orchestrator, "request_json", request)
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "collector_runs", lambda *_: runs)
    monkeypatch.setattr(orchestrator, "publish_collector_completion", lambda *args: published.append(args))
    assert "SKIPPED" in orchestrator.publish_trusted_collector_completion("owner/repo", "token", 778, "a" * 64)
    assert not published


def test_trusted_completion_rejects_unreachable_main_revision(monkeypatch):
    run = {
        "id": 777,
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": "b" * 40,
        "status": "completed",
        "conclusion": "success",
    }

    def request(_repo, _token, _method, path, _payload=None):
        return {
            "actions/runs/777": run,
            "": {"default_branch": "main"},
            "commits/main": {"sha": "c" * 40},
            "compare/" + "b" * 40 + "..." + "c" * 40: {"status": "diverged"},
        }[path]

    monkeypatch.setattr(orchestrator, "request_json", request)
    assert "SKIPPED" in orchestrator.publish_trusted_collector_completion("owner/repo", "token", 777)


def test_collector_artifacts_keep_receipt_and_proof_separate():
    workflow = yaml.safe_load(COLLECTOR_WORKFLOW_FILE.read_text(encoding="utf-8"))
    uploads = [
        step["with"]
        for step in workflow["jobs"]["collect"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 2
    assert any(item["path"] == "reviewer-results.json" for item in uploads)
    assert any(item["path"] == "collector-dispatch-proof.txt" for item in uploads)
    assert all("github.run_attempt" in item["name"] for item in uploads)


def test_collector_completion_accepts_trusted_ancestor_of_current_main(monkeypatch):
    old_main = "b" * 40
    current_main = "c" * 40

    def request(_repository, _token, _method, path, _payload=None):
        if path == f"commits/{HEAD}/statuses?per_page=100":
            return [
                {
                    "state": "success",
                    "context": "Hunter Reviewer Collector / PR #472",
                    "description": "collector_run_id=777",
                    "creator": {"login": "github-actions[bot]"},
                }
            ]
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": current_main}
        if path == "actions/runs/777":
            return {
                "id": 777,
                "head_branch": "main",
                "head_sha": old_main,
                "path": ".github/workflows/hunter-reviewer-collector.yml",
                "event": "repository_dispatch",
                "status": "completed",
                "conclusion": "success",
            }
        if path == f"compare/{old_main}...{current_main}":
            return {"status": "ahead"}
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    state, run_id, reason = orchestrator.read_collector_completion("owner/repo", "token", 472, HEAD)

    assert reason is None
    assert state == "present"
    assert run_id == 777


def test_remediation_generation_accepts_only_configured_authority_bots(monkeypatch):
    def graphql(**_kwargs):
        def node(thread_id, login):
            return {
                "id": thread_id,
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "databaseId": len(thread_id),
                            "createdAt": "2026-09-19T12:00:00Z",
                            "author": {"login": login, "__typename": "Bot"},
                        }
                    ]
                },
            }

        return {
            "repository": {
                "pullRequest": {
                    "author": {"login": "fafa33"},
                    "reviewThreads": {
                        "nodes": [
                            node("codex-thread", "chatgpt-codex-connector"),
                            node("hunter-thread", "github-actions[bot]"),
                            node("random-thread", "unrelated-review-bot"),
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            }
        }

    monkeypatch.setattr(orchestrator.transport, "request_graphql_json", graphql)
    threads = orchestrator.blocking_reviewer_threads("owner/repo", "token", 473)
    assert [thread.thread_id for thread in threads] == ["codex-thread"]


def test_dispatch_identity_and_timestamp_are_durable_before_dispatch(monkeypatch):
    published = []
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 777)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: published.append(cycle))
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0))

    def accepted_then_process_dies(*_args):
        raise RuntimeError("post-acceptance transport loss")

    monkeypatch.setattr(orchestrator, "dispatch_collector", accepted_then_process_dies)

    with pytest.raises(RuntimeError, match="post-acceptance"):
        orchestrator.ensure_collector("owner/repo", "token", 473, HEAD)

    assert len(published) == 1
    assert published[0].trigger_id == 777
    assert published[0].started_at
    assert orchestrator._older_than(published[0].started_at, orchestrator.COLLECTOR_LIVENESS_GRACE_SECONDS) is False


# --- PR #547: a completed, exhausted reviewer chain must end the opportunity ---
#
# The exact-head cycle status this orchestrator publishes carries no `creator`
# field in GitHub's combined-status response, so `_parse_cycle` rejected it,
# `read_cycle` reported "absent", and Merge Readiness reported
# WAITING_FOR_REVIEWER forever -- including after a collector completed with
# Codex NO_ACK_TIMEOUT, Copilot REVIEW_TIMEOUT and Gemini/Groq
# PROVIDER_UNAVAILABLE. An exhausted chain must terminate the opportunity.


def _exhausted_cycle_status(creator, context, description):
    status = {"context": context, "description": description, "created_at": "2026-09-30T10:39:31Z"}
    if creator is not None:
        status["creator"] = {"login": creator}
    return status


def test_exhausted_provider_chain_terminates_the_opportunity(monkeypatch):
    """Codex NO_ACK_TIMEOUT -> Copilot REVIEW_TIMEOUT -> Gemini/Groq
    PROVIDER_UNAVAILABLE -> collector complete -> reconcile must produce a
    terminal state, never an indefinite WAITING_FOR_REVIEWER."""

    stored = {"published": []}
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_args: (
            "present",
            orchestrator.ReviewCycle(
                pr_number=472,
                head_sha=HEAD,
                state="WAITING_FOR_REVIEWER",
                provider_id="",
                trigger_id=36703742750,
                started_at="2026-09-30T10:00:00Z",
                config_digest=orchestrator.reviewer_pool_config_digest(),
                generation_id="gen-1",
            ),
            None,
        ),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "collector_needs_dispatch", lambda *_args: False, raising=False)
    # This exhausted-chain fixture has no correlated successful collector run.
    monkeypatch.setattr(orchestrator, "collector_runs", lambda *_args: [], raising=False)
    monkeypatch.setattr(
        orchestrator,
        "publish_cycle",
        lambda *_a, cycle: stored["published"].append(cycle),
        raising=False,
    )

    cycle = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, "gen-1")

    # The opportunity terminates, and it terminates as a non-blocking terminal
    # state that grants no review authority.
    assert cycle.state != "WAITING_FOR_REVIEWER"
    assert cycle.state in orchestrator.TERMINAL_NONBLOCKING_STATES
    assert stored["published"] and stored["published"][-1].state == cycle.state
    # A terminal non-blocking state is published as a non-pending status.
    assert cycle.state in orchestrator.TERMINAL_NONBLOCKING_STATES


# --- Codex P1: a status with no reported creator is not, by itself, a binding.
#
# context, description and target_url are all caller-controlled. A publisher who
# can post a status can cite an existing trusted default-branch run in target_url
# and write any cycle state into the description. So `creator == null` may only
# assert a non-authoritative pending state, and the run recorded in the payload
# must be the very run target_url names.


# --- Codex P1 4147741248: the publisher must be authenticated, strictly. -----
#
# The combined status endpoint reports `creator: null` for every status, including
# trusted workflow posts, so a creator check read from it could never be satisfied.
# That is what forced a creator-less fallback trusting a caller-controlled payload.
# `read_cycle` now reads the status LIST endpoint, which carries the real
# publisher, so the strict check stands alone and no derivation path exists.

CYCLE_CTX = f"{orchestrator.CONTEXT_PREFIX}472"
GEN = "e4a1e847caf03a75"
RUN_ID = 36710945665
RUN_URL = f"https://github.com/owner/repo/actions/runs/{RUN_ID}"
TRUSTED = "github-actions[bot]"


def _status(creator, description, target_url=RUN_URL, context=CYCLE_CTX):
    status = {
        "context": context,
        "description": description,
        "created_at": "2026-01-01T00:00:00Z",
        "target_url": target_url,
    }
    if creator is not None:
        status["creator"] = {"login": creator}
    return status


def _desc(state, provider="", trigger=RUN_ID, generation=GEN, digest=None):
    return f"{state}|{provider}|{trigger}|{digest or ('d' * 64)}|{generation}"


def test_combined_status_endpoint_exposes_no_creator():
    """Regression guard: the combined endpoint reports creator=null even for a
    status the list endpoint attributes to the trusted publisher. Proven live for
    one identical status id on this repository."""

    combined = {"context": CYCLE_CTX, "description": _desc("REVIEW_CLEAR"), "creator": None}
    listed = {"context": CYCLE_CTX, "description": _desc("REVIEW_CLEAR"), "creator": {"login": TRUSTED}}

    # The combined shape is exactly why the strict check could not be satisfied
    # from that endpoint: it carries no publisher at all.
    assert orchestrator._parse_cycle(combined, 472, HEAD) is None
    # The same status read from the list endpoint is authenticated and accepted.
    assert orchestrator._parse_cycle(listed, 472, HEAD) is not None


def test_read_cycle_reads_the_status_list_endpoint():
    """`read_cycle` must not use the combined endpoint for provenance."""

    import inspect

    source = inspect.getsource(orchestrator.read_cycle)
    assert "_all_commit_statuses(repository, token, head_sha)" in source
    assert 'f"commits/{head_sha}/status"' not in source
    # Only the list endpoint, so the publisher is actually populated.
    assert "commits/{head_sha}/statuses?per_page=100" in inspect.getsource(orchestrator._all_commit_statuses)


def test_trusted_creator_cycle_is_accepted():
    parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc("WAITING_FOR_REVIEWER")), 472, HEAD)
    assert parsed is not None
    assert parsed.state == "WAITING_FOR_REVIEWER"
    assert parsed.trigger_id == RUN_ID


def test_missing_creator_is_rejected():
    assert orchestrator._parse_cycle(_status(None, _desc("WAITING_FOR_REVIEWER")), 472, HEAD) is None


def test_wrong_creator_is_rejected():
    assert orchestrator._parse_cycle(_status("attacker", _desc("WAITING_FOR_REVIEWER")), 472, HEAD) is None


def test_forged_terminal_timeout_is_rejected_without_trusted_creator():
    """A creator-less payload cannot terminate the opportunity any more: there is
    no age-based or status-age authentication left to lean on."""

    assert orchestrator._parse_cycle(_status(None, _desc("REVIEW_TIMED_OUT")), 472, HEAD) is None
    assert orchestrator._parse_cycle(_status("attacker", _desc("REVIEW_TIMED_OUT")), 472, HEAD) is None


def test_genuine_terminal_states_from_the_trusted_creator_are_accepted():
    """An authenticated publisher may still assert the terminal outcomes, which is
    what makes the bounded opportunity terminate instead of stalling."""

    for state in ("REVIEW_TIMED_OUT", "POOL_EXHAUSTED", "REVIEWER_UNAVAILABLE"):
        parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc(state)), 472, HEAD)
        assert parsed is not None, state
        assert parsed.state == state


def test_genuine_review_clear_and_findings_open_remain_accepted():
    for state in ("REVIEW_CLEAR", "FINDINGS_OPEN"):
        parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc(state)), 472, HEAD)
        assert parsed is not None, state
        assert parsed.state == state


def test_exact_head_and_generation_binding_are_unchanged():
    """Every non-provenance binding still holds, authenticated creator or not."""

    assert orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR")), 473, HEAD) is None
    assert (
        orchestrator._parse_cycle(
            _status(TRUSTED, _desc("REVIEW_CLEAR"), context=f"{orchestrator.CONTEXT_PREFIX}999"), 472, HEAD
        )
        is None
    )
    assert (
        orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR", generation="not-a-generation")), 472, HEAD)
        is None
    )
    assert orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR", digest="short")), 472, HEAD) is None


def test_completed_collector_settles_stale_pending_without_waiting_for_full_chain(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    run = _collector_run("completed", "success")
    run["updated_at"] = "2020-01-01T00:00:00Z"
    stored = _ensure_harness(monkeypatch, cycle, [run])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"
    assert stored["dispatches"] == 0
    assert stored["published"][-1].head_sha == HEAD


def test_recently_completed_collector_preserves_status_propagation_grace(monkeypatch):
    from datetime import UTC, datetime

    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    run = _collector_run("completed", "success")
    run["updated_at"] = datetime.now(UTC).isoformat()
    stored = _ensure_harness(monkeypatch, cycle, [run])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_stale_success_with_active_retry_cannot_outlive_bounded_ceiling(monkeypatch):
    from datetime import UTC, datetime, timedelta

    budget = orchestrator.independent_review_opportunity_seconds()
    ceiling = budget * orchestrator.ACTIVE_COLLECTOR_GRACE_MULTIPLIER
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(ceiling + 60))
    success = _collector_run("completed", "success")
    success["updated_at"] = (datetime.now(UTC) - timedelta(seconds=ceiling)).isoformat()
    stored = _ensure_harness(monkeypatch, cycle, [success, _collector_run("in_progress")])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "REVIEW_TIMED_OUT"
    assert [item.state for item in stored["published"]] == ["REVIEW_TIMED_OUT"]


def test_completed_collector_grace_survives_expired_independent_budget(monkeypatch):
    from datetime import UTC, datetime, timedelta

    budget = orchestrator.independent_review_opportunity_seconds()
    cycle = make_cycle(trigger_id=123, started_at=_seconds_ago(budget + 60))
    run = _collector_run("completed", "success")
    run["updated_at"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    stored = _ensure_harness(monkeypatch, cycle, [run])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_completed_collector_grace_boundary_at_179_and_180_seconds(monkeypatch):
    from datetime import UTC, datetime, timedelta

    fixed_now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    real_datetime = orchestrator.datetime

    class FixedDatetime:
        @staticmethod
        def now(tz):
            return fixed_now

        @staticmethod
        def fromisoformat(value):
            return real_datetime.fromisoformat(value)

    monkeypatch.setattr(orchestrator, "datetime", FixedDatetime)
    for age, expected in ((179, "WAITING_FOR_REVIEWER"), (180, "REVIEW_TIMED_OUT")):
        cycle = make_cycle(trigger_id=123, started_at="2026-10-09T11:58:00Z")
        run = _collector_run("completed", "success")
        run["updated_at"] = (fixed_now - timedelta(seconds=age)).isoformat()
        stored = _ensure_harness(monkeypatch, cycle, [run])
        assert orchestrator.ensure_collector("owner/repo", "token", 472, HEAD).state == expected
        assert bool(stored["published"]) == (age == 180)


def test_completed_collector_with_active_retry_never_settles_early(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    completed = _collector_run("completed", "success")
    completed["updated_at"] = "2020-01-01T00:00:00Z"
    active = _collector_run("in_progress", run_id=2)
    stored = _ensure_harness(monkeypatch, cycle, [completed, active])
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_newer_successful_collector_prevents_premature_settlement(monkeypatch):
    from datetime import UTC, datetime

    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    old = _collector_run("completed", "success", run_id=1)
    old["updated_at"] = "2020-01-01T00:00:00Z"
    new = _collector_run("completed", "success", run_id=2)
    new["updated_at"] = datetime.now(UTC).isoformat()
    stored = _ensure_harness(monkeypatch, cycle, [old, new])
    assert orchestrator.ensure_collector("owner/repo", "token", 472, HEAD).state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_malformed_latest_success_timestamp_fails_closed(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    old = _collector_run("completed", "success", run_id=1)
    old["updated_at"] = "2020-01-01T00:00:00Z"
    new = _collector_run("completed", "success", run_id=2)
    new["updated_at"] = "not-a-date"
    stored = _ensure_harness(monkeypatch, cycle, [old, new])
    assert orchestrator.ensure_collector("owner/repo", "token", 472, HEAD).state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_completed_collector_rejects_naive_timestamps(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    run = _collector_run("completed", "success")
    run["updated_at"] = "2020-01-01T00:00:00"
    stored = _ensure_harness(monkeypatch, cycle, [run])
    assert orchestrator.ensure_collector("owner/repo", "token", 472, HEAD).state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def test_completed_collector_does_not_overwrite_concurrent_terminal(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    run = _collector_run("completed", "success")
    run["updated_at"] = "2020-01-01T00:00:00Z"
    stored = _ensure_harness(monkeypatch, cycle, [run])
    terminal = replace(cycle, state="REVIEWER_UNAVAILABLE")
    reads = iter([cycle, terminal])
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("present", next(reads), None))
    assert orchestrator.ensure_collector("owner/repo", "token", 472, HEAD).state == "REVIEWER_UNAVAILABLE"
    assert stored["published"] == []


def test_duplicate_collector_appearing_at_terminal_boundary_blocks_timeout(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at=_five_minutes_ago())
    successful = _collector_run("completed", "success")
    successful["updated_at"] = "2020-01-01T00:00:00Z"
    runs = [successful]
    stored = _ensure_harness(monkeypatch, cycle, runs)
    original_read = orchestrator.read_cycle
    reads = 0

    def racing_read(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            runs.append(_collector_run("in_progress", run_id=2))
        return original_read(*args)

    monkeypatch.setattr(orchestrator, "read_cycle", racing_read)
    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)
    assert result.state == "WAITING_FOR_REVIEWER"
    assert stored["published"] == []


def _candidate_status_publishers(document):
    """Candidate-owned workflow definitions must never receive status/check write authority."""
    if not isinstance(document, dict):
        return []
    triggers = _triggers(document)
    push = document.get("on", document.get(True))
    push = push.get("push") if isinstance(push, dict) else None
    main_only_push = (
        isinstance(push, dict)
        and push.get("branches") == ["main"]
        and not push.get("branches-ignore")
        and not push.get("tags")
    )
    if (
        not ({"pull_request", "pull_request_review", "pull_request_review_comment"} & set(triggers))
        and not ("push" in triggers and not main_only_push)
        and "workflow_dispatch" not in triggers
    ):
        return []
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        return ["missing-workflow-permissions"]
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    unsafe = []
    for block in blocks:
        if isinstance(block, str) and block.strip() == "write-all":
            unsafe.append("write-all")
        elif isinstance(block, dict):
            unsafe.extend(key for key in ("statuses", "checks") if str(block.get(key, "")).strip() in WORKFLOW_DRIVING)
    return unsafe


def test_candidate_workflows_cannot_publish_protected_commit_statuses():
    unsafe = [(path.name, _candidate_status_publishers(document)) for path, document in _workflow_documents()]
    assert [(name, grants) for name, grants in unsafe if grants] == []


@pytest.mark.parametrize(
    "trigger",
    [
        "on: pull_request",
        "on: pull_request_review",
        "on: pull_request_review_comment",
        "on: [push, pull_request]",
        '"on": {pull_request: null}',
        "on: push",
        "on: {push: {branches-ignore: [main]}}",
        "on: workflow_dispatch",
    ],
)
@pytest.mark.parametrize(
    "grant", ["permissions: {statuses: write}", "permissions: {checks: write}", "permissions: write-all"]
)
def test_candidate_status_spoofing_mutation_is_rejected(trigger, grant):
    assert _candidate_status_publishers(yaml.safe_load(f"{trigger}\n{grant}\n"))


@pytest.mark.parametrize("scope", ["statuses", "checks"])
def test_job_override_cannot_publish_protected_status(scope):
    document = yaml.safe_load(
        f"on: pull_request\npermissions: {{{scope}: read}}\njobs:\n  spoof:\n    permissions: {{{scope}: write}}\n"
    )
    assert _candidate_status_publishers(document) == [scope]


def test_job_write_all_cannot_publish_protected_status():
    document = yaml.safe_load(
        "on: pull_request\npermissions: {contents: read}\njobs:\n  spoof:\n    permissions: write-all\n"
    )
    assert _candidate_status_publishers(document) == ["write-all"]


def test_candidate_missing_explicit_permission_baseline_is_rejected():
    document = yaml.safe_load("on: pull_request\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    assert _candidate_status_publishers(document) == ["missing-workflow-permissions"]


def test_main_only_push_is_trusted():
    document = yaml.safe_load("on: {push: {branches: [main]}}\npermissions: {statuses: write}\n")
    assert _candidate_status_publishers(document) == []


def test_scheduled_trusted_collector_recovery_uses_exact_attempt_artifacts():
    from pathlib import Path

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    )
    step = next(
        item
        for item in workflow["jobs"]["reconcile"]["steps"]
        if item.get("name") == "Publish trusted collector completion"
    )
    assert "github.event_name == 'schedule'" in step["if"]
    script = step["run"]
    assert "status=completed&per_page=100" in script
    assert "gh api --paginate" in script
    assert "collector_run_id=$run_id" in script
    assert "grep -qE" in script
    assert "recovery_deadline" in script
    assert "recovered < 5" in script
    assert "pulls/$pr" in script
    assert "creator.login" in script and "github-actions[bot]" in script
    assert "run_attempt" in script
    assert "hunter-reviewer-results-*-${attempt}" in script
    assert "hunter-dispatch-proof-*-${attempt}" in script
    assert "trusted-collector-complete" in script
    assert 'rm -rf "$receipt"' in script
    assert 'find "$receipt/results" -type f' in script
    assert 'find "$receipt/proof" -type f' in script


def test_commit_status_pagination_keeps_old_trusted_dispatch_proof(monkeypatch):
    calls = []
    proof = {
        "context": "Hunter Collector Dispatch Proof / PR #472",
        "creator": {"login": "github-actions[bot]"},
        "id": 7,
    }

    def request(_repo, _token, _method, path, _payload=None):
        calls.append(path)
        if path.endswith("page=100"):
            return [{"id": n} for n in range(100)]
        if path.endswith("page=2"):
            return [proof]
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    statuses = orchestrator._all_commit_statuses("owner/repo", "token", HEAD)
    assert len(statuses) == 101
    assert proof in statuses
    assert len(calls) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"head_branch": "attacker"},
        {"status": "completed", "conclusion": "failure"},
    ],
)
def test_untrusted_earlier_claimant_cannot_block_valid_collector(monkeypatch, override):
    valid = {
        "id": 778,
        "created_at": "2026-10-09T19:02:00Z",
        "display_title": f"Hunter Reviewer Collector PR 472 HEAD {HEAD}",
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "success",
    }
    earlier = dict(valid, id=777, created_at="2026-10-09T19:01:00Z", **override)
    request, cycle, _runs = _trusted_collector_fixture(valid, first_run=earlier)
    published = []
    monkeypatch.setattr(orchestrator, "request_json", request)
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "publish_collector_completion", lambda *args: published.append(args))
    result = orchestrator.publish_trusted_collector_completion("owner/repo", "token", 778, "a" * 64)
    assert result.startswith("PUBLISHED:")
    assert len(published) == 1


def test_scheduled_recovery_status_search_does_not_short_circuit_paginated_api():
    """PRH-112: grep -q can SIGPIPE the producer under pipefail."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    assert 'gh api --paginate "repos/$GITHUB_REPOSITORY/commits/$head/statuses?per_page=100"' in workflow
    assert "| grep -qE '^[0-9]+$'" not in workflow
    assert "| grep -E '^[0-9]+$' | wc -l" in workflow


def test_prh112_regression_rejects_original_sigpipe_mutation():
    """Prove that restoring the original early-closing grep fails the guard."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    safe = "| grep -E '^[0-9]+$' | wc -l | grep -qE '^[[:space:]]*[1-9][0-9]*$'"
    unsafe = "| grep -qE '^[0-9]+$'"
    assert safe in workflow
    mutated = workflow.replace(safe, unsafe, 1)
    assert mutated != workflow
    assert safe not in mutated
    assert unsafe in mutated


def test_pr592_defect_registry_never_claims_unverified_findings_prevented():
    """The learning ledger must not silently promote historical review titles."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/PR592_COPILOT_FINDING_LEDGER.json").read_text())
    registry = json.loads((root / "docs/DEFECT_REGISTRY.json").read_text())
    by_id = {entry["id"]: entry for entry in registry["defects"]}
    assert len(ledger["findings"]) == ledger["captured_findings"] == 43
    for finding in ledger["findings"]:
        assert finding["id"] in by_id
        assert finding["status"] != "prevented"
        assert finding.get("verification")


def test_missing_workflow_permission_baseline_is_detected_in_action_guard():
    import yaml

    document = yaml.safe_load("on: pull_request\njobs: {build: {runs-on: ubuntu-latest}}\n")
    assert "missing-workflow-permissions" in list(_actions_levels(document))


def test_scheduled_collector_recovery_bounds_history_fetch_before_processing():
    """An unbounded --paginate prefetch must never precede the recovery deadline."""
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    recovery = workflow.split("- name: Recover trusted collector completion", 1)[-1]
    assert "timeout 12s gh api" in recovery
    assert "scanned_pages < 3" in recovery
    assert "SECONDS < recovery_deadline" in recovery
    assert "mapfile -t candidates" not in recovery
    assert (
        'gh api --paginate "repos/$GITHUB_REPOSITORY/actions/workflows/hunter-reviewer-collector.yml/runs'
        not in recovery
    )


def test_privileged_recovery_uses_default_branch_dispatch_not_candidate_ref():
    from pathlib import Path

    workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
    for name in ("hunter-governance-reconcile.yml", "hunter-merge-readiness.yml"):
        source = (workflows / name).read_text()
        triggers = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        assert "repository_dispatch:" in triggers
        assert "hunter-trusted-recovery" in triggers
        assert "workflow_dispatch:" not in triggers


def test_privileged_collector_never_loads_candidate_selected_workflow():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / ".github/workflows/hunter-reviewer-collector.yml").read_text()
    triggers = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "repository_dispatch:" in triggers
    assert "hunter-reviewer-collect" in triggers
    assert "workflow_dispatch:" not in triggers
    assert "pull_request:" not in triggers
    assert "pull_request_target:" not in triggers
    orchestrator_source = (root / "scripts/hunter_review_orchestrator.py").read_text()
    assert '"event_type": "hunter-reviewer-collect"' in orchestrator_source
    assert '"client_payload": {' in orchestrator_source
