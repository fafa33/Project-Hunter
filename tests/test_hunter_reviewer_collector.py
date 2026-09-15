from __future__ import annotations

import copy

import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
POOL = {
    "last_resort": "opencode",
    "timeout_policy": {"retries_per_agent": 1},
    "agents": (
        {
            "id": "codex",
            "priority": 1,
            "enabled": True,
            "ack_timeout_seconds": 90,
            "review_timeout_seconds": 900,
            "retryable": True,
            "trigger_method": "github-pr-comment:@codex review",
            "github_login": "chatgpt-codex-connector[bot]",
            "evidence_parser": "github-review-ack.v1",
        },
    ),
}


class Backend:
    def __init__(self, response=False, mutate=False):
        self.clock = 0.0
        self.triggers = []
        self.response = response
        self.mutate = mutate

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds

    def head(self):
        return ("b" * 40) if self.mutate and self.clock else HEAD

    def trigger(self, agent, number):
        self.triggers.append((agent["id"], number))
        return {"id": len(self.triggers), "created_at": "2026-09-13T00:00:00Z"}

    def responded(self, agent, trigger):
        return self.response


def test_real_timeout_and_all_configured_retries_are_required():
    backend = Backend()
    results = collector.collect_attempts(POOL, HEAD, backend)
    assert backend.triggers == [("codex", 1), ("codex", 2)]
    assert [r["ack_elapsed_seconds"] for r in results] == [90, 90]
    assert all(r["outcome"] == "unavailable" for r in results)


def test_response_prevents_exhaustion_and_lower_reviewer_invocation():
    backend = Backend(response=True)
    pool = copy.deepcopy(POOL)
    pool["agents"] += ({**pool["agents"][0], "id": "alternate", "priority": 2},)
    results = collector.collect_attempts(pool, HEAD, backend)
    assert backend.triggers == [("codex", 1)]
    assert results[0]["outcome"] == "responded"


def test_head_change_aborts_without_fabricating_exhaustion():
    with pytest.raises(ValueError, match="HEAD changed"):
        collector.collect_attempts(POOL, HEAD, Backend(mutate=True))


def test_evidence_transport_failure_is_not_reviewer_exhaustion():
    backend = Backend()

    def unavailable(*args):
        raise RuntimeError("GitHub unavailable")

    backend.responded = unavailable
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        collector.collect_attempts(POOL, HEAD, backend)


def test_trusted_run_must_execute_default_branch_revision():
    run = {
        "id": 123,
        "run_attempt": 1,
        "head_sha": "c" * 40,
        "head_branch": "main",
        "path": ".github/workflows/hunter-reviewer-collector.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
    }
    assert collector.valid_run(run, 123, "main", "c" * 40)
    assert not collector.valid_run({**run, "head_branch": "candidate"}, 123, "main", "c" * 40)
    assert not collector.valid_run({**run, "head_sha": HEAD}, 123, "main", "c" * 40)
    assert not collector.valid_run({**run, "path": ".github/workflows/untrusted.yml"}, 123, "main", "c" * 40)


def _install_receipt(monkeypatch, *, mutate=None, available=False):
    import hashlib
    import io
    import json
    import zipfile

    records = collector.collect_attempts(POOL, HEAD, Backend())
    receipt = {
        "schema": collector.SCHEMA,
        "repository": "owner/repo",
        "pr_number": 469,
        "head_sha": HEAD,
        "run_id": 123,
        "run_attempt": 1,
        "claims_id": "d" * 64,
        "configuration_digest": collector.configuration_digest(POOL),
        "attempts": records,
    }
    if mutate:
        mutate(receipt)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("reviewer-results.json", json.dumps(receipt))
    archive = stream.getvalue()

    def request(repository, token, method, path):
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": "c" * 40}
        if path == "actions/runs/123":
            return {
                "id": 123,
                "run_attempt": 1,
                "head_sha": "c" * 40,
                "head_branch": "main",
                "path": collector.WORKFLOW,
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
            }
        if path.startswith("actions/runs/123/artifacts?"):
            return {
                "artifacts": [
                    {
                        "id": 5,
                        "name": f"hunter-reviewer-results-{HEAD}-1",
                        "expired": False,
                        "workflow_run": {"id": 123},
                        "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                    }
                ]
            }
        if path.startswith("issues/comments/"):
            number = int(path.rsplit("/", 1)[-1])
            return {
                "body": collector.trigger_body(HEAD, "d" * 64, POOL["agents"][0], 123, 1, number),
                "created_at": "2026-09-13T00:00:00Z",
                "user": {"login": "github-actions[bot]"},
                "issue_url": "https://api.github.com/repos/owner/repo/issues/469",
            }
        if path == "pulls/469":
            return {"state": "open", "head": {"sha": HEAD}}
        if path.startswith(f"commits/{HEAD}/check-runs?"):
            return {
                "check_runs": [
                    {"id": 9, "name": "Governance Agent Preflight", "status": "completed", "conclusion": "success"}
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    monkeypatch.setattr(collector, "download_artifact", lambda *a: archive)
    monkeypatch.setattr(collector.GitHubBackend, "responded", lambda *a: available)
    # This fixture validates exhaustion for an alternate, not Guard snapshot gates.
    pool = copy.deepcopy(POOL)
    return pool


def test_immutable_collector_receipt_proves_configured_exhaustion(monkeypatch):
    pool = _install_receipt(monkeypatch)
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["attempt_count"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(head_sha="b" * 40),
        lambda r: r.update(run_attempt=2),
        lambda r: r["attempts"].pop(),
        lambda r: r["attempts"][0].update(ack_timeout_seconds=1),
        lambda r: r["attempts"][0].update(elapsed_seconds=1),
        lambda r: r["attempts"][1].update(trigger_id=1),
        lambda r: r["attempts"][0].update(outcome="responded"),
    ],
)
def test_malformed_or_incomplete_trusted_receipt_fails_closed(monkeypatch, mutation):
    pool = _install_receipt(monkeypatch, mutate=mutation)
    with pytest.raises(ValueError):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


def test_later_reviewer_response_invalidates_recorded_exhaustion(monkeypatch):
    pool = _install_receipt(monkeypatch, available=True)
    with pytest.raises(ValueError, match="available"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


def test_head_change_while_receipt_is_verified_fails_closed(monkeypatch):
    pool = _install_receipt(monkeypatch)
    heads = iter((HEAD, "b" * 40))
    monkeypatch.setattr(collector.GitHubBackend, "head", lambda _self: next(heads))
    with pytest.raises(ValueError, match="HEAD changed"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


def test_guard_snapshot_comes_from_independent_live_prerequisites(monkeypatch):
    pool = _install_receipt(monkeypatch)
    monkeypatch.setattr(collector.governance, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(collector.governance, "read_trusted_upgrade_status", lambda *a: ("success", ""))
    evidence = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")
    assert evidence["unresolved_thread_count"] == 0
    assert evidence["governance_state"] == "success"
    assert evidence["trusted_preflight_state"] == "success"


def test_guard_snapshot_rejects_unresolved_threads(monkeypatch):
    pool = _install_receipt(monkeypatch)
    monkeypatch.setattr(collector.governance, "read_unresolved_review_threads", lambda *a: (("thread",), None))
    with pytest.raises(ValueError, match="zero unresolved threads"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")


def test_collector_workflow_can_write_pr_conversation_triggers():
    import yaml

    workflow = yaml.safe_load((collector.review.ROOT / collector.WORKFLOW).read_text())
    permissions = workflow["permissions"]

    assert permissions.get("pull-requests") == "write"


def test_no_ack_fails_over_after_short_budget():
    backend = Backend()
    pool = copy.deepcopy(POOL)
    agent = pool["agents"][0]
    agent["ack_timeout_seconds"] = 30
    agent["review_timeout_seconds"] = 300
    results = collector.collect_attempts(pool, HEAD, backend)
    assert results[0]["ack_elapsed_seconds"] == 30
    assert results[0]["outcome"] == "unavailable"


def test_acknowledged_review_uses_execution_budget_not_ack_budget():
    class AckBackend(Backend):
        def acknowledged(self, agent, trigger):
            return self.clock >= 5

        def completed(self, agent, trigger):
            return self.clock >= 35

    backend = AckBackend()
    pool = copy.deepcopy(POOL)
    agent = pool["agents"][0]
    agent["ack_timeout_seconds"] = 30
    agent["review_timeout_seconds"] = 60
    results = collector.collect_attempts(pool, HEAD, backend)
    assert results[0]["ack_elapsed_seconds"] == 5
    assert results[0]["elapsed_seconds"] == 35
    assert results[0]["outcome"] == "responded"


def test_offline_local_fallback_is_skipped_without_waiting():
    backend = Backend()

    def availability(agent):
        return "offline" if agent["id"] == "local-ollama" else "online"

    backend.availability = availability
    pool = copy.deepcopy(POOL)
    pool["agents"] += (
        {
            "id": "local-ollama",
            "priority": 2,
            "enabled": True,
            "ack_timeout_seconds": 30,
            "review_timeout_seconds": 600,
            "retryable": True,
            "trigger_method": "github-workflow:hunter-local-reviewer.yml",
            "evidence_parser": "hunter.local-review.v1",
        },
    )
    results = collector.collect_attempts(pool, HEAD, backend)
    local = [item for item in results if item["agent_id"] == "local-ollama"]
    assert len(local) == 1
    assert local[0]["outcome"] == "unavailable"
    assert local[0]["availability_state"] == "offline"
    assert local[0]["elapsed_seconds"] == 0


def test_collector_workflow_can_dispatch_local_reviewer():
    import yaml

    workflow = yaml.safe_load((collector.review.ROOT / collector.WORKFLOW).read_text())
    assert workflow["permissions"].get("actions") == "write"


def test_online_local_that_never_acknowledges_fails_over_after_one_short_budget():
    backend = Backend()
    backend.availability = lambda agent: "online"
    pool = {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 1},
        "agents": (
            {
                "id": "local-ollama",
                "priority": 1,
                "enabled": True,
                "ack_timeout_seconds": 30,
                "review_timeout_seconds": 600,
                "retryable": True,
                "trigger_method": "github-workflow:hunter-local-reviewer.yml",
                "evidence_parser": "hunter.local-review.v1",
            },
        ),
    }
    results = collector.collect_attempts(pool, HEAD, backend)
    assert len(results) == 1
    assert results[0]["outcome"] == "unavailable"
    assert results[0]["ack_elapsed_seconds"] == 30


def test_local_trigger_dispatches_trusted_workflow_instead_of_pr_comment(monkeypatch):
    calls = []
    backend = collector.GitHubBackend("owner/repo", "token", 472, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda repo, token, method, path, payload=None: calls.append((method, path, payload)) or {},
    )
    agent = {
        "id": "local-ollama",
        "trigger_method": "github-workflow:hunter-local-reviewer.yml",
        "github_login": "",
    }
    trigger = backend.trigger(agent, 1)
    assert calls == [
        (
            "POST",
            "actions/workflows/hunter-local-reviewer.yml/dispatches",
            {"ref": "main", "inputs": {"pr_number": "472", "head_sha": HEAD, "claims_id": "d" * 64}},
        )
    ]
    assert trigger["kind"] == "local-workflow"


def test_local_ack_requires_job_to_have_started(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 472, HEAD, "d" * 64, 123, 1)
    trigger = {"kind": "local-workflow", "id": 55, "created_at": "2026-09-15T00:00:00Z"}
    monkeypatch.setattr(backend, "_local_run", lambda _trigger: {"id": 55, "status": "in_progress"})
    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda *_args: {"jobs": [{"id": 8, "status": "in_progress", "conclusion": None}]},
    )
    assert backend.acknowledged({"id": "local-ollama"}, trigger) is True


def test_triage_only_local_reviewer_is_not_authority_exhaustion_requirement():
    pool = copy.deepcopy(POOL)
    pool["agents"] += (
        {
            "id": "local-ollama",
            "priority": 2,
            "enabled": True,
            "authority_eligible": False,
            "ack_timeout_seconds": 30,
            "review_timeout_seconds": 600,
            "retryable": True,
            "trigger_method": "github-workflow:hunter-local-reviewer.yml",
            "evidence_parser": "hunter.local-review.v1",
        },
    )
    assert [a["id"] for a in collector.review.authority_pool_reviewers(pool)] == ["codex"]


def test_codex_trigger_permission_failure_fails_over_instead_of_crashing():
    class PermissionFailoverBackend(Backend):
        def trigger(self, agent, number):
            if agent["id"] == "codex":
                raise collector.governance.transport.GitHubRequestError(
                    "GitHub HTTP 403: Resource not accessible by integration",
                    category="permanent",
                    status_code=403,
                )
            return super().trigger(agent, number)

        def acknowledged(self, agent, trigger):
            return agent["id"] == "alternate"

        def completed(self, agent, trigger):
            return agent["id"] == "alternate"

    codex = {**POOL["agents"][0], "retryable": False, "ack_timeout_seconds": 30, "review_timeout_seconds": 300}
    alternate = {
        **codex,
        "id": "alternate",
        "priority": 2,
        "trigger_method": "github-workflow:hunter-local-reviewer.yml",
        "evidence_parser": "hunter.local-review.v1",
    }
    pool = {"last_resort": "opencode", "timeout_policy": {"retries_per_agent": 1}, "agents": (codex, alternate)}

    results = collector.collect_attempts(pool, HEAD, PermissionFailoverBackend())

    assert [item["agent_id"] for item in results] == ["codex", "alternate"]
    assert results[0]["outcome"] == "unavailable"
    assert results[0]["failure_class"] == "permanent"
    assert results[0]["failure_status"] == 403
    assert results[1]["outcome"] == "responded"


def test_triage_only_response_does_not_prevent_hosted_fallback():
    class TriageThenHostedBackend(Backend):
        def acknowledged(self, agent, trigger):
            return True

        def completed(self, agent, trigger):
            return True

    backend = TriageThenHostedBackend()
    local = {
        **POOL["agents"][0],
        "id": "local-ollama",
        "priority": 1,
        "authority_eligible": False,
        "retryable": False,
        "trigger_method": "github-workflow:hunter-local-reviewer.yml",
        "evidence_parser": "hunter.local-review.v1",
    }
    codex = {**POOL["agents"][0], "id": "codex", "priority": 2, "retryable": False}
    pool = {"last_resort": "opencode", "timeout_policy": {"retries_per_agent": 0}, "agents": (local, codex)}

    results = collector.collect_attempts(pool, HEAD, backend)

    assert [item["agent_id"] for item in results] == ["local-ollama", "codex"]
    assert [item["outcome"] for item in results] == ["responded", "responded"]


def test_review_execution_budget_starts_after_acknowledgement():
    class LateAckBackend(Backend):
        def __init__(self):
            super().__init__()
            self.clock = 0.0

        def now(self):
            return self.clock

        def sleep(self, seconds):
            self.clock += seconds

        def acknowledged(self, agent, trigger):
            return self.clock >= 25.0

        def completed(self, agent, trigger):
            return self.clock >= 325.0

    agent = {**POOL["agents"][0], "retryable": False, "ack_timeout_seconds": 30, "review_timeout_seconds": 300}
    pool = {"last_resort": "opencode", "timeout_policy": {"retries_per_agent": 0}, "agents": (agent,)}

    result = collector.collect_attempts(pool, HEAD, LateAckBackend())

    assert result[0]["outcome"] == "responded"
    assert result[0]["ack_elapsed_seconds"] >= 25.0
    assert result[0]["elapsed_seconds"] >= 325.0
