#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Any

import hunter_governance_review_v2 as governance


def _comments(repository: str, token: str, pr: int) -> list[dict[str, Any]]:
    result = []
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"pulls/{pr}/comments?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("review comments payload must be a list")
        result.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            return result
        page += 1


def collect(repository: str, token: str, pr: int, head: str, base: str) -> list[dict[str, Any]]:
    result = []
    for item in _comments(repository, token, pr):
        if (item.get("commit_id") or item.get("original_commit_id")) != head:
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        result.append(
            {
                "source": "github-review",
                "provider": "github-review",
                "event_id": f"review-comment-{item.get('id')}",
                "source_pr": pr,
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": str(user.get("login") or "github-review"),
                "path": item.get("path"),
                "line": item.get("line") or item.get("original_line"),
                "message": str(item.get("body") or "review finding"),
                "availability": "available",
                "classification": None,
                "invariant": None,
                "affected_paths": [],
                "fix_reference": None,
                "regression_evidence": [],
                "claimed_family_id": None,
            }
        )
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repository", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    a = p.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    print(json.dumps(collect(a.repository, token, a.pr, a.head, a.base), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
