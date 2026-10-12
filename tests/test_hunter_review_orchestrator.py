from __future__ import annotations

import os
import pathlib
import subprocess

import hunter_github_transport as transport
import hunter_pre_ready_review as pre_ready
import hunter_review_orchestrator as orchestrator
import pytest
import yaml

HEAD = "a" * 40
REPOSITORY_ROOT = pathlib.Path(orchestrator.__file__).resolve().parents[1]


def _five_minutes_ago():
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if path == f"commits/{HEAD}/statuses?per_page=100":
            # The status LIST endpoint, which is the one that carries the publisher.
            return [
                {
                    "context": "Hunter Review Orchestration / PR #472",
                    "description": f"WAITING_FOR_REVIEWER|local|0|{'d' * 64}",
                    "target_url": "https://github.com/owner/repo/actions/runs/123",
                    "created_at": "2026-09-15T00:00:00Z",
                    "creator": {"login": "github-actions[bot]"},
                }
            ]
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


def _absent_cycle_with_recorded_dispatches(monkeypatch) -> dict:
    """Stub an unrecorded exact head whose dispatches append real in-progress
    collector runs to the run listing ``ensure_collector`` reads back."""

    stored: dict = {"dispatches": 0}
    runs: list[dict] = []

    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_args: ("absent", None, None))
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999, raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, **_kwargs: None, raising=False)

    def dispatch_collector(
        _repository, _token, pr_number, head_sha, generation_id=orchestrator.BASE_GENERATION_ID, _election=None
    ):
        stored["dispatches"] += 1
        runs.append(_collector_run("in_progress", pr_number=pr_number, head_sha=head_sha, run_id=stored["dispatches"]))

    monkeypatch.setattr(orchestrator, "dispatch_collector", dispatch_collector, raising=False)

    def request_json(_repository, _token, _method, path, _payload=None):
        if path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs"):
            if "event=workflow_dispatch" in path:  # pre-migration collectors: none draining
                return {"workflow_runs": []}
            return {"workflow_runs": runs}
        if path == "":
            return {"default_branch": "main"}
        if path.startswith("commits/") and "/statuses" in path:
            return []
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(orchestrator, "request_json", request_json)

    return stored


def _seconds_ago(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _budget() -> int:
    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    return pre_ready.reviewer_chain_worst_case_seconds(pool)


def test_collector_workflow_lifetime_covers_the_reviewer_chain_budget():
    """PR #535 live evidence: the collector's declared lifetime must not silently

    contradict the reviewer budgets it is supposed to run to completion. A
    workflow ``timeout-minutes`` shorter than the worst case every enabled
    reviewer can spend (as ``docs/CODE_WRITE_POLICY.json`` itself configures
    it) cancels the job mid-invocation and turns a reviewer still within its
    own configured budget into a false "unavailable". This is the single
    coherence check both magic numbers -- the workflow's static YAML timeout
    and the pool's per-agent budgets -- are validated against, rather than two
    independently hand-set figures that can drift apart again.
    """

    document = yaml.safe_load(
        pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-reviewer-collector.yml").read_text()
    )
    workflow_seconds = int(document["jobs"]["collect"]["timeout-minutes"]) * 60

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    required_seconds = pre_ready.reviewer_chain_worst_case_seconds(pool)

    assert workflow_seconds >= required_seconds


def test_independent_review_opportunity_matches_the_same_canonical_budget():
    """The orchestrator's own pending-cycle timeout must derive from the same

    source as the collector workflow's lifetime, not carry its own
    disconnected constant that can silently fall out of step with it.
    """

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    assert orchestrator.independent_review_opportunity_seconds() == pre_ready.reviewer_chain_worst_case_seconds(pool)


def test_the_derived_budget_is_still_readable_as_a_module_attribute():
    """The budget is a derivation, but trusted default-branch readers still reach
    it as a module attribute.

    The trusted orchestrator replay harness imports this *candidate's* module
    while its own scenario logic is still the default branch's version, and that
    version reads the budget through `INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS`.
    Dropping the name broke the candidate-facing half of that harness before the
    candidate can ever prove itself, which is the same bootstrap limitation the
    Review Opportunity migration identity exists to work around. Both access forms
    must therefore keep resolving, to the same derived value.
    """

    pool, error = pre_ready.load_reviewer_pool()
    assert pool is not None and not error
    derived = pre_ready.reviewer_chain_worst_case_seconds(pool)

    assert orchestrator.INDEPENDENT_REVIEW_OPPORTUNITY_SECONDS == derived
    assert orchestrator.independent_review_opportunity_seconds() == derived

    with pytest.raises(AttributeError):
        _unknown = orchestrator.NOT_A_REAL_ORCHESTRATOR_ATTRIBUTE


def _ensure_harness(monkeypatch, cycle, runs, epochs=1):
    """Drive ensure_collector against a recorded cycle and a collector run listing.

    ``epochs`` is how many owner epochs were already elected: the dispatch budget is
    counted from those authoritative records, never from listed runs.
    """

    stored = {"dispatches": 0, "published": []}
    owner = orchestrator.CollectorOwner(0, 1, "0" * 64, "2026-01-01T00:00:00Z")
    monkeypatch.setattr(orchestrator, "read_owner_chain", lambda *_args: [owner] * epochs)
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
        if path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs"):
            if "event=workflow_dispatch" in path:  # pre-migration collectors: none draining
                return {"workflow_runs": []}
            return {"workflow_runs": runs}
        if path == "":
            return {"default_branch": "main"}
        if path.startswith("commits/") and "/statuses" in path:
            return []
        pytest.fail(f"unexpected request {path}")

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


# ---------------------------------------------------------------------------
# Production defect: two reconciles landing close together each observed the
# commit-status cycle as "absent" (a stale/racy read of that one idempotency
# record) and each independently dispatched a collector for the same PR, exact
# head, and generation. `ensure_collector` must suppress a duplicate dispatch
# whenever a correlated collector run already exists, independent of whatever
# `read_cycle` itself answered -- never relying on the commit-status cycle as
# the only idempotency boundary.
# ---------------------------------------------------------------------------


def _render_run_name(template: str, generation_id: str) -> str:
    """Render the workflow run-name template the way Actions would.

    The generation expression is `a && b || c`, which yields `b` when the input
    is a non-empty string and `c` when it is empty or absent.
    """

    suffix = f" GEN {generation_id}" if generation_id else ""
    return (
        template.replace("${{ github.event.client_payload.pr_number }}", "472")
        .replace("${{ github.event.client_payload.head_sha }}", HEAD)
        .replace(
            "${{ github.event.client_payload.generation_id && format(' GEN {0}', github.event.client_payload.generation_id) || '' }}",
            suffix,
        )
    )


def _workflow_documents():
    directory = pathlib.Path(REPOSITORY_ROOT, ".github/workflows")
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _permission_blocks(document):
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        return ["missing-workflow-permissions"]
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    return [block for block in blocks if isinstance(block, dict)]


#: Permission levels that can drive another workflow run.
WORKFLOW_DRIVING = frozenset({"write", "admin"})


def _actions_levels(document):
    """Yield the effective `actions` level of every permission block in `document`.

    A workflow may declare permissions as a mapping or as one of the blanket
    strings, and `write-all` grants `actions: write` just as surely as spelling
    the scope out does. Reading only mappings would let the shorter spelling pass
    a guard that the longer one fails.
    """
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        yield "missing-workflow-permissions"
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    for block in blocks:
        if isinstance(block, str):
            yield "write" if block.strip() == "write-all" else "read"
        elif isinstance(block, dict) and block.get("actions") is not None:
            yield str(block["actions"]).strip()


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
        and any(level in WORKFLOW_DRIVING | {"missing-workflow-permissions"} for level in _actions_levels(document))
    ]
    assert privileged == []


@pytest.mark.parametrize(
    ("source", "candidate_controlled"),
    [
        ("on:\n  pull_request:\n    branches: [main]\n", True),
        ("on: pull_request\n", True),
        ("on: [push, pull_request]\n", True),
        ('"on":\n  pull_request: null\n', True),
        # `pull_request_target` runs the base-branch copy of the workflow file,
        # so its permissions are not candidate-reachable and are not flagged.
        ("on:\n  pull_request_target:\n    types: [opened]\n", False),
        ("on:\n  workflow_run:\n    workflows: [Other]\n", False),
        ("on:\n  push:\n    branches: [main]\n", False),
        ("on: workflow_dispatch\n", False),
    ],
)
def test_candidate_controlled_triggers_are_read_from_every_declaration_shape(source, candidate_controlled):
    assert ("pull_request" in _triggers(yaml.safe_load(source))) is candidate_controlled


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        ({"actions": "write"}, ["write"]),
        ({"actions": "read"}, ["read"]),
        ({"actions": " write "}, ["write"]),
        ({"contents": "read"}, []),
        ({}, []),
        (None, ["missing-workflow-permissions"]),
        # Blanket declarations grant every scope, `actions` included.
        ("write-all", ["write"]),
        ("read-all", ["read"]),
    ],
)
def test_the_actions_level_is_read_from_every_permission_shape(permissions, expected):
    assert list(_actions_levels({"permissions": permissions})) == expected


def test_a_job_cannot_re_grant_what_the_workflow_block_gave_up():
    """A read-only workflow block does not excuse a job that takes the scope back."""
    document = {
        "permissions": {"actions": "read"},
        "jobs": {"privileged": {"permissions": {"actions": "write"}}},
    }
    assert any(level in WORKFLOW_DRIVING for level in _actions_levels(document))


def test_the_guard_still_describes_a_repository_that_grants_the_permission_somewhere():
    """The guard above is only meaningful while `actions: write` exists at all."""
    privileged = [
        path.name
        for path, document in _workflow_documents()
        if isinstance(document, dict) and any(level in WORKFLOW_DRIVING for level in _actions_levels(document))
    ]
    assert privileged, "no workflow declares actions:write; this guard no longer describes the repository"


def test_governance_review_workflow_keeps_only_read_access_to_actions():
    """It still reads collector run evidence; it may no longer drive workflows."""

    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-review.yml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "pull_request" not in _triggers(document)
    assert "pull_request_target" in _triggers(document)
    assert "workflow_dispatch" not in _triggers(document)
    assert document["permissions"]["statuses"] == "write"
    blocks = _permission_blocks(document)
    assert blocks and all(str(block.get("actions", "read")).strip() == "read" for block in blocks)
    assert "python scripts/hunter_review_orchestrator.py" not in path.read_text(encoding="utf-8")


def _pull_request_target_types(workflow_name):
    document = yaml.safe_load(
        pathlib.Path(REPOSITORY_ROOT, ".github/workflows", workflow_name).read_text(encoding="utf-8")
    )
    triggers = document.get("on", document.get(True))
    return triggers, triggers.get("pull_request_target") or {}


def _assert_draft_pr_starts_no_review(monkeypatch, pr_number):
    """A Draft PR short-circuits before the review-request lookup and before
    any collector dispatch."""

    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "draft": True, "head": {"sha": HEAD}},
    )
    prerequisite_calls = []
    monkeypatch.setattr(
        orchestrator,
        "review_request_state",
        lambda *_args: prerequisite_calls.append(True)
        or orchestrator.ReviewRequestReadiness(True, "d" * 64, "", "success"),
        raising=False,
    )
    dispatch_calls = []
    monkeypatch.setattr(orchestrator, "ensure_collector", lambda *_args: dispatch_calls.append(True), raising=False)

    assert orchestrator.ensure_current("owner/repo", "token", pr_number) is None
    assert prerequisite_calls == []
    assert dispatch_calls == []


def test_default_branch_without_orchestrator_publishes_bootstrap_pending_without_dispatching_authority():
    """A bootstrap PR cannot invoke a controller that trusted main does not contain.

    The migration state itself is decided by the trusted default-branch bridge --
    see ``tests/test_pr473_trusted_bridge_bootstrap.py`` -- because only that tree
    can tell whether the controller has landed, and only that tree can adopt an
    exact-head review once one exists. What this lane owns is the guarantee that
    it hands the decision to that bridge and does nothing else: it runs the
    trusted copy out of ``engine/``, never a candidate copy of the orchestrator,
    and reaches no dispatch or comment surface on the way.
    """

    path = pathlib.Path(REPOSITORY_ROOT, ".github/workflows/hunter-governance-review.yml")
    workflow = path.read_text(encoding="utf-8")
    document = yaml.safe_load(workflow)

    runs = [
        step["run"] for job in document["jobs"].values() for step in job["steps"] if isinstance(step.get("run"), str)
    ]
    assert any('python "${BRIDGE}" governance' in run for run in runs)

    for run in runs:
        assert "python scripts/hunter_review_orchestrator.py" not in run
        assert "actions/workflows/" not in run
        assert "issues/${PR_NUMBER}/comments" not in run
        assert "${GITHUB_WORKSPACE}/engine/scripts/" in run or "BRIDGE" not in run

    checkouts = [
        step
        for job in document["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert checkouts, "the lane must check out the trusted tree it executes"
    for step in checkouts:
        with_block = step.get("with") or {}
        assert with_block.get("ref") == "${{ github.event.repository.default_branch }}"
        assert with_block.get("path") == "engine"


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


def _readiness_harness(monkeypatch, *, prerequisite_state, request_valid, request_state="present"):
    """Wire the exact-head prerequisites the reconcile transition reads.

    `prerequisite_state` is the trusted preflight/admission state, returned
    rather than made unreachable: the orchestrator consults it for every pull
    request outside the bounded Review Opportunity migration identity, so a
    test that suppressed it would prove nothing about the real path.
    """

    head = HEAD
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda *_args: {"state": "open", "head": {"sha": head}},
        raising=False,
    )
    document = {"review_request": {"schema": "hunter.review-request.v1", "claims_id": "c" * 64}}

    import hunter_governance_review_v2 as governance

    monkeypatch.setattr(
        governance, "read_trusted_upgrade_status", lambda *_args: (prerequisite_state, "prereq detail"), raising=False
    )
    if request_state == "present":
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: ("present", document, None), raising=False
        )
    else:
        monkeypatch.setattr(
            governance, "read_head_pre_ready_review", lambda *_args: (request_state, None, "absent"), raising=False
        )
    monkeypatch.setattr(
        governance,
        "valid_current_review_request",
        lambda *_args: (request_valid, "stale finding F-8 has no exact-head correction evidence"),
        raising=False,
    )
    return document


def _collector_harness(monkeypatch):
    stored = {"cycle": None, "dispatches": 0}
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_args: ("present", stored["cycle"], None) if stored["cycle"] else ("absent", None, None),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64, raising=False)
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 555, raising=False)
    monkeypatch.setattr(orchestrator, "_all_commit_statuses", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(orchestrator, "publish_cycle", lambda *_args, cycle: stored.update(cycle=cycle), raising=False)
    monkeypatch.setattr(orchestrator, "collector_liveness", lambda *_args: ("missing", 0), raising=False)
    monkeypatch.setattr(
        orchestrator,
        "dispatch_collector",
        lambda *_args: stored.update(dispatches=stored["dispatches"] + 1),
        raising=False,
    )
    monkeypatch.setattr(orchestrator, "current_remediation_generation", lambda *_args: "gen-1", raising=False)
    return stored


def test_readiness_reports_which_blocker_applies(monkeypatch):
    """The reason survives to the caller, so the blocker is actionable rather
    than a bare False."""

    _readiness_harness(monkeypatch, prerequisite_state="success", request_valid=False)
    blocked = orchestrator.review_request_state("owner/repo", "token", 472, HEAD)
    assert blocked.ready is False
    assert blocked.prerequisite_state == "success"
    assert "stale finding F-8" in blocked.reason

    _readiness_harness(monkeypatch, prerequisite_state="pending", request_valid=False)
    pending = orchestrator.review_request_state("owner/repo", "token", 472, HEAD)
    assert pending.ready is False
    assert pending.prerequisite_state == "pending"
    assert "prereq detail" in pending.reason


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
    # Disabled triage-only reviewers are not part of the active pool. Until
    # trusted health admission exists, authority progression starts at Codex.
    assert orchestrator.first_pool_provider() == "codex"
    assert orchestrator.next_authority_provider(after="codex") == "copilot"
    assert orchestrator.next_authority_provider(after="gemini") == "groq"
    assert orchestrator.next_authority_provider(after="groq") == str(pool["last_resort"]) == "hunter-guard"


def test_unreadable_runner_probe_neither_crashes_nor_skips_the_reviewer(monkeypatch):
    """GITHUB_TOKEN cannot read repository runners; 403 is not evidence of absence."""

    def forbidden(*_args, **_kwargs):
        raise transport.GitHubRequestError("no Administration:read", category="permanent", status_code=403)

    monkeypatch.setattr(orchestrator, "request_json", forbidden)

    assert orchestrator.runner_state("owner/repo", "token") == "unknown"
    decision = orchestrator.select_provider("owner/repo", "token")
    assert decision.state == "REVIEW_IN_PROGRESS"
    assert decision.next_provider == "codex"
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


# ---------------------------------------------------------------------------
# Production defect: a completed Hunter Reviewer Collector run always executes
# from the trusted default branch, so its own workflow_run.head_sha is main's
# SHA, never the candidate's. Reconcile's PR derivation must never search open
# PRs by that head_sha for this specific trigger; the candidate PR/head is
# instead the trusted identity the collector's own run-name already carries.
# ---------------------------------------------------------------------------

RECONCILE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "hunter-governance-reconcile.yml"
COLLECTOR_WORKFLOW_FILE = REPOSITORY_ROOT / ".github" / "workflows" / "hunter-reviewer-collector.yml"
_COLLECTOR_BRANCH_MARKER = (
    'elif [[ "${event_name}" == "workflow_run" '
    '&& "${EVENT_WORKFLOW_RUN_NAME}" == "Hunter Reviewer Collector" ]]; then'
)


def _reconcile_run_script() -> str:
    document = yaml.safe_load(RECONCILE_WORKFLOW.read_text(encoding="utf-8"))
    for step in document["jobs"]["reconcile"]["steps"]:
        if step.get("name") == "Refresh lightweight governance status":
            return str(step["run"])
    raise AssertionError("reconcile governance-refresh step not found")


def _collector_branch_body() -> str:
    script = _reconcile_run_script()
    assert _COLLECTOR_BRANCH_MARKER in script
    return script.split(_COLLECTOR_BRANCH_MARKER, 1)[1].split("elif", 1)[0]


def _extract_collector_pr_numbers(title: str) -> str:
    """Run the exact committed shell text that derives pr_numbers for a
    completed 'Hunter Reviewer Collector' run, against a real bash process --
    exercising the real committed script rather than a reimplementation."""

    completed = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{_collector_branch_body()}\nprintf "%s" "$pr_numbers"'],
        env={"EVENT_WORKFLOW_RUN_TITLE": title, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_remediation_generation_accepts_only_configured_authority_bots(monkeypatch):
    def graphql(**_kwargs):
        def node(thread_id, login):
            return {
                "id": thread_id,
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "databaseId": len(thread_id),
                            "createdAt": "2026-09-19T12:00:00Z",
                            "author": {"login": login, "__typename": "Bot"},
                        }
                    ]
                },
            }

        return {
            "repository": {
                "pullRequest": {
                    "author": {"login": "fafa33"},
                    "reviewThreads": {
                        "nodes": [
                            node("codex-thread", "chatgpt-codex-connector"),
                            node("hunter-thread", "github-actions[bot]"),
                            node("random-thread", "unrelated-review-bot"),
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            }
        }

    monkeypatch.setattr(orchestrator.transport, "request_graphql_json", graphql)
    threads = orchestrator.blocking_reviewer_threads("owner/repo", "token", 473)
    assert [thread.thread_id for thread in threads] == ["codex-thread"]


# --- PR #547: a completed, exhausted reviewer chain must end the opportunity ---
#
# The exact-head cycle status this orchestrator publishes carries no `creator`
# field in GitHub's combined-status response, so `_parse_cycle` rejected it,
# `read_cycle` reported "absent", and Merge Readiness reported
# WAITING_FOR_REVIEWER forever -- including after a collector completed with
# Codex NO_ACK_TIMEOUT, Copilot REVIEW_TIMEOUT and Gemini/Groq
# PROVIDER_UNAVAILABLE. An exhausted chain must terminate the opportunity.


def _exhausted_cycle_status(creator, context, description):
    status = {"context": context, "description": description, "created_at": "2026-09-30T10:39:31Z"}
    if creator is not None:
        status["creator"] = {"login": creator}
    return status


# --- Codex P1: a status with no reported creator is not, by itself, a binding.
#
# context, description and target_url are all caller-controlled. A publisher who
# can post a status can cite an existing trusted default-branch run in target_url
# and write any cycle state into the description. So `creator == null` may only
# assert a non-authoritative pending state, and the run recorded in the payload
# must be the very run target_url names.


# --- Codex P1 4147741248: the publisher must be authenticated, strictly. -----
#
# The combined status endpoint reports `creator: null` for every status, including
# trusted workflow posts, so a creator check read from it could never be satisfied.
# That is what forced a creator-less fallback trusting a caller-controlled payload.
# `read_cycle` now reads the status LIST endpoint, which carries the real
# publisher, so the strict check stands alone and no derivation path exists.

CYCLE_CTX = f"{orchestrator.CONTEXT_PREFIX}472"
GEN = "e4a1e847caf03a75"
RUN_ID = 36710945665
RUN_URL = f"https://github.com/owner/repo/actions/runs/{RUN_ID}"
TRUSTED = "github-actions[bot]"


def _status(creator, description, target_url=RUN_URL, context=CYCLE_CTX):
    status = {
        "context": context,
        "description": description,
        "created_at": "2026-01-01T00:00:00Z",
        "target_url": target_url,
    }
    if creator is not None:
        status["creator"] = {"login": creator}
    return status


def _desc(state, provider="", trigger=RUN_ID, generation=GEN, digest=None):
    return f"{state}|{provider}|{trigger}|{digest or ('d' * 64)}|{generation}"


def test_combined_status_endpoint_exposes_no_creator():
    """Regression guard: the combined endpoint reports creator=null even for a
    status the list endpoint attributes to the trusted publisher. Proven live for
    one identical status id on this repository."""

    combined = {"context": CYCLE_CTX, "description": _desc("REVIEW_CLEAR"), "creator": None}
    listed = {"context": CYCLE_CTX, "description": _desc("REVIEW_CLEAR"), "creator": {"login": TRUSTED}}

    # The combined shape is exactly why the strict check could not be satisfied
    # from that endpoint: it carries no publisher at all.
    assert orchestrator._parse_cycle(combined, 472, HEAD) is None
    # The same status read from the list endpoint is authenticated and accepted.
    assert orchestrator._parse_cycle(listed, 472, HEAD) is not None


def test_read_cycle_reads_the_status_list_endpoint():
    """`read_cycle` must not use the combined endpoint for provenance."""

    import inspect

    source = inspect.getsource(orchestrator.read_cycle)
    assert "_all_commit_statuses(repository, token, head_sha)" in source
    assert 'f"commits/{head_sha}/status"' not in source
    # Only the list endpoint, so the publisher is actually populated.
    assert "commits/{head_sha}/statuses?per_page=100" in inspect.getsource(orchestrator._all_commit_statuses)


def test_trusted_creator_cycle_is_accepted():
    parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc("WAITING_FOR_REVIEWER")), 472, HEAD)
    assert parsed is not None
    assert parsed.state == "WAITING_FOR_REVIEWER"
    assert parsed.trigger_id == RUN_ID


def test_missing_creator_is_rejected():
    assert orchestrator._parse_cycle(_status(None, _desc("WAITING_FOR_REVIEWER")), 472, HEAD) is None


def test_wrong_creator_is_rejected():
    assert orchestrator._parse_cycle(_status("attacker", _desc("WAITING_FOR_REVIEWER")), 472, HEAD) is None


def test_forged_terminal_timeout_is_rejected_without_trusted_creator():
    """A creator-less payload cannot terminate the opportunity any more: there is
    no age-based or status-age authentication left to lean on."""

    assert orchestrator._parse_cycle(_status(None, _desc("REVIEW_TIMED_OUT")), 472, HEAD) is None
    assert orchestrator._parse_cycle(_status("attacker", _desc("REVIEW_TIMED_OUT")), 472, HEAD) is None


def test_genuine_terminal_states_from_the_trusted_creator_are_accepted():
    """An authenticated publisher may still assert the terminal outcomes, which is
    what makes the bounded opportunity terminate instead of stalling."""

    for state in ("REVIEW_TIMED_OUT", "POOL_EXHAUSTED", "REVIEWER_UNAVAILABLE"):
        parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc(state)), 472, HEAD)
        assert parsed is not None, state
        assert parsed.state == state


def test_genuine_review_clear_and_findings_open_remain_accepted():
    for state in ("REVIEW_CLEAR", "FINDINGS_OPEN"):
        parsed = orchestrator._parse_cycle(_status(TRUSTED, _desc(state)), 472, HEAD)
        assert parsed is not None, state
        assert parsed.state == state


def test_exact_head_and_generation_binding_are_unchanged():
    """Every non-provenance binding still holds, authenticated creator or not."""

    assert orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR")), 473, HEAD) is None
    assert (
        orchestrator._parse_cycle(
            _status(TRUSTED, _desc("REVIEW_CLEAR"), context=f"{orchestrator.CONTEXT_PREFIX}999"), 472, HEAD
        )
        is None
    )
    assert (
        orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR", generation="not-a-generation")), 472, HEAD)
        is None
    )
    assert orchestrator._parse_cycle(_status(TRUSTED, _desc("REVIEW_CLEAR", digest="short")), 472, HEAD) is None


def _candidate_status_publishers(document):
    """Candidate-owned workflow definitions must never receive status/check write authority."""
    if not isinstance(document, dict):
        return []
    triggers = _triggers(document)
    push = document.get("on", document.get(True))
    push = push.get("push") if isinstance(push, dict) else None
    main_only_push = (
        isinstance(push, dict)
        and push.get("branches") == ["main"]
        and not push.get("branches-ignore")
        and not push.get("tags")
    )
    if (
        not ({"pull_request", "pull_request_review", "pull_request_review_comment"} & set(triggers))
        and not ("push" in triggers and not main_only_push)
        and "workflow_dispatch" not in triggers
    ):
        return []
    blocks = [document.get("permissions")]
    if blocks[0] is None:
        return ["missing-workflow-permissions"]
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        blocks.extend(job.get("permissions") for job in jobs.values() if isinstance(job, dict))
    unsafe = []
    for block in blocks:
        if isinstance(block, str) and block.strip() == "write-all":
            unsafe.append("write-all")
        elif isinstance(block, dict):
            unsafe.extend(key for key in ("statuses", "checks") if str(block.get(key, "")).strip() in WORKFLOW_DRIVING)
    return unsafe


def test_candidate_workflows_cannot_publish_protected_commit_statuses():
    unsafe = [(path.name, _candidate_status_publishers(document)) for path, document in _workflow_documents()]
    assert [(name, grants) for name, grants in unsafe if grants] == []


@pytest.mark.parametrize(
    "trigger",
    [
        "on: pull_request",
        "on: pull_request_review",
        "on: pull_request_review_comment",
        "on: [push, pull_request]",
        '"on": {pull_request: null}',
        "on: push",
        "on: {push: {branches-ignore: [main]}}",
        "on: workflow_dispatch",
    ],
)
@pytest.mark.parametrize(
    "grant", ["permissions: {statuses: write}", "permissions: {checks: write}", "permissions: write-all"]
)
def test_candidate_status_spoofing_mutation_is_rejected(trigger, grant):
    assert _candidate_status_publishers(yaml.safe_load(f"{trigger}\n{grant}\n"))


@pytest.mark.parametrize("scope", ["statuses", "checks"])
def test_job_override_cannot_publish_protected_status(scope):
    document = yaml.safe_load(
        f"on: pull_request\npermissions: {{{scope}: read}}\njobs:\n  spoof:\n    permissions: {{{scope}: write}}\n"
    )
    assert _candidate_status_publishers(document) == [scope]


def test_job_write_all_cannot_publish_protected_status():
    document = yaml.safe_load(
        "on: pull_request\npermissions: {contents: read}\njobs:\n  spoof:\n    permissions: write-all\n"
    )
    assert _candidate_status_publishers(document) == ["write-all"]


def test_candidate_missing_explicit_permission_baseline_is_rejected():
    document = yaml.safe_load("on: pull_request\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    assert _candidate_status_publishers(document) == ["missing-workflow-permissions"]


def test_main_only_push_is_trusted():
    document = yaml.safe_load("on: {push: {branches: [main]}}\npermissions: {statuses: write}\n")
    assert _candidate_status_publishers(document) == []


def test_commit_status_pagination_keeps_old_trusted_dispatch_proof(monkeypatch):
    calls = []
    proof = {
        "context": "Hunter Collector Dispatch Proof / PR #472",
        "creator": {"login": "github-actions[bot]"},
        "id": 7,
    }

    def request(_repo, _token, _method, path, _payload=None):
        calls.append(path)
        if path.endswith("page=100"):
            return [{"id": n} for n in range(100)]
        if path.endswith("page=2"):
            return [proof]
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    statuses = orchestrator._all_commit_statuses("owner/repo", "token", HEAD)
    assert len(statuses) == 101
    assert proof in statuses
    assert len(calls) == 2


def test_pr592_defect_registry_never_claims_unverified_findings_prevented():
    """The learning ledger must not silently promote historical review titles."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/PR592_COPILOT_FINDING_LEDGER.json").read_text())
    registry = json.loads((root / "docs/DEFECT_REGISTRY.json").read_text())
    by_id = {entry["id"]: entry for entry in registry["defects"]}
    assert len(ledger["findings"]) == ledger["captured_findings"] == 43
    for finding in ledger["findings"]:
        assert finding["id"] in by_id
        assert finding["status"] != "prevented"
        assert finding.get("verification")


def test_missing_workflow_permission_baseline_is_detected_in_action_guard():
    import yaml

    document = yaml.safe_load("on: pull_request\njobs: {build: {runs-on: ubuntu-latest}}\n")
    assert "missing-workflow-permissions" in list(_actions_levels(document))
