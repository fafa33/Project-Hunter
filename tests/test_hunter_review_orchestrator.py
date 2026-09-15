from __future__ import annotations

import hunter_review_orchestrator as orchestrator

HEAD = "a" * 40


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


def test_trusted_governance_workflows_auto_ensure_review_cycle():
    root = orchestrator.__file__ and orchestrator.__file__.rsplit("/scripts/", 1)[0]
    for relative in (
        ".github/workflows/hunter-governance-review.yml",
        ".github/workflows/hunter-governance-reconcile.yml",
    ):
        text = open(f"{root}/{relative}", encoding="utf-8").read()
        assert "actions: write" in text
        assert "hunter_review_orchestrator.py" in text
        assert " ensure " in text or " ensure\\" in text


def test_governance_workflow_bootstraps_only_from_trusted_default_branch():
    root = orchestrator.__file__ and orchestrator.__file__.rsplit("/scripts/", 1)[0]
    text = open(f"{root}/.github/workflows/hunter-governance-review.yml", encoding="utf-8").read()
    assert 'if [ ! -f "${ORCHESTRATOR}" ]; then' in text
    assert "Governance review above handled review authority" in text
    assert "${GITHUB_WORKSPACE}/engine/scripts/hunter_review_orchestrator.py" in text
    assert "${GITHUB_WORKSPACE}/scripts/hunter_review_orchestrator.py" not in text


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

    assert orchestrator.review_prerequisites_ready("owner/repo", "token", 472, HEAD) is True


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
    assert decision.next_provider == "opencode"
    assert decision.reason == "offline"


def test_missing_mac_runner_skips_local_without_red(monkeypatch):
    monkeypatch.setattr(orchestrator, "runner_state", lambda *_args, **_kwargs: "missing", raising=False)
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "FAILOVER_IN_PROGRESS"
    assert decision.next_provider == "opencode"


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
    assert decision.next_provider == "opencode"


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
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    state, run_id, reason = orchestrator.read_collector_completion("owner/repo", "token", 472, HEAD)
    assert state == "absent"
    assert run_id is None
    assert reason is None
