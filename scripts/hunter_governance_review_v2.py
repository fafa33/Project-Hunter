"""Low-friction governance sanity review for Project Hunter.

The active merge authority is intentionally limited to current merge risk. This
check does not judge PR prose, branch naming, Issue identity, reactions, or
process history. It does enforce one durable admission invariant: a PR head must
already have passed the repository's branch-level pre-PR preflight on that exact
SHA before governance can become green.
"""

from __future__ import annotations

import argparse
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
REQUIRED_RULESET_CHECKS = {
    "Quality Gates",
    "dependency-review",
    "CodeQL",
    "Hunter Governance Review",
    "Hunter Merge Readiness",
}


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


def candidate_admission(repository: str, token: str, head_sha: str) -> tuple[str, str]:
    encoded_sha = quote(head_sha, safe="")
    payload = request_json(
        repository,
        token,
        "GET",
        f"actions/runs?head_sha={encoded_sha}&event=push&per_page=100",
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Pre-PR workflow-run payload is unavailable")

    matching = [
        run
        for run in payload.get("workflow_runs") or []
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
    return "success", "Exact-head branch preflight passed before PR governance progression."


def ruleset_conformance(repository: str, token: str) -> tuple[str, str]:
    summaries = request_json(repository, token, "GET", "rulesets?per_page=100")
    if not isinstance(summaries, list):
        raise RuntimeError("Repository ruleset listing is unavailable")

    active_main_rulesets: list[dict[str, Any]] = []
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get("enforcement") != "active":
            continue
        ruleset_id = summary.get("id")
        if not ruleset_id:
            continue
        detail = request_json(repository, token, "GET", f"rulesets/{ruleset_id}")
        if not isinstance(detail, dict):
            continue
        ref_condition = (detail.get("conditions") or {}).get("ref_name") or {}
        includes = set(ref_condition.get("include") or [])
        if "refs/heads/main" in includes:
            active_main_rulesets.append(detail)

    if not active_main_rulesets:
        return "failure", "Repository protection drift: no active ruleset protects refs/heads/main."

    required_contexts: set[str] = set()
    for ruleset in active_main_rulesets:
        for rule in ruleset.get("rules") or []:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters") or {}
            for check in parameters.get("required_status_checks") or []:
                if isinstance(check, dict):
                    context = str(check.get("context") or "").strip()
                    if context:
                        required_contexts.add(context)

    missing = sorted(REQUIRED_RULESET_CHECKS - required_contexts)
    if missing:
        return "failure", "Repository protection drift: required status checks missing: " + ", ".join(missing)
    return "success", "Main ruleset requires every canonical Hunter merge status."


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

    admission_state, admission_description = candidate_admission(repository, token, head_sha)
    if admission_state != "success":
        publish(repository, token, head_sha, admission_state, admission_description)
        return 0

    ruleset_state, ruleset_description = ruleset_conformance(repository, token)
    if ruleset_state != "success":
        publish(repository, token, head_sha, ruleset_state, ruleset_description)
        return 0

    publish(
        repository,
        token,
        head_sha,
        "success",
        "Candidate admitted on exact-head preflight and canonical main protection is enforced.",
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
