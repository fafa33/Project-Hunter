"""DFF-043: elapsed reviewer time includes every blocking operation."""

from __future__ import annotations

import copy
import time
import urllib.request

import hunter_pre_ready_review as review
import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40


def pool(budget=30):
    configured, error = review.load_reviewer_pool()
    assert configured is not None and not error
    configured = copy.deepcopy(configured)
    agent = copy.deepcopy(review.enabled_pool_reviewers(configured)[0])
    agent.update(review_timeout_seconds=budget, ack_timeout_seconds=budget, retryable=False, authority_eligible=True)
    configured["agents"] = [agent]
    return configured


class ClockBackend:
    def __init__(self, trigger_seconds=0, poll_seconds=0, response="waiting"):
        self.clock = 0
        self.trigger_seconds = trigger_seconds
        self.poll_seconds = poll_seconds
        self.response = response

    def head(self):
        return HEAD

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def trigger(self, agent, number):
        self.clock += self.trigger_seconds
        return {"id": 1, "created_at": "2026-11-14T00:00:00Z"}

    def response_state(self, agent, trigger):
        self.clock += self.poll_seconds
        return self.response

    def acknowledged(self, agent, trigger):
        return True


def test_trigger_execution_is_accounted_in_attempt_elapsed():
    backend = ClockBackend(trigger_seconds=12, response="clear")
    record = collector.collect_attempts(pool(), HEAD, backend)[0]
    assert record["elapsed_seconds"] == 12
    assert record["outcome"] == "clear"


@pytest.mark.parametrize("response", ["clear", "blocking", "unavailable"])
def test_late_terminal_response_cannot_supply_review_evidence(response):
    record = collector.collect_attempts(pool(), HEAD, ClockBackend(poll_seconds=90, response=response))[0]
    assert record["outcome"] == "timed_out"


def test_blocking_http_body_cannot_exceed_attempt_deadline(monkeypatch):
    class BlockingBody:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            time.sleep(1)
            return b"clear"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: BlockingBody())

    class RealBackend(ClockBackend):
        def now(self):
            return time.monotonic()

        def response_state(self, agent, trigger):
            with urllib.request.urlopen("https://example.invalid", timeout=30) as response:
                return response.read().decode()

    started = time.monotonic()
    record = collector.collect_attempts(pool(0.05), HEAD, RealBackend())[0]
    assert record["outcome"] == "timed_out"
    assert time.monotonic() - started < 0.5


def test_interrupted_trigger_never_fabricates_completed_attempt():
    import hunter_github_transport as transport

    snapshots = []

    class Interrupted(ClockBackend):
        def trigger(self, agent, number):
            raise transport.RequestDeadlineExceeded("ambiguous POST")

    with pytest.raises(ValueError, match="ambiguous"):
        collector.collect_attempts(
            pool(),
            HEAD,
            Interrupted(),
            progress=lambda records, current: snapshots.append((copy.deepcopy(records), current)),
        )
    assert snapshots[-1][0] == []
    assert snapshots[-1][1]["stage"] == "deadline-exceeded"
    assert snapshots[-1][1]["trigger_id"] is None


def test_retry_after_cannot_extend_attempt_deadline():
    import hunter_github_transport as transport

    calls = []
    started = time.monotonic()

    def unavailable():
        calls.append(1)
        raise transport.GitHubRequestError("rate limited", category="transient", retry_after=8)

    with pytest.raises(transport.RequestDeadlineExceeded):
        with transport.request_deadline(started + 0.05):
            transport.execute_with_retry(unavailable, what="test rate limit")
    assert calls == [1]
    assert time.monotonic() - started < 0.5


def test_nested_timer_preserves_outer_deadline_and_cleans_up():
    import signal

    import hunter_github_transport as transport

    previous_handler = signal.getsignal(signal.SIGALRM)
    with pytest.raises(transport.RequestDeadlineExceeded):
        with transport.request_deadline(time.monotonic() + 0.05):
            with transport.request_deadline(time.monotonic() + 1):
                time.sleep(1)
    assert signal.getsignal(signal.SIGALRM) == previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert transport.bounded_timeout(30) == 30


def test_deadline_requires_supported_main_thread():
    from concurrent.futures import ThreadPoolExecutor

    import hunter_github_transport as transport

    def worker():
        with transport.request_deadline(time.monotonic() + 1):
            pytest.fail("unbounded worker executed")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(RuntimeError, match="POSIX main thread"):
            executor.submit(worker).result()


def test_http_socket_timeout_is_clamped(monkeypatch):
    import hunter_github_transport as transport

    timeouts = []

    def opening(_request, *, timeout):
        timeouts.append(timeout)
        raise transport.RequestDeadlineExceeded("stop before network")

    monkeypatch.setattr(urllib.request, "urlopen", opening)
    with pytest.raises(transport.RequestDeadlineExceeded):
        with transport.request_deadline(time.monotonic() + 0.1):
            transport.rest_json(url="https://example.invalid", method="GET", headers={}, data=None, token="not-a-token")
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 0.1


def test_interrupted_main_preserves_diagnostics_without_receipt(monkeypatch, tmp_path):
    import json
    import sys

    output = tmp_path / "reviewer-results.json"
    monkeypatch.setattr(sys, "argv", ["collector", "--pr", "740", "--head", HEAD, "--output", str(output)])
    for key, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "test",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    claims = {"test": "binding"}
    claims_id = review.review_id(claims)
    document = {"claims": claims, "review_request": {"schema": "hunter.review-request.v1", "claims_id": claims_id}}
    monkeypatch.setattr(collector.governance, "read_head_pre_ready_review", lambda *_: ("present", document, ""))
    monkeypatch.setattr(collector.orchestration, "current_remediation_generation", lambda *_: "")

    class Interrupted(ClockBackend):
        def trigger(self, agent, number):
            raise collector.CollectorInterrupted("runner terminated")

    monkeypatch.setattr(collector, "GitHubBackend", lambda *_: Interrupted())
    with pytest.raises(collector.CollectorInterrupted):
        collector.main()
    assert not output.exists()
    diagnostic = json.loads((tmp_path / "reviewer-progress.json").read_text())
    assert diagnostic["complete"] is False
    assert diagnostic["current"]["stage"] == "interrupted"
    assert diagnostic["attempts"] == []
    assert collector.orchestration.collector_receipt_problem(
        diagnostic, repository="owner/repo", pr_number=740, head_sha=HEAD, run_id=123
    )


def test_workflow_reserves_bounded_cleanup_and_separates_partial_artifacts():
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/hunter-reviewer-collector.yml").read_text())
    job = workflow["jobs"]["collect"]
    steps = job["steps"]
    assert all("timeout-minutes" in step for step in steps)
    assert sum(step["timeout-minutes"] for step in steps) <= job["timeout-minutes"]
    collection = next(step for step in steps if step.get("name") == "Collect actual configured reviewer invocations")
    configured, error = review.load_reviewer_pool()
    assert configured is not None and not error
    assert collection["timeout-minutes"] * 60 > review.reviewer_chain_worst_case_seconds(configured)
    uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    diagnostics = next(step for step in uploads if step["with"]["path"] == "reviewer-progress.json")
    assert diagnostics["if"] == "always()"
    complete = next(step for step in uploads if step["with"]["path"] == "reviewer-results.json")
    assert complete.get("if", "success()") == "success()"
    assert diagnostics["with"]["name"] != complete["with"]["name"]
