#!/usr/bin/env python3
"""Collect exact-head GitHub review observations without assigning defect authority."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import hunter_governance_review_v2 as governance


def collect(repository: str, token: str, pr: int, head: str, base: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"pulls/{pr}/comments?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("review comments payload must be a list")
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("review comment must be an object")
            commit_id = item.get("commit_id") or item.get("original_commit_id")
            if commit_id != head:
                continue
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            observations.append(
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
        if len(payload) < 100:
            break
        page += 1
    page = 1
    while True:
        payload = governance.request_json(repository, token, "GET", f"pulls/{pr}/reviews?per_page=100&page={page}")
        if not isinstance(payload, list):
            raise ValueError("reviews payload must be a list")
        for item in payload:
            if not isinstance(item, dict) or item.get("commit_id") != head:
                continue
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            observations.append(
                {
                    "source": "github-review",
                    "provider": "github-review",
                    "event_id": f"review-{item.get('id')}",
                    "source_pr": pr,
                    "reviewed_head_sha": head,
                    "reviewed_base_sha": base,
                    "reviewer": str(user.get("login") or "github-review"),
                    "path": None,
                    "line": None,
                    "message": body,
                    "availability": "available",
                    "classification": None,
                    "invariant": None,
                    "affected_paths": [],
                    "fix_reference": None,
                    "regression_evidence": [],
                    "claimed_family_id": None,
                }
            )
        if len(payload) < 100:
            break
        page += 1
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    print(json.dumps(collect(args.repository, token, args.pr, args.head, args.base), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
