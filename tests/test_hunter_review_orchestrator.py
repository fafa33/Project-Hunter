from __future__ import annotations

import pathlib
import re

import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import pytest
import yaml

HEAD = "a" * 40
REPOSITORY_ROOT = pathlib.Path(orchestrator.__file__).resolve().parents[1]


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
        if path == f"commits/{HEAD}/status":
            return {
                "statuses": [
                    {
                        "context": "Hunter Review Orchestration / PR #472",
                        "description": f"WAITING_FOR_REVIEWER|local|0|{'d' * 64}",
                        "target_url": "https://github.com/owner/repo/actions/runs/123",
                        "created_at": "2026-09-15T00:00:00Z",
                        "creator": {"login": "github-actions[bot]"},
                    }
                ]
            }
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

    cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")
    stored = _ensure_harness(monkeypatch, cycle, [_collector_run("completed", "failure")])

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 1
    assert result.trigger_id == 999


def test_a_missing_collector_run_is_redispatched(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")
    stored = _ensure_harness(monkeypatch, cycle, [])

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

    assert stored["dispatches"] == 1


def test_a_live_or_successful_collector_is_never_duplicated(monkeypatch):
    for run in (
        _collector_run("in_progress"),
        _collector_run("queued"),
        _collector_run("completed", "success"),
    ):
        cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")
        stored = _ensure_harness(monkeypatch, cycle, [run])

        result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD)

        assert stored["dispatches"] == 0
        assert result == cycle


def test_redispatch_stops_at_the_bounded_budget(monkeypatch):
    cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")
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
    cycle = make_cycle(trigger_id=123, started_at="2020-01-01T00:00:00Z")

    def unavailable(*_args, **_kwargs):
        raise transport.GitHubRequestError("rate limited", category="transient", status_code=429)

    monkeypatch.setattr(orchestrator, "request_json", unavailable)

    assert orchestrator.collector_needs_dispatch("owner/repo", "token", cycle) is False


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


def test_collector_workflow_renders_the_correlated_run_name():
    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-reviewer-collector.yml").read_text(encoding="utf-8")
    rendered = orchestrator.collector_run_name(472, HEAD)
    template = yaml.safe_load(text)["run-name"]
    assert template.replace("${{ inputs.pr_number }}", "472").replace("${{ inputs.head_sha }}", HEAD) == rendered


def test_dispatch_collector_sends_only_exact_identity(monkeypatch):
    seen = []
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda repository, token, method, path, payload=None: seen.append((method, path, payload)),
    )

    orchestrator.dispatch_collector("owner/repo", "token", 472, HEAD)

    assert seen == [
        (
            "POST",
            "actions/workflows/hunter-reviewer-collector.yml/dispatches",
            {"ref": "main", "inputs": {"pr_number": "472", "head_sha": HEAD}},
        )
    ]


def _workflow_documents():
    directory = pathlib.Path(REPOSITORY_ROOT, ".github/workflows")
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _permission_blocks(document):
    blocks = [document.get("permissions")]
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    return [block for block in blocks if isinstance(block, dict)]


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
        and any(str(block.get("actions", "")).strip() == "write" for block in _permission_blocks(document))
    ]
    assert privileged == []


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
    assert "pull_request" in _triggers(document)
    blocks = _permission_blocks(document)
    assert blocks and all(str(block.get("actions", "read")).strip() == "read" for block in blocks)
    assert "python scripts/hunter_review_orchestrator.py" not in path.read_text(encoding="utf-8")


def test_orchestration_bootstraps_only_from_the_trusted_default_branch_checkout():
    text = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-reconcile.yml").read_text(
        encoding="utf-8"
    )
    assert "if [ ! -f scripts/hunter_review_orchestrator.py ]; then" in text
    assert "Bootstrap phase" in text


def test_default_branch_without_orchestrator_publishes_bootstrap_pending_without_dispatching_authority():
    """A bootstrap PR cannot invoke a controller that trusted main does not contain."""

    workflow = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-review.yml").read_text(
        encoding="utf-8"
    )

    assert "BOOTSTRAP_PENDING_TRUSTED_CONTROLLER" in workflow
    assert 'CONTROLLER="${GITHUB_WORKSPACE}/engine/scripts/hunter_review_orchestrator.py"' in workflow
    bootstrap, trusted_controller = workflow.split('python "${BRIDGE}" governance', 1)
    assert 'if [ ! -f "${CONTROLLER}" ]; then' in bootstrap
    assert "actions/workflows/" not in bootstrap
    assert "issues/${PR_NUMBER}/comments" not in bootstrap
    assert "python scripts/hunter_review_orchestrator.py" not in bootstrap
    assert trusted_controller


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
    monkeypatch.setattr(orchestrator, "review_prerequisites_ready", lambda *_args: False, raising=False)
    calls = []
    monkeypatch.setattr(orchestrator, "ensure_collector", lambda *_args: calls.append(True), raising=False)

    result = orchestrator.ensure_current("owner/repo", "token", 472)

    assert result is None
    assert calls == []


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
    # Triage-only reviewers cannot terminate the authority search, so they are
    # never a failover destination even though they are first in the pool.
    assert orchestrator.first_pool_provider() == "local-ollama"
    assert orchestrator.next_authority_provider(after="codex") == str(pool["last_resort"]) == "hunter-guard"


def test_unreadable_runner_probe_neither_crashes_nor_skips_the_reviewer(monkeypatch):
    """GITHUB_TOKEN cannot read repository runners; 403 is not evidence of absence."""

    def forbidden(*_args, **_kwargs):
        raise transport.GitHubRequestError("no Administration:read", category="permanent", status_code=403)

    monkeypatch.setattr(orchestrator, "request_json", forbidden)

    assert orchestrator.runner_state("owner/repo", "token") == "unknown"
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "REVIEW_IN_PROGRESS"
    assert decision.next_provider == "local-ollama"
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
                "event": "workflow_dispatch",
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
                "event": "workflow_dispatch",
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
