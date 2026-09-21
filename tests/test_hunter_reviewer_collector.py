from __future__ import annotations

import copy
import hashlib
import io
import json
import urllib.error
import zipfile

import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40


@pytest.fixture(autouse=True)
def _no_resolved_blocking_threads(monkeypatch):
    """Default every case in this module to the base remediation generation.

    These cases describe a candidate whose authenticated blocking findings have
    never been resolved, which is exactly the base generation. Stubbing the
    thread read keeps them off the network; the remediation-generation behaviour
    itself is exercised by tests that patch this evidence explicitly.
    """

    monkeypatch.setattr(collector.orchestration, "blocking_reviewer_threads", lambda *_a, **_k: ())


POOL = {
    "last_resort": "opencode",
    "timeout_policy": {"retries_per_agent": 0},
    "agents": (
        {
            "id": "codex",
            "priority": 1,
            "enabled": True,
            "ack_timeout_seconds": 30,
            "review_timeout_seconds": 300,
            "retryable": True,
            "trigger_method": "github-pr-comment:@codex review",
            "github_login": "chatgpt-codex-connector[bot]",
            "evidence_parser": "github-review-ack.v1",
        },
    ),
}


class Backend:
    def __init__(self, response=False, mutate=False, response_state=None):
        self.clock = 0.0
        self.run_id = 123
        self.run_attempt = 1
        self.triggers = []
        self.response = response
        self.mutate = mutate
        self.forced_state = response_state

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

    def acknowledged(self, agent, trigger):
        return True

    def response_state(self, agent, trigger):
        if self.forced_state:
            return self.forced_state
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
    assert not collector.valid_run({**run, "head_sha": "not-a-sha"}, 123, "main", "not-a-sha")
    assert not collector.valid_run({**run, "path": ".github/workflows/untrusted.yml"}, 123, "main", "c" * 40)


def _install_receipt(
    monkeypatch,
    *,
    mutate=None,
    available=False,
    response_state=None,
    retries=0,
    generation_id=collector.BASE_GENERATION,
):
    import hashlib
    import io
    import json
    import zipfile

    pool = copy.deepcopy(POOL)
    pool["timeout_policy"]["retries_per_agent"] = retries
    records = collector.collect_attempts(pool, HEAD, Backend(response_state=response_state))
    receipt = {
        "schema": collector.SCHEMA,
        "repository": "owner/repo",
        "pr_number": 469,
        "head_sha": HEAD,
        "run_id": 123,
        "run_attempt": 1,
        "claims_id": "d" * 64,
        "configuration_digest": collector.configuration_digest(pool),
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
        if path.startswith("compare/"):
            return {"status": "ahead"}
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
                "body": collector.trigger_body(HEAD, "d" * 64, POOL["agents"][0], 123, 1, number, generation_id),
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
    return pool


def test_exhaustion_receipt_from_a_prior_remediation_generation_is_not_reusable(monkeypatch):
    """Exhaustion is generation-scoped, never a permanent property of the head.

    Once the blocking findings a cycle produced are resolved, every reviewer that
    cycle recorded as exhausted is owed another invocation. Replaying the old
    receipt would hand authority to a lower-priority reviewer over one that has
    not actually been asked about the remediated state.
    """

    pool = _install_receipt(monkeypatch)
    monkeypatch.setattr(
        collector.orchestration,
        "blocking_reviewer_threads",
        lambda *_a, **_k: (collector.orchestration.BlockingThread("PRRT_1", 4042982311, "2026-09-18T01:35:24Z", True),),
    )

    with pytest.raises(ValueError, match="predates the current remediation generation"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


def test_a_receipt_bound_to_the_current_remediation_generation_still_verifies(monkeypatch):
    resolved = collector.orchestration.BlockingThread("PRRT_1", 4042982311, "2026-09-18T01:35:24Z", True)
    generation = collector.orchestration.remediation_generation_id(HEAD, "d" * 64, (resolved,))
    pool = _install_receipt(
        monkeypatch,
        mutate=lambda receipt: receipt.update(remediation_generation_id=generation),
        generation_id=generation,
    )
    monkeypatch.setattr(collector.orchestration, "blocking_reviewer_threads", lambda *_a, **_k: (resolved,))

    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")

    assert [attempt["agent_id"] for attempt in result["reviewer_attempts"]] == ["codex"]


def test_immutable_collector_receipt_proves_configured_exhaustion(monkeypatch):
    pool = _install_receipt(monkeypatch)
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["attempt_count"] == 1


def test_main_branch_advance_does_not_invalidate_trusted_collector_receipt(monkeypatch):
    pool = _install_receipt(monkeypatch)

    original = collector.governance.request_json

    def request(repository, token, method, path):
        if path == "commits/main":
            return {"sha": "e" * 40}
        if path.startswith("compare/"):
            return {"status": "ahead"}
        return original(repository, token, method, path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["attempt_count"] == 1


def test_collector_run_off_current_default_branch_history_fails_closed(monkeypatch):
    pool = _install_receipt(monkeypatch)

    original = collector.governance.request_json

    def request(repository, token, method, path):
        if path.startswith("compare/"):
            return {"status": "diverged"}
        return original(repository, token, method, path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    with pytest.raises(ValueError, match="default-branch history"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(head_sha="b" * 40),
        lambda r: r.update(run_attempt=2),
        lambda r: r["attempts"].pop(),
        lambda r: r["attempts"][0].update(review_timeout_seconds=1),
        lambda r: r["attempts"][0].update(ack_timeout_seconds=1),
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
    assert backend.response_state(POOL["agents"][0], trigger) == "blocking"


def test_codex_policy_is_single_bounded_300_second_invocation():
    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    codex = next(agent for agent in pool["agents"] if agent["id"] == "codex")
    assert pool["timeout_policy"]["retries_per_agent"] == 0
    assert codex["review_timeout_seconds"] == 300


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
        "trigger_id": 7,
    }
    comment = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": json.dumps(ack),
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [comment])
    assert backend.response_state(POOL["agents"][0], trigger) == "clear"


def test_structured_clear_ack_rejects_mismatched_trigger_id(monkeypatch):
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
        "trigger_id": 8,
    }
    comment = {
        "id": 8,
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": json.dumps(ack),
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [] if "reviews" in _a[2] else [comment])
    assert backend.response_state(POOL["agents"][0], trigger) == "blocking"


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
    assert backend.response_state(POOL["agents"][0], trigger) == "blocking"


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


def test_dispatch_only_artifact_name_uses_exact_input_head():
    workflow = (collector.review.ROOT / collector.WORKFLOW).read_text()
    assert "pull_request_target:" not in workflow
    assert "name: hunter-reviewer-results-${{ inputs.head_sha }}-${{ github.run_attempt }}" in workflow


def test_substantive_not_available_phrase_is_not_unavailability():
    assert not collector.GitHubBackend._unavailable(
        "This required migration is not available in this patch and is a blocking finding."
    )


def test_substantive_rate_limit_phrase_is_not_unavailability():
    assert not collector.GitHubBackend._unavailable(
        "This patch lacks a rate limit on the public endpoint and that is a blocking finding."
    )


def test_policy_enables_server_side_gemini_and_groq_after_codex():
    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    agents = sorted(collector.review.enabled_pool_reviewers(pool), key=lambda a: a["priority"])
    assert [(a["id"], a["priority"]) for a in agents] == [
        ("local-ollama", 1),
        ("hermes", 2),
        ("codex", 3),
        ("copilot", 4),
        ("gemini", 5),
        ("groq", 6),
    ]
    assert agents[3]["trigger_method"] == "github-review-request:copilot-pull-request-reviewer[bot]"
    assert agents[4]["trigger_method"] == "api:gemini"
    assert agents[5]["trigger_method"] == "api:groq"
    assert all(a["review_timeout_seconds"] == 300 for a in agents[1:])


def test_collector_workflow_exposes_only_server_reviewer_secrets():
    import yaml

    workflow = yaml.safe_load((collector.review.ROOT / collector.WORKFLOW).read_text())
    env = workflow["jobs"]["collect"]["steps"][1]["env"]
    assert env["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert env["GROQ_API_KEY"] == "${{ secrets.GROQ_API_KEY }}"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"verdict": "clear", "summary": "No blocking defects."}, "clear"),
        ({"verdict": "blocking", "summary": "Unsafe authority bypass."}, "blocking"),
        ({"verdict": "clear", "summary": "Blocking finding remains."}, "blocking"),
    ],
)
def test_external_reviewer_verdict_is_fail_closed(payload, expected):
    assert collector.external_verdict(payload) == expected


def test_api_reviewer_trigger_is_exact_head_bound_and_synchronous(monkeypatch):
    agent = {
        "id": "gemini",
        "priority": 2,
        "enabled": True,
        "ack_timeout_seconds": 30,
        "review_timeout_seconds": 300,
        "retryable": False,
        "trigger_method": "api:gemini",
        "evidence_parser": "provider-json.v1",
    }
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda a, n: {"verdict": "clear", "summary": "No blocking defects remain after exact-head review."},
    )
    monkeypatch.setattr(backend, "_existing_trigger", lambda a, n: None)
    ids = iter(range(10, 20))
    monkeypatch.setattr(
        backend, "_post_comment", lambda body: {"id": next(ids), "created_at": "2026-09-17T00:00:00Z", "body": body}
    )
    trigger = backend.trigger(agent, 1)
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda *_a, **_k: [
            {
                "id": 11,
                "user": {"login": "github-actions[bot]"},
                "body": trigger["body"],
            },
            {
                "id": 12,
                "user": {"login": "github-actions[bot]"},
                "body": json.dumps(
                    {
                        "schema": "hunter.reviewer-result.v1",
                        "head_sha": HEAD,
                        "claims_id": "d" * 64,
                        "reviewer_agent": "gemini",
                        "collector_run_id": 123,
                        "trigger_id": 10,
                        "verdict": "clear",
                        "summary": "No blocking defects remain after exact-head review.",
                        "response_digest": trigger["response_digest"],
                    },
                    sort_keys=True,
                ),
            },
        ],
    )
    assert trigger["head_sha"] == HEAD
    assert trigger["provider"] == "gemini"
    assert backend.response_state(agent, trigger) == "clear"


def test_codex_trigger_is_reused_for_same_exact_head_claims(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 999, 1)
    agent = POOL["agents"][0]
    key = collector.invocation_key(HEAD, "d" * 64, "codex", 1)
    old = {
        "id": 77,
        "created_at": "2026-09-17T00:00:00Z",
        "body": f"Invocation key: {key}.\nCollector invocation: 123/1/codex/1.",
        "user": {"login": "github-actions[bot]"},
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [old])
    monkeypatch.setattr(backend, "_post_comment", lambda *_a: pytest.fail("must not invoke Codex twice"))
    trigger = backend.trigger(agent, 1)
    assert trigger["id"] == 77
    assert trigger["collector_run_id"] == 123


def test_api_trigger_and_result_are_persisted_before_authority(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = {**POOL["agents"][0], "id": "gemini", "priority": 2, "trigger_method": "api:gemini"}
    monkeypatch.setattr(backend, "_existing_trigger", lambda *_a: None)
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda *_a: {"verdict": "clear", "summary": "No blocking defects remain after exact-head review."},
    )
    bodies = []
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda body: bodies.append(body) or {"id": len(bodies), "created_at": "2026-09-17T00:00:00Z", "body": body},
    )
    trigger = backend.trigger(agent, 1)
    assert len(bodies) == 3
    assert "hunter.reviewer-trigger.v1" in bodies[0]
    assert "hunter.reviewer-result.v1" in bodies[1]
    assert "hunter.review-ack.v1" in bodies[2]
    assert trigger["result_comment_id"] == 2


def test_api_trigger_body_is_canonically_verifiable():
    agent = {**POOL["agents"][0], "id": "gemini", "priority": 2, "trigger_method": "api:gemini"}
    body = collector.api_trigger_body(HEAD, "d" * 64, agent, 123, 1, 1)
    parsed = collector.parse_api_trigger(body)
    assert parsed == {
        "schema": "hunter.reviewer-trigger.v1",
        "head_sha": HEAD,
        "claims_id": "d" * 64,
        "reviewer_agent": "gemini",
        "collector_run_id": 123,
        "collector_run_attempt": 1,
        "attempt_number": 1,
        "invocation_key": collector.invocation_key(HEAD, "d" * 64, "gemini", 1),
    }


def test_reused_invocation_preserves_original_run_and_attempt(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 999, 7)
    agent = POOL["agents"][0]
    key = collector.invocation_key(HEAD, "d" * 64, "codex", 1)
    old = {
        "id": 77,
        "created_at": "2026-09-17T00:00:00Z",
        "body": collector.trigger_body(HEAD, "d" * 64, agent, 123, 2, 1),
        "user": {"login": "github-actions[bot]"},
    }
    assert f"Invocation key: {key}." in old["body"]
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [old])
    trigger = backend.trigger(agent, 1)
    assert trigger["collector_run_id"] == 123
    assert trigger["collector_run_attempt"] == 2


def test_reused_api_invocation_recovers_persisted_result(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 999, 7)
    agent = {**POOL["agents"][0], "id": "gemini", "priority": 2, "trigger_method": "api:gemini"}
    trigger_body = collector.api_trigger_body(HEAD, "d" * 64, agent, 123, 2, 1)
    trigger = {
        "id": 77,
        "created_at": "2026-09-17T00:00:00Z",
        "body": trigger_body,
        "user": {"login": "github-actions[bot]"},
    }
    result = {
        "id": 78,
        "created_at": "2026-09-17T00:00:01Z",
        "body": collector.api_result_body(
            HEAD, "d" * 64, agent, 123, 77, "unavailable", "provider unavailable", "e" * 64
        ),
        "user": {"login": "github-actions[bot]"},
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [trigger, result])
    monkeypatch.setattr(backend, "_invoke_external", lambda *_a: pytest.fail("must not invoke API twice"))
    reused = backend.trigger(agent, 1)
    assert reused["head_sha"] == HEAD
    assert reused["state"] == "unavailable"
    assert reused["collector_run_id"] == 123
    assert reused["collector_run_attempt"] == 2
    assert reused["result_comment_id"] == 78


def test_substantive_temporarily_unavailable_phrase_is_blocking(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = POOL["agents"][0]
    trigger = {"id": 1, "created_at": "2026-09-17T00:00:00Z", "collector_run_id": 123}
    comment = {
        "created_at": "2026-09-17T00:00:01Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "The controller is temporarily unavailable after this transition, which is a blocking finding.",
    }
    monkeypatch.setattr(collector, "_pages", lambda _r, _t, path, *_a: [] if "/reviews" in path else [comment])
    assert backend.response_state(agent, trigger) == "blocking"


def test_groq_authority_verifies_prior_api_exhaustion_from_trusted_collector(monkeypatch):
    import hashlib
    import io
    import zipfile

    pool = copy.deepcopy(POOL)
    pool["agents"] += (
        {
            "id": "gemini",
            "priority": 2,
            "enabled": True,
            "ack_timeout_seconds": 30,
            "review_timeout_seconds": 300,
            "retryable": False,
            "trigger_method": "api:gemini",
            "evidence_parser": "provider-json.v1",
        },
        {
            "id": "groq",
            "priority": 3,
            "enabled": True,
            "ack_timeout_seconds": 30,
            "review_timeout_seconds": 300,
            "retryable": False,
            "trigger_method": "api:groq",
            "evidence_parser": "provider-json.v1",
        },
    )
    codex_trigger = collector.trigger_body(HEAD, "d" * 64, pool["agents"][0], 123, 1, 1)
    gemini_trigger = collector.api_trigger_body(HEAD, "d" * 64, pool["agents"][1], 123, 1, 1)
    receipt = {
        "schema": collector.SCHEMA,
        "repository": "owner/repo",
        "pr_number": 469,
        "head_sha": HEAD,
        "run_id": 123,
        "run_attempt": 1,
        "claims_id": "d" * 64,
        "configuration_digest": collector.configuration_digest(pool),
        "attempts": [
            {
                "agent_id": "codex",
                "priority": 1,
                "ack_timeout_seconds": 30,
                "review_timeout_seconds": 300,
                "trigger_method": "github-pr-comment:@codex review",
                "evidence_parser": "github-review-ack.v1",
                "retryable": True,
                "attempt_number": 1,
                "trigger_id": 1,
                "trigger_created_at": "2026-09-13T00:00:00Z",
                "elapsed_seconds": 300,
                "outcome": "timed_out",
            },
            {
                "agent_id": "gemini",
                "priority": 2,
                "ack_timeout_seconds": 30,
                "review_timeout_seconds": 300,
                "trigger_method": "api:gemini",
                "evidence_parser": "provider-json.v1",
                "retryable": False,
                "attempt_number": 1,
                "trigger_id": 2,
                "trigger_created_at": "2026-09-13T00:01:00Z",
                "elapsed_seconds": 0,
                "outcome": "unavailable",
                "provider": "gemini",
                "response_digest": "e" * 64,
                "head_sha": HEAD,
            },
        ],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("reviewer-results.json", json.dumps(receipt))
    archive = stream.getvalue()

    def request(repository, token, method, path):
        if path == "":
            return {"default_branch": "main"}
        if path == "commits/main":
            return {"sha": "e" * 40}
        if path == "actions/runs/123":
            return {
                "id": 123,
                "run_attempt": 1,
                "head_sha": "c" * 40,
                "head_branch": "main",
                "path": collector.WORKFLOW,
                "event": "pull_request_target",
                "status": "completed",
                "conclusion": "success",
            }
        if path.startswith("compare/"):
            return {"status": "ahead"}
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
        if path == "issues/comments/1":
            return {
                "id": 1,
                "body": codex_trigger,
                "created_at": "2026-09-13T00:00:00Z",
                "user": {"login": "github-actions[bot]"},
                "issue_url": "https://api.github.com/repos/owner/repo/issues/469",
            }
        if path == "issues/comments/2":
            return {
                "id": 2,
                "body": gemini_trigger,
                "created_at": "2026-09-13T00:01:00Z",
                "user": {"login": "github-actions[bot]"},
                "issue_url": "https://api.github.com/repos/owner/repo/issues/469",
            }
        if path.startswith("issues/469/comments?"):
            return [
                {
                    "id": 2,
                    "body": gemini_trigger,
                    "created_at": "2026-09-13T00:01:00Z",
                    "user": {"login": "github-actions[bot]"},
                },
                {
                    "id": 3,
                    "body": json.dumps(
                        {
                            "schema": "hunter.reviewer-result.v1",
                            "head_sha": HEAD,
                            "claims_id": "d" * 64,
                            "reviewer_agent": "gemini",
                            "collector_run_id": 123,
                            "trigger_id": 2,
                            "verdict": "unavailable",
                            "summary": "gemini HTTP 429",
                            "response_digest": "e" * 64,
                        },
                        sort_keys=True,
                    ),
                    "created_at": "2026-09-13T00:01:01Z",
                    "user": {"login": "github-actions[bot]"},
                },
            ]
        if path == "pulls/469":
            return {"state": "open", "head": {"sha": HEAD}}
        if path.startswith("pulls/469/reviews?"):
            return []
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    monkeypatch.setattr(collector, "download_artifact", lambda *a: archive)
    evidence = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "groq")
    assert [attempt["agent_id"] for attempt in evidence["reviewer_attempts"]] == ["codex", "gemini"]


# Copilot review 2026-09-17: fail-closed parser and ordering regressions.
def test_external_verdict_rejects_non_string_contract_fields():
    assert collector.external_verdict({"verdict": "clear", "summary": ["No blockers"]}) == "blocking"
    assert collector.external_verdict({"verdict": ["clear"], "summary": "No blockers"}) == "blocking"


def test_external_verdict_rejects_ambiguous_clear_summary():
    assert (
        collector.external_verdict({"verdict": "clear", "summary": "Critical security vulnerability remains"})
        == "blocking"
    )


def test_native_codex_clear_rejects_same_line_blocker_and_accepts_heading():
    good = f"### 💡 Codex Review\n\nDidn't find any major issues.\n\n**Reviewed commit:** `{HEAD[:10]}`"
    bad = f"### 💡 Codex Review\n\nDidn't find any major issues. Blocking finding: unsafe bypass\n\n**Reviewed commit:** `{HEAD[:10]}`"
    assert collector.GitHubBackend._native_clear(good, HEAD)
    assert not collector.GitHubBackend._native_clear(bad, HEAD)
    assert collector.governance.native_codex_clear_review(good, HEAD)
    assert not collector.governance.native_codex_clear_review(bad, HEAD)


def test_external_reviewers_use_supported_provider_models(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "claims", 1, 1)
    monkeypatch.setattr(backend, "_candidate_diff", lambda: "diff --git a/x b/x")
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            if "generativelanguage.googleapis.com" in seen[-1][0]:
                return b'{"candidates":[{"content":{"parts":[{"text":"{\\"verdict\\":\\"clear\\",\\"summary\\":\\"No blockers\\"}"}]}}]}'
            return (
                b'{"choices":[{"message":{"content":"{\\"verdict\\":\\"clear\\",\\"summary\\":\\"No blockers\\"}"}}]}'
            )

    def capture(req, **_kwargs):
        body = json.loads(req.data.decode())
        seen.append((req.full_url, body))
        return Response()

    monkeypatch.setattr(collector.urllib.request, "urlopen", capture)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test")
    backend._invoke_external({"id": "gemini", "review_timeout_seconds": 1, "trigger_method": "api:gemini"}, 1)
    backend._invoke_external({"id": "groq", "review_timeout_seconds": 1, "trigger_method": "api:groq"}, 1)
    assert "models/gemini-3.7-flash:generateContent" in seen[0][0]
    assert seen[1][1]["model"] == "openai/gpt-oss-120b"


def test_api_auth_failures_are_explicit_unavailability(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "claims", 1, 1)
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    monkeypatch.setattr(backend, "_candidate_diff", lambda: "diff --git a/x b/x")

    def denied(*_a, **_k):
        raise urllib.error.HTTPError("https://example", 401, "unauthorized", {}, None)

    monkeypatch.setattr(collector.urllib.request, "urlopen", denied)
    payload = backend._invoke_external({"id": "gemini", "review_timeout_seconds": 1, "trigger_method": "api:gemini"}, 1)
    assert payload["verdict"] == "unavailable"


def test_response_state_uses_latest_exact_head_review(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    trigger = {"id": 7, "created_at": "2026-09-17T00:00:00Z"}
    reviews = [
        {
            "id": 8,
            "submitted_at": "2026-09-17T00:00:01Z",
            "commit_id": HEAD,
            "state": "APPROVED",
            "body": "",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        },
        {
            "id": 9,
            "submitted_at": "2026-09-17T00:00:02Z",
            "commit_id": HEAD,
            "state": "CHANGES_REQUESTED",
            "body": "Blocking regression",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        },
    ]
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: reviews if "reviews" in _a[2] else [])
    assert backend.response_state(POOL["agents"][0], trigger) == "blocking"


def test_parse_native_trigger_binds_head_claims_run_and_attempt():
    agent = POOL["agents"][0]
    body = collector.trigger_body(HEAD, "d" * 64, agent, 123, 2, 1)
    parsed = collector.parse_native_trigger(body)
    assert parsed == {
        "head_sha": HEAD,
        "claims_id": "d" * 64,
        "reviewer_agent": "codex",
        "collector_run_id": 123,
        "collector_run_attempt": 2,
        "attempt_number": 1,
        "remediation_generation_id": collector.BASE_GENERATION,
    }
    assert collector.parse_native_trigger(body.replace(HEAD, "b" * 40, 1)) is None


def test_canonical_pool_preserves_server_side_fallback_chain():
    import json

    policy = json.loads((collector.review.ROOT / "docs/CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    agents = sorted(pool["agents"], key=lambda a: a["priority"])
    assert [a["id"] for a in agents] == ["local-ollama", "hermes", "codex", "copilot", "gemini", "groq"]
    assert [a["id"] for a in collector.review.authority_pool_reviewers(pool)] == ["codex", "copilot", "gemini", "groq"]
    assert agents[3]["trigger_method"] == "github-review-request:copilot-pull-request-reviewer[bot]"
    assert agents[4]["trigger_method"] == "api:gemini"
    assert agents[5]["trigger_method"] == "api:groq"
    assert all(a["review_timeout_seconds"] == 300 and a["retryable"] is False for a in agents[1:])
    assert pool["last_resort"] == "hunter-guard"


def test_collector_workflow_exposes_server_side_provider_secrets():
    workflow = (collector.review.ROOT / ".github/workflows/hunter-reviewer-collector.yml").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in workflow
    assert "GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}" in workflow


def test_last_resort_recognition_has_no_default_authority_type():
    """The guard's extra snapshot gates cannot be skipped by omitting an argument.

    ``load_exhaustion`` decides whether the extra live prerequisites apply by
    comparing its ``authority_type`` argument against the pool's declared
    ``last_resort``. A default value for that argument would read a caller who
    omitted it as some other reviewer and silently waive those gates, so the
    argument is required and this pins that.
    """
    import inspect

    signature = inspect.signature(collector.load_exhaustion)
    parameter = signature.parameters["authority_type"]
    assert parameter.default is inspect.Parameter.empty


LOCAL_TRIAGE = {
    "id": "local-ollama",
    "priority": 1,
    "enabled": True,
    "ack_timeout_seconds": 30,
    "review_timeout_seconds": 300,
    "retryable": False,
    "trigger_method": "github-workflow:hunter-local-reviewer.yml",
    "evidence_parser": "hunter.local-review.v1",
    "authority_eligible": False,
}


def _local_review(**overrides):
    result = {
        "schema": "hunter.local-review.v1",
        "head_sha": HEAD,
        "claims_id": "d" * 64,
        "model": "qwen2.5-coder:7b",
        "verdict": "clear",
        "summary": "Reviewed the exact-head diff and found no blocking defect.",
        "findings": [],
    }
    result.update(overrides)
    return result


def test_every_enabled_canonical_reviewer_declares_a_trigger_the_collector_performs():
    """The defect: a reviewer was enabled at priority 1 with an unimplemented trigger.

    Collection is ordered and fail-closed, so that did not skip the local
    reviewer -- it aborted the walk before Codex or any other authority reviewer
    was ever attempted.
    """

    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    assert collector.unsupported_pool_triggers(pool) == ()
    assert {collector.trigger_scheme(agent) for agent in pool["agents"]} <= collector.TRIGGER_SCHEMES


def test_an_enabled_reviewer_with_an_unperformable_trigger_is_reported():
    pool = {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": ({**LOCAL_TRIAGE, "trigger_method": "carrier-pigeon:hunter"}, POOL["agents"][0]),
    }
    assert collector.unsupported_pool_triggers(pool) == ("local-ollama",)
    # A disabled reviewer cannot abort a walk it is not part of.
    disabled = {**pool, "agents": ({**pool["agents"][0], "enabled": False}, POOL["agents"][0])}
    assert collector.unsupported_pool_triggers(disabled) == ()


def test_the_push_boundary_guard_refuses_an_unperformable_reviewer_trigger(monkeypatch):
    import hunter_defect_prevention_preflight as preflight

    pool = {
        "last_resort": "hunter-guard",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": ({**LOCAL_TRIAGE, "trigger_method": "carrier-pigeon:hunter"},),
    }
    monkeypatch.setattr(preflight.pre_ready, "load_reviewer_pool", lambda _policy: (pool, ""))
    errors = preflight.validate_code_write_policy()
    assert any("cannot perform" in error and "local-ollama" in error for error in errors)


def test_a_workflow_reviewer_is_dispatched_from_the_trusted_default_branch(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "d" * 64, 123, 2)
    calls = []

    def request(repository, token, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "":
            return {"default_branch": "main"}
        if path.endswith("/runs?event=workflow_dispatch&branch=main&per_page=100"):
            return {"workflow_runs": []}
        if path.endswith("/dispatches"):
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    trigger = backend.trigger(LOCAL_TRIAGE, 1)

    dispatch = next(call for call in calls if call[1].endswith("/dispatches"))
    assert dispatch[0] == "POST"
    assert dispatch[1] == "actions/workflows/hunter-local-reviewer.yml/dispatches"
    assert dispatch[2]["ref"] == "main"
    assert dispatch[2]["inputs"] == {
        "pr_number": "469",
        "head_sha": HEAD,
        "claims_id": "d" * 64,
        "correlation_id": collector.workflow_correlation_id(HEAD, "d" * 64, "local-ollama", 123, 2, 1),
    }
    assert trigger["workflow"] == "hunter-local-reviewer.yml"
    assert trigger["correlation_id"] == dispatch[2]["inputs"]["correlation_id"]


def test_a_workflow_trigger_method_cannot_address_another_actions_route():
    for hostile in ("../../secrets.yml", "owner/repo/actions", "hunter-local-reviewer.yml?x=1", ""):
        with pytest.raises(ValueError):
            collector.workflow_trigger_file({**LOCAL_TRIAGE, "trigger_method": f"github-workflow:{hostile}"})
    assert collector.workflow_trigger_file(LOCAL_TRIAGE) == "hunter-local-reviewer.yml"


def test_a_dispatch_the_trusted_branch_cannot_accept_is_reviewer_unavailability(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "d" * 64, 123, 1)

    def request(repository, token, method, path, payload=None):
        if path == "":
            return {"default_branch": "main"}
        if path.endswith("/runs?event=workflow_dispatch&branch=main&per_page=100"):
            return {"workflow_runs": []}
        raise collector.governance.transport.GitHubRequestError(
            "workflow not found on the trusted branch", category="permanent", status_code=404
        )

    monkeypatch.setattr(collector.governance, "request_json", request)
    trigger = backend.trigger(LOCAL_TRIAGE, 1)
    assert backend.response_state(LOCAL_TRIAGE, trigger) == "unavailable"
    assert backend.acknowledged(LOCAL_TRIAGE, trigger) is False


def _workflow_backend(monkeypatch, *, run, artifact_result, pr=469):
    backend = collector.GitHubBackend("owner/repo", "token", pr, HEAD, "d" * 64, 123, 1)
    archive = None
    if artifact_result is not None:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            bundle.writestr("reviewer-result.json", json.dumps(artifact_result))
        archive = stream.getvalue()

    def request(repository, token, method, path, payload=None):
        if path == "":
            return {"default_branch": "main"}
        if path.endswith("/runs?event=workflow_dispatch&branch=main&per_page=100"):
            return {"workflow_runs": [run] if run else []}
        if path.endswith("/dispatches"):
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda *_a, **_k: (
            [
                {
                    "id": 5,
                    "name": f"hunter-local-review-{pr}-{HEAD}",
                    "expired": False,
                    "workflow_run": {"id": 7001},
                    "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                }
            ]
            if archive is not None
            else []
        ),
    )
    monkeypatch.setattr(collector, "download_artifact", lambda *_a: archive)
    return backend


def _run(**overrides):
    run = {
        "id": 7001,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": ".github/workflows/hunter-local-reviewer.yml",
        "name": collector.LOCAL_REVIEW_RUN_NAME_PREFIX
        + collector.workflow_correlation_id(HEAD, "d" * 64, "local-ollama", 123, 1, 1),
        "created_at": "2026-09-18T00:00:00Z",
        "status": "completed",
        "conclusion": "success",
    }
    run.update(overrides)
    return run


def test_a_dispatched_local_review_result_becomes_the_reviewer_verdict(monkeypatch):
    backend = _workflow_backend(monkeypatch, run=_run(), artifact_result=_local_review())
    trigger = backend.trigger(LOCAL_TRIAGE, 1)
    assert backend.acknowledged(LOCAL_TRIAGE, trigger) is True
    assert backend.response_state(LOCAL_TRIAGE, trigger) == "clear"
    assert trigger["id"] == 7001
    assert trigger["created_at"] == "2026-09-18T00:00:00Z"


def test_a_dispatched_local_review_finding_is_a_blocking_result(monkeypatch):
    findings = [{"severity": "high", "path": "a.py", "line": 1, "evidence": "unbounded retry"}]
    backend = _workflow_backend(
        monkeypatch, run=_run(), artifact_result=_local_review(verdict="findings", findings=findings)
    )
    trigger = backend.trigger(LOCAL_TRIAGE, 1)
    assert backend.response_state(LOCAL_TRIAGE, trigger) == "blocking"


def test_a_queued_dispatched_run_is_not_yet_acknowledged(monkeypatch):
    backend = _workflow_backend(monkeypatch, run=_run(status="queued", conclusion=None), artifact_result=None)
    trigger = backend.trigger(LOCAL_TRIAGE, 1)
    assert backend.acknowledged(LOCAL_TRIAGE, trigger) is False
    assert backend.response_state(LOCAL_TRIAGE, trigger) == "waiting"


def test_a_failed_or_resultless_dispatched_run_is_unavailability_not_a_verdict(monkeypatch):
    failed = _workflow_backend(monkeypatch, run=_run(conclusion="failure"), artifact_result=None)
    assert failed.response_state(LOCAL_TRIAGE, failed.trigger(LOCAL_TRIAGE, 1)) == "unavailable"
    empty = _workflow_backend(monkeypatch, run=_run(), artifact_result=None)
    assert empty.response_state(LOCAL_TRIAGE, empty.trigger(LOCAL_TRIAGE, 1)) == "unavailable"


def test_an_unrelated_run_of_the_same_workflow_is_not_this_invocation(monkeypatch):
    for mutation in (
        {"name": "Hunter Local Reviewer " + "0" * 64, "display_title": ""},
        {"head_branch": "candidate"},
        {"event": "push"},
        {"path": ".github/workflows/untrusted.yml"},
    ):
        backend = _workflow_backend(monkeypatch, run=_run(**mutation), artifact_result=_local_review())
        trigger = backend.trigger(LOCAL_TRIAGE, 1)
        assert backend.response_state(LOCAL_TRIAGE, trigger) == "waiting"
        assert backend.acknowledged(LOCAL_TRIAGE, trigger) is False


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_local_review(), "clear"),
        (_local_review(verdict="findings", findings=[{"severity": "high"}]), "blocking"),
        # A clear verdict that still lists findings fails closed to its findings.
        (_local_review(findings=[{"severity": "high"}]), "blocking"),
        # A blocking verdict with no finding names no defect to act on.
        (_local_review(verdict="findings"), "unavailable"),
        (_local_review(head_sha="b" * 40), "unavailable"),
        (_local_review(claims_id="e" * 64), "unavailable"),
        (_local_review(schema="hunter.local-review.v0"), "unavailable"),
        (_local_review(summary="  "), "unavailable"),
        (_local_review(findings="none"), "unavailable"),
        (_local_review(verdict="approved"), "unavailable"),
        (None, "unavailable"),
    ],
)
def test_only_an_exact_head_claims_bound_consistent_local_review_is_a_verdict(result, expected):
    assert collector.local_review_state(result, HEAD, "d" * 64) == expected


def test_a_triage_only_verdict_does_not_end_the_authority_search():
    """The triage reviewer is never review authority, so it cannot close the pool."""

    class TriageBackend(Backend):
        def response_state(self, agent, trigger):
            return "clear" if agent["id"] == "local-ollama" else "waiting"

    pool = {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": (LOCAL_TRIAGE, {**POOL["agents"][0], "priority": 2}),
    }
    backend = TriageBackend()
    records = collector.collect_attempts(pool, HEAD, backend)

    assert backend.triggers == [("local-ollama", 1), ("codex", 1)]
    assert [record["outcome"] for record in records] == ["clear", "timed_out"]


def test_an_unacknowledged_reviewer_fails_over_on_its_acknowledgement_budget():
    """An offline runner costs the acknowledgement budget, not the review budget."""

    class SilentBackend(Backend):
        def acknowledged(self, agent, trigger):
            return agent["id"] != "local-ollama"

        def response_state(self, agent, trigger):
            return "waiting"

    pool = {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": (LOCAL_TRIAGE, {**POOL["agents"][0], "priority": 2}),
    }
    backend = SilentBackend()
    records = collector.collect_attempts(pool, HEAD, backend)

    assert [record["outcome"] for record in records] == ["unacknowledged", "timed_out"]
    assert records[0]["elapsed_seconds"] == 30
    assert records[1]["elapsed_seconds"] == 300


def _triage_receipt(monkeypatch, *, local_outcome="clear", local_run_id=7001, local_ack=True, mutate=None):
    """A trusted receipt whose pool leads with the triage-only local reviewer."""

    pool = {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": (LOCAL_TRIAGE, {**POOL["agents"][0], "priority": 2}),
    }

    class MixedBackend(Backend):
        def __init__(self):
            super().__init__()
            self.run_id, self.run_attempt = 123, 1

        def trigger(self, agent, number):
            self.triggers.append((agent["id"], number))
            if agent["id"] == "local-ollama":
                return {
                    "id": local_run_id,
                    "created_at": "2026-09-18T00:00:00Z",
                    "kind": "github-workflow",
                    "workflow": "hunter-local-reviewer.yml",
                    "correlation_id": collector.workflow_correlation_id(HEAD, "d" * 64, "local-ollama", 123, 1, number),
                }
            return {"id": 1, "created_at": "2026-09-13T00:00:00Z"}

        def acknowledged(self, agent, trigger):
            return local_ack or agent["id"] != "local-ollama"

        def response_state(self, agent, trigger):
            return local_outcome if agent["id"] == "local-ollama" else "waiting"

    records = collector.collect_attempts(pool, HEAD, MixedBackend())
    receipt = {
        "schema": collector.SCHEMA,
        "repository": "owner/repo",
        "pr_number": 469,
        "head_sha": HEAD,
        "run_id": 123,
        "run_attempt": 1,
        "claims_id": "d" * 64,
        "configuration_digest": collector.configuration_digest(pool),
        "attempts": records,
    }
    if mutate:
        mutate(receipt)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("reviewer-results.json", json.dumps(receipt))
    archive = stream.getvalue()

    def request(repository, token, method, path, payload=None):
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
        if path.startswith("actions/runs/") and path[len("actions/runs/") :].isdigit():
            other = int(path[len("actions/runs/") :])
            if other == local_run_id:
                return _run(id=local_run_id)
            # A real, well-formed run of the same workflow that this dispatch
            # did not produce: only the correlation identity separates them.
            return _run(id=other, name="Hunter Local Reviewer " + "0" * 64)
        if path.startswith("compare/"):
            return {"status": "ahead"}
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
            return {
                "body": collector.trigger_body(HEAD, "d" * 64, pool["agents"][1], 123, 1, 1),
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
    monkeypatch.setattr(collector.GitHubBackend, "response_state", lambda *a: "waiting")
    monkeypatch.setattr(collector.governance, "read_unresolved_review_threads", lambda *a: ((), None))
    monkeypatch.setattr(collector.governance, "read_trusted_upgrade_status", lambda *a: ("success", ""))
    return pool


def test_a_triage_reviewer_is_verified_but_never_becomes_exhaustion_evidence(monkeypatch):
    """The reconciliation end-to-end: no legacy budget field, no unknown agent.

    The receipt still proves the triage reviewer was actually dispatched and
    answered at this exact head, so a skipped reviewer is caught; but the
    exhaustion evidence names only authority-eligible reviewers, because the
    shared authority verifier rejects any other agent id outright.
    """

    pool = _triage_receipt(monkeypatch)
    evidence = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")

    attempts = evidence["reviewer_attempts"]
    assert [attempt["agent_id"] for attempt in attempts] == ["codex"]
    assert attempts[0]["ack_timeout_seconds"] == 30
    assert attempts[0]["review_timeout_seconds"] == 300
    assert "timeout_seconds" not in attempts[0]
    assert collector.review._exhaustion_error(pool, {"reviewer_attempts": attempts}, "opencode") is None


@pytest.mark.parametrize(
    ("reason", "mutation"),
    [
        # A run this dispatch did not produce.
        ("trigger mismatch", lambda r: r["attempts"][0].update(trigger_id=9999)),
        # A timestamp the run itself does not carry.
        ("trigger mismatch", lambda r: r["attempts"][0].update(trigger_created_at="2026-01-01T00:00:00Z")),
        # A different collector attempt derives a different correlation identity.
        ("trigger mismatch", lambda r: r["attempts"][0].update(collector_run_attempt=9)),
        # No run at all can only be recorded as unavailability, never as a verdict.
        ("must record unavailability", lambda r: r["attempts"][0].update(trigger_id=0)),
        # The triage reviewer's record is replaced by the next reviewer's.
        ("configuration/retry result mismatch", lambda r: r["attempts"].pop(0)),
        # The ordered walk produced no evidence at all.
        ("skipped or not fully retried", lambda r: r["attempts"].clear()),
        # Legacy single-budget evidence is no longer admissible.
        (
            "configuration/retry result mismatch",
            lambda r: r["attempts"][0].pop("review_timeout_seconds"),
        ),
    ],
)
def test_a_forged_triage_invocation_fails_the_trusted_receipt_closed(monkeypatch, reason, mutation):
    pool = _triage_receipt(monkeypatch, mutate=mutation)
    with pytest.raises(ValueError, match=reason):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")


def test_an_offline_local_runner_is_admissible_receipt_evidence(monkeypatch):
    """The common case: the Mac is off, so the dispatch produces no run at all.

    That must not invalidate the receipt the hosted authority reviewer depends
    on -- an unreachable triage runner is an availability state, never a
    candidate defect.
    """

    pool = _triage_receipt(monkeypatch, local_outcome="waiting", local_run_id=0, local_ack=False)
    evidence = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")
    assert [attempt["agent_id"] for attempt in evidence["reviewer_attempts"]] == ["codex"]


def test_a_dispatch_with_no_run_cannot_claim_a_verdict(monkeypatch):
    pool = _triage_receipt(
        monkeypatch,
        local_outcome="waiting",
        local_run_id=0,
        local_ack=False,
        mutate=lambda r: r["attempts"][0].update(outcome="clear"),
    )
    with pytest.raises(ValueError, match="must record unavailability"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "opencode")


def test_recorded_attempt_count_is_what_the_reviewer_actually_cost(monkeypatch):
    """A reviewer that settles on its first attempt never claims the retry budget.

    Only a silent timeout is retried, so an explicit unavailability produces one
    record; the verifier must account for exactly that, because the shared
    authority verifier enforces the full retry count for transient failures.
    """

    pool = _install_receipt(
        monkeypatch,
        response_state="unavailable",
        retries=1,
    )
    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")
    assert result["reviewer_attempts"][0]["attempt_count"] == 1
    assert result["reviewer_attempts"][0]["failure_class"] == "permanent"


def test_external_reviewer_oversized_diff_is_bounded_unavailability(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 473, HEAD, "d" * 64, 123, 1)
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=None):
            return b"x" * (collector.EXTERNAL_PROMPT_LIMIT + 1)

    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda *_args, **_kwargs: {"state": "open", "base": {"sha": "0" * 40}, "head": {"sha": HEAD}},
    )
    monkeypatch.setattr(collector.urllib.request, "urlopen", lambda *_args, **_kwargs: OversizedResponse())
    payload = backend._invoke_external(
        {"id": "gemini", "review_timeout_seconds": 300, "trigger_method": "api:gemini"}, 1
    )
    assert payload["verdict"] == "unavailable"
    assert "context budget" in payload["summary"]


def test_candidate_diff_preserves_legitimate_empty_diff(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 473, HEAD, "d" * 64, 123, 1)

    class EmptyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=None):
            return b""

    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda *_args, **_kwargs: {"state": "open", "base": {"sha": "0" * 40}, "head": {"sha": HEAD}},
    )
    monkeypatch.setattr(collector.urllib.request, "urlopen", lambda *_args, **_kwargs: EmptyResponse())
    assert backend._candidate_diff() == ""


def test_copilot_policy_uses_authenticated_review_request():
    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    copilot = next(a for a in collector.review.enabled_pool_reviewers(pool) if a["id"] == "copilot")
    assert copilot["github_login"] == "copilot-pull-request-reviewer[bot]"
    assert copilot["trigger_method"] == "github-review-request:copilot-pull-request-reviewer[bot]"
    assert copilot["ack_timeout_seconds"] == 30
    assert copilot["review_timeout_seconds"] == 300
    assert copilot["retryable"] is False


def test_copilot_comment_review_with_findings_is_blocking(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 480, HEAD, "d" * 64, 123, 1)
    agent = {
        "id": "copilot",
        "trigger_method": "github-review-request:copilot-pull-request-reviewer[bot]",
        "github_login": "copilot-pull-request-reviewer[bot]",
    }
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}
    review = {
        "id": 77,
        "user": {"login": "copilot-pull-request-reviewer[bot]"},
        "submitted_at": "2026-09-18T20:01:00Z",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "Changes recommended",
    }

    def pages(_repo, _token, path):
        if path.endswith("/reviews"):
            return [review]
        if "/reviews/77/comments" in path:
            return [{"id": 1}]
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    assert backend.response_state(agent, trigger) == "blocking"


def test_copilot_clean_exact_head_review_is_clear(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 480, HEAD, "d" * 64, 123, 1)
    agent = {
        "id": "copilot",
        "trigger_method": "github-review-request:copilot-pull-request-reviewer[bot]",
        "github_login": "copilot-pull-request-reviewer[bot]",
    }
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}
    review = {
        "id": 78,
        "user": {"login": "copilot-pull-request-reviewer[bot]"},
        "submitted_at": "2026-09-18T20:01:00Z",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "### 🟢 Approval recommended\n\nNo unresolved blocking issues were identified.",
    }

    def pages(_repo, _token, path):
        if path.endswith("/reviews"):
            return [review]
        if "/reviews/78/comments" in path:
            return []
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    assert backend.response_state(agent, trigger) == "clear"


# --- DFF-025: a reviewer the trusted branch cannot drive is per-reviewer
# --- unavailability, never the end of the collection. -------------------------


def _request_error(status):
    return collector.governance.transport.GitHubRequestError("x", category="permanent", status_code=status)


def _dispatch_refused(status):
    return collector.ReviewerDispatchRefused(_request_error(status))


def _exhausted(status):
    last = collector.governance.transport.GitHubRequestError("x", category="transient", status_code=status)
    return collector.governance.transport.GitHubUnavailable("what", attempts=3, last=last)


def _backend():
    backend = collector.GitHubBackend.__new__(collector.GitHubBackend)
    backend.repository, backend.token, backend.pr = "owner/repo", "token", 1
    backend.expected_head, backend.claims_id = HEAD, "claims"
    backend.run_id, backend.run_attempt = 1, 1
    backend.generation_id = collector.BASE_GENERATION
    backend._default_branch = None
    return backend


WORKFLOW_AGENT = {"id": "codex", "trigger_method": "github-workflow:hunter-local-reviewer.yml"}


@pytest.mark.parametrize("status", [403, 404, 422])
def test_undispatchable_reviewer_probe_is_unavailability_not_collection_failure(monkeypatch, status):
    """The adoption probe reaches the same resources the dispatch does.

    Before DFF-025 only the dispatch call itself honoured this policy, so a
    rejection from the probe that precedes it aborted the whole collection.
    """

    def request_json(repository, token, method, path, payload=None):
        if path.startswith("actions/workflows/"):
            raise _request_error(status)
        return {"default_branch": "main"}

    monkeypatch.setattr(collector.governance, "request_json", request_json)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    trigger = _backend()._dispatch_workflow_reviewer(WORKFLOW_AGENT, 1)

    assert trigger["state"] == "unavailable"
    assert trigger["dispatch_status"] == status


def test_exhausted_infrastructure_still_fails_closed(monkeypatch):
    def request_json(repository, token, method, path, payload=None):
        raise _exhausted(503)

    monkeypatch.setattr(collector.governance, "request_json", request_json)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    with pytest.raises(collector.governance.transport.GitHubUnavailable):
        _backend()._dispatch_workflow_reviewer(WORKFLOW_AGENT, 1)


def test_exhausted_node_resolution_404_is_never_reviewer_unavailability(monkeypatch):
    """A retried-out 404 carries status 404 but is an infrastructure inconsistency.

    The transport documents it as never meaning absence, so it must stay
    fail-closed instead of silently skipping a reviewer that may be healthy.
    """

    def request_json(repository, token, method, path, payload=None):
        raise _exhausted(404)

    monkeypatch.setattr(collector.governance, "request_json", request_json)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")

    with pytest.raises(collector.governance.transport.GitHubUnavailable):
        _backend()._dispatch_workflow_reviewer(WORKFLOW_AGENT, 1)


class _PoolBackend:
    """Only the first reviewer is undispatchable; the rest are healthy."""

    def __init__(self, error):
        self.error, self.triggered = error, []

    def head(self):
        return HEAD

    def now(self):
        return 0.0

    def sleep(self, seconds):
        pass

    def trigger(self, agent, number):
        self.triggered.append(agent["id"])
        if agent["id"] == "codex":
            raise self.error
        return {"id": 1, "created_at": "t"}

    def response_state(self, agent, trigger):
        return "clear"

    def acknowledged(self, agent, trigger):
        return True


def _isolation_pool():
    def agent(identifier, priority, trigger_method):
        return {
            "id": identifier,
            "priority": priority,
            "enabled": True,
            "retryable": False,
            "ack_timeout_seconds": 1,
            "review_timeout_seconds": 1,
            "trigger_method": trigger_method,
            "evidence_parser": "parser",
            "authority_eligible": True,
        }

    return {
        "timeout_policy": {"retries_per_agent": 0},
        "agents": [agent("codex", 1, "github-workflow:x.yml"), agent("gemini", 2, "api:gemini")],
    }


def test_one_undispatchable_reviewer_does_not_strand_the_whole_pool():
    """The defect that produced MISSING_REVIEW_AUTHORITY on a healthy candidate.

    A single undispatchable reviewer used to abort collection before any later
    reviewer was invoked, so the candidate was reported as lacking authority
    when nothing about the candidate was wrong.
    """

    backend = _PoolBackend(_dispatch_refused(404))

    records = collector.collect_attempts(_isolation_pool(), HEAD, backend)

    assert backend.triggered == ["codex", "gemini"]
    assert [record["outcome"] for record in records] == ["unavailable", "clear"]


def test_pool_isolation_does_not_swallow_infrastructure_exhaustion():
    backend = _PoolBackend(_exhausted(503))

    with pytest.raises(collector.governance.transport.GitHubUnavailable):
        collector.collect_attempts(_isolation_pool(), HEAD, backend)


def test_pool_isolation_does_not_swallow_a_head_change():
    """HEAD movement is a correctness signal and must still abort."""

    class Moved(_PoolBackend):
        def head(self):
            return "b" * 40

    with pytest.raises(ValueError, match="HEAD changed"):
        collector.collect_attempts(_isolation_pool(), HEAD, Moved(_dispatch_refused(404)))


# --- A refused non-workflow trigger has no id to verify against ---------------


def _refused_trigger(outcome, elapsed=0.0):
    """Rewrite the receipt's single attempt as a trigger that was never posted."""

    def mutate(receipt):
        receipt["attempts"][0].update(
            trigger_id=0, trigger_created_at="", outcome=outcome, elapsed_seconds=elapsed, ack_elapsed_seconds=0.0
        )

    return mutate


def test_a_refused_comment_trigger_verifies_as_recorded_unavailability(monkeypatch):
    """Per-reviewer isolation must produce a receipt the trusted verifier accepts.

    A 403/404/422 from the comment post means no comment exists, so there is no
    id to verify against -- the same situation as a workflow dispatch the trusted
    branch never accepted, and admissible on the same terms.
    """

    pool = _install_receipt(monkeypatch, mutate=_refused_trigger("unavailable"))

    result = collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")

    assert [attempt["agent_id"] for attempt in result["reviewer_attempts"]] == ["codex"]


def test_a_refused_comment_trigger_may_not_claim_a_spent_review_budget(monkeypatch):
    """Zero trigger id plus a timeout asserts a trigger that never existed."""

    pool = _install_receipt(monkeypatch, mutate=_refused_trigger("timed_out", elapsed=300.0))

    with pytest.raises(ValueError, match="must record unavailability"):
        collector.load_exhaustion("owner/repo", "token", 469, HEAD, pool, 123, "alternate")


def test_an_undrivable_api_reviewer_is_isolated_like_a_workflow_reviewer():
    """The isolation must cover every scheme, not only workflow dispatch."""

    class ApiBackend(_PoolBackend):
        def trigger(self, agent, number):
            self.triggered.append(agent["id"])
            if agent["id"] == "gemini":
                raise self.error
            return {"id": 1, "created_at": "t"}

    backend = ApiBackend(_dispatch_refused(403))
    pool = {
        "timeout_policy": {"retries_per_agent": 0},
        "agents": [
            {
                "id": "gemini",
                "priority": 1,
                "enabled": True,
                "retryable": False,
                "ack_timeout_seconds": 1,
                "review_timeout_seconds": 1,
                "trigger_method": "api:gemini",
                "evidence_parser": "parser",
                "authority_eligible": True,
            },
            {
                "id": "codex",
                "priority": 2,
                "enabled": True,
                "retryable": False,
                "ack_timeout_seconds": 1,
                "review_timeout_seconds": 1,
                "trigger_method": "github-pr-comment:@codex review",
                "evidence_parser": "parser",
                "authority_eligible": True,
            },
        ],
    }

    records = collector.collect_attempts(pool, HEAD, backend)

    assert backend.triggered == ["gemini", "codex"]
    assert records[0]["outcome"] == "unavailable"
    assert records[0]["trigger_id"] == 0


def test_api_failure_after_trigger_creation_preserves_trigger_identity(monkeypatch):
    """A provider rejection after the trigger comment exists is not a zero-id refusal."""
    backend = _backend()
    agent = {
        "id": "gemini",
        "trigger_method": "api:gemini",
        "priority": 1,
        "enabled": True,
        "retryable": False,
        "ack_timeout_seconds": 1,
        "review_timeout_seconds": 1,
        "evidence_parser": "parser",
        "authority_eligible": True,
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda _body: {
            "id": 991,
            "created_at": "2026-09-19T18:00:00Z",
            "body": "trigger",
        },
    )

    def fail_after_trigger(*_args, **_kwargs):
        raise _request_error(403)

    monkeypatch.setattr(backend, "_invoke_external", fail_after_trigger)

    trigger = backend.trigger(agent, 1)

    assert trigger["id"] == 991
    assert trigger["state"] == "unavailable"
    assert trigger["collector_run_id"] == backend.run_id


def test_collect_attempts_keeps_nonzero_identity_for_post_trigger_api_failure(monkeypatch):
    backend = _backend()
    agent = {
        "id": "gemini",
        "trigger_method": "api:gemini",
        "priority": 1,
        "enabled": True,
        "retryable": False,
        "ack_timeout_seconds": 1,
        "review_timeout_seconds": 1,
        "evidence_parser": "parser",
        "authority_eligible": True,
    }
    pool = {"timeout_policy": {"retries_per_agent": 0}, "agents": [agent]}
    monkeypatch.setattr(backend, "head", lambda: HEAD)
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda _body: {
            "id": 992,
            "created_at": "2026-09-19T18:00:00Z",
            "body": "trigger",
        },
    )
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda *_a, **_k: (_ for _ in ()).throw(_request_error(422)),
    )

    records = collector.collect_attempts(pool, HEAD, backend)

    assert records[0]["outcome"] == "unavailable"
    assert records[0]["trigger_id"] == 992


def test_provider_refusal_after_api_trigger_persists_verifiable_unavailability(monkeypatch):
    """A real trigger plus provider refusal must leave durable API-result evidence."""

    backend = _backend()
    agent = {
        "id": "gemini",
        "trigger_method": "api:gemini",
        "priority": 1,
        "enabled": True,
        "retryable": False,
        "ack_timeout_seconds": 1,
        "review_timeout_seconds": 1,
        "evidence_parser": "parser",
        "authority_eligible": True,
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])
    posted = []

    def post(body):
        posted.append(body)
        return {
            "id": 1200 + len(posted),
            "created_at": "2026-09-19T20:00:00Z",
            "body": body,
        }

    monkeypatch.setattr(backend, "_post_comment", post)
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda *_a, **_k: (_ for _ in ()).throw(_request_error(403)),
    )

    trigger = backend.trigger(agent, 1)

    assert trigger["id"] == 1201
    assert trigger["state"] == "unavailable"
    assert trigger["result_comment_id"] == 1202
    assert trigger["response_digest"]
    assert len(posted) == 2
    assert '"verdict":"unavailable"' in posted[1].replace(" ", "")


def test_api_result_persistence_failure_after_provider_return_fails_closed(monkeypatch):
    """A completed provider call without durable result evidence fails closed."""

    backend = _backend()
    agent = {
        "id": "gemini",
        "trigger_method": "api:gemini",
        "priority": 1,
        "enabled": True,
        "retryable": False,
        "ack_timeout_seconds": 1,
        "review_timeout_seconds": 1,
        "evidence_parser": "parser",
        "authority_eligible": True,
    }
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])
    calls = 0

    def post(_body):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"id": 1301, "created_at": "2026-09-19T20:00:00Z", "body": "trigger"}
        raise _request_error(403)

    monkeypatch.setattr(backend, "_post_comment", post)
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda *_a, **_k: {"verdict": "clear", "summary": "clean"},
    )

    with pytest.raises(ValueError, match="API result comment could not be persisted"):
        backend.trigger(agent, 1)


def test_existing_api_trigger_adoption_preserves_identity_on_result_read_failure(monkeypatch):
    """A post-trigger _api_result failure must keep the real trigger id and mark unavailable."""

    backend = _backend()
    agent = {
        "id": "gemini",
        "trigger_method": "api:gemini",
        "priority": 1,
        "enabled": True,
        "retryable": False,
        "ack_timeout_seconds": 1,
        "review_timeout_seconds": 1,
        "evidence_parser": "parser",
        "authority_eligible": True,
    }
    marker = backend.invocation_marker(agent, 1)
    existing = {
        "id": 7777,
        "created_at": "2026-09-19T19:00:00Z",
        "body": f"Collector invocation: 1/1/gemini/1.\nInvocation key: {marker}.",
    }
    monkeypatch.setattr(backend, "_existing_trigger", lambda _a, _n: existing)
    calls = []

    def api_result(_a, trigger):
        calls.append(trigger["id"])
        raise _request_error(403)

    monkeypatch.setattr(backend, "_api_result", api_result)

    trigger = backend.trigger(agent, 1)

    assert trigger["id"] == 7777
    assert trigger["state"] == "unavailable"
    assert trigger["dispatch_status"] == 403
    assert calls == [7777]


def test_canonical_pool_includes_hermes_as_benchmark_gated_triage():
    policy = json.loads((collector.review.ROOT / "docs/CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    pool = policy["review_progression"]["review_authority"]["reviewer_pool"]
    agents = sorted(pool["agents"], key=lambda a: a["priority"])
    hermes = next(a for a in agents if a["id"] == "hermes")
    assert hermes["trigger_method"] == "github-workflow:hunter-hermes-reviewer.yml"
    assert hermes["authority_eligible"] is False
    assert hermes["ack_timeout_seconds"] == 30
    assert hermes["review_timeout_seconds"] == 300
    assert "hermes" not in {a["id"] for a in collector.review.authority_pool_reviewers(pool)}


def test_definitively_offline_self_hosted_runner_fails_over_without_dispatch(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(collector.orchestration, "runner_state", lambda *_a, **_k: "offline")
    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )
    trigger = backend.trigger({**LOCAL_TRIAGE, "availability_probe": "self-hosted-runner"}, 1)
    assert trigger["id"] == 0
    assert trigger["state"] == "unavailable"
    assert trigger["availability_probe"] == "offline"
    assert backend.response_state(LOCAL_TRIAGE, trigger) == "unavailable"


def test_unknown_runner_probe_still_uses_authenticated_ack_path(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 469, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(collector.orchestration, "runner_state", lambda *_a, **_k: "unknown")
    calls = []

    def request(repository, token, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "":
            return {"default_branch": "main"}
        if path.endswith("/runs?event=workflow_dispatch&branch=main&per_page=100"):
            return {"workflow_runs": []}
        if path.endswith("/dispatches"):
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(collector.governance, "request_json", request)
    trigger = backend.trigger({**LOCAL_TRIAGE, "availability_probe": "self-hosted-runner"}, 1)
    assert trigger["id"] == 0
    assert any(path.endswith("/dispatches") for _, path, _ in calls)
