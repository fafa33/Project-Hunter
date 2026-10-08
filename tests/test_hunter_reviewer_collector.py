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
    # A native clear *comment* that predates the trigger is still not a response:
    # exact-head adoption regardless of ordering covers authenticated review
    # objects only (see tests/test_exact_head_codex_clear_adoption.py).
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


def test_codex_policy_uses_github_native_review_request_with_bounded_review_budget():
    """Codex is invoked through GitHub's native requested-reviewer endpoint, so
    the acknowledgement budget is a genuine, separate delivery acknowledgement
    (it settles the moment the trigger exists) and stays short.

    The review budget, however, must be bounded, single-attempt, and not
    shorter than Codex's own observed normal latency (PR #529 ~29 min,
    PR #530 ~21 min; PR #535 live evidence) -- otherwise the collector treats
    ordinary hosted latency as unavailability and fails over for a reviewer
    that was still within its own configured budget.
    """
    pool, error = collector.review.load_reviewer_pool()
    assert not error and pool is not None
    codex = next(agent for agent in pool["agents"] if agent["id"] == "codex")
    assert pool["timeout_policy"]["retries_per_agent"] == 0
    assert codex["review_timeout_seconds"] >= 30 * 60
    assert codex["review_timeout_seconds"] <= pool["timeout_policy"]["max_seconds"]
    assert codex["ack_timeout_seconds"] == 30
    assert codex["trigger_method"] == "github-review-request:chatgpt-codex-connector[bot]"
    assert codex["evidence_parser"] == "github-review-native.v1"


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
        ("codex", 1),
        ("copilot", 2),
        ("gemini", 3),
        ("groq", 4),
    ]
    assert agents[1]["trigger_method"] == "github-review-request:copilot-pull-request-reviewer[bot]"
    assert agents[2]["trigger_method"] == "api:gemini"
    assert agents[3]["trigger_method"] == "api:groq"
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
        ({"verdict": "clear", "summary": "No blocking defects.", "findings": []}, "clear"),
        (
            {
                "verdict": "blocking",
                "summary": "Unsafe authority bypass.",
                "findings": [{"severity": "high", "path": "gate.py", "line": 1, "evidence": "authority bypass"}],
            },
            "blocking",
        ),
        (
            {
                "verdict": "clear",
                "summary": "Blocking finding remains.",
                "findings": [
                    {"severity": "high", "path": "gate.py", "line": 1, "evidence": "blocking finding remains"}
                ],
            },
            "blocking",
        ),
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
        lambda a, n: {
            "verdict": "clear",
            "summary": "No blocking defects remain after exact-head review.",
            "findings": [],
        },
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
                        "findings": [],
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


def test_codex_trigger_creation_is_delivery_not_acknowledgement(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = POOL["agents"][0]
    trigger = {"id": 77, "created_at": "2026-09-22T09:52:53Z", "body": "@codex review"}
    monkeypatch.setattr(collector, "_pages", lambda *_a, **_k: [])

    assert backend.acknowledged(agent, trigger) is False


def test_a_codex_quota_denial_is_read_as_unavailable_not_as_an_acknowledgement(monkeypatch):
    """Issue #560 PR #561: a Codex comment is the provider writing, not proof it ever started.

    There is no proven public automatic re-review API for Codex, so no comment may stand in for execution.
    The denial is still recognised, and it is recognised as the truthful answer it is: unavailable.
    """

    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = POOL["agents"][0]
    trigger = {"id": 77, "created_at": "2026-09-22T09:52:53Z", "body": "@codex review", "collector_run_id": 123}
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda _repo, _token, path, *_a, **_k: (
            [
                {
                    "id": 88,
                    "user": {"login": collector.governance.reviewer_login(agent)},
                    "created_at": "2026-09-22T09:52:54Z",
                    "body": "Codex usage limit reached. Try again later.",
                }
            ]
            if path.endswith("issues/476/comments")
            else []
        ),
    )

    assert backend.acknowledged(agent, trigger) is False
    assert backend.response_state(agent, trigger) == "unavailable"


def test_an_exact_head_codex_review_is_the_real_acknowledgement(monkeypatch):
    """Only an authenticated review object for this exact head acknowledges the invocation."""

    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = POOL["agents"][0]
    trigger = {"id": 77, "created_at": "2026-09-22T09:52:53Z", "body": "@codex review", "collector_run_id": 123}
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda _repo, _token, path, *_a, **_k: (
            [
                {
                    "id": 91,
                    "user": {"login": collector.governance.reviewer_login(agent)},
                    "submitted_at": "2026-09-22T09:55:00Z",
                    "commit_id": HEAD,
                    "state": "COMMENTED",
                    "body": "No blocking defects found.",
                }
            ]
            if path.endswith("pulls/476/reviews")
            else []
        ),
    )

    assert backend.acknowledged(agent, trigger) is True


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
        lambda *_a: {
            "verdict": "clear",
            "summary": "No blocking defects remain after exact-head review.",
            "findings": [],
        },
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
            HEAD, "d" * 64, agent, 123, 77, "unavailable", "provider unavailable", [], "e" * 64
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
                            "findings": [],
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
    assert collector.external_verdict({"verdict": "clear", "summary": ["No blockers"], "findings": []}) == "unavailable"
    assert collector.external_verdict({"verdict": ["clear"], "summary": "No blockers", "findings": []}) == "unavailable"


def test_external_verdict_rejects_ambiguous_clear_summary():
    assert (
        collector.external_verdict(
            {"verdict": "clear", "summary": "Critical security vulnerability remains", "findings": []}
        )
        == "unavailable"
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
    assert [a["id"] for a in agents] == ["codex", "copilot", "gemini", "groq", "local-ollama"]
    assert agents[-1]["enabled"] is False
    assert agents[-1]["authority_eligible"] is False
    assert [a["id"] for a in collector.review.authority_pool_reviewers(pool)] == ["codex", "copilot", "gemini", "groq"]
    assert agents[1]["trigger_method"] == "github-review-request:copilot-pull-request-reviewer[bot]"
    assert agents[2]["trigger_method"] == "api:gemini"
    assert agents[3]["trigger_method"] == "api:groq"
    assert all(a["retryable"] is False for a in agents[:4])
    assert all(a["review_timeout_seconds"] == 300 for a in agents[1:4])
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
    assert records[0]["reason_code"] == "NO_ACK_TIMEOUT"
    assert records[1]["elapsed_seconds"] == 300
    assert records[1]["reason_code"] == "REVIEW_TIMEOUT"


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


PR553_COPILOT_OVERVIEW_BODY = (
    "<!-- ccr-overview-v2 -->\n\n"
    "## Copilot review overview\n\n"
    "### 🔵 Needs a closer look\n\n"
    "Root-of-trust signing-key changes require final human review.\n\n"
    "**Review effort:** Lite  \n"
    "**Findings:** None"
)


def _copilot_overview_state(monkeypatch, inline_comments):
    backend = collector.GitHubBackend("owner/repo", "token", 480, HEAD, "d" * 64, 123, 1)
    agent = {
        "id": "copilot",
        "trigger_method": "github-review-request:copilot-pull-request-reviewer[bot]",
        "github_login": "copilot-pull-request-reviewer[bot]",
    }
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}
    review = {
        "id": 79,
        "user": {"login": "copilot-pull-request-reviewer[bot]"},
        "submitted_at": "2026-09-18T20:01:00Z",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": PR553_COPILOT_OVERVIEW_BODY,
    }

    def pages(_repo, _token, path):
        if path.endswith("/reviews"):
            return [review]
        if "/reviews/79/comments" in path:
            return inline_comments
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    return backend.response_state(agent, trigger)


def test_copilot_overview_v2_findings_none_exact_head_is_not_blocking_findings(monkeypatch):
    assert _copilot_overview_state(monkeypatch, []) == "clear"


def test_copilot_overview_v2_with_inline_comment_remains_blocking(monkeypatch):
    assert _copilot_overview_state(monkeypatch, [{"id": 1}]) == "blocking"


def test_external_blocking_without_actionable_findings_is_unavailable():
    payload = {"verdict": "blocking", "summary": "Authority may be unsafe.", "findings": []}
    assert collector.external_verdict(payload) == "unavailable"


def test_external_blocking_requires_structured_actionable_finding():
    payload = {
        "verdict": "blocking",
        "summary": "Unsafe authority bypass.",
        "findings": [{"severity": "high", "path": "scripts/gate.py", "line": 42, "evidence": "bypass remains"}],
    }
    assert collector.external_verdict(payload) == "blocking"


def test_external_clear_requires_empty_findings():
    payload = {
        "verdict": "clear",
        "summary": "No blocking defects remain.",
        "findings": [{"severity": "high", "path": "scripts/gate.py", "line": 42, "evidence": "bypass remains"}],
    }
    assert collector.external_verdict(payload) == "blocking"


def test_api_blocking_result_publishes_actionable_findings(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 476, HEAD, "d" * 64, 123, 1)
    agent = {**POOL["agents"][0], "id": "gemini", "priority": 2, "trigger_method": "api:gemini"}
    finding = {"severity": "high", "path": "scripts/gate.py", "line": 42, "evidence": "authority bypass remains"}
    monkeypatch.setattr(backend, "_existing_trigger", lambda *_a: None)
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda *_a: {"verdict": "blocking", "summary": "Authority bypass remains.", "findings": [finding]},
    )
    bodies = []
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda body: bodies.append(body) or {"id": len(bodies), "created_at": "2026-09-22T00:00:00Z", "body": body},
    )
    trigger = backend.trigger(agent, 1)
    result = collector.parse_api_result(bodies[1])
    assert trigger["state"] == "blocking"
    assert result is not None
    assert result["findings"] == [finding]


def test_invalid_api_blocker_fails_over_with_precise_reason():
    class InvalidThenClear(Backend):
        def trigger(self, agent, number):
            self.triggers.append((agent["id"], number))
            state = "unavailable" if agent["id"] == "gemini" else "clear"
            return {
                "id": len(self.triggers),
                "created_at": "2026-09-22T00:00:00Z",
                "state": state,
                "invalid_result": agent["id"] == "gemini",
            }

        def response_state(self, agent, trigger):
            return trigger["state"]

    pool = copy.deepcopy(POOL)
    pool["agents"] = (
        {**pool["agents"][0], "id": "gemini", "priority": 1, "trigger_method": "api:gemini"},
        {**pool["agents"][0], "id": "groq", "priority": 2, "trigger_method": "api:groq"},
    )
    backend = InvalidThenClear()
    results = collector.collect_attempts(pool, HEAD, backend)
    assert backend.triggers == [("gemini", 1), ("groq", 1)]
    assert results[0]["reason_code"] == "INVALID_REVIEW_RESULT"
    assert results[1]["outcome"] == "clear"


# Issue #534: the connector's real quota denial is prose that links a usage
# dashboard, so an exact-string matcher never recognised a genuinely exhausted
# provider and the attempt consumed the whole review budget. These cases pin the
# authenticated denial to an immediate failover, and pin the near-misses that
# must NOT be read as unavailability.


def _codex_backend() -> collector.GitHubBackend:
    return collector.GitHubBackend("owner/repo", "token", 480, HEAD, "d" * 64, 123, 1)


def _codex_agent() -> dict:
    return {
        "id": "codex",
        "trigger_method": "github-pr-comment:@codex review",
        "github_login": "chatgpt-codex-connector[bot]",
    }


REAL_CODEX_QUOTA_DENIAL = (
    "You have reached your Codex usage limits for code reviews. You can see your limits in the "
    "[Codex usage dashboard](https://chatgpt.com/codex/cloud/settings/usage).\n"
    "To continue using code reviews, you can upgrade your account or add credits to your account "
    "and enable them for code reviews in your "
    "[settings](https://chatgpt.com/codex/cloud/settings/code-review)."
)


def test_authenticated_codex_quota_denial_is_unavailable(monkeypatch):
    """The connector's real quota prose is recognised as unavailability."""

    backend = _codex_backend()
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}

    def pages(_repo, _token, path):
        if path.endswith("/reviews"):
            return []
        return [
            {
                "id": 1,
                "user": {"login": "chatgpt-codex-connector[bot]"},
                "created_at": "2026-09-18T20:00:30Z",
                "body": REAL_CODEX_QUOTA_DENIAL,
            }
        ]

    monkeypatch.setattr(collector, "_pages", pages)
    assert backend.response_state(_codex_agent(), trigger) == "unavailable"


def test_authenticated_codex_unavailability_is_reported_immediately(monkeypatch):
    """An authenticated denial fails over at once instead of waiting out the budget."""

    calls: list[float] = []

    class QuotaDenied:
        def __init__(self):
            self.run_id = 123
            self.run_attempt = 1
            self.triggered = 0
            self.clock = 0.0

        def head(self):
            return HEAD

        def now(self):
            return self.clock

        def sleep(self, seconds):
            # Any sleeping at all is a budget burn; the denial must be consumed
            # on the first observation, before a single wait.
            calls.append(self.clock)
            self.clock += max(0.0, float(seconds))

        def trigger(self, agent, number):
            self.triggered += 1
            return {"id": self.triggered, "created_at": "2026-09-22T00:00:00Z", "collector_run_id": 123}

        def response_state(self, agent, trigger):
            return "unavailable"

    pool = copy.deepcopy(POOL)
    pool["agents"] = ({**pool["agents"][0], "id": "codex", "priority": 1},)
    backend = QuotaDenied()
    results = collector.collect_attempts(pool, HEAD, backend)

    assert results[0]["outcome"] == "unavailable"
    assert results[0]["reason_code"] == "QUOTA_OR_USAGE_LIMIT"
    assert calls == [], "an authenticated denial must not consume any wait budget"
    assert backend.triggered == 1


def test_substantive_review_mentioning_limits_is_not_unavailability(monkeypatch):
    """A review that merely discusses limits is a review, not an absence."""

    backend = _codex_backend()
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}
    body = (
        "### Codex Review\n\n**Reviewed commit:** `" + HEAD + "`\n\n"
        "The collector treats an exhausted provider as unavailability, and the usage limits "
        "discussion in ADPR-0012 is correct.\n"
    )

    def pages(_repo, _token, path):
        if path.endswith("/reviews"):
            return [
                {
                    "id": 5,
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "submitted_at": "2026-09-18T20:02:00Z",
                    "commit_id": HEAD,
                    "state": "COMMENTED",
                    "body": body,
                }
            ]
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    state = backend.response_state(_codex_agent(), trigger)
    assert state in {"clear", "blocking"}
    assert state != "unavailable"


def test_a_review_quoting_the_denial_on_an_issue_comment_is_not_unavailability(monkeypatch):
    """A substantive review that quotes the denial prose must still be a review.

    The issue-comment path classifies denial before substantive review text, and
    the real quota denial is long prose, so length cannot separate the two. A
    review that quotes the denial therefore has to be separated structurally --
    by Codex's own reviewed-commit marker -- or a genuine review of this exact
    head is failed over as though the provider had never answered.
    """

    backend = _codex_backend()
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}
    body = (
        "**Reviewed commit:** `" + HEAD + "`\n\n"
        'The earlier connector notice said "You have reached your Codex usage limits", '
        "which I believe was stale: this review found one substantive defect in "
        "scripts/hunter_review_orchestrator.py and must be remediated."
    )

    def pages(_repo, _token, path):
        if "issues/" in path and path.endswith("/comments"):
            return [
                {
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "created_at": "2026-09-18T20:02:00Z",
                    "body": body,
                }
            ]
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    state = backend.response_state(_codex_agent(), trigger)
    assert state == "blocking", state
    assert state != "unavailable"


def test_the_real_quota_denial_on_an_issue_comment_is_still_unavailable(monkeypatch):
    """The structural marker must not mask a genuine denial.

    The denial is 317 non-space characters -- longer than any minimum-substantive
    threshold -- so it is recognised by carrying no reviewed-commit marker, not
    by being short.
    """

    backend = _codex_backend()
    trigger = {"created_at": "2026-09-18T20:00:00Z", "collector_run_id": 123, "id": 9}

    def pages(_repo, _token, path):
        if "issues/" in path and path.endswith("/comments"):
            return [
                {
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                    "created_at": "2026-09-18T20:02:00Z",
                    "body": REAL_CODEX_QUOTA_DENIAL,
                }
            ]
        return []

    monkeypatch.setattr(collector, "_pages", pages)
    assert backend.response_state(_codex_agent(), trigger) == "unavailable"


def test_trigger_creation_is_never_acknowledgement():
    """Creating the trigger proves delivery to GitHub, not that Codex started."""

    pool = copy.deepcopy(POOL)
    codex = (
        next(a for a in pool["agents"] if a.get("id") == "codex")
        if any(a.get("id") == "codex" for a in pool["agents"])
        else None
    )
    if codex is None:
        pytest.skip("pool fixture does not declare codex")
    assert codex["ack_timeout_seconds"] <= codex["review_timeout_seconds"]


# Collector run 36496500403 reviewed PR #541 and Gemini returned a substantive
# no-finding review of it. The collector filed that review as
# outcome=unavailable / reason_code=INVALID_REVIEW_RESULT, so the ordered pool
# read a performed review as no review at all.
GEMINI_P541_PRODUCTION_RESULT = {
    "verdict": "clear",
    "summary": "No security, correctness, or fail-closed defects identified.",
    "findings": [],
}


def test_gemini_production_no_finding_review_of_pr_541_is_clear():
    assert collector.external_verdict(dict(GEMINI_P541_PRODUCTION_RESULT)) == "clear"


def test_gemini_production_clear_ends_collection_as_clear_not_invalid(monkeypatch):
    class ExternalBackend(collector.GitHubBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.clock = 0.0

        def now(self):
            return self.clock

        def sleep(self, _seconds):
            return None

    backend = ExternalBackend("owner/repo", "token", 541, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(
        collector.governance, "request_json", lambda *_a, **_k: {"state": "open", "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(backend, "_existing_trigger", lambda *_a: None)
    monkeypatch.setattr(backend, "_invoke_external", lambda *_a: dict(GEMINI_P541_PRODUCTION_RESULT))
    bodies = []
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda body: bodies.append(body) or {"id": len(bodies), "created_at": "2026-09-29T00:00:00Z", "body": body},
    )
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda *_a, **_k: [
            {"id": index + 10, "user": {"login": "github-actions[bot]"}, "body": body}
            for index, body in enumerate(bodies)
        ],
    )
    pool = copy.deepcopy(POOL)
    pool["agents"] = ({**POOL["agents"][0], "id": "gemini", "priority": 1, "trigger_method": "api:gemini"},)
    attempts = collector.collect_attempts(pool, HEAD, backend)
    assert [attempt["outcome"] for attempt in attempts] == ["clear"]
    assert attempts[0]["reason_code"] == "CLEAR"
    assert collector.parse_api_result(bodies[1])["summary"] == GEMINI_P541_PRODUCTION_RESULT["summary"]


@pytest.mark.parametrize(
    "summary",
    [
        "No blocking defects.",
        "No blocking defects remain after exact-head review.",
        "No security, correctness, or fail-closed defects identified.",
        "No remaining substantive findings or issues.",
        "No new major issues were identified.",
        "no security, correctness, or fail-closed defects identified",
        "Found no reproducible defects.",
        "There are no outstanding blocking findings in the diff.",
    ],
)
def test_external_verdict_accepts_qualified_no_defect_summaries(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "clear"


@pytest.mark.parametrize(
    "summary",
    [
        "The change looks reasonable to me.",
        "No way to tell whether the gate actually holds.",
        "No review was performed; the diff could not be read.",
        "No not-blocking issues are guaranteed here.",
        "No findings, but one critical defect remains in trust verification.",
        "No security, correctness, or fail-closed defects identified; the bypass is exploitable.",
    ],
)
def test_external_verdict_still_fails_closed_on_non_denials_and_disclosures(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "unavailable"


def test_external_verdict_production_wording_with_findings_still_blocks():
    assert (
        collector.external_verdict(
            {
                **GEMINI_P541_PRODUCTION_RESULT,
                "findings": [
                    {"severity": "high", "path": "scripts/gate.py", "line": 42, "evidence": "authority bypass"}
                ],
            }
        )
        == "blocking"
    )


# The P1 review finding on PR #542: CLEAR_DENIAL walked an open run of words
# between "no" and the defect term, so a summary whose *subject* had changed, or
# which conceded the possibility of defects, read as a clean denial of them.
def test_production_gemini_no_defect_sentence_is_still_a_clean_denial():
    assert collector.clear_summary_denies_defects("No security, correctness, or fail-closed defects identified.")
    assert collector.external_verdict(dict(GEMINI_P541_PRODUCTION_RESULT)) == "clear"


@pytest.mark.parametrize(
    "summary",
    [
        # The denial never names defects: it reports the review, not its result.
        "No review was performed and possible issues remain.",
        "No review was performed; the diff could not be read.",
        "No substantive review was conducted and potential governance gaps remain.",
        "The exact-head diff was not reviewed.",
        # A clean denial and a residue in the same sentence.
        "No blocking defects, but the concurrency path may still be wrong.",
        "No defects found; deeper correctness issues could exist.",
    ],
)
def test_external_verdict_rejects_review_failure_and_hedged_clear_summaries(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "unavailable"


@pytest.mark.parametrize(
    "summary",
    [
        "No substantive non-blocking issues were found.",
        "No not-blocking issues are guaranteed here.",
        "No non-blocking defects remain.",
        "No issues other than two cosmetic nits.",
    ],
)
def test_external_verdict_rejects_negated_defect_denials(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "unavailable"


@pytest.mark.parametrize(
    "summary",
    [
        "No way to tell whether the gate actually holds.",
        "Unclear whether any defects remain.",
        "The change looks reasonable to me.",
        "Looks clean overall.",
    ],
)
def test_external_verdict_rejects_ambiguous_clear_summaries(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "unavailable"


def test_external_verdict_rejects_a_denial_run_that_stops_denying_defects():
    # "foo" is not a defect qualifier, so the run is denying something other
    # than defects even though the clause ends in a defect term.
    assert (
        collector.external_verdict({"verdict": "clear", "summary": "No foo issues in the diff.", "findings": []})
        == "unavailable"
    )
    # One incoherent denial is enough: a second clean denial cannot rescue it.
    assert (
        collector.external_verdict(
            {"verdict": "clear", "summary": "No review was conducted. No blocking defects.", "findings": []}
        )
        == "unavailable"
    )


def test_external_verdict_keeps_real_blocking_findings_blocking():
    finding = {
        "severity": "high",
        "path": "scripts/hunter_reviewer_collector.py",
        "line": 60,
        "evidence": "clear denial admits a summary that concedes open issues",
    }
    assert (
        collector.external_verdict(
            {
                "verdict": "blocking",
                "summary": "No review was performed and possible issues remain.",
                "findings": [finding],
            }
        )
        == "blocking"
    )
    # A clear verdict that still carries findings fails closed to those findings.
    assert collector.external_verdict({**GEMINI_P541_PRODUCTION_RESULT, "findings": [finding]}) == "blocking"


def _provider_http_error(code, payload, headers=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return urllib.error.HTTPError(
        "https://api.groq.com/openai/v1/chat/completions",
        code,
        "Forbidden",
        (
            headers
            if headers is not None
            else {"Authorization": "Bearer gsk_LIVE_HEADER_SECRET", "X-Key": "gsk_LIVE_HEADER_SECRET"}
        ),
        io.BytesIO(body),
    )


def _groq_http_error_payload(monkeypatch, code, payload, key="gsk_LIVE_BODY_SECRET_0123456789"):
    backend = collector.GitHubBackend("owner/repo", "token", 541, HEAD, "d" * 64, 123, 1)
    monkeypatch.setenv("GROQ_API_KEY", key)
    monkeypatch.setattr(backend, "_candidate_diff", lambda: "diff --git a/x b/x")

    def denied(*_args, **_kwargs):
        raise _provider_http_error(code, payload)

    monkeypatch.setattr(collector.urllib.request, "urlopen", denied)
    return backend._invoke_external({"id": "groq", "review_timeout_seconds": 1, "trigger_method": "api:groq"}, 1)


@pytest.mark.parametrize(
    ("payload", "cause"),
    [
        (
            {
                "error": {
                    "message": "Model 'openai/gpt-oss-120b' is not available for your account",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "model_not_found",
                }
            },
            "model_access",
        ),
        (
            {"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}},
            "authentication_or_permission",
        ),
        (
            {"error": {"message": "Access denied", "type": "forbidden", "code": "permission_denied"}},
            "authentication_or_permission",
        ),
        (
            {"error": {"message": "Rate limit reached", "type": "rate_limit_error", "code": "rate_limit_exceeded"}},
            "quota_or_account",
        ),
        (
            {"error": {"message": "You have no credits remaining", "code": "insufficient_quota"}},
            "quota_or_account",
        ),
        (
            {"error": {"message": "missing required parameter 'model'", "type": "invalid_request_error"}},
            "malformed_request",
        ),
    ],
)
def test_groq_http_failure_names_the_provider_cause(monkeypatch, payload, cause):
    code = 401 if payload["error"].get("code") == "invalid_api_key" else 403
    summary = _groq_http_error_payload(monkeypatch, code, payload)["summary"]
    assert f"groq HTTP {code} [{cause}]" in summary
    for expected in (
        "model_not_found",
        "invalid_api_key",
        "permission_denied",
        "rate_limit_exceeded",
        "insufficient_quota",
    ):
        if expected in json.dumps(payload["error"]):
            assert expected in summary


def test_gemini_permission_denial_is_distinguished_from_a_bad_key(monkeypatch):
    backend = collector.GitHubBackend("owner/repo", "token", 541, HEAD, "d" * 64, 123, 1)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyLiveGeminiKeySecret0123456789")
    monkeypatch.setattr(backend, "_candidate_diff", lambda: "diff --git a/x b/x")

    def denied(*_args, **_kwargs):
        raise _provider_http_error(
            403,
            {
                "error": {
                    "code": 403,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "PERMISSION_DENIED",
                }
            },
        )

    monkeypatch.setattr(collector.urllib.request, "urlopen", denied)
    summary = backend._invoke_external(
        {"id": "gemini", "review_timeout_seconds": 1, "trigger_method": "api:gemini"}, 1
    )["summary"]
    assert "[authentication_or_permission]" in summary
    assert "PERMISSION_DENIED" in summary
    assert "AIzaSyLiveGeminiKeySecret0123456789" not in summary


def test_provider_http_failure_keeps_its_unavailability_semantics(monkeypatch):
    payload = _groq_http_error_payload(
        monkeypatch, 403, {"error": {"message": "Access denied", "code": "permission_denied"}}
    )
    assert payload["verdict"] == "unavailable"
    assert set(payload) == {"verdict", "summary"}

    agent = {**POOL["agents"][0], "id": "groq", "priority": 1, "trigger_method": "api:groq", "retryable": False}
    backend = collector.GitHubBackend("owner/repo", "token", 541, HEAD, "d" * 64, 123, 1)
    monkeypatch.setattr(backend, "_existing_trigger", lambda *_a: None)
    monkeypatch.setattr(backend, "_invoke_external", lambda *_a: payload)
    bodies = []
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda body: bodies.append(body) or {"id": len(bodies), "created_at": "2026-09-29T00:00:00Z", "body": body},
    )
    trigger = backend.trigger(agent, 1)
    assert trigger["state"] == "unavailable"
    assert trigger["invalid_result"] is False
    assert collector.parse_api_result(bodies[1])["verdict"] == "unavailable"


def test_provider_http_diagnostics_never_expose_secrets_or_raw_bodies(monkeypatch):
    key = "gsk_LIVE_BODY_SECRET_0123456789"
    echo = _groq_http_error_payload(
        monkeypatch,
        403,
        {
            "error": {
                "message": (
                    f"Request rejected. api_key={key} Authorization: Bearer gsk_LIVE_ECHOED_TOKEN_VALUE "
                    f"x-api-key: {key} hunter-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk9Ll8"
                ),
                "code": "permission_denied",
                "type": "forbidden",
            }
        },
        key=key,
    )
    summary = echo["summary"]
    for secret in (
        key,
        "gsk_LIVE_ECHOED_TOKEN_VALUE",
        "hunter-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk9Ll8",
        "gsk_LIVE_HEADER_SECRET",
        "Authorization",
    ):
        assert secret not in summary
    assert "Bearer" not in summary
    assert "[authentication_or_permission]" in summary


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Blocked by corporate proxy. token=hunter-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk9Ll8</body></html>",
        b"",
        b"not json at all",
        json.dumps({"error": {"message": "z" * 20000, "code": "y" * 20000}}).encode(),
        b'{"unexpected": {"nested": ["shape", 1, true, null]}}',
        b"\xff\xfe\x00binary",
    ],
)
def test_provider_http_diagnostics_stay_bounded_on_unusable_bodies(monkeypatch, body):
    summary = _groq_http_error_payload(monkeypatch, 403, body)["summary"]
    assert summary.startswith("groq HTTP 403")
    assert len(summary) <= 64
    assert "hunter-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk9Ll8" not in summary


# Copilot review of PR #542 at 2502778: a denial clears only what it denies, so
# anything outside it that asserts a defect or contrasts with it fails closed.
@pytest.mark.parametrize(
    "summary",
    [
        "No blocking defects, but one issue remains.",
        "No security defects identified, but the implementation has a correctness issue.",
        "No blocking findings; one minor issue is still open.",
        "No security defects. The implementation has a correctness bug.",
        "No blocking issues were found, however the retry path is broken.",
        "No new findings, although the migration is incorrect.",
        "No outstanding defects; tests fail on the exact head.",
        "No blocking defects identified, yet two regressions were introduced.",
        "No security issues. One problem remains in the parser.",
    ],
)
def test_clear_summary_with_a_disclosure_outside_its_denial_is_not_clear(summary):
    assert not collector.clear_summary_denies_defects(summary)
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "unavailable"


@pytest.mark.parametrize(
    "summary",
    [
        "No security, correctness, or fail-closed defects identified.",
        "No blocking defects remain after exact-head review.",
        "No security, correctness, or fail-closed defects identified; the gate remains fail-closed.",
        "Reviewed the complete exact-head diff and found no blocking governance defects.",
    ],
)
def test_clear_summary_without_any_residual_assertion_stays_clear(summary):
    assert collector.external_verdict({"verdict": "clear", "summary": summary, "findings": []}) == "clear"


def test_specific_model_message_outranks_a_generic_forbidden_type(monkeypatch):
    payload = {"error": {"type": "forbidden", "message": "model is not available for this account"}}
    summary = _groq_http_error_payload(monkeypatch, 403, payload)["summary"]
    assert summary.startswith("groq HTTP 403 [model_access]")
    assert "gsk_LIVE_BODY_SECRET_0123456789" not in summary
    assert "gsk_LIVE_HEADER_SECRET" not in summary


def test_generic_type_still_classifies_when_no_specific_evidence_exists(monkeypatch):
    payload = {"error": {"type": "forbidden", "message": "Request refused."}}
    summary = _groq_http_error_payload(monkeypatch, 403, payload)["summary"]
    assert summary.startswith("groq HTTP 403 [authentication_or_permission]")


def test_groq_http_400_is_unavailable_with_a_bounded_redacted_diagnostic(monkeypatch):
    key = "gsk_LIVE_BODY_SECRET_0123456789"
    payload = {
        "error": {
            "message": f"'response_format' is unsupported for this request (key {key})" + " x" * 400,
            "type": "invalid_request_error",
        }
    }
    result = _groq_http_error_payload(monkeypatch, 400, payload, key=key)
    assert result["verdict"] == "unavailable"
    assert result["summary"].startswith("groq HTTP 400 [malformed_request]")
    assert key not in result["summary"]
    assert len(result["summary"]) < 400


def test_groq_http_400_fails_over_to_the_next_reviewer_without_authority(monkeypatch):
    class ExternalBackend(collector.GitHubBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.clock = 0.0

        def now(self):
            return self.clock

        def sleep(self, _seconds):
            return None

    backend = ExternalBackend("owner/repo", "token", 541, HEAD, "d" * 64, 123, 1)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_LIVE_BODY_SECRET_0123456789")
    monkeypatch.setattr(backend, "_candidate_diff", lambda: "diff --git a/x b/x")
    monkeypatch.setattr(
        collector.governance, "request_json", lambda *_a, **_k: {"state": "open", "head": {"sha": HEAD}}
    )
    monkeypatch.setattr(backend, "_existing_trigger", lambda *_a: None)

    def malformed(*_args, **_kwargs):
        raise _provider_http_error(400, {"error": {"message": "invalid payload", "type": "invalid_request_error"}})

    monkeypatch.setattr(collector.urllib.request, "urlopen", malformed)
    invoke = backend._invoke_external
    monkeypatch.setattr(
        backend,
        "_invoke_external",
        lambda agent, *rest: (invoke(agent, *rest) if agent["id"] == "groq" else dict(GEMINI_P541_PRODUCTION_RESULT)),
    )
    bodies = []
    monkeypatch.setattr(
        backend,
        "_post_comment",
        lambda body: bodies.append(body) or {"id": len(bodies), "created_at": "2026-09-29T00:00:00Z", "body": body},
    )
    monkeypatch.setattr(
        collector,
        "_pages",
        lambda *_a, **_k: [
            {"id": index + 10, "user": {"login": "github-actions[bot]"}, "body": body}
            for index, body in enumerate(bodies)
        ],
    )
    pool = copy.deepcopy(POOL)
    pool["agents"] = (
        {**POOL["agents"][0], "id": "groq", "priority": 1, "trigger_method": "api:groq"},
        {**POOL["agents"][0], "id": "gemini", "priority": 2, "trigger_method": "api:gemini"},
    )
    attempts = collector.collect_attempts(pool, HEAD, backend)
    assert [attempt["agent_id"] for attempt in attempts] == ["groq", "gemini"]
    assert [attempt["outcome"] for attempt in attempts] == ["unavailable", "clear"]
    groq_result = next(
        collector.parse_api_result(body)
        for body in bodies
        if (collector.parse_api_result(body) or {}).get("reviewer_agent") == "groq"
    )
    assert groq_result["verdict"] == "unavailable"
    assert "[malformed_request]" in groq_result["summary"]


# --- Issue #560 PR #561: a requested reviewer is not provider execution ---------------------------------
#
# Live proof on PR #561: `POST /requested_reviewers` was accepted for both review-request reviewers and
# produced no provider review, because ruleset 23493180 carries `review_on_push: false`. Treating the
# accepted request as an acknowledgement started a 300s (or 1800s) review budget for a review that was never
# going to arrive, and then reported a timeout as though a reviewer had declined. These tests pin the truth
# instead: the accepted request acknowledges nothing, and the configuration that would produce an automatic
# review is verified read-only before any budget is spent waiting for one.

COPILOT_AGENT = {
    "id": "copilot",
    "priority": 1,
    "enabled": True,
    "ack_timeout_seconds": 30,
    "review_timeout_seconds": 300,
    "retryable": False,
    "trigger_method": "github-review-request:copilot-pull-request-reviewer[bot]",
    "github_login": "copilot-pull-request-reviewer[bot]",
    "evidence_parser": "github-review-ack.v1",
}
CODEX_REQUEST_AGENT = {
    "id": "codex",
    "priority": 2,
    "enabled": True,
    "ack_timeout_seconds": 30,
    "review_timeout_seconds": 1800,
    "retryable": False,
    "trigger_method": "github-review-request:chatgpt-codex-connector[bot]",
    "github_login": "chatgpt-codex-connector[bot]",
    "evidence_parser": "github-review-ack.v1",
}
#: The real `GET /rulesets` summary for PR #561's ruleset 23493180: GitHub does NOT include `rules` here.
RULESET_SUMMARY = {
    "id": 23493180,
    "name": "Automatic Copilot code review",
    "target": "branch",
    "source_type": "Repository",
    "source": "fafa33/Project-Hunter",
    "enforcement": "active",
}
#: ...and the detail endpoint, which is where the rule and its parameters actually live.
RULESET_DETAIL_ON = {
    **RULESET_SUMMARY,
    "rules": [{"type": "copilot_code_review", "parameters": {"review_on_push": True}}],
}
RULESET_DETAIL_OFF = {
    **RULESET_SUMMARY,
    "rules": [{"type": "copilot_code_review", "parameters": {"review_on_push": False}}],
}


def _request_pool(agent):
    return {
        "last_resort": "opencode",
        "timeout_policy": {"retries_per_agent": 0},
        "agents": (agent,),
    }


class _RequestBackend(collector.GitHubBackend):
    """The real GitHub backend with the clock pinned, so nothing is faked except the API facts."""

    def __init__(self, rulesets, detail=None, reviews=(), comments=(), *, rulesets_readable=True, detail_readable=True):
        super().__init__("fafa33/Project-Hunter", "token", 561, HEAD, "d" * 64, 123, 1)
        self.clock = 0.0
        self.rulesets = list(rulesets)
        self.detail = RULESET_DETAIL_ON if detail is None else detail
        self.reviews = list(reviews)
        self.comments = list(comments)
        self.rulesets_readable = rulesets_readable
        self.detail_readable = detail_readable
        self.posted = []
        self.requested = []
        self.mutating_calls = []
        self.detail_calls = []

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += max(0.0, float(seconds))

    def head(self):
        return HEAD

    def _post_comment(self, body):
        self.posted.append(body)
        return {"id": 100 + len(self.posted), "created_at": "2026-10-04T12:00:00Z", "body": body}

    def _existing_trigger(self, agent, number):
        return None

    def route(self, method, path):
        """Route only the endpoints this loop reads, so every request the code makes is visible."""

        if path.startswith("rulesets/"):
            self.detail_calls.append((method, path))
            if not self.detail_readable:
                raise RuntimeError("ruleset detail unreadable")
            return self.detail
        if path.startswith("rulesets"):
            if not self.rulesets_readable:
                raise RuntimeError("rulesets unreadable")
            return self.rulesets
        if path.endswith("requested_reviewers"):
            self.requested.append((method, path))
            return {"number": 1}
        if path.startswith("pulls/561/reviews"):
            return self.reviews
        if path.startswith("issues/561/comments"):
            return self.comments
        if path.startswith("pulls/561"):
            return {"number": 561, "state": "open", "head": {"sha": HEAD}}
        self.mutating_calls.append((method, path))
        return {"number": 1}


def _backend(
    monkeypatch, rulesets, detail=None, reviews=(), comments=(), *, rulesets_readable=True, detail_readable=True
):
    backend = _RequestBackend(
        rulesets, detail, reviews, comments, rulesets_readable=rulesets_readable, detail_readable=detail_readable
    )
    monkeypatch.setattr(
        collector.governance,
        "request_json",
        lambda _repository, _token, method, path, payload=None: backend.route(method, path),
    )
    return backend


def test_an_accepted_requested_reviewer_is_never_a_provider_execution_ack(monkeypatch) -> None:
    """The PR #561 failure mode: the request POST was accepted and no provider review ever arrived."""

    backend = _backend(monkeypatch, [RULESET_SUMMARY])
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    # The request really was sent and accepted, and that is all it proved.
    assert backend.requested == [("POST", "pulls/561/requested_reviewers")]
    assert [attempt["outcome"] for attempt in attempts] == ["timed_out"]
    assert attempts[0]["reason_code"] == "REVIEW_TIMEOUT"
    assert attempts[0]["elapsed_seconds"] == 300  # no start ACK exists; allow the full review window


def test_native_codex_request_waits_full_review_budget_without_fabricated_ack():
    """A standard GitHub review request has no start ACK; 30 seconds is not a review timeout."""

    class DelayedReview(Backend):
        def acknowledged(self, agent, trigger):
            raise AssertionError("native GitHub review requests cannot prove provider-start ACK")

        def response_state(self, agent, trigger):
            return "clear" if self.clock >= 1740 else "waiting"

    agent = {**POOL["agents"][0], "trigger_method": "github-review-request:chatgpt-codex-connector[bot]", "review_timeout_seconds": 1800}
    backend = DelayedReview()
    records = collector.collect_attempts({"timeout_policy": {"retries_per_agent": 0}, "agents": (agent,)}, HEAD, backend)
    assert records[0]["outcome"] == "clear"
    assert records[0]["elapsed_seconds"] == 1740
    assert records[0]["ack_elapsed_seconds"] == 1740  # observation time, not a claimed ACK


def test_native_codex_request_silence_uses_review_timeout_not_ack_timeout():
    agent = {**POOL["agents"][0], "trigger_method": "github-review-request:chatgpt-codex-connector[bot]", "review_timeout_seconds": 1800}
    backend = Backend()
    records = collector.collect_attempts({"timeout_policy": {"retries_per_agent": 0}, "agents": (agent,)}, HEAD, backend)
    assert records[0]["outcome"] == "timed_out"
    assert records[0]["elapsed_seconds"] == 1800
    assert records[0]["reason_code"] == "REVIEW_TIMEOUT"


def test_copilot_is_config_blocked_immediately_when_review_on_push_is_false(monkeypatch) -> None:
    """Ruleset 23493180 as it actually is on PR #561: the rule exists and does not review on push."""

    backend = _backend(monkeypatch, [RULESET_SUMMARY], detail=RULESET_DETAIL_OFF)
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert backend.requested == []  # nothing would review the result, so nothing is requested
    assert [attempt["outcome"] for attempt in attempts] == ["unavailable"]
    assert attempts[0]["reason_code"] == "PROVIDER_CONFIG_BLOCKED"
    assert attempts[0]["elapsed_seconds"] == 0  # no fake 300s wait for a review that cannot happen


@pytest.mark.parametrize(
    ("summaries", "detail", "reason"),
    [
        ([], RULESET_DETAIL_ON, "NO_ACTIVE_COPILOT_CODE_REVIEW"),
        ([{**RULESET_SUMMARY, "enforcement": "disabled"}], RULESET_DETAIL_ON, "NO_ACTIVE_COPILOT_CODE_REVIEW"),
        (
            [{**RULESET_SUMMARY, "id": 77}],
            {**RULESET_DETAIL_ON, "rules": [{"type": "required_signatures"}]},
            "NO_ACTIVE_COPILOT_CODE_REVIEW",
        ),
        ([{**RULESET_SUMMARY, "id": None}], RULESET_DETAIL_ON, "RULESETS_MALFORMED"),
        ([{**RULESET_SUMMARY, "id": "23493180"}], RULESET_DETAIL_ON, "RULESETS_MALFORMED"),
        ([42], RULESET_DETAIL_ON, "RULESETS_UNREADABLE"),
        ([RULESET_SUMMARY], {**RULESET_DETAIL_ON, "rules": "malformed"}, "RULESETS_MALFORMED"),
        ([RULESET_SUMMARY], {**RULESET_DETAIL_ON, "rules": [{"type": "copilot_code_review"}]}, "RULESETS_MALFORMED"),
        (
            [RULESET_SUMMARY],
            {**RULESET_DETAIL_ON, "rules": [{"type": "copilot_code_review", "parameters": {}}]},
            "REVIEW_ON_PUSH_DISABLED",
        ),
        (
            [RULESET_SUMMARY],
            {**RULESET_DETAIL_ON, "rules": [{"type": "copilot_code_review", "parameters": {"review_on_push": "true"}}]},
            "REVIEW_ON_PUSH_DISABLED",
        ),
        ([RULESET_SUMMARY, {**RULESET_SUMMARY, "id": 23493181}], RULESET_DETAIL_ON, "RULESETS_MALFORMED"),
        (
            [{**RULESET_SUMMARY, "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}}],
            RULESET_DETAIL_OFF,
            "REVIEW_ON_PUSH_DISABLED",
        ),
        (
            [{**RULESET_SUMMARY, "conditions": {"ref_name": {"include": ["main"], "exclude": ["main"]}}}],
            RULESET_DETAIL_OFF,
            "NO_ACTIVE_COPILOT_CODE_REVIEW",
        ),
        (
            [{**RULESET_SUMMARY, "conditions": {"ref_name": {"exclude": []}}}],
            RULESET_DETAIL_OFF,
            "NO_ACTIVE_COPILOT_CODE_REVIEW",
        ),
        (
            [{**RULESET_SUMMARY, "conditions": {"ref_name": {"include": ["main"], "exclude": ["main"]}}}],
            RULESET_DETAIL_ON,
            "NO_ACTIVE_COPILOT_CODE_REVIEW",
        ),
        ([{**RULESET_SUMMARY, "conditions": "malformed"}], RULESET_DETAIL_ON, "NO_ACTIVE_COPILOT_CODE_REVIEW"),
    ],
)
def test_a_missing_or_malformed_configuration_fails_closed(monkeypatch, summaries, detail, reason) -> None:
    """Every unusable configuration is the same operational fact, so every one of them blocks immediately."""

    backend = _backend(monkeypatch, summaries, detail=detail)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (False, reason)
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert backend.requested == []
    assert [attempt["outcome"] for attempt in attempts] == ["unavailable"]
    assert attempts[0]["reason_code"] == "PROVIDER_CONFIG_BLOCKED"
    assert attempts[0]["config_reason"] == reason
    assert reason in collector.AUTOMATIC_PUSH_REVIEW_REASONS


def test_the_ruleset_list_summary_is_not_read_as_the_rule_and_the_detail_is(monkeypatch) -> None:
    """The contract GitHub actually implements: `GET /rulesets` carries no `rules`, the detail does.

    A summary-only ruleset is not malformed and must not be blocked, which is the bug that
    classified a healthy configuration as RULESETS_MALFORMED.
    """

    backend = _backend(monkeypatch, [RULESET_SUMMARY], detail=RULESET_DETAIL_ON)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (True, "REVIEW_ON_PUSH")
    assert backend.detail_calls == [("GET", "rulesets/23493180")]
    # The live false configuration, read from its detail, blocks truthfully.
    live = _backend(monkeypatch, [RULESET_SUMMARY], detail=RULESET_DETAIL_OFF)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (False, "REVIEW_ON_PUSH_DISABLED")
    assert live.detail_calls == [("GET", "rulesets/23493180")]


def test_an_unreadable_ruleset_detail_fails_closed(monkeypatch) -> None:
    """A candidate ruleset whose detail cannot be read must never be assumed healthy."""

    backend = _backend(monkeypatch, [RULESET_SUMMARY], detail_readable=False)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (False, "RULESETS_UNREADABLE")
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert attempts[0]["reason_code"] == "PROVIDER_CONFIG_BLOCKED"
    assert attempts[0]["config_reason"] == "RULESETS_UNREADABLE"


def test_applicability_to_the_pull_request_base_branch_is_checked_not_assumed(monkeypatch) -> None:
    """`review_on_push: true` on a ruleset that excludes the base branch is not an automatic review of this PR."""

    covered = {**RULESET_SUMMARY, "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}}
    assert collector.ruleset_applies_to_ref(covered, "main") is True
    _backend(monkeypatch, [covered], detail=RULESET_DETAIL_ON)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (True, "REVIEW_ON_PUSH")
    other = {**RULESET_SUMMARY, "conditions": {"ref_name": {"include": ["refs/heads/release"], "exclude": []}}}
    assert collector.ruleset_applies_to_ref(other, "main") is False
    assert collector.ruleset_applies_to_ref(RULESET_SUMMARY, "main") is True
    _backend(monkeypatch, [other], detail=RULESET_DETAIL_ON)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (False, "NO_ACTIVE_COPILOT_CODE_REVIEW")


def test_an_unreadable_configuration_fails_closed(monkeypatch) -> None:
    backend = _backend(monkeypatch, [RULESET_SUMMARY], rulesets_readable=False)
    assert collector.automatic_push_review("fafa33/Project-Hunter", "token") == (False, "RULESETS_UNREADABLE")
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert attempts[0]["reason_code"] == "PROVIDER_CONFIG_BLOCKED"


def test_a_verified_push_review_configuration_and_an_exact_head_review_stay_valid(monkeypatch) -> None:
    """The positive case is unchanged: review_on_push true, an exact-head review, a real clear."""

    backend = _backend(
        monkeypatch,
        [RULESET_SUMMARY],
        reviews=[
            {
                "id": 900,
                "user": {"login": "copilot-pull-request-reviewer[bot]"},
                "submitted_at": "2026-10-04T12:05:00Z",
                "commit_id": HEAD,
                "state": "APPROVED",
                "body": "No blocking defects found.",
            }
        ],
    )
    attempts = collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert backend.requested == [("POST", "pulls/561/requested_reviewers")]
    assert [attempt["outcome"] for attempt in attempts] == ["clear"]
    assert attempts[0]["reason_code"] == "CLEAR"


def test_the_configuration_gate_never_mutates_a_ruleset(monkeypatch) -> None:
    """The gate is a reader. Enabling automatic review is the owner's action, outside this repository."""

    backend = _backend(monkeypatch, [RULESET_SUMMARY], detail=RULESET_DETAIL_OFF)
    collector.collect_attempts(_request_pool(COPILOT_AGENT), HEAD, backend)
    assert [call for call in backend.mutating_calls if "ruleset" in call[1]] == []
    assert backend.route("GET", "rulesets") == [RULESET_SUMMARY]  # the ruleset is untouched


def test_a_codex_request_with_no_provider_activity_is_never_an_ack(monkeypatch) -> None:
    """No proven public automatic re-review API exists for Codex, so a request alone cannot acknowledge."""

    backend = _backend(monkeypatch, [RULESET_SUMMARY], comments=[{"user": {"login": "github-actions[bot]"}}])
    trigger = {"id": 42, "created_at": "2026-10-04T12:00:00Z", "collector_run_id": 123}
    assert backend.acknowledged(dict(CODEX_REQUEST_AGENT), trigger) is False


def test_a_codex_comment_is_not_a_canonical_workaround(monkeypatch) -> None:
    """An ``@codex`` comment is the collector's own writing; it never stands in for provider execution."""

    backend = _backend(
        monkeypatch,
        [RULESET_SUMMARY],
        comments=[{"user": {"login": "chatgpt-codex-connector[bot]"}, "created_at": "2026-10-04T12:01:00Z"}],
    )
    base_agent = dict(next(iter(POOL["agents"])))
    comment_agent = {**base_agent, "id": "codex", "trigger_method": "github-pr-comment:@codex review"}
    trigger = {"id": 42, "created_at": "2026-10-04T12:00:00Z", "collector_run_id": 123}
    assert backend.acknowledged(comment_agent, trigger) is False
