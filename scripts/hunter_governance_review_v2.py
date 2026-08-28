"""Trusted governance sanity review plus exact-head candidate admission helpers.

Hunter Governance Review is a required merge prerequisite. A successful status
therefore requires both a clean merge state and successful exact-head candidate
admission; the separate Draft controller remains defense in depth only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from typing import Any
from urllib.parse import quote

import hunter_github_transport as transport

CONTEXT = "Hunter Governance Review"
PRE_PR_WORKFLOW_NAME = "Hunter / Pre-PR Preflight"
PRE_PR_WORKFLOW_PATH = ".github/workflows/hunter-pre-pr-preflight.yml"
PREFLIGHT_UPGRADE_STATUS_PREFIX = "Hunter Trusted Preflight Upgrade / PR #"
PREFLIGHT_OWNED_PATHS = frozenset(
    {
        ".github/workflows/hunter-pre-pr-preflight.yml",
        "scripts/hunter_pr_preflight.py",
        "scripts/hunter_architecture_index_preflight.py",
        "scripts/hunter_artifact_preflight.py",
        "scripts/hunter_defect_prevention_preflight.py",
        "scripts/hunter_pre_push.py",
    }
)


def request_json(repository: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    return transport.request_rest_json(
        url=f"https://api.github.com/repos/{repository}/{path}",
        method=method,
        headers={},
        data=data,
        token=token,
        what=f"{method} {path}",
    )


def publish(repository: str, token: str, sha: str, state: str, description: str) -> None:
    run_id = os.environ.get("GITHUB_RUN_ID") or ""
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    target_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""
    request_json(
        repository,
        token,
        "POST",
        f"statuses/{sha}",
        {
            "state": state,
            "context": CONTEXT,
            "description": description[:140],
            "target_url": target_url,
        },
    )
    print(f"{sha[:10]} {CONTEXT}: {state} — {description[:140]}")


def read_mergeability(repository: str, token: str, pr_number: int) -> dict[str, Any]:
    pr: dict[str, Any] = {}
    for attempt in range(3):
        payload = request_json(repository, token, "GET", f"pulls/{pr_number}")
        if not isinstance(payload, dict):
            raise RuntimeError("Pull request payload is unavailable")
        pr = payload
        if pr.get("mergeable") is not None:
            break
        if attempt < 2:
            time.sleep(2)
    return pr


def read_pr_changed_paths(repository: str, token: str, pr_number: int) -> tuple[bool, tuple[str, ...], str | None]:
    collected: list[str] = []
    try:
        for page in range(1, 31):
            payload = request_json(
                repository,
                token,
                "GET",
                f"pulls/{pr_number}/files?per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                return False, (), "pull request file listing payload is not a list"
            collected.extend(
                str(item.get("filename") or "").strip()
                for item in payload
                if isinstance(item, dict) and item.get("filename")
            )
            if len(payload) < 100:
                return True, tuple(collected), None
        return False, (), "pull request file listing exceeds the supported 3000-file proof boundary"
    except transport.GitHubRequestError as exc:
        return False, (), f"GitHub request error: {exc}"
    except Exception as exc:
        return False, (), f"unexpected error: {type(exc).__name__}: {exc}"


def read_head_preflight_mode(repository: str, token: str, head_sha: str) -> tuple[str, str | None]:
    encoded_sha = quote(head_sha, safe="")
    try:
        payload = request_json(
            repository,
            token,
            "GET",
            f"contents/.hunter-preflight-mode?ref={encoded_sha}",
        )
    except transport.GitHubRequestError as exc:
        if exc.status_code == 404:
            return "normal", None
        return "unavailable", f"GitHub request error ({exc.status_code}): {exc}"
    except Exception as exc:
        return "unavailable", f"unexpected error: {type(exc).__name__}: {exc}"

    if isinstance(payload, dict):
        if payload.get("message") == "Not Found":
            return "normal", None
        content = payload.get("content")
        if isinstance(content, str) and content:
            try:
                raw = base64.b64decode(content).decode("utf-8").strip()
            except Exception as exc:
                return "invalid", f"failed to decode base64 mode content: {exc}"
            if raw == "tests-first-red":
                return "tests-first-red", None
            return "invalid", f"unsupported preflight mode content: {raw!r}"
        return "normal", None
    return "unavailable", "non-dict payload for .hunter-preflight-mode"


def _upgrade_status_context(pr_number: int) -> str:
    return f"{PREFLIGHT_UPGRADE_STATUS_PREFIX}{pr_number}"


def read_trusted_upgrade_status(
    repository: str,
    token: str,
    head_sha: str,
    pr_number: int,
) -> tuple[str, str]:
    encoded_sha = quote(head_sha, safe="")
    payload = request_json(repository, token, "GET", f"commits/{encoded_sha}/statuses?per_page=100")
    if not isinstance(payload, list):
        return "failure", "Candidate admission blocked: trusted upgrade status evidence is malformed."

    context = _upgrade_status_context(pr_number)
    matching = [
        status for status in payload if isinstance(status, dict) and str(status.get("context") or "") == context
    ]
    if not matching:
        return "missing", "Candidate admission blocked: exact-head trusted preflight upgrade status is missing."

    latest = max(matching, key=lambda status: int(status.get("id") or 0))
    state = str(latest.get("state") or "").strip()
    if state == "success":
        return "success", "Exact-head trusted candidate preflight validation passed."
    if state == "pending":
        return "pending", "Waiting for exact-head trusted candidate preflight validation."
    return "failure", f"Candidate admission blocked: trusted candidate preflight validation={state or 'unknown'}."


def candidate_admission(repository: str, token: str, head_sha: str, pr_number: int | None = None) -> tuple[str, str]:
    touches_protected_preflight = False
    if pr_number is not None:
        ok, changed_paths, error = read_pr_changed_paths(repository, token, pr_number)
        if not ok:
            return "failure", f"Candidate admission blocked: changed-file evidence is unavailable ({error})."
        touches_protected_preflight = any(path in PREFLIGHT_OWNED_PATHS for path in changed_paths)

    head_mode, mode_error = read_head_preflight_mode(repository, token, head_sha)
    if head_mode == "unavailable":
        return "failure", f"Candidate admission blocked: preflight mode evidence is unavailable ({mode_error})."
    if head_mode == "invalid":
        return "failure", f"Candidate admission blocked: invalid .hunter-preflight-mode content ({mode_error})."
    if head_mode == "tests-first-red":
        return "failure", "Candidate admission blocked: tests-first-red work must remain Draft-only."

    if touches_protected_preflight:
        if pr_number is None:
            return "failure", "Candidate admission blocked: protected preflight changes require PR-bound proof."
        proof_state, proof_description = read_trusted_upgrade_status(repository, token, head_sha, pr_number)
        if proof_state == "missing":
            return "failure", proof_description
        return proof_state, proof_description

    encoded_sha = quote(head_sha, safe="")
    payload = request_json(
        repository,
        token,
        "GET",
        f"actions/runs?head_sha={encoded_sha}&event=push&per_page=100",
    )
    if not isinstance(payload, dict):
        return "failure", "Candidate admission blocked: branch preflight run evidence is malformed."

    workflow_runs = payload.get("workflow_runs")
    if not isinstance(workflow_runs, list):
        return "failure", "Candidate admission blocked: workflow_runs payload is malformed."

    matching = [
        run
        for run in workflow_runs
        if isinstance(run, dict)
        and str(run.get("head_sha") or "") == head_sha
        and str(run.get("name") or "") == PRE_PR_WORKFLOW_NAME
        and str(run.get("path") or "") == PRE_PR_WORKFLOW_PATH
        and str(run.get("event") or "") == "push"
    ]
    if not matching:
        return "failure", "Candidate admission blocked: exact-head branch preflight is missing."

    latest = max(matching, key=lambda run: int(run.get("id") or 0))
    status = str(latest.get("status") or "")
    conclusion = str(latest.get("conclusion") or "")
    if status != "completed":
        return "pending", "Waiting for exact-head branch preflight to complete."
    if conclusion != "success":
        return "failure", f"Candidate admission blocked: exact-head branch preflight={conclusion or 'unknown'}."
    return "success", "Exact-head branch preflight passed before review progression."


def review(repository: str, token: str, pr_number: int) -> int:
    pr = read_mergeability(repository, token, pr_number)
    if pr.get("state") != "open":
        print(f"PR #{pr_number} is not open; no governance status published.")
        return 0

    base_ref = str((pr.get("base") or {}).get("ref") or "").strip()
    if base_ref != "main":
        print(f"PR #{pr_number} targets {base_ref or 'an unavailable base'}; no governance status published.")
        return 0

    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
    if not head_sha:
        raise RuntimeError(f"PR #{pr_number} head SHA is unavailable")

    if pr.get("mergeable") is False:
        publish(
            repository, token, head_sha, "failure", "Blocking governance finding: pull request has merge conflicts."
        )
        return 0
    if pr.get("mergeable") is None:
        publish(repository, token, head_sha, "pending", "Waiting for GitHub to resolve current mergeability.")
        return 0

    admission_state, admission_description = candidate_admission(repository, token, head_sha, pr_number)
    if admission_state != "success":
        status_state = "pending" if admission_state == "pending" else "failure"
        publish(repository, token, head_sha, status_state, admission_description)
        return 0

    publish(
        repository,
        token,
        head_sha,
        "success",
        "Exact-head candidate admission and current merge-state governance checks passed.",
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Hunter lightweight governance sanity review")
    result.add_argument("--pr", type=int, required=True)
    result.add_argument("--repository", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        return review(args.repository, token, args.pr)
    except transport.GitHubUnavailable as exc:
        print(f"Governance review infrastructure unavailable: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Governance review failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
