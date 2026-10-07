"""AT-41 / AT-42 / AT-47: static guard over the GitHub-native Issue-agent workflows (ADR 0037 D1, D9).

The guard functions operate on the parsed workflow (never on prose), and each carries paired fixtures: a
representation that does not actually satisfy the rule must be rejected, and a canonically valid
equivalent must be accepted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
LIFECYCLE = "hunter-issue-agent-trigger.yml"
GOVERNED = (
    LIFECYCLE,
    "hunter-issue-agent-reconcile.yml",
    "hunter-issue-agent-candidate-pr.yml",
    "hunter-issue-agent-replacement-rehearsal.yml",
    "hunter-issue-agent-knowledge.yml",
    "hunter-issue-agent-source-handling-bootstrap.yml",
)

CONTROL, EXECUTOR = "hunter-issue-agent-control", "hunter-issue-agent-executor"
VALIDATOR, PUBLISHER = "hunter-issue-agent-validator", "hunter-issue-agent-publisher"
LIFECYCLE_DOMAINS = {
    "authorize": CONTROL,
    "bind": CONTROL,
    "resume-bind": CONTROL,
    "record-validation": CONTROL,
    "finalize": CONTROL,
    "execute": EXECUTOR,
    "validate": VALIDATOR,
    "publish": PUBLISHER,
}
#: Which secrets each trust domain may ever see (ADR 0037 D1).
DOMAIN_SECRETS = {
    CONTROL: {
        "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY",
        "HUNTER_ISSUE_AGENT_STATE_SIGNING_KEY",
        "HUNTER_SOURCE_HANDLING_SIGNING_KEY",
        "HUNTER_PROMPT_AUTOMATION_SIGNING_KEY",
    },
    EXECUTOR: {"HUNTER_ISSUE_AGENT_HANDOFF_KEY", "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY"},
    VALIDATOR: {"HUNTER_ISSUE_AGENT_RESULT_KEY"},
    PUBLISHER: {
        "HUNTER_ISSUE_AGENT_RESULT_KEY",
        "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN",
        "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY",
    },
    None: {"HUNTER_ISSUE_AGENT_PR_TOKEN", "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY"},
}
CONTENT_JOBS = ("authorize", "execute", "validate", "publish")
CONTROL_SHA_REFS = {"${{ github.sha }}", "${{ needs.resume-bind.outputs.control_sha || github.sha }}"}
_PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
_SECRET = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")
_RETIRED = re.compile(r"WEBHOOK|PROVISIONING|RAILWAY|N8N", re.IGNORECASE)


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(job: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield from job.get("steps") or []


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


# --- guard functions ---------------------------------------------------------------------------------


def unpinned_actions(workflow: Mapping[str, Any]) -> list[str]:
    return [
        str(step["uses"])
        for job in workflow["jobs"].values()
        for step in steps(job)
        if "uses" in step and _PINNED.fullmatch(str(step["uses"])) is None
    ]


def id_token_grants(workflow: Mapping[str, Any]) -> list[str]:
    scopes = [workflow.get("permissions")] + [job.get("permissions") for job in workflow["jobs"].values()]
    return [
        f"id-token: {scope['id-token']}" for scope in scopes if isinstance(scope, Mapping) and "id-token" in scope
    ] + [
        str(scope) for scope in scopes if isinstance(scope, str)
    ]  # write-all / read-all are refused too


def caches(workflow: Mapping[str, Any]) -> list[str]:
    found = []
    for job in workflow["jobs"].values():
        for step in steps(job):
            uses = str(step.get("uses", ""))
            if uses.startswith("actions/cache") or "cache" in (step.get("with") or {}):
                found.append(uses or str(step.get("name")))
    return found


def secrets_in_run(workflow: Mapping[str, Any]) -> list[str]:
    return [
        name
        for job in workflow["jobs"].values()
        for step in steps(job)
        for name in _SECRET.findall(str(step.get("run", "")))
    ]


def job_secrets(job: Mapping[str, Any]) -> set[str]:
    return {name for text in strings(job) for name in _SECRET.findall(text)}


def retired_clients(workflow: Mapping[str, Any]) -> list[str]:
    """AT-42: no env variable, secret or variable of a retired Railway/webhook/n8n client."""

    names = set()
    for text in strings(workflow):
        names.update(_SECRET.findall(text))
        names.update(re.findall(r"vars\.([A-Za-z0-9_]+)", text))
    for job in workflow["jobs"].values():
        names.update((job.get("env") or {}).keys())
        for step in steps(job):
            names.update((step.get("env") or {}).keys())
    return sorted(name for name in names if _RETIRED.search(name))


def queued_concurrency(workflow: Mapping[str, Any]) -> bool:
    concurrency = workflow.get("concurrency")
    return (
        isinstance(concurrency, Mapping)
        and concurrency.get("queue") == "max"
        and concurrency.get("cancel-in-progress") is False
    )


# --- the governed workflows ------------------------------------------------------------------------


@pytest.mark.parametrize("name", GOVERNED)
def test_every_action_is_pinned_by_commit_sha(name: str) -> None:
    assert unpinned_actions(load(name)) == []


@pytest.mark.parametrize("name", GOVERNED)
def test_no_id_token_no_cache_and_no_secret_in_a_command(name: str) -> None:
    workflow = load(name)
    assert id_token_grants(workflow) == []
    assert caches(workflow) == []
    assert secrets_in_run(workflow) == []


@pytest.mark.parametrize("name", GOVERNED)
def test_least_privilege_is_declared_per_job(name: str) -> None:
    workflow = load(name)
    assert workflow.get("permissions") == {}
    for job_id, job in workflow["jobs"].items():
        assert isinstance(job.get("permissions"), Mapping), job_id


@pytest.mark.parametrize("name", GOVERNED)
def test_runs_queue_instead_of_being_cancelled(name: str) -> None:
    assert queued_concurrency(load(name))


@pytest.mark.parametrize("name", GOVERNED)
def test_no_retired_railway_webhook_or_n8n_client(name: str) -> None:
    """AT-42: rollback leaves no execution path because there is no client to re-enable."""

    assert retired_clients(load(name)) == []


@pytest.mark.parametrize("name", GOVERNED)
def test_every_checkout_drops_its_credentials(name: str) -> None:
    for job in load(name)["jobs"].values():
        for step in steps(job):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert (step.get("with") or {}).get("persist-credentials") is False


def _authorize_prepare_invocations(run: str) -> list[str]:
    return re.findall(
        r"python scripts/hunter_issue_agent_lifecycle\.py authorize-prepare[ \t]*\\\n[ \t]+--(?:document|event)[^\n]+",
        run,
    )


def test_authorize_prepare_multiline_commands_preserve_cli_arguments() -> None:
    """DFF-052: a wrapped shell command may not detach required CLI arguments."""

    run = next(
        step["run"]
        for step in steps(load(LIFECYCLE)["jobs"]["authorize"])
        if step.get("name") == "Mint, verify, compile and seal (no durable write)"
    )
    invocations = _authorize_prepare_invocations(run)
    assert len(invocations) == 2
    assert all("--out-dir" in invocation for invocation in invocations)


def test_authorize_prepare_guard_rejects_comment_disguised_as_continuation() -> None:
    hostile = (
        "python scripts/hunter_issue_agent_lifecycle.py authorize-prepare # \\\n"
        '  --event "$GITHUB_EVENT_PATH" --out-dir "$RUNNER_TEMP/authorize"'
    )
    assert _authorize_prepare_invocations(hostile) == []


def test_each_lifecycle_job_runs_in_its_own_trust_domain() -> None:
    jobs = load(LIFECYCLE)["jobs"]
    assert {job_id: job.get("environment") for job_id, job in jobs.items()} == LIFECYCLE_DOMAINS


def test_secrets_never_cross_a_trust_domain() -> None:
    jobs = load(LIFECYCLE)["jobs"]
    for job_id, job in jobs.items():
        assert job_secrets(job) <= DOMAIN_SECRETS[job["environment"]], job_id
    model_holders = {
        job_id for job_id, job in jobs.items() if "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY" in job_secrets(job)
    }
    push_holders = {
        job_id for job_id, job in jobs.items() if "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN" in job_secrets(job)
    }
    assert model_holders == {"execute"} and push_holders == {"publish"}


@pytest.mark.parametrize("name", GOVERNED[1:])
def test_other_workflows_hold_no_role_secret(name: str) -> None:
    for job in load(name)["jobs"].values():
        assert job_secrets(job) <= DOMAIN_SECRETS[job.get("environment")]


def test_role_tokens_are_minimal() -> None:
    jobs = load(LIFECYCLE)["jobs"]
    assert jobs["execute"]["permissions"] == {}
    assert jobs["validate"]["permissions"] == {"actions": "read"}
    assert set(jobs["publish"]["permissions"].values()) == {"read"}
    for job_id, domain in LIFECYCLE_DOMAINS.items():
        if domain == CONTROL:
            assert "write" not in {
                v for k, v in jobs[job_id]["permissions"].items() if k not in ("contents", "actions")
            }


def test_knowledge_workflow_refuses_fork_origin_workflow_runs() -> None:
    job = load("hunter-issue-agent-knowledge.yml")["jobs"]["knowledge-ingest"]
    condition = str(job["if"])
    assert "github.event_name != 'workflow_run'" in condition
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in condition


def test_content_processing_jobs_run_exactly_control_sha() -> None:
    jobs = load(LIFECYCLE)["jobs"]
    for job_id in CONTENT_JOBS:
        checkouts = [s for s in steps(jobs[job_id]) if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert len(checkouts) == 1, job_id
        assert checkouts[0]["with"]["ref"] in CONTROL_SHA_REFS, job_id


def test_every_lifecycle_job_goes_through_the_fail_closed_entry_point() -> None:
    for name in (LIFECYCLE, "hunter-issue-agent-reconcile.yml", "hunter-issue-agent-knowledge.yml"):
        for job_id, job in load(name)["jobs"].items():
            commands = [str(s["run"]) for s in steps(job) if "run" in s and "pip install" not in str(s["run"])]
            python = [c for c in commands if "python " in c]
            assert python and all("scripts/hunter_issue_agent_lifecycle.py" in c for c in python), job_id


def test_the_publisher_identity_is_the_code_write_policy_actor() -> None:
    """AT-47: the publication job is exactly the CODE_WRITE_POLICY Issue-agent publisher actor."""

    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    actor = policy["code_write_paths"]["issue_agent_publisher"]["actor"]
    job = load(LIFECYCLE)["jobs"][actor["job"]]
    assert actor["workflow_path"] == f".github/workflows/{LIFECYCLE}"
    assert job["environment"] == actor["environment"]


# --- adversarial bypass fixtures (guard self-tests) -----------------------------------------------------


def workflow_with(step: Mapping[str, Any], **top: Any) -> dict[str, Any]:
    return {"permissions": {}, "jobs": {"j": {"permissions": {}, "steps": [step]}}, **top}


@pytest.mark.parametrize(
    "uses",
    [
        "actions/checkout@v7",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b",  # 39 hex
        "actions/checkout@3D3C42E5AAC5BA805825DA76410C181273BA90B1",  # not lowercase canonical
        "actions/checkout@main # 3d3c42e5aac5ba805825da76410c181273ba90b1",  # the SHA only in a comment
        "./.github/actions/local",
        "docker://alpine:3",
    ],
)
def test_an_unpinned_reference_is_refused(uses: str) -> None:
    assert unpinned_actions(workflow_with({"uses": uses})) == [uses]


def test_a_pinned_reference_with_a_version_comment_is_accepted() -> None:
    parsed = yaml.safe_load(
        "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    assert unpinned_actions(parsed) == []


@pytest.mark.parametrize(
    "permissions",
    [{"id-token": "write"}, {"id-token": "read"}, "write-all", "read-all"],
)
def test_any_id_token_or_blanket_grant_is_refused(permissions: object) -> None:
    workflow = {"permissions": {}, "jobs": {"j": {"permissions": permissions, "steps": []}}}
    assert id_token_grants(workflow) != []


def test_explicit_empty_and_scoped_grants_are_accepted() -> None:
    workflow = {"permissions": {}, "jobs": {"j": {"permissions": {"contents": "read"}, "steps": []}}}
    assert id_token_grants(workflow) == []


@pytest.mark.parametrize(
    "step",
    [
        {"uses": "actions/setup-python@" + "a" * 40, "with": {"cache": "pip"}},
        {"uses": "actions/setup-python@" + "a" * 40, "with": {"cache": ""}},
        {"uses": "actions/cache@" + "a" * 40},
        {"uses": "actions/cache/restore@" + "a" * 40},
    ],
)
def test_any_cache_is_refused(step: Mapping[str, Any]) -> None:
    assert caches(workflow_with(step)) != []


@pytest.mark.parametrize(
    "run",
    ["echo ${{ secrets.X }}", "echo ${{secrets.X}}", "curl -H 'a: ${{   secrets.X   }}'"],
)
def test_a_secret_interpolated_into_a_command_is_refused(run: str) -> None:
    assert secrets_in_run(workflow_with({"run": run})) == ["X"]


def test_a_secret_passed_through_step_env_is_accepted() -> None:
    assert secrets_in_run(workflow_with({"run": 'echo "$X" >/dev/null', "env": {"X": "${{ secrets.X }}"}})) == []


@pytest.mark.parametrize(
    "workflow",
    [
        workflow_with({"run": "true", "env": {"HUNTER_ISSUE_AGENT_WEBHOOK_URL": "x"}}),
        workflow_with({"run": "true", "env": {"URL": "${{ secrets.HUNTER_ISSUE_AGENT_PROVISIONING_URL }}"}}),
        workflow_with({"run": "true", "env": {"T": "${{ secrets.RAILWAY_TOKEN }}"}}),
        workflow_with({"run": "true", "env": {"U": "${{ vars.N8N_BASE_URL }}"}}),
        {"permissions": {}, "jobs": {"j": {"permissions": {}, "env": {"railway_api": "x"}, "steps": []}}},
    ],
)
def test_a_retired_client_by_any_route_is_refused(workflow: Mapping[str, Any]) -> None:
    assert retired_clients(workflow) != []


def test_prose_mentioning_the_retirement_is_not_a_client() -> None:
    """A comment or step name about the retirement is not a client (no false positive on documentation)."""

    workflow = workflow_with({"name": "Railway is retired (ADR 0037 D9)", "run": "echo no webhook"})
    assert retired_clients(workflow) == []


@pytest.mark.parametrize(
    "concurrency",
    [
        None,
        {"group": "g"},
        {"group": "g", "queue": "single"},
        {"group": "g", "queue": "max", "cancel-in-progress": True},
    ],
)
def test_a_cancelling_or_dropping_concurrency_is_refused(concurrency: object) -> None:
    assert not queued_concurrency({"concurrency": concurrency})


def test_resolve_finding_is_event_driven_for_pr_review_lifecycle() -> None:
    """DFF-053: review findings must refresh the visible resolve-finding gate without waiting for cron/manual dispatch."""
    document = load("hunter-issue-agent-reconcile.yml")
    triggers = next((value for key, value in document.items() if key is True or key == "on"), None)
    assert isinstance(triggers, dict)
    assert "pull_request_review" not in triggers
    assert "pull_request_review_comment" not in triggers
    assert set(triggers["pull_request_target"]["types"]) >= {"synchronize", "ready_for_review", "reopened"}
    job = document["jobs"]["resolve-finding"]
    assert job["name"] == "resolve-finding"
    assert job["permissions"]["pull-requests"] == "write"
    assert "scripts/hunter_issue_agent_lifecycle.py resolve-finding" in str(job["steps"])
