"""Adversarial tests of the native collector -> verified publisher -> governance flow.

Identity is GitHub's ``pull_request_target`` event, re-verified against fresh API state; a collector
result becomes a Hunter result only through the trusted publisher after the actual run, its
integrity-checked receipt and the current pull request have all been verified. A forged run title,
an untrusted dispatch, a stale head, a replayed or partial receipt, a duplicate or re-run event, or
unavailable evidence never produces a result: the cycle stays pending (fail closed).
"""

from __future__ import annotations

import hunter_collector_publisher as publisher
import hunter_governance_review_v2 as governance
import hunter_review_orchestrator as orchestration
import hunter_reviewer_collector as collector
import pytest
import test_review_orchestrator_collector_finalization as finalization
from native_github_fake import (
    CLAIMS,
    COLLECTOR_PATH,
    COLLECTOR_RUN,
    HEAD,
    PR,
    PUBLISHER_RUN,
    REPOSITORY,
    NativeFake,
    receipt_for,
)

OTHER_HEAD = "b" * 40


@pytest.fixture
def github(monkeypatch):
    fake = NativeFake().install(monkeypatch)
    fake.allow_review_request(monkeypatch)
    return fake


def start(**overrides):
    arguments = {"event": "pull_request_target"}
    arguments.update(overrides)
    return collector.verify_native_start(REPOSITORY, "token", PR, HEAD, COLLECTOR_RUN, **arguments)


def exhausted_receipt(**overrides):
    values = {
        "repository": REPOSITORY,
        "pr_number": PR,
        "head_sha": HEAD,
        "run_id": COLLECTOR_RUN,
        "claims_id": CLAIMS,
        "remediation_generation_id": orchestration.BASE_GENERATION_ID,
    }
    values.update(overrides)
    return finalization.make_receipt(**values)


def publish_run():
    return publisher.publish(REPOSITORY, "token", COLLECTOR_RUN)


# --- start gate: the collector's identity is the native event, re-verified -----------------------------------


def test_a_clean_native_event_may_collect_for_its_exact_head(github):
    decision = start()
    assert decision.proceed and decision.claims_id == CLAIMS and decision.generation == orchestration.BASE_GENERATION_ID


@pytest.mark.parametrize("event", ["repository_dispatch", "workflow_dispatch", "push", "schedule", "pull_request", ""])
def test_an_untrusted_event_never_collects(github, event):
    with pytest.raises(ValueError, match="untrusted event"):
        start(event=event)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.runs[COLLECTOR_RUN].update(event="repository_dispatch"),
        lambda g: g.runs[COLLECTOR_RUN].update(event="workflow_dispatch"),
        lambda g: g.runs[COLLECTOR_RUN].update(path=".github/workflows/ci.yml"),
        lambda g: g.runs[COLLECTOR_RUN].update(head_sha=OTHER_HEAD),
        lambda g: g.runs[COLLECTOR_RUN].update(id=COLLECTOR_RUN + 1),
        lambda g: g.runs[COLLECTOR_RUN].update(head_sha="not-a-sha"),
        lambda g: g.runs[COLLECTOR_RUN].update(pull_requests=[]),
        lambda g: g.runs[COLLECTOR_RUN].update(pull_requests=[{"number": PR + 1}]),
    ],
    ids=["dispatch", "manual", "other-workflow", "other-head", "other-run", "bad-revision", "no-pr", "other-pr"],
)
def test_a_run_that_is_not_this_prs_pull_request_target_collector_run_never_collects(github, mutate):
    mutate(github)
    with pytest.raises(ValueError):
        start()


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda g: g.prs[PR].update(state="closed"), "not open"),
        (lambda g: g.prs[PR].update(draft=True), "draft"),
        (lambda g: g.prs[PR].update(base={"ref": "release"}), "default branch"),
        (lambda g: g.prs[PR].update(head={"sha": OTHER_HEAD}), "no longer the pull-request head"),
    ],
    ids=["closed", "draft", "retargeted", "stale-head"],
)
def test_a_pull_request_that_moved_on_is_skipped_without_a_receipt(github, mutate, reason):
    mutate(github)
    decision = start()
    assert not decision.proceed and reason in decision.reason


def test_unreadable_pull_request_state_fails_closed_instead_of_collecting(github):
    github.unavailable.add(f"pulls/{PR}")
    with pytest.raises(Exception, match="unavailable"):
        start()


def test_an_unready_prerequisite_is_a_quiet_retryable_skip(github, monkeypatch):
    github.allow_review_request(monkeypatch, ready=False, state="pending", reason="preflight still running")
    decision = start()
    assert not decision.proceed and "not usable" in decision.reason


@pytest.mark.parametrize("state", ["success", "present", "invalid"])
def test_a_request_that_can_never_resolve_itself_is_reported_not_swallowed(github, monkeypatch, state):
    github.allow_review_request(monkeypatch, ready=False, state=state, reason="request is not valid for this head")
    with pytest.raises(orchestration.ReviewRequestBlocked):
        start()


@pytest.mark.parametrize("state", ["absent", "unavailable"])
def test_an_absent_or_unreadable_request_stays_retryable(github, monkeypatch, state):
    github.allow_review_request(monkeypatch, ready=False, state=state, reason="nothing committed yet")
    assert not start().proceed


def test_an_authenticated_exact_head_codex_clear_needs_no_collection(github, monkeypatch):
    monkeypatch.setattr(orchestration, "exact_head_codex_clear_exists", lambda *_a: True)
    assert not start().proceed


def test_a_terminal_cycle_for_this_head_and_generation_is_not_collected_again(github):
    github.add_cycle_status(state="REVIEWER_UNAVAILABLE")
    decision = start()
    assert not decision.proceed and "already REVIEWER_UNAVAILABLE" in decision.reason


def test_an_open_cycle_does_not_block_a_manual_rerun(github):
    github.add_cycle_status(state="WAITING_FOR_REVIEWER")
    assert start().proceed


def test_a_terminal_cycle_published_by_the_collector_run_is_not_believed_and_does_not_skip(github):
    github.add_cycle_status(state="REVIEWER_UNAVAILABLE", run_id=COLLECTOR_RUN)
    assert start().proceed


def _trigger_comment(generation: str, claims: str = CLAIMS, head: str = HEAD, login: str = "github-actions[bot]"):
    agent = {
        "id": "codex",
        "trigger_method": "github-pr-comment:@codex review",
        "github_login": "chatgpt-codex-connector[bot]",
    }
    return {"user": {"login": login}, "body": collector.trigger_body(head, claims, agent, 1, 1, 1, generation)}


def test_the_generation_budget_counts_distinct_generations_from_head_keyed_trigger_comments(github, monkeypatch):
    generations = ["0000000000000001", "0000000000000002", "0000000000000003"]
    github.comments[PR] = [_trigger_comment(item) for item in generations]
    monkeypatch.setattr(orchestration, "current_remediation_generation", lambda *_a: "0000000000000004")
    decision = start()
    assert not decision.proceed and "budget" in decision.reason
    # A generation already spent is the same cycle, not a new one.
    monkeypatch.setattr(orchestration, "current_remediation_generation", lambda *_a: generations[0])
    assert start().proceed


def test_trigger_comments_for_another_head_claims_or_author_spend_no_budget(github, monkeypatch):
    github.comments[PR] = [
        _trigger_comment("0000000000000001", head=OTHER_HEAD),
        _trigger_comment("0000000000000002", claims="e" * 64),
        _trigger_comment("0000000000000003", login="someone-else"),
    ]
    monkeypatch.setattr(orchestration, "current_remediation_generation", lambda *_a: "0000000000000009")
    assert collector.remediation_generations_used(REPOSITORY, "token", PR, HEAD, CLAIMS) == set()
    assert start().proceed


# --- run and receipt verification: nothing is trusted alone --------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.runs[COLLECTOR_RUN].update(event="repository_dispatch"),
        lambda g: g.runs[COLLECTOR_RUN].update(event="workflow_dispatch"),
        lambda g: g.runs[COLLECTOR_RUN].update(path=".github/workflows/ci.yml"),
        lambda g: g.runs[COLLECTOR_RUN].update(head_sha=OTHER_HEAD),
        lambda g: g.runs[COLLECTOR_RUN].update(conclusion="failure"),
        lambda g: g.runs[COLLECTOR_RUN].update(conclusion="cancelled"),
        lambda g: g.runs[COLLECTOR_RUN].update(conclusion="timed_out"),
        lambda g: g.runs[COLLECTOR_RUN].update(status="in_progress", conclusion=None),
        lambda g: g.runs[COLLECTOR_RUN].update(pull_requests=[]),
        lambda g: g.prs[PR].update(base={"ref": "release"}),
    ],
    ids=[
        "dispatch",
        "manual",
        "other-workflow",
        "other-head",
        "failed",
        "cancelled",
        "timed-out",
        "still-running",
        "no-pr",
        "retargeted",
    ],
)
def test_only_a_completed_successful_trusted_collector_run_is_verified(github, mutate):
    github.add_receipt(receipt_for())
    mutate(github)
    with pytest.raises(ValueError):
        collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)


def test_a_verified_run_yields_its_own_receipt_and_identity(github):
    github.add_receipt(receipt_for())
    run, receipt = collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)
    assert run["id"] == COLLECTOR_RUN and (receipt["pr_number"], receipt["head_sha"]) == (PR, HEAD)


def test_a_forged_run_title_confers_no_identity(github):
    """The run title is display only: a title naming another PR and head changes nothing."""
    github.runs[COLLECTOR_RUN]["display_title"] = f"Hunter Reviewer Collector PR 999 HEAD {OTHER_HEAD}"
    github.add_receipt(receipt_for())
    _run, receipt = collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)
    assert (receipt["pr_number"], receipt["head_sha"]) == (PR, HEAD)
    with pytest.raises(ValueError):  # and it cannot be used to claim the other PR either
        collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN, 999, OTHER_HEAD)


@pytest.mark.parametrize(
    "build",
    [
        lambda g: None,  # no artifact at all
        lambda g: (g.add_receipt(receipt_for()), g.add_receipt(receipt_for())),  # duplicate artifact
        lambda g: (g.add_receipt(receipt_for()), g.artifacts[COLLECTOR_RUN][0].update(expired=True)),
        lambda g: g.add_receipt(receipt_for(), tamper_digest=True),
        lambda g: (g.add_receipt(receipt_for()), g.artifacts[COLLECTOR_RUN][0].update(workflow_run={"id": 7})),
        lambda g: (
            g.add_receipt(receipt_for()),
            g.artifacts[COLLECTOR_RUN][0].update(name="hunter-reviewer-results-x-1"),
        ),
        lambda g: (
            g.add_receipt(receipt_for()),
            g.artifacts[COLLECTOR_RUN][0].update(name=f"hunter-reviewer-results-{HEAD}-2"),
        ),
    ],
    ids=["missing", "duplicate", "expired", "digest-mismatch", "another-run", "bad-name", "other-attempt"],
)
def test_an_artifact_that_is_not_the_runs_own_intact_receipt_is_refused(github, build):
    build(github)
    with pytest.raises(ValueError):
        collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": COLLECTOR_RUN + 1},
        {"run_attempt": 2},
        {"claims_id": "short"},
        {"attempts": "x"},
        {"repository": "evil/repo"},
        {"schema": "hunter.other.v1"},
    ],
)
def test_a_receipt_that_disagrees_with_the_run_that_produced_it_is_refused(github, overrides):
    receipt = receipt_for(**overrides)
    github.add_receipt(receipt_for())
    # Replace the stored archive with the inconsistent receipt under the real artifact identity.
    github.artifacts[COLLECTOR_RUN].clear()
    github.add_receipt({**receipt, "head_sha": receipt["head_sha"]})
    github.artifacts[COLLECTOR_RUN][0]["name"] = f"hunter-reviewer-results-{receipt['head_sha']}-1"
    with pytest.raises(ValueError):
        collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)


def test_a_rerun_attempt_is_verified_against_its_own_attempt_and_artifact(github):
    github.runs[COLLECTOR_RUN]["run_attempt"] = 2
    github.add_receipt(receipt_for(attempt=1))  # the first attempt's artifact name carries attempt 1
    github.artifacts[COLLECTOR_RUN][0]["name"] = f"hunter-reviewer-results-{HEAD}-1"
    with pytest.raises(ValueError):
        collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)
    github.artifacts[COLLECTOR_RUN].clear()
    github.add_receipt(receipt_for(attempt=2))
    assert collector.verified_collector_receipt(REPOSITORY, "token", COLLECTOR_RUN)[0]["run_attempt"] == 2


# --- the trusted publisher ------------------------------------------------------------------------------------


def test_an_exhausted_receipt_publishes_the_non_blocking_terminal_state_once(github):
    github.add_receipt(exhausted_receipt())
    outcome, pr = publish_run()
    assert outcome == "PUBLISHED: REVIEWER_UNAVAILABLE" and pr == PR
    [(sha, status)] = github.posted
    assert sha == HEAD and status["context"] == f"{orchestration.CONTEXT_PREFIX}{PR}"
    assert status["state"] == "success" and status["description"].startswith("REVIEWER_UNAVAILABLE|")
    assert status["target_url"].endswith(f"/actions/runs/{PUBLISHER_RUN}")  # cites the publisher's own run
    state, cycle, _ = orchestration.read_cycle(REPOSITORY, "token", PR, HEAD)
    assert state == "present" and cycle.state == "REVIEWER_UNAVAILABLE" and cycle.trigger_id == COLLECTOR_RUN


def test_a_duplicate_or_re_delivered_event_publishes_nothing_more(github):
    github.add_receipt(exhausted_receipt())
    publish_run()
    outcome, pr = publish_run()
    assert outcome == "SKIPPED: the cycle is already REVIEWER_UNAVAILABLE" and pr == PR
    assert len(github.posted) == 1


def test_a_status_the_collector_posted_for_itself_is_not_a_published_result(github):
    github.add_receipt(exhausted_receipt())
    github.add_cycle_status(state="REVIEWER_UNAVAILABLE", run_id=COLLECTOR_RUN)
    outcome, _pr = publish_run()
    assert outcome.startswith("PUBLISHED")  # the forged collector-cited status did not suppress the real one


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda g: g.prs[PR].update(state="closed"), "SKIPPED: pull request is not open"),
        (lambda g: g.prs[PR].update(draft=True), "SKIPPED: the pull request is a draft"),
        (lambda g: g.prs[PR].update(head={"sha": OTHER_HEAD}), "SKIPPED: the collected head is no longer"),
    ],
    ids=["closed", "draft", "stale-head"],
)
def test_a_result_for_a_pull_request_that_moved_on_is_not_published(github, mutate, expected):
    github.add_receipt(exhausted_receipt())
    mutate(github)
    outcome, pr = publish_run()
    assert outcome.startswith(expected) and pr is None and github.posted == []


def test_a_receipt_for_other_claims_or_a_stale_generation_publishes_nothing(github, monkeypatch):
    github.add_receipt(exhausted_receipt(claims_id="e" * 64))
    outcome, pr = publish_run()
    assert outcome.startswith("NO TERMINAL RESULT") and pr == PR and github.posted == []
    github.artifacts[COLLECTOR_RUN].clear()
    github.add_receipt(exhausted_receipt())
    monkeypatch.setattr(orchestration, "current_remediation_generation", lambda *_a: "0000000000000007")
    outcome, _pr = publish_run()
    assert outcome.startswith("NO TERMINAL RESULT") and github.posted == []


def test_a_receipt_with_a_substantive_reviewer_verdict_never_becomes_unavailability(github):
    attempts = finalization._exhausted_attempts()
    attempts[0] = finalization._record(finalization._agents()[0], "clear")
    github.add_receipt(exhausted_receipt(attempts=attempts))
    outcome, pr = publish_run()
    assert outcome.startswith("NO TERMINAL RESULT") and pr == PR and github.posted == []


def test_a_partial_receipt_is_incomplete_evidence_and_publishes_nothing(github):
    github.add_receipt(exhausted_receipt(attempts=finalization._exhausted_attempts()[:1]))
    outcome, _pr = publish_run()
    assert outcome.startswith("NO TERMINAL RESULT") and github.posted == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.runs[COLLECTOR_RUN].update(conclusion="failure"),
        lambda g: g.runs[COLLECTOR_RUN].update(conclusion="cancelled"),
        lambda g: g.runs[COLLECTOR_RUN].update(event="repository_dispatch"),
        lambda g: g.unavailable.add(f"actions/runs/{COLLECTOR_RUN}/artifacts"),
        lambda g: g.unavailable.add(f"pulls/{PR}"),
        lambda g: g.prs[PR].update(base={"ref": "release"}),
    ],
    ids=["failed", "cancelled", "dispatched", "artifacts-unavailable", "pr-unavailable", "retargeted"],
)
def test_a_missing_or_unverifiable_result_fails_closed_and_publishes_nothing(github, mutate):
    github.add_receipt(exhausted_receipt())
    mutate(github)
    with pytest.raises((ValueError, RuntimeError, OSError, orchestration.ReviewRequestBlocked, KeyError)):
        publish_run()
    assert github.posted == []


def test_a_verified_run_with_no_receipt_is_a_quiet_skip_that_publishes_nothing(github):
    """A deliberate collector skip writes no receipt: the publisher run stays green and the cycle pending."""
    outcome, pr = publish_run()
    assert outcome.startswith("SKIPPED: the collector run left no receipt") and pr is None
    assert github.posted == []


def test_an_unreadable_or_duplicated_receipt_is_still_a_hard_failure(github):
    github.add_receipt(exhausted_receipt())
    github.add_receipt(exhausted_receipt())
    with pytest.raises(ValueError, match="unique immutable"):
        publish_run()
    assert github.posted == []


def test_the_publisher_has_no_other_way_to_write_a_status(github):
    """Only the exhausted-pool terminal state exists: nothing publishes clear, findings or a timeout."""
    assert orchestration.TERMINAL_NONBLOCKING_STATES == frozenset({"REVIEWER_UNAVAILABLE", "POOL_EXHAUSTED"})
    import inspect

    source = inspect.getsource(publisher)
    assert "REVIEW_TIMED_OUT" not in source and "REVIEW_CLEAR" not in source and "FINDINGS_OPEN" not in source


# --- cycle statuses are believed only from a trusted publisher run on the default branch -----------------------


@pytest.mark.parametrize(
    ("mutate", "believed"),
    [
        (lambda g: None, True),
        (lambda g: g.runs[PUBLISHER_RUN].update(path=COLLECTOR_PATH), False),
        (lambda g: g.runs[PUBLISHER_RUN].update(path=".github/workflows/ci.yml"), False),
        (lambda g: g.runs[PUBLISHER_RUN].update(head_branch="candidate"), False),
        (lambda g: g.statuses[HEAD][0].update(creator={"login": "someone"}), False),
        (lambda g: g.statuses[HEAD][0].update(context="Hunter Review Orchestration / PR #1"), False),
        (lambda g: g.statuses[HEAD][0].update(target_url=""), False),
        (lambda g: g.prs[PR].update(head={"sha": OTHER_HEAD}), False),
        (lambda g: g.prs[PR].update(state="closed"), False),
    ],
    ids=[
        "publisher",
        "collector-run",
        "other-workflow",
        "off-branch",
        "other-author",
        "other-pr",
        "no-run",
        "stale",
        "closed",
    ],
)
def test_a_cycle_is_believed_only_when_a_trusted_publisher_run_published_it(github, mutate, believed):
    github.add_cycle_status(state="REVIEWER_UNAVAILABLE", run_id=PUBLISHER_RUN)
    mutate(github)
    state, cycle, _error = orchestration.read_cycle(REPOSITORY, "token", PR, HEAD)
    assert (state == "present") is believed and (cycle is not None) is believed


def test_no_cycle_at_all_is_pending_never_success(github):
    state, _cycle, _error = orchestration.read_cycle(REPOSITORY, "token", PR, HEAD)
    assert state == "absent"
    assert governance.review_orchestration_state(REPOSITORY, "token", PR, HEAD)[0] == "WAITING_FOR_REVIEWER"
    assert governance.pending_review_authority_state(REPOSITORY, "token", PR, HEAD)[0] == "pending"


# --- governance accepts reviewer evidence only from a verified collector run ---------------------------------


def trigger(**overrides):
    value = {
        "collector_run_id": COLLECTOR_RUN,
        "collector_run_attempt": 1,
        "head_sha": HEAD,
        "claims_id": CLAIMS,
    }
    value.update(overrides)
    return value


def test_governance_accepts_a_trigger_whose_run_and_receipt_name_this_pr_head_claims_and_generation(github):
    github.add_receipt(receipt_for())
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(), PR) is True


@pytest.mark.parametrize(
    ("overrides", "pr"),
    [
        ({"collector_run_id": COLLECTOR_RUN + 5}, PR),
        ({"collector_run_attempt": 2}, PR),
        ({"head_sha": OTHER_HEAD}, PR),
        ({"claims_id": "e" * 64}, PR),
        ({"remediation_generation_id": "0000000000000001"}, PR),
        ({"collector_run_id": "1001"}, PR),
        ({}, PR + 1),
    ],
    ids=["other-run", "other-attempt", "other-head", "other-claims", "other-generation", "string-run-id", "other-pr"],
)
def test_governance_refuses_a_trigger_that_does_not_match_the_verified_receipt(github, overrides, pr):
    github.add_receipt(receipt_for())
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(**overrides), pr) is False


def test_governance_refuses_a_trigger_from_a_failed_dispatched_or_unreceipted_run(github):
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(), PR) is False  # no receipt
    github.add_receipt(receipt_for())
    github.runs[COLLECTOR_RUN]["conclusion"] = "failure"
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(), PR) is False
    github.runs[COLLECTOR_RUN].update(conclusion="success", event="repository_dispatch")
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(), PR) is False
    github.runs[COLLECTOR_RUN]["event"] = "pull_request_target"
    github.unavailable.add(f"actions/runs/{COLLECTOR_RUN}")
    assert governance.trusted_collector_run(REPOSITORY, "token", trigger(), PR) is False
