"""Issue #575: the trusted workflow must create ingress inputs inside RUNNER_TEMP.

This executes the actual ``Refresh lightweight governance status`` shell step from
``hunter-governance-reconcile.yml``. The only substitutions are GitHub expression
values; the real resolver ingress, the real shell consumer loop and real
``mktemp`` semantics (with ``TMPDIR`` unset) remain in the executed path.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import test_issue_agent_resolution_reconcile as rr
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "hunter-governance-reconcile.yml"
STEP_NAME = "Refresh lightweight governance status"
INGRESS = ROOT / "scripts" / "hunter_resolution_reconcile_ingress.py"


def _workflow_step() -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(item for item in document["jobs"]["reconcile"]["steps"] if item.get("name") == STEP_NAME)
    source = str(step["run"]).replace("${{ github.event_name }}", "workflow_run")
    source = source.replace("${{ github.event.pull_request.number }}", "789")
    assert "${{" not in source
    return source


def _write_shims(
    directory: pathlib.Path,
    *,
    jobs: pathlib.Path,
    artifacts: pathlib.Path,
    archive: pathlib.Path,
    gh_log: pathlib.Path,
    mktemp_log: pathlib.Path,
    head: str,
) -> None:
    gh = directory / "gh"
    gh.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_LOG"
api_path=""
for arg in "$@"; do
  case "$arg" in
    repos/*) api_path="$arg" ;;
  esac
done
case "$api_path" in
  */actions/runs/*/jobs?*) cat "$JOBS_SOURCE" ;;
  */actions/runs/*/artifacts?*) cat "$ARTIFACTS_SOURCE" ;;
  */actions/artifacts/*/zip) cat "$ARCHIVE_SOURCE" ;;
  */pulls/*) printf '%s\\n' "$EXPECTED_HEAD" ;;
  */commits/*/statuses) printf '0\\n' ;;
  */statuses/*) printf '{}\\n' ;;
  *) printf '{}\\n' ;;
esac
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    python = directory / "python"
    python.write_text(
        """#!/bin/sh
case "${1:-}" in
  */hunter_resolution_reconcile_ingress.py) exec "$REAL_PYTHON" "$@" ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    python.chmod(0o755)

    mktemp = directory / "mktemp"
    mktemp.write_text(
        """#!/bin/sh
if [ "$#" -eq 0 ]; then
  created="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/tmp.XXXXXXXXXX")" || exit 1
else
  created="$(/usr/bin/mktemp "$@")" || exit 1
fi
printf '%s\\n' "$created" >> "$MKTEMP_LOG"
printf '%s\\n' "$created"
""",
        encoding="utf-8",
    )
    mktemp.chmod(0o755)


def _run_step(
    tmp_path: pathlib.Path,
    *,
    digest: str,
    archive: bytes,
    runner_temp_enabled: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    source_root = tmp_path / "sources"
    for directory in (runner_temp, workspace, source_root):
        directory.mkdir()

    artifacts = rr._artifacts(digest=digest)
    event_path = workspace / "event.json"
    jobs_path = workspace / "jobs.json"
    artifacts_path = workspace / "artifacts.json"
    archive_path = workspace / "artifact.zip"
    rr._write(event_path, rr._event())
    rr._write(jobs_path, rr._jobs())
    rr._write(artifacts_path, artifacts)
    archive_path.write_bytes(archive)

    gh_log = tmp_path / "gh.log"
    mktemp_log = tmp_path / "mktemp.log"
    gh_log.touch()
    mktemp_log.touch()
    _write_shims(
        source_root,
        jobs=jobs_path,
        artifacts=artifacts_path,
        archive=archive_path,
        gh_log=gh_log,
        mktemp_log=mktemp_log,
        head=rr.HEAD,
    )
    script = tmp_path / "workflow-step.sh"
    script.write_text("#!/bin/bash\nset -euo pipefail\n" + _workflow_step() + "\n", encoding="utf-8")
    script.chmod(0o755)

    env = {key: value for key, value in os.environ.items() if key not in {"TMPDIR", "PYTHONPATH"}}
    env.update(
        {
            "PATH": str(source_root) + os.pathsep + env["PATH"],
            "PYTHONPATH": str(ROOT / "scripts"),
            "REAL_PYTHON": sys.executable,
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_REPOSITORY": rr.REPOSITORY,
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "46000000000",
            "GITHUB_RUN_ATTEMPT": "1",
            "EVENT_WORKFLOW_RUN_NAME": rr.WORKFLOW_NAME,
            "EVENT_WORKFLOW_RUN_TITLE": "",
            "EVENT_WORKFLOW_RUN_HEAD_SHA": rr.HEAD,
            "EVENT_WORKFLOW_RUN_PR_NUMBER": "789",
            "EVENT_WORKFLOW_RUN_ID": str(rr.RUN_ID),
            "EVENT_WORKFLOW_RUN_ATTEMPT": str(rr.ATTEMPT),
            "EVENT_WORKFLOW_RUN_CONCLUSION": "success",
            "GH_LOG": str(gh_log),
            "MKTEMP_LOG": str(mktemp_log),
            "JOBS_SOURCE": str(jobs_path),
            "ARTIFACTS_SOURCE": str(artifacts_path),
            "ARCHIVE_SOURCE": str(archive_path),
            "EXPECTED_HEAD": rr.HEAD,
        }
    )
    if runner_temp_enabled:
        env["RUNNER_TEMP"] = str(runner_temp)
    else:
        env.pop("RUNNER_TEMP", None)
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        completed,
        gh_log.read_text(encoding="utf-8").splitlines(),
        mktemp_log.read_text(encoding="utf-8").splitlines(),
    )


def _assert_trusted_temp_files(runner_temp: pathlib.Path, created: list[str]) -> None:
    assert len(created) == 3, created
    trusted = runner_temp.resolve()
    for raw in created:
        path = pathlib.Path(raw)
        assert path.resolve().is_relative_to(trusted), (raw, str(trusted))
        assert path.resolve().parent == trusted, raw
    assert all(not pathlib.Path(raw).exists() for raw in created), "temporary inputs were not cleaned up"


def test_workflow_creates_ingress_inputs_inside_trusted_runner_temp(tmp_path: pathlib.Path) -> None:
    evidence = rr._evidence(pr_numbers=[789])
    payload, digest = rr._archive(evidence)
    completed, gh_calls, created = _run_step(tmp_path, digest=digest, archive=payload)

    _assert_trusted_temp_files(tmp_path / "runner-temp", created)
    assert completed.returncode == 0, completed.stderr
    assert "Cannot read resolver jobs" not in completed.stderr
    assert any("/statuses/" in line for line in gh_calls), gh_calls


def test_empty_and_invalid_evidence_never_wake_reconciliation(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    evidence = rr._evidence(pr_numbers=[])
    payload, digest = rr._archive(evidence)
    completed, gh_calls, created = _run_step(empty, digest=digest, archive=payload)
    _assert_trusted_temp_files(empty / "runner-temp", created)
    assert completed.returncode == 0, completed.stderr
    assert not any("/statuses/" in line for line in gh_calls), gh_calls

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    valid_evidence = rr._evidence(pr_numbers=[789])
    payload, _digest = rr._archive(valid_evidence)
    completed, gh_calls, created = _run_step(
        invalid,
        digest="sha256:" + "0" * 64,
        archive=payload,
    )
    _assert_trusted_temp_files(invalid / "runner-temp", created)
    assert completed.returncode == 0, completed.stderr
    assert not any("/statuses/" in line for line in gh_calls), gh_calls


def test_missing_runner_temp_fails_closed_without_creating_inputs(tmp_path: pathlib.Path) -> None:
    evidence = rr._evidence(pr_numbers=[789])
    payload, digest = rr._archive(evidence)
    completed, gh_calls, created = _run_step(
        tmp_path,
        digest=digest,
        archive=payload,
        runner_temp_enabled=False,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "RUNNER_TEMP must be a trusted directory" in completed.stdout + completed.stderr
    assert created == [], created
    assert not any("/statuses/" in line for line in gh_calls), gh_calls


def test_traversal_and_symlink_inputs_still_fail_closed(tmp_path: pathlib.Path) -> None:
    runner_temp = tmp_path / "runner-temp"
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    for directory in (runner_temp, workspace, outside):
        directory.mkdir()
    monkeypatch_env = {
        **os.environ,
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_WORKSPACE": str(workspace),
        "PYTHONPATH": str(ROOT / "scripts"),
    }
    evidence = rr._evidence(pr_numbers=[789])
    payload, digest = rr._archive(evidence)
    rr._write(workspace / "event.json", rr._event())
    rr._write(workspace / "jobs.json", rr._jobs())
    rr._write(workspace / "artifacts.json", rr._artifacts(digest=digest))
    (workspace / "artifact.zip").write_bytes(payload)
    rr._write(outside / "event.json", rr._event())
    (runner_temp / "linked-event.json").symlink_to(outside / "event.json")

    argv = [
        sys.executable,
        str(INGRESS),
        "--event",
        str(runner_temp / "linked-event.json"),
        "--jobs",
        str(workspace / "jobs.json"),
        "--artifacts",
        str(workspace / "artifacts.json"),
        "--evidence-zip",
        str(workspace / "artifact.zip"),
        "--repository",
        rr.REPOSITORY,
        "--for-shell",
    ]
    linked = subprocess.run(argv, capture_output=True, text=True, check=False, env=monkeypatch_env)
    assert linked.returncode == 2
    assert "trusted workflow input boundary" in linked.stderr

    argv[argv.index(str(runner_temp / "linked-event.json"))] = str(workspace / ".." / outside.name / "event.json")
    traversal = subprocess.run(argv, capture_output=True, text=True, check=False, env=monkeypatch_env)
    assert traversal.returncode == 2
    assert "trusted workflow input boundary" in traversal.stderr
