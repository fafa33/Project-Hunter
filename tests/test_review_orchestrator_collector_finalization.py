"""The collector ends an exhausted reviewer pool itself (Issue #574 / PR #576).

Live evidence: a dispatched collector completed with every enabled reviewer unavailable or silent, yet no reconcile ever
followed (17 of 17 recorded collector completions had no collector-triggered ``workflow_run``), so the exact-head cycle
sat in ``WAITING_FOR_REVIEWER``. ``collector-complete`` now finalizes from the collector's own receipt. These tests pin
what it may and may not conclude. Nothing here proves that a reviewer reviewed anything.
"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import hunter_pre_ready_review as pre_ready
import hunter_review_orchestrator as orchestrator
import pytest
import yaml

HEAD = "b" * 40
OTHER_HEAD = "c" * 40
PR = 576
RUN_ID = 37765353885
CLAIMS = "c0bdb6b357b7cd0e3f842f77c3b5a3d8fce6d517cb4970988a077b41390a5ad9"
GENERATION = ""
REPOSITORY = "owner/repo"
ROOT = pathlib.Path(orchestrator.__file__).resolve().parents[1]


def _pool() -> dict[str, Any]:
    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    return pool


def _agents() -> list[dict[str, Any]]:
    return sorted(pre_ready.enabled_pool_reviewers(_pool()), key=lambda agent: agent["priority"])


def _record(agent: dict[str, Any], outcome: str, number: int = 1, trigger_id: int | None = None) -> dict[str, Any]:
    elapsed = {
        "unacknowledged": agent["ack_timeout_seconds"],
        "timed_out": agent["review_timeout_seconds"],
        "unavailable": 1.0,
        "clear": 5.0,
        "blocking": 5.0,
    }[outcome]
    record = {name: agent[name] for name in orchestrator._RECORD_POOL_FIELDS}
    record.update(
        agent_id=agent["id"],
        attempt_number=number,
        trigger_id=trigger_id if trigger_id is not None else 1000 + agent["priority"] * 10 + number,
        outcome=outcome,
        elapsed_seconds=elapsed,
    )
    return record


def _exhausted_attempts() -> list[dict[str, Any]]:
    """What the collector recorded on PR #576: Codex and Copilot silent, Gemini 503, Groq 403."""

    records = []
    for agent in _agents():
        outcome = "unacknowledged" if agent["trigger_method"].startswith("github-review-request:") else "unavailable"
        records.append(_record(agent, outcome))
    return records


def make_receipt(**overrides: Any) -> dict[str, Any]:
    import hunter_reviewer_collector as collector

    receipt: dict[str, Any] = {
        "schema": orchestrator.COLLECTOR_RESULTS_SCHEMA,
        "repository": REPOSITORY,
        "pr_number": PR,
        "head_sha": HEAD,
        "run_id": RUN_ID,
        "run_attempt": 1,
        "claims_id": CLAIMS,
        "remediation_generation_id": GENERATION,
        "configuration_digest": collector.configuration_digest(_pool()),
        "attempts": _exhausted_attempts(),
    }
    receipt.update(overrides)
    return receipt


def verdict(receipt: Any, **overrides: Any) -> orchestrator.PoolExhaustion:
    arguments: dict[str, Any] = {
        "repository": REPOSITORY,
        "pr_number": PR,
        "head_sha": HEAD,
        "run_id": RUN_ID,
        "claims_id": CLAIMS,
        "generation_id": GENERATION,
        "pool": _pool(),
    }
    arguments.update(overrides)
    return orchestrator.pool_exhaustion(receipt, **arguments)


def test_the_receipt_schema_constant_tracks_the_collector():
    import hunter_reviewer_collector as collector

    assert orchestrator.COLLECTOR_RESULTS_SCHEMA == collector.SCHEMA


def test_a_complete_matching_receipt_of_silent_and_unavailable_reviewers_is_exhaustion():
    result = verdict(make_receipt())
    assert result.exhausted, result.reason


@pytest.mark.parametrize("outcome", ["clear", "blocking"])
def test_a_substantive_verdict_from_an_authority_reviewer_is_never_unavailability(outcome):
    attempts = _exhausted_attempts()
    attempts[0] = _record(_agents()[0], outcome)
    assert not verdict(make_receipt(attempts=attempts[:1])).exhausted
    assert not verdict(make_receipt(attempts=attempts)).exhausted


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(schema="hunter.other.v1"),
        lambda r: r.update(repository="other/repo"),
        lambda r: r.update(pr_number=PR + 1),
        lambda r: r.update(pr_number=str(PR)),
        lambda r: r.update(head_sha=OTHER_HEAD),
        lambda r: r.update(run_id=RUN_ID + 1),
        lambda r: r.update(run_attempt=0),
        lambda r: r.update(run_attempt="1"),
        lambda r: r.update(claims_id="d" * 64),
        lambda r: r.update(remediation_generation_id="0123456789abcdef"),
        lambda r: r.update(configuration_digest="e" * 64),
        lambda r: r.pop("attempts"),
        lambda r: r.update(attempts="none"),
        lambda r: r.update(attempts=[]),
    ],
)
def test_a_stale_mixed_or_malformed_receipt_identity_is_never_exhaustion(mutate):
    receipt = make_receipt()
    mutate(receipt)
    assert not verdict(receipt).exhausted


@pytest.mark.parametrize("receipt", [None, [], "text", 3, {}])
def test_a_receipt_that_is_not_the_expected_object_is_never_exhaustion(receipt):
    assert not verdict(receipt).exhausted


def test_a_partial_receipt_is_not_exhaustion():
    attempts = _exhausted_attempts()
    assert not verdict(make_receipt(attempts=attempts[:-1])).exhausted
    assert not verdict(make_receipt(attempts=attempts[:1])).exhausted


def test_extra_out_of_order_or_duplicated_records_are_not_exhaustion():
    attempts = _exhausted_attempts()
    extra = [*attempts, copy.deepcopy(attempts[-1])]
    swapped = [attempts[1], attempts[0], *attempts[2:]]
    duplicated = copy.deepcopy(attempts)
    duplicated[1]["trigger_id"] = duplicated[0]["trigger_id"]
    duplicated[1]["agent_id"] = duplicated[0]["agent_id"]
    for candidate in (extra, swapped, duplicated):
        assert not verdict(make_receipt(attempts=candidate)).exhausted


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rec: rec.update(outcome="skipped"),
        lambda rec: rec.update(outcome=None),
        lambda rec: rec.update(elapsed_seconds="fast"),
        lambda rec: rec.update(elapsed_seconds=-1),
        lambda rec: rec.update(elapsed_seconds=99999),
        lambda rec: rec.update(trigger_id=True),
        lambda rec: rec.update(trigger_id=-5),
        lambda rec: rec.update(ack_timeout_seconds=1),
        lambda rec: rec.update(trigger_method="api:other"),
        lambda rec: rec.update(attempt_number=2),
        lambda rec: rec.update(agent_id="somebody-else"),
    ],
)
def test_a_malformed_invocation_record_is_never_exhaustion(mutate):
    attempts = _exhausted_attempts()
    mutate(attempts[0])
    assert not verdict(make_receipt(attempts=attempts)).exhausted


def test_a_timeout_or_missing_ack_must_have_actually_spent_its_budget():
    agents = _agents()
    early_silence = _exhausted_attempts()
    early_silence[0]["elapsed_seconds"] = early_silence[0]["ack_timeout_seconds"] - 1
    assert not verdict(make_receipt(attempts=early_silence)).exhausted
    early_timeout = _exhausted_attempts()
    early_timeout[0] = _record(agents[0], "timed_out")
    early_timeout[0]["elapsed_seconds"] = agents[0]["review_timeout_seconds"] - 1
    assert not verdict(make_receipt(attempts=early_timeout)).exhausted


def test_a_final_timeout_is_exhaustion_only_after_the_retry_budget_is_spent():
    pool = _pool()
    agents = _agents()
    retries = pool["timeout_policy"]["retries_per_agent"]
    attempts = []
    for agent in agents:
        budget = 1 + (retries if agent["retryable"] else 0)
        for number in range(1, budget + 1):
            attempts.append(_record(agent, "timed_out", number))
    assert verdict(make_receipt(attempts=attempts)).exhausted
    if retries and any(agent["retryable"] for agent in agents):
        assert not verdict(make_receipt(attempts=attempts[:-1])).exhausted


# --- finalize_exhausted_pool: what it publishes, and when it must not -----------------------------------------------


class Harness:
    def __init__(self, monkeypatch, tmp_path, *, receipt=None, cycles=None, generation=GENERATION):
        self.published: list[orchestrator.ReviewCycle] = []
        self.dispatches = 0
        self.reads = 0
        self.cycles = cycles if cycles is not None else [self.open_cycle()]
        self.path = tmp_path / "reviewer-results.json"
        self.path.write_text(json.dumps(make_receipt() if receipt is None else receipt), encoding="utf-8")
        monkeypatch.setattr(orchestrator, "current_run_id", lambda: RUN_ID)
        monkeypatch.setattr(
            orchestrator,
            "review_request_state",
            lambda *_a: orchestrator.ReviewRequestReadiness(True, CLAIMS, "", "success"),
        )
        monkeypatch.setattr(orchestrator, "current_remediation_generation", lambda *_a: generation)
        monkeypatch.setattr(orchestrator, "read_cycle", self.read_cycle)
        monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_a, cycle: self.published.append(cycle))
        monkeypatch.setattr(orchestrator, "dispatch_collector", self.forbid_dispatch)

    @staticmethod
    def open_cycle(**overrides: Any) -> orchestrator.ReviewCycle:
        values: dict[str, Any] = {
            "pr_number": PR,
            "head_sha": HEAD,
            "state": "WAITING_FOR_REVIEWER",
            "provider_id": "",
            "trigger_id": 37765294070,
            "started_at": "2026-10-08T10:43:30Z",
            "config_digest": orchestrator.reviewer_pool_config_digest(),
            "generation_id": GENERATION,
        }
        values.update(overrides)
        return orchestrator.ReviewCycle(**values)

    def read_cycle(self, *_a):
        index = min(self.reads, len(self.cycles) - 1)
        self.reads += 1
        cycle = self.cycles[index]
        return ("present", cycle, None) if cycle is not None else ("absent", None, None)

    def forbid_dispatch(self, *_a, **_k):
        self.dispatches += 1
        raise AssertionError("finalization must never dispatch a collector")

    def run(self, head: str = HEAD, run_id: int = RUN_ID) -> str:
        return orchestrator.finalize_exhausted_pool(REPOSITORY, "token", PR, head, run_id, self.path)


def test_collector_completion_without_any_wake_up_ends_the_open_cycle(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    assert harness.run() == "PUBLISHED: REVIEWER_UNAVAILABLE"
    (published,) = harness.published
    assert published.state == "REVIEWER_UNAVAILABLE"
    assert (published.pr_number, published.head_sha, published.generation_id) == (PR, HEAD, GENERATION)
    assert published.config_digest == orchestrator.reviewer_pool_config_digest()
    assert published.trigger_id == 37765294070 and published.started_at == "2026-10-08T10:43:30Z"
    assert harness.dispatches == 0


def test_the_terminal_state_is_non_blocking_authority_free_and_idempotent():
    cycle = Harness.open_cycle(state="REVIEWER_UNAVAILABLE")
    assert orchestrator.governance_projection(cycle) == ("success", "REVIEWER_UNAVAILABLE")
    assert "REVIEWER_UNAVAILABLE" in orchestrator.TERMINAL_NONBLOCKING_STATES
    assert "REVIEWER_UNAVAILABLE" not in orchestrator.PENDING_STATES
    assert cycle.state not in orchestrator.OPEN_CYCLE_STATES


@pytest.mark.parametrize(
    "state", ["REVIEWER_UNAVAILABLE", "REVIEW_TIMED_OUT", "POOL_EXHAUSTED", "REVIEW_CLEAR", "FINDINGS_OPEN"]
)
def test_an_already_terminal_cycle_is_never_published_over(monkeypatch, tmp_path, state):
    harness = Harness(monkeypatch, tmp_path, cycles=[Harness.open_cycle(state=state)])
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


def test_a_second_completion_for_the_same_head_publishes_nothing(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    assert harness.run().startswith("PUBLISHED")
    harness.cycles = [harness.published[-1]]
    harness.reads = 0
    assert harness.run().startswith("SKIPPED")
    assert len(harness.published) == 1


def test_a_cycle_that_ends_concurrently_between_the_check_and_the_publish_is_left_alone(monkeypatch, tmp_path):
    ended = Harness.open_cycle(state="REVIEW_TIMED_OUT")
    harness = Harness(monkeypatch, tmp_path, cycles=[Harness.open_cycle(), ended])
    assert harness.run() == "SKIPPED: the cycle ended concurrently"
    assert harness.published == []


def test_a_stale_generation_or_other_pool_configuration_is_never_finalized(monkeypatch, tmp_path):
    other_generation = "0123456789abcdef"
    for cycle in (
        Harness.open_cycle(generation_id=other_generation),
        Harness.open_cycle(config_digest="e" * 64),
    ):
        harness = Harness(monkeypatch, tmp_path, cycles=[cycle])
        assert harness.run().startswith("SKIPPED")
        assert harness.published == []
    # The trusted generation moved on after the receipt was written: the receipt is stale evidence.
    harness = Harness(monkeypatch, tmp_path, generation=other_generation)
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


def test_a_head_that_changed_or_has_no_cycle_publishes_nothing(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path, cycles=[None])
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []
    harness = Harness(monkeypatch, tmp_path)
    assert harness.run(head="short").startswith("SKIPPED")
    assert harness.run(head=OTHER_HEAD).startswith("SKIPPED")
    assert harness.published == []


def test_a_completion_from_a_different_run_is_refused(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    assert harness.run(run_id=RUN_ID + 1).startswith("SKIPPED")
    assert harness.published == []


@pytest.mark.parametrize("content", ["", "not json", "[]", "{}", json.dumps({"attempts": []})])
def test_an_unusable_receipt_file_leaves_the_cycle_open(monkeypatch, tmp_path, content):
    harness = Harness(monkeypatch, tmp_path)
    harness.path.write_text(content, encoding="utf-8")
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


def test_a_missing_or_oversized_receipt_file_leaves_the_cycle_open(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    harness.path.unlink()
    assert harness.run().startswith("SKIPPED")
    harness.path.write_text(" " * (orchestrator.COLLECTOR_RESULTS_MAX_BYTES + 1), encoding="utf-8")
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


def test_a_review_request_that_is_not_usable_leaves_the_cycle_open(monkeypatch, tmp_path):
    harness = Harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator,
        "review_request_state",
        lambda *_a: orchestrator.ReviewRequestReadiness(False, "", "stale", "success"),
    )
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


@pytest.mark.parametrize("outcome", ["clear", "blocking"])
def test_a_substantive_verdict_stays_with_the_authenticated_review_path(monkeypatch, tmp_path, outcome):
    attempts = _exhausted_attempts()
    attempts[0] = _record(_agents()[0], outcome)
    harness = Harness(monkeypatch, tmp_path, receipt=make_receipt(attempts=attempts[:1]))
    assert harness.run().startswith("SKIPPED")
    assert harness.published == []


# --- Reading the terminal state back: who may assert it ---------------------------------------------------------------


def _status_world(
    monkeypatch, *, state: str, run_path: str, event: str = "workflow_dispatch", creator="github-actions[bot]"
):
    config = orchestrator.reviewer_pool_config_digest()
    status = {
        "creator": {"login": creator},
        "context": f"{orchestrator.CONTEXT_PREFIX}{PR}",
        "description": f"{state}||37765294070|{config}|",
        "created_at": "2026-10-08T10:44:50Z",
        "target_url": f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}",
    }
    run = {"head_branch": "main", "path": run_path, "event": event}

    def fake(repository, token, method, path, payload=None):
        if path == f"pulls/{PR}":
            return {"state": "open", "head": {"sha": HEAD}}
        if path.startswith(f"commits/{HEAD}/statuses"):
            return [status]
        if path == "":
            return {"default_branch": "main"}
        if path == f"actions/runs/{RUN_ID}":
            return run
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", fake)


def test_a_collector_run_may_assert_only_the_unavailable_terminal_state(monkeypatch):
    _status_world(monkeypatch, state="REVIEWER_UNAVAILABLE", run_path=orchestrator.COLLECTOR_WORKFLOW_PATH)
    state, cycle, _ = orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)
    assert state == "present" and cycle is not None and cycle.state == "REVIEWER_UNAVAILABLE"
    for forbidden in ("REVIEW_CLEAR", "FINDINGS_OPEN", "REVIEW_TIMED_OUT", "WAITING_FOR_REVIEWER"):
        _status_world(monkeypatch, state=forbidden, run_path=orchestrator.COLLECTOR_WORKFLOW_PATH)
        assert orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)[0] == "absent"


def test_a_collector_status_needs_a_dispatched_default_branch_run_and_the_trusted_creator(monkeypatch):
    _status_world(
        monkeypatch,
        state="REVIEWER_UNAVAILABLE",
        run_path=orchestrator.COLLECTOR_WORKFLOW_PATH,
        event="pull_request_target",
    )
    assert orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)[0] == "absent"
    _status_world(
        monkeypatch,
        state="REVIEWER_UNAVAILABLE",
        run_path=orchestrator.COLLECTOR_WORKFLOW_PATH,
        creator="someone-else",
    )
    assert orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)[0] == "absent"
    _status_world(monkeypatch, state="REVIEWER_UNAVAILABLE", run_path=".github/workflows/other.yml")
    assert orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)[0] == "absent"


def test_reconcile_published_states_are_still_believed(monkeypatch):
    _status_world(
        monkeypatch,
        state="REVIEW_TIMED_OUT",
        run_path=".github/workflows/hunter-governance-reconcile.yml",
        event="workflow_run",
    )
    state, cycle, _ = orchestrator.read_cycle(REPOSITORY, "token", PR, HEAD)
    assert state == "present" and cycle is not None and cycle.state == "REVIEW_TIMED_OUT"


# --- The wiring that makes the finalization run ---------------------------------------------------------------------


def test_the_collector_workflow_runs_collector_complete_in_its_own_working_directory():
    document = yaml.safe_load((ROOT / ".github/workflows/hunter-reviewer-collector.yml").read_text(encoding="utf-8"))
    steps = document["jobs"]["collect"]["steps"]
    output = next(step for step in steps if "hunter_reviewer_collector.py" in str(step.get("run", "")))
    complete = next(step for step in steps if "collector-complete" in str(step.get("run", "")))
    assert steps.index(output) < steps.index(complete)
    assert f"--output {orchestrator.COLLECTOR_RESULTS_FILE}" in " ".join(str(output["run"]).split())
    assert complete["if"] == "success()"


def test_collector_complete_defaults_to_the_receipt_the_collector_step_writes():
    arguments = orchestrator.parser().parse_args(
        ["collector-complete", "--repository", REPOSITORY, "--pr", "1", "--head", HEAD, "--run-id", "2"]
    )
    assert arguments.results == orchestrator.COLLECTOR_RESULTS_FILE
