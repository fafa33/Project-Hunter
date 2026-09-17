from __future__ import annotations

import copy

import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
POOL = {
    "last_resort": "opencode",
    "timeout_policy": {"retries_per_agent": 0},
    "agents": (
        {
            "id": "codex",
            "priority": 1,
            "enabled": True,
            "timeout_seconds": 300,
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

    def response_state(self, agent, trigger):
        return "clear" if self.response else "waiting"


def test_real_timeout_uses_one_invocation_per_reviewer():
    backend = Backend()
    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    results = collector.collect_attempts(pool, HEAD, backend)
    assert backend.triggers == [("codex", 1)]
    assert [r["elapsed_seconds"] for r in results] == [300]
    assert all(r["outcome"] == "timed_out" for r in results)


def test_response_prevents_exhaustion_and_lower_reviewer_invocation():
    backend = Backend(response=True)
    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    pool["agents"] += ({**pool["agents"][0], "id": "alternate", "priority": 2},)
    results = collector.collect_attempts(pool, HEAD, backend)
    assert backend.triggers == [("codex", 1)]
    assert results[0]["outcome"] == "clear"


def test_head_change_aborts_without_fabricating_exhaustion():
    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    with pytest.raises(ValueError, match="HEAD changed"):
        collector.collect_attempts(pool, HEAD, Backend(mutate=True))


def test_evidence_transport_failure_is_not_reviewer_exhaustion():
    backend = Backend()

    def unavailable(*args):
        raise RuntimeError("GitHub unavailable")

    backend.responded = unavailable
    backend.response_state = unavailable
    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        collector.collect_attempts(pool, HEAD, backend)


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


def _install_receipt(monkeypatch, *, mutate=None, available=False, response_state=None):
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
            if number != 1:
                raise AssertionError(path)
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
    monkeypatch.setattr(
        collector.GitHubBackend,
        "response_state",
        lambda *a: response_state or ("clear" if available else "waiting"),
    )
    # This fixture validates exhaustion for an alternate, not Guard snapshot gates.
    pool = copy.deepcopy(POOL)
    return pool


def test_immutable_collector_receipt_proves_configured_exhaustion(monkeypatch):
    pool = _install_receipt(monkeypatch)
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["attempt_count"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(head_sha="b" * 40),
        lambda r: r.update(run_attempt=2),
        lambda r: r["attempts"].pop(),
        lambda r: r["attempts"][0].update(timeout_seconds=1),
        lambda r: r["attempts"][0].update(elapsed_seconds=1),
        lambda r: r["attempts"][0].update(trigger_id=999),
        lambda r: r["attempts"][0].update(outcome="clear"),
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


def test_one_invocation_per_reviewer_then_fast_failover():
    class ClassifiedBackend(Backend):
        def response_state(self, agent, trigger):
            return "unavailable" if agent["id"] == "codex" else "clear"

    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    pool["agents"] += ({**pool["agents"][0], "id": "alternate", "priority": 2},)
    backend = ClassifiedBackend()

    results = collector.collect_attempts(pool, HEAD, backend)

    assert backend.triggers == [("codex", 1), ("alternate", 1)]
    assert [record["outcome"] for record in results] == ["unavailable", "clear"]
    assert backend.clock == 0


def test_native_codex_clear_is_correlated_to_trigger_and_exact_head(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    native = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": ("Codex Review: Didn't find any major issues. Nice work!\n\n" "**Reviewed commit:** `aaaaaaaaaa`"),
    }

    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [native])

    assert backend.response_state(POOL["agents"][0], trigger) == "clear"
    native["created_at"] = "2026-09-16T23:59:59Z"
    assert backend.response_state(POOL["agents"][0], trigger) == "waiting"


def test_blocking_response_stops_without_lower_reviewer_invocation():
    class ClassifiedBackend(Backend):
        def response_state(self, agent, trigger):
            return "blocking"

    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = 0
    pool["agents"] += ({**pool["agents"][0], "id": "alternate", "priority": 2},)
    backend = ClassifiedBackend()
    results = collector.collect_attempts(pool, HEAD, backend)
    assert backend.triggers == [("codex", 1)]
    assert results[0]["outcome"] == "blocking"


def test_native_codex_wrong_head_is_not_a_response(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    native = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": ("Codex Review: Didn't find any major issues. Nice work!\n\n" "**Reviewed commit:** `bbbbbbbbbb`"),
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [native])
    assert backend.response_state(POOL["agents"][0], trigger) == "waiting"


def test_codex_policy_is_single_bounded_300_second_invocation():
    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    codex = next(agent for agent in pool["agents"] if agent["id"] == "codex")
    assert pool["timeout_policy"]["retries_per_agent"] == 0
    assert codex["timeout_seconds"] == 300


def test_native_codex_unavailable_response_fails_over_immediately(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    unavailable = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "To use Codex here, create a Codex account and connect to github.",
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [unavailable])
    assert backend.response_state(POOL["agents"][0], trigger) == "unavailable"


def test_structured_clear_ack_is_correlated_to_trigger_claims(monkeypatch):
    import json

    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    ack = {
        "schema": "hunter.review-ack.v1",
        "head_sha": HEAD,
        "claims_id": "d" * 64,
        "verdict": "clear",
        "summary": "Reviewed the complete exact-head diff and found no blocking governance defects.",
        "collector_run_id": 123,
    }
    comment = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": json.dumps(ack),
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [comment])
    assert backend.response_state(POOL["agents"][0], trigger) == "clear"


def test_native_codex_clear_with_contradictory_text_is_not_clear(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    native = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": (
            "Codex Review: Didn't find any major issues.\n\n"
            "**Reviewed commit:** `aaaaaaaaaa`\nBlocking finding: unsafe bypass"
        ),
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [native])
    assert backend.response_state(POOL["agents"][0], trigger) == "waiting"


def test_unavailable_codex_does_not_grant_review_authority(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    unavailable = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex usage limit reached. Try again later.",
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [unavailable])
    assert backend.response_state(POOL["agents"][0], trigger) == "unavailable"
    assert not collector.governance.native_codex_clear_review(unavailable["body"], HEAD)


def test_collector_automatically_runs_for_each_pr_head_change():
    workflow = (collector.review.ROOT / collector.WORKFLOW).read_text()
    assert "pull_request_target:" in workflow
    assert "- synchronize" in workflow
    assert "github.event.pull_request.number" in workflow
    assert "github.event.pull_request.head.sha" in workflow


def test_explicit_unavailability_is_valid_exhaustion_without_waiting_full_timeout(monkeypatch):
    pool = _install_receipt(
        monkeypatch,
        mutate=lambda r: r["attempts"][0].update(outcome="unavailable", elapsed_seconds=0),
        response_state="unavailable",
    )
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["failure_class"] == "permanent"
    assert result["reviewer_attempts"][0]["attempt_count"] == 1


def test_pull_request_target_collector_run_is_trusted_on_default_branch():
    run = {
        "id": 123,
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": "b" * 40,
        "path": collector.WORKFLOW,
        "event": "pull_request_target",
        "status": "completed",
        "conclusion": "success",
    }
    assert collector.valid_run(run, 123, "main", "b" * 40)


def test_automatic_artifact_name_uses_derived_candidate_head():
    workflow = (collector.review.ROOT / collector.WORKFLOW).read_text()
    assert (
        "name: hunter-reviewer-results-${{ github.event.pull_request.head.sha || inputs.head_sha }}-${{ github.run_attempt }}"
        in workflow
    )


def test_substantive_not_available_phrase_is_not_unavailability():
    assert not collector.GitHubBackend._unavailable(
        "This required migration is not available in this patch and is a blocking finding."
    )
