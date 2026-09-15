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
            "timeout_seconds": 900,
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
    assert [r["elapsed_seconds"] for r in results] == [900, 900]
    assert all(r["outcome"] == "timed_out" for r in results)


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
        lambda r: r["attempts"][0].update(timeout_seconds=1),
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
