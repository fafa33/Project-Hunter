#!/usr/bin/env python3
"""Collect bounded exact-head SonarQube Cloud observations without defect authority."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

SONAR_API = "https://sonarcloud.io/api"
MAX_ISSUES = 100
JsonGetter = Callable[[str], Any]


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Project-Hunter/sonar-learning"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- fixed SonarCloud origin only
        return json.load(response)


def _url(endpoint: str, **params: object) -> str:
    return f"{SONAR_API}/{endpoint}?{urllib.parse.urlencode(params)}"


def _unavailable(pr: int, head: str, base: str, reason: str) -> list[dict[str, Any]]:
    return [
        {
            "source": "sonar",
            "provider": "sonar",
            "event_id": f"sonar-unavailable-{head}",
            "source_pr": pr,
            "reviewed_head_sha": head,
            "reviewed_base_sha": base,
            "reviewer": "sonarqube-cloud",
            "path": None,
            "line": None,
            "message": reason,
            "availability": "unavailable",
            "classification": None,
            "invariant": None,
            "affected_paths": [],
            "fix_reference": None,
            "regression_evidence": [],
            "claimed_family_id": None,
        }
    ]


def collect(project: str, pr: int, head: str, base: str, *, get_json: JsonGetter = _get_json) -> list[dict[str, Any]]:
    """Return raw Sonar observations only when Sonar proves the PR analysis is for exact HEAD."""
    try:
        payload = get_json(_url("project_pull_requests/list", project=project))
        pull_requests = payload.get("pullRequests") if isinstance(payload, dict) else None
        if not isinstance(pull_requests, list):
            return _unavailable(pr, head, base, "Sonar pull-request metadata unavailable")
        match = next(
            (item for item in pull_requests if isinstance(item, dict) and str(item.get("key")) == str(pr)), None
        )
        commit = match.get("commit") if isinstance(match, dict) and isinstance(match.get("commit"), dict) else {}
        if not match or commit.get("sha") != head:
            return _unavailable(pr, head, base, "Sonar exact-head analysis is not available yet")
        issues_payload = get_json(
            _url("issues/search", componentKeys=project, pullRequest=pr, resolved="false", ps=MAX_ISSUES)
        )
    except Exception:  # noqa: BLE001 -- optional external sensor must degrade to bounded unavailability evidence
        return _unavailable(pr, head, base, "SonarQube Cloud is unavailable")

    issues = issues_payload.get("issues") if isinstance(issues_payload, dict) else None
    total = issues_payload.get("total") if isinstance(issues_payload, dict) else None
    if not isinstance(issues, list) or type(total) is not int or total < 0:
        return _unavailable(pr, head, base, "Sonar issue payload is malformed")
    if total > MAX_ISSUES:
        return _unavailable(pr, head, base, f"Sonar issue set exceeds bounded limit {MAX_ISSUES}")

    observations: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict) or type(item.get("key")) is not str:
            return _unavailable(pr, head, base, "Sonar issue payload contains a malformed issue")
        component = str(item.get("component") or "")
        path = component.split(":", 1)[1] if ":" in component else None
        line = item.get("line") if type(item.get("line")) is int and item["line"] > 0 else None
        rule = str(item.get("rule") or "unknown-rule")
        severity = str(item.get("severity") or "unknown-severity")
        message = str(item.get("message") or "Sonar finding")
        observations.append(
            {
                "source": "sonar",
                "provider": "sonar",
                "event_id": f"sonar-issue-{item['key']}",
                "source_pr": pr,
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": "sonarqube-cloud",
                "path": path,
                "line": line,
                "message": f"[{severity}] {rule}: {message}",
                "availability": "available",
                "classification": None,
                "invariant": None,
                "affected_paths": [],
                "fix_reference": None,
                "regression_evidence": [],
                "claimed_family_id": None,
            }
        )
    if not observations:
        observations.append(
            {
                "source": "sonar",
                "provider": "sonar",
                "event_id": f"sonar-clean-{head}",
                "source_pr": pr,
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": "sonarqube-cloud",
                "path": None,
                "line": None,
                "message": "Sonar exact-head analysis reports no unresolved issues",
                "availability": "available",
                "classification": None,
                "invariant": None,
                "affected_paths": [],
                "fix_reference": None,
                "regression_evidence": [],
                "claimed_family_id": None,
            }
        )
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.project, args.pr, args.head, args.base), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
