"""Issue #575: verified resolution must wake trusted governance reconciliation.

GitHub's jobs REST response has no ``steps[].outputs`` field.  The resolver's
scoped PR identities therefore travel in a uniquely named, immutable Actions
artifact that the trusted reconciler reads back by run id and attempt.  These
tests use the documented job and artifact response shapes and exercise the
real shell consumer, not synthetic response fields.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

WORKFLOW_NAME = "Hunter / Issue Agent Reconcile"
RESOLVER_JOB = "resolve-finding"
RESOLVE_OUTPUT = "resolved_pr_numbers"
REPOSITORY = "fafa33/Project-Hunter"
REPOSITORY_ID = 123456789
RUN_ID = 45999110514
ATTEMPT = 1
HEAD = "a" * 40
ARTIFACT_NAME = f"hunter-resolution-reconcile-{RUN_ID}-{ATTEMPT}"
EVIDENCE_FILE = "resolution-evidence.json"


def _triggers(document: dict[str, Any]) -> dict[str, Any]:
    on = document.get("on")
    if on is not None:
        return on
    # PyYAML parses an unquoted `on:` key as boolean True.
    return next(value for key, value in document.items() if isinstance(key, bool) and key is True)


def _event(
    *,
    conclusion: str = "success",
    run_id: int = RUN_ID,
    attempt: int = ATTEMPT,
    name: str = "workflow_run",
    head_sha: str = HEAD,
) -> dict[str, Any]:
    return {
        "name": name,
        "repository": {"id": REPOSITORY_ID, "full_name": REPOSITORY},
        "workflow_run": {
            "id": run_id,
            "run_attempt": attempt,
            "name": WORKFLOW_NAME,
            "conclusion": conclusion,
            "head_sha": head_sha,
            "status": "completed",
        },
    }


def _jobs(
    *,
    conclusion: str = "success",
    run_id: int = RUN_ID,
    attempt: int = ATTEMPT,
    workflow_name: str | None = WORKFLOW_NAME,
    job_name: str = RESOLVER_JOB,
) -> dict[str, Any]:
    """The documented ``list jobs for a workflow run attempt`` response."""

    step = {
        "name": "Prove the exact head, answer and resolve the exact thread",
        "status": "completed",
        "conclusion": "success",
        "number": 6,
        "started_at": "2026-11-27T15:00:00Z",
        "completed_at": "2026-11-27T15:01:00Z",
    }
    assert "outputs" not in step  # GitHub does not expose step outputs here.
    return {
        "total_count": 1,
        "jobs": [
            {
                "id": 99,
                "run_id": run_id,
                "run_url": f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}",
                "run_attempt": attempt,
                "node_id": "J_abc",
                "head_sha": HEAD,
                "url": f"https://api.github.com/repos/{REPOSITORY}/actions/jobs/99",
                "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/job/99",
                "status": "completed",
                "conclusion": conclusion,
                "created_at": "2026-11-27T15:00:00Z",
                "started_at": "2026-11-27T15:00:10Z",
                "completed_at": "2026-11-27T15:01:00Z",
                "name": job_name,
                "steps": [step],
                "check_run_url": f"https://api.github.com/repos/{REPOSITORY}/check-runs/99",
                "labels": ["ubuntu-latest"],
                "runner_id": 1,
                "runner_name": "runner",
                "runner_group_id": 1,
                "runner_group_name": "Default",
                "workflow_name": workflow_name,
                "head_branch": "main",
            }
        ],
    }


def _evidence(*, pr_numbers: Sequence[object], run_id: int = RUN_ID, attempt: int = ATTEMPT) -> dict[str, Any]:
    return {
        "schema": "hunter-resolution-reconcile-evidence-v1",
        "repository": REPOSITORY,
        "workflow": WORKFLOW_NAME,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": HEAD,
        "pr_numbers": list(pr_numbers),
    }


def _artifacts(
    *,
    name: str = ARTIFACT_NAME,
    expired: bool = False,
    run_id: int = RUN_ID,
    repository_id: int = REPOSITORY_ID,
    head_sha: str = HEAD,
    digest: str,
    count: int = 1,
) -> dict[str, Any]:
    item = {
        "id": 424242,
        "node_id": "A_abc",
        "name": name,
        "size_in_bytes": 128,
        "url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/424242",
        "archive_download_url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/424242/zip",
        "expired": expired,
        "created_at": "2026-11-27T15:01:00Z",
        "expires_at": "2026-12-27T15:01:00Z",
        "updated_at": "2026-11-27T15:01:00Z",
        "digest": digest,
        "workflow_run": {
            "id": run_id,
            "repository_id": repository_id,
            "head_repository_id": repository_id,
            "head_branch": "main",
            "head_sha": head_sha,
        },
    }
    return {"total_count": count, "artifacts": [dict(item, id=424242 + index) for index in range(count)]}


def _archive(evidence: dict[str, Any], *, member: str = EVIDENCE_FILE) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(member, json.dumps(evidence, sort_keys=True))
    payload = buffer.getvalue()
    return payload, "sha256:" + hashlib.sha256(payload).hexdigest()


def _write(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _select(
    event: dict[str, Any],
    jobs: dict[str, Any],
    artifacts: dict[str, Any],
    evidence: dict[str, Any],
    *,
    archive: bytes | None = None,
    name: str | None = None,
) -> tuple[int, ...]:
    from hunter_resolution_reconcile_ingress import select_resolved_pull_requests

    payload, digest = _archive(evidence)
    return select_resolved_pull_requests(
        event,
        jobs,
        artifacts=artifacts if "digest" in artifacts.get("artifacts", [{}])[0] else _artifacts(digest=digest),
        evidence=evidence,
        archive=archive if archive is not None else payload,
        event_name=name,
        repository=REPOSITORY,
    )


def test_governance_reconcile_uses_the_actual_issue_agent_completion_event() -> None:
    document = yaml.safe_load((ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text())
    on = _triggers(document)
    assert "pull_request" not in on
    workflow_run = on["workflow_run"]
    assert workflow_run["types"] == ["completed"]
    assert WORKFLOW_NAME in workflow_run["workflows"]
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in document["jobs"]["reconcile"]["if"]
    )
    assert document["jobs"]["reconcile"]["steps"][0]["with"]["ref"] == "main"
    assert document["permissions"]["actions"] == "write"
    assert document["permissions"]["statuses"] == "write"


def test_resolver_publishes_an_authenticated_artifact_without_gaining_workflow_authority() -> None:
    workflow = (ROOT / ".github/workflows/hunter-issue-agent-reconcile.yml").read_text()
    job = yaml.safe_load(workflow)["jobs"]["resolve-finding"]
    assert "actions" not in job["permissions"]
    resolve_step = next(step for step in job["steps"] if "lifecycle.py resolve-finding" in str(step.get("run", "")))
    assert resolve_step["id"] == "resolve"
    upload_steps = [step for step in job["steps"] if "upload-artifact" in str(step.get("uses", ""))]
    assert len(upload_steps) == 1
    assert (
        upload_steps[0]["with"]["name"] == "hunter-resolution-reconcile-${{ github.run_id }}-${{ github.run_attempt }}"
    )
    assert upload_steps[0]["with"]["if-no-files-found"] == "error"
    source = (ROOT / "scripts/hunter_issue_agent_lifecycle.py").read_text()
    body = source.split("def cmd_resolve(", 1)[1].split("\ndef cmd_resume_bind(", 1)[0]
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    assert "_output(resolved_pr_numbers=" in code
    assert "deferred to trusted scheduled sweep" not in code
    assert "dispatch" not in code and "workflow run" not in code


def test_successful_trusted_artifact_selects_only_the_affected_pr() -> None:
    evidence = _evidence(pr_numbers=[789])
    _, digest = _archive(evidence)
    assert _select(_event(), _jobs(), _artifacts(digest=digest), evidence) == (789,)
    evidence = _evidence(pr_numbers=[788, 789])
    _, digest = _archive(evidence)
    assert _select(_event(), _jobs(), _artifacts(digest=digest), evidence) == (788, 789)


def test_jobs_api_shape_without_step_outputs_is_the_only_producer_identity() -> None:
    # A fake `steps[].outputs` field cannot create a wake-up; the trusted artifact is required.
    jobs = _jobs()
    jobs["jobs"][0]["steps"][0]["outputs"] = {RESOLVE_OUTPUT: "789"}
    evidence = _evidence(pr_numbers=[])
    payload, digest = _archive(evidence)
    assert (
        _select(
            _event(),
            jobs,
            _artifacts(digest=digest),
            evidence,
            archive=payload,
        )
        == ()
    )


def test_failed_or_incomplete_resolution_never_creates_false_readiness() -> None:
    evidence = _evidence(pr_numbers=[789])
    payload, digest = _archive(evidence)
    artifacts = _artifacts(digest=digest)
    assert _select(_event(conclusion="failure"), _jobs(), artifacts, evidence, archive=payload) == ()
    assert _select(_event(conclusion="cancelled"), _jobs(), artifacts, evidence, archive=payload) == ()
    assert _select(_event(), _jobs(conclusion="failure"), artifacts, evidence, archive=payload) == ()
    jobs = _jobs()
    jobs["jobs"] = []
    jobs["total_count"] = 0
    assert _select(_event(), jobs, artifacts, evidence, archive=payload) == ()
    assert _select(_event(name="push"), _jobs(), artifacts, evidence, archive=payload) == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a["artifacts"][0].update(name="hunter-resolution-reconcile-other"),
        lambda a: a["artifacts"][0].update(expired=True),
        lambda a: a["artifacts"][0]["workflow_run"].update(id=RUN_ID + 1),
        lambda a: a["artifacts"][0]["workflow_run"].update(repository_id=REPOSITORY_ID + 1),
        lambda a: a["artifacts"][0]["workflow_run"].update(head_sha="b" * 40),
        lambda a: a.update(total_count=2, artifacts=a["artifacts"] + a["artifacts"]),
        lambda a: a["artifacts"][0].update(digest="sha256:" + "0" * 64),
    ],
)
def test_untrusted_ambiguous_or_stale_artifacts_fail_closed(mutate: Any) -> None:
    evidence = _evidence(pr_numbers=[789])
    payload, digest = _archive(evidence)
    artifacts = _artifacts(digest=digest)
    mutate(artifacts)
    assert _select(_event(), _jobs(), artifacts, evidence, archive=payload) == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(schema="hunter-resolution-reconcile-evidence-v2"),
        lambda e: e.update(repository="other/repo"),
        lambda e: e.update(workflow="Hunter Governance Review"),
        lambda e: e.update(run_id=RUN_ID + 1),
        lambda e: e.update(run_attempt=ATTEMPT + 1),
        lambda e: e.update(head_sha="b" * 40),
        lambda e: e.update(pr_numbers=[0]),
        lambda e: e.update(pr_numbers=[789, "not-a-pr"]),
        lambda e: e.update(pr_numbers=["789,790"]),
        lambda e: e.pop("pr_numbers"),
    ],
)
def test_mismatched_or_malformed_evidence_fails_closed(mutate: Any) -> None:
    evidence = _evidence(pr_numbers=[789])
    mutate(evidence)
    payload, digest = _archive(evidence)
    assert _select(_event(), _jobs(), _artifacts(digest=digest), evidence, archive=payload) == ()


def test_resolve_output_contract_runs_through_the_real_shell_loop(tmp_path: pathlib.Path) -> None:
    script = ROOT / "scripts" / "hunter_resolution_reconcile_ingress.py"
    cases = [
        ([], ""),
        ([789], "789\n"),
        ([788, 789, 789], "788\n789\n"),
    ]
    for numbers, expected in cases:
        evidence = _evidence(pr_numbers=numbers)
        payload, digest = _archive(evidence)
        event_path = tmp_path / "event.json"
        jobs_path = tmp_path / "jobs.json"
        artifacts_path = tmp_path / "artifacts.json"
        archive_path = tmp_path / "artifact.zip"
        _write(event_path, _event())
        _write(jobs_path, _jobs())
        _write(artifacts_path, _artifacts(digest=digest))
        archive_path.write_bytes(payload)
        shell = r"""
set -euo pipefail
pr_numbers="$(
  python3 "$SCRIPT" \
    --event "$EVENT" --jobs "$JOBS" --artifacts "$ARTIFACTS" \
    --evidence-zip "$ARCHIVE" --repository "$REPOSITORY" --for-shell
)"
seen=""
while read -r pr_number; do
  [ -z "$pr_number" ] && continue
  seen+="${pr_number}"$'\n'
done <<< "$pr_numbers"
printf '%s' "$seen"
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            capture_output=True,
            text=True,
            check=True,
            env={
                **os.environ,
                "SCRIPT": str(script),
                "EVENT": str(event_path),
                "JOBS": str(jobs_path),
                "ARTIFACTS": str(artifacts_path),
                "ARCHIVE": str(archive_path),
                "REPOSITORY": REPOSITORY,
                "PYTHONPATH": str(ROOT / "scripts"),
            },
        )
        assert result.stdout == expected

    # A malformed identity never reaches the PR loop.
    evidence = _evidence(pr_numbers=[789, "not-a-pr"])
    payload, digest = _archive(evidence)
    _write(tmp_path / "artifacts.json", _artifacts(digest=digest))
    (tmp_path / "artifact.zip").write_bytes(payload)
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--event",
            str(tmp_path / "event.json"),
            "--jobs",
            str(tmp_path / "jobs.json"),
            "--artifacts",
            str(tmp_path / "artifacts.json"),
            "--evidence-zip",
            str(tmp_path / "artifact.zip"),
            "--repository",
            REPOSITORY,
            "--for-shell",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
    )
    assert result.stdout == ""


def test_reconcile_consumes_the_artifact_output_contract_and_rebinds_exact_head() -> None:
    text = (ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text()
    assert "actions/runs/${EVENT_WORKFLOW_RUN_ID}/attempts/${EVENT_WORKFLOW_RUN_ATTEMPT}/jobs" in text
    assert "actions/runs/${EVENT_WORKFLOW_RUN_ID}/artifacts" in text
    assert "artifacts/$(cat" in text or "artifact_id" in text
    assert "--evidence-zip" in text
    assert "--for-shell" in text
    assert "resolved_pr_numbers=" not in text
    assert 'pr_numbers="${resolution_output#pr_numbers=}"' not in text
    assert "hunter-resolution-reconcile/${EVENT_WORKFLOW_RUN_ID}/${EVENT_WORKFLOW_RUN_ATTEMPT}" in text
    assert "select(.context ==" in text and "${resolution_marker}" in text
    assert "recheck_head" in text
    assert '[[ "${recheck_head}" != "${invocation_head}" ]]' in text
    assert '--expected-head "${invocation_head}"' in text


def test_manual_thread_resolution_without_a_trusted_artifact_is_recovery_only() -> None:
    evidence = _evidence(pr_numbers=[])
    payload, digest = _archive(evidence)
    assert _select(_event(), _jobs(), _artifacts(digest=digest), evidence, archive=payload) == ()


def test_scheduled_sweep_remains_the_recovery_path_only() -> None:
    document = yaml.safe_load((ROOT / ".github/workflows/hunter-governance-reconcile.yml").read_text())
    on = _triggers(document)
    assert on["schedule"] == [{"cron": "*/5 * * * *"}]
    assert document["concurrency"]["cancel-in-progress"] is False
