"""Issue #575: verified resolution must wake trusted governance reconciliation.

GitHub delivers no workflow event for a manually resolved review thread.  The
automatic resolver job does have a real, trusted completion event: its workflow
run completes only after the durable knowledge append and thread mutation have
finished.  This contract uses that event, but only when the completed resolver
job publishes scoped PR numbers; failed, missing, duplicated or malformed
outputs produce no wake-up and leave the existing bounded sweep as recovery.
"""

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

WORKFLOW_NAME = "Hunter / Issue Agent Reconcile"
RESOLVE_OUTPUT = "resolved_pr_numbers"


def _triggers(document):
    return document.get(True) or document.get("on")


def _event(conclusion="success", run_id=45999110514, attempt=1):
    return {
        "name": "workflow_run",
        "workflow_run": {
            "id": run_id,
            "run_attempt": attempt,
            "name": WORKFLOW_NAME,
            "conclusion": conclusion,
        },
    }


def _jobs(output=None, conclusion="success", job_conclusion="success"):
    outputs = {} if output is None else {RESOLVE_OUTPUT: output}
    return {
        "jobs": [
            {
                "name": "resolve-finding",
                "conclusion": job_conclusion,
                "steps": [{"name": "resolve", "outputs": outputs}],
            }
        ]
    }


def test_governance_reconcile_uses_the_actual_issue_agent_completion_event():
    document = yaml.safe_load((ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text())
    on = _triggers(document)
    assert "pull_request" not in on
    workflow_run = on["workflow_run"]
    assert workflow_run["types"] == ["completed"]
    assert WORKFLOW_NAME in workflow_run["workflows"]
    # The privileged job still resolves its file from the default branch and
    # still refuses any non-default ref, including workflow_dispatch.
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in document["jobs"]["reconcile"]["if"]
    )
    assert document["jobs"]["reconcile"]["steps"][0]["with"]["ref"] == "main"
    assert document["permissions"]["actions"] == "write"
    assert document["permissions"]["statuses"] == "write"


def test_resolver_publishes_scoped_output_without_gaining_workflow_authority():
    workflow = (ROOT / ".github/workflows/hunter-issue-agent-reconcile.yml").read_text()
    job = yaml.safe_load(workflow)["jobs"]["resolve-finding"]
    assert "actions" not in job["permissions"]
    resolve_step = next(step for step in job["steps"] if "lifecycle.py resolve-finding" in str(step.get("run", "")))
    assert resolve_step["id"] == "resolve"
    source = (ROOT / "scripts/hunter_issue_agent_lifecycle.py").read_text()
    body = source.split("def cmd_resolve(", 1)[1].split("\ndef cmd_resume_bind(", 1)[0]
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    assert "_output(resolved_pr_numbers=" in code
    assert "deferred to trusted scheduled sweep" not in code
    assert "dispatch" not in code and "workflow run" not in code


def test_successful_resolver_output_selects_only_the_affected_pr():
    from hunter_resolution_reconcile_ingress import select_resolved_pull_requests

    assert select_resolved_pull_requests(_event(), _jobs("789")) == (789,)
    assert select_resolved_pull_requests(_event(), _jobs("788, 789")) == (788, 789)


def test_failed_or_incomplete_resolution_never_creates_false_readiness():
    from hunter_resolution_reconcile_ingress import select_resolved_pull_requests

    assert select_resolved_pull_requests(_event("failure"), _jobs("789")) == ()
    assert select_resolved_pull_requests(_event("cancelled"), _jobs("789")) == ()
    assert select_resolved_pull_requests(_event(), _jobs("789", job_conclusion="failure")) == ()
    assert select_resolved_pull_requests(_event(), _jobs()) == ()
    assert select_resolved_pull_requests({**_event(), "name": "push"}, _jobs("789")) == ()


def test_duplicate_malformed_and_untrusted_outputs_fail_closed():
    from hunter_resolution_reconcile_ingress import select_resolved_pull_requests

    assert select_resolved_pull_requests(_event(), _jobs("789,789,789")) == (789,)
    assert select_resolved_pull_requests(_event(), _jobs("789,not-a-pr")) == ()
    assert select_resolved_pull_requests(_event(), _jobs("0,-1,789")) == ()
    assert select_resolved_pull_requests(_event(), _jobs("999999999999")) == ()
    two_outputs = {
        "jobs": [
            {
                "name": "resolve-finding",
                "conclusion": "success",
                "steps": [
                    {"name": "one", "outputs": {RESOLVE_OUTPUT: "789"}},
                    {"name": "two", "outputs": {RESOLVE_OUTPUT: "788"}},
                ],
            }
        ]
    }
    assert select_resolved_pull_requests(_event(), two_outputs) == ()


def test_manual_thread_resolution_without_a_trusted_resolver_output_is_recovery_only():
    from hunter_resolution_reconcile_ingress import select_resolved_pull_requests

    # GitHub emits no workflow_run for an owner resolving a thread in the UI.
    # The bounded 5-minute sweep remains the only path for that state change.
    assert select_resolved_pull_requests(_event(), _jobs()) == ()


def test_reconcile_dedupes_events_and_rebinds_exact_head_before_refresh():
    text = (ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    assert "hunter-resolution-reconcile/${EVENT_WORKFLOW_RUN_ID}/${EVENT_WORKFLOW_RUN_ATTEMPT}" in text
    assert "select(.context ==" in text and "${resolution_marker}" in text
    assert "recheck_head" in text
    assert '[[ "${recheck_head}" != "${invocation_head}" ]]' in text
    assert '--expected-head "${invocation_head}"' in text


def test_scheduled_sweep_remains_the_recovery_path_only():
    document = yaml.safe_load((ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text())
    on = _triggers(document)
    assert on["schedule"] == [{"cron": "*/5 * * * *"}]
    assert document["concurrency"]["cancel-in-progress"] is False
