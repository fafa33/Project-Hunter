"""Low-friction governance sanity review for Project Hunter.

The active merge authority is intentionally limited to current merge risk. This
check does not judge PR prose, branch naming, Issue identity, reactions, or
process history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import hunter_github_transport as transport

CONTEXT = "Hunter Governance Review"


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


def review(repository: str, token: str, pr_number: int) -> int:
    pr = read_mergeability(repository, token, pr_number)
    if pr.get("state") != "open":
        print(f"PR #{pr_number} is not open; no governance status published.")
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

    publish(
        repository,
        token,
        head_sha,
        "success",
        "No blocking governance defect in current merge state; code and review gates remain authoritative.",
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
