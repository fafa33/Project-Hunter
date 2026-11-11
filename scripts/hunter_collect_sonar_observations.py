#!/usr/bin/env python3
"""Collect bounded exact-head SonarQube Cloud observations without defect authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

SONAR_API = "https://sonarcloud.io/api"
MAX_ISSUES = 100
JsonGetter = Callable[[str], Any]


def sha256_path(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


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
        # Query both supported boolean states; Sonar's resolved parameter is
        # not a comma-separated enum. Preserve historical findings after fixes.
        open_payload = get_json(
            _url("issues/search", componentKeys=project, pullRequest=pr, resolved="false", ps=MAX_ISSUES)
        )
        fixed_payload = get_json(
            _url("issues/search", componentKeys=project, pullRequest=pr, resolved="true", ps=MAX_ISSUES)
        )

        def valid_page(page: Any) -> bool:
            return (
                isinstance(page, dict)
                and isinstance(page.get("issues"), list)
                and type(page.get("total")) is int
                and page["total"] >= 0
                and page["total"] == len(page["issues"])
                and page["total"] <= MAX_ISSUES
            )

        if not valid_page(open_payload) or not valid_page(fixed_payload):
            return _unavailable(
                pr, head, base, "Sonar issue count is inconsistent, exceeds bounded limit or is malformed"
            )
        raw_issues = open_payload["issues"] + fixed_payload["issues"]
        if any(not isinstance(item, dict) or not isinstance(item.get("key"), str) for item in raw_issues):
            return _unavailable(pr, head, base, "Sonar issue payload contains a malformed issue")
        combined = {item["key"]: item for item in raw_issues}
        issues_payload = {"issues": list(combined.values()), "total": len(combined)}
        # Sonar quality-gate duplication is a metric, not an issues/search issue.
        # Capture it as an observation, never as independent remediation authority.
        measures = get_json(
            _url(
                "measures/component",
                component=project,
                pullRequest=pr,
                metricKeys="new_duplicated_lines_density,new_duplicated_lines",
            )
        )
        raw_measures = measures.get("component", {}).get("measures") if isinstance(measures, dict) else None
        if not isinstance(raw_measures, list):
            return _unavailable(pr, head, base, "Sonar duplication measures unavailable")
        metrics = {}
        for measure in raw_measures:
            if not isinstance(measure, dict):
                return _unavailable(pr, head, base, "Sonar duplication measure malformed")
            periods = measure.get("periods")
            if not isinstance(periods, list) or len(periods) != 1 or not isinstance(periods[0], dict):
                return _unavailable(pr, head, base, "Sonar duplication period unavailable")
            metrics[measure.get("metric")] = periods[0].get("value")
        try:
            density = float(metrics["new_duplicated_lines_density"])
            duplicated = int(metrics["new_duplicated_lines"])
            if not (0 <= density <= 100 and 0 <= duplicated <= 1000000):
                raise ValueError("invalid duplication metric")
        except (ValueError, TypeError, KeyError, OverflowError):
            return _unavailable(pr, head, base, "Sonar duplication measures invalid")
        duplicate_files: list[tuple[str, int]] = []
        if density > 3.0:
            tree = get_json(
                _url(
                    "measures/component_tree",
                    component=project,
                    pullRequest=pr,
                    metricKeys="new_duplicated_lines",
                    qualifiers="FIL",
                    ps=500,
                )
            )
            files = tree.get("components") if isinstance(tree, dict) else None
            paging = tree.get("paging") if isinstance(tree, dict) else None
            if (
                not isinstance(files, list)
                or len(files) > 500
                or (isinstance(paging, dict) and paging.get("total", 0) > len(files))
            ):
                return _unavailable(pr, head, base, "Sonar duplicate-file evidence incomplete")
            for file in files:
                if not isinstance(file, dict) or not isinstance(file.get("key"), str):
                    return _unavailable(pr, head, base, "Sonar duplicate-file evidence malformed")
                values = file.get("measures")
                if not isinstance(values, list):
                    return _unavailable(pr, head, base, "Sonar duplicate-file metrics missing")
                for value in values:
                    if isinstance(value, dict) and value.get("metric") == "new_duplicated_lines":
                        periods = value.get("periods")
                        if not isinstance(periods, list) or len(periods) != 1:
                            return _unavailable(pr, head, base, "Sonar duplicate-file period missing")
                        try:
                            count = int(periods[0]["value"])
                        except (KeyError, ValueError, TypeError, IndexError):
                            return _unavailable(pr, head, base, "Sonar duplicate-file count invalid")
                        if count > 0:
                            path = file["key"].removeprefix(project + ":")
                            if path == file["key"] or path.startswith("/") or ".." in path.split("/"):
                                return _unavailable(pr, head, base, "Sonar duplicate-file path invalid")
                            duplicate_files.append((path, count))
            if not duplicate_files:
                return _unavailable(pr, head, base, "Sonar duplication gate failed without file evidence")
        # Close the TOCTOU window: a newer Sonar analysis can replace the PR
        # issue view between the first metadata read and issues/search.
        refreshed = get_json(_url("project_pull_requests/list", project=project))
        refreshed_prs = refreshed.get("pullRequests") if isinstance(refreshed, dict) else None
        refreshed_match = (
            next(
                (item for item in refreshed_prs if isinstance(item, dict) and str(item.get("key")) == str(pr)),
                None,
            )
            if isinstance(refreshed_prs, list)
            else None
        )
        refreshed_commit = (
            refreshed_match.get("commit")
            if isinstance(refreshed_match, dict) and isinstance(refreshed_match.get("commit"), dict)
            else {}
        )
        if not refreshed_match or refreshed_commit.get("sha") != head:
            return _unavailable(pr, head, base, "Sonar analysis changed during collection")
    except Exception:  # noqa: BLE001 -- optional external sensor must degrade to bounded unavailability evidence
        return _unavailable(pr, head, base, "SonarQube Cloud is unavailable")

    issues = issues_payload.get("issues") if isinstance(issues_payload, dict) else None
    total = issues_payload.get("total") if isinstance(issues_payload, dict) else None
    if not isinstance(issues, list) or type(total) is not int or total < 0:
        return _unavailable(pr, head, base, "Sonar issue payload is malformed")
    if total > MAX_ISSUES or len(issues) > MAX_ISSUES:
        return _unavailable(pr, head, base, f"Sonar issue set exceeds bounded limit {MAX_ISSUES}")
    if len(issues) != total:
        return _unavailable(pr, head, base, "Sonar issue count is inconsistent")

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
        # Historical findings must survive a successful remediation. The provider
        # identity is stable across subsequent analyses; status is evidence only.
        issue_status = str(item.get("issueStatus") or item.get("status") or "UNKNOWN")
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
                "message": f"[{severity}] {rule}: {message} [sonar_status={issue_status}]",
                "availability": "available",
                "classification": None,
                "invariant": None,
                "affected_paths": [],
                "fix_reference": None,
                "regression_evidence": [],
                "claimed_family_id": None,
            }
        )
    for path, count in duplicate_files:
        observations.append(
            {
                "source": "sonar",
                "provider": "sonar",
                "event_id": f"sonar-issue-duplication-{head}-{sha256_path(path)}",
                "source_pr": pr,
                "reviewed_head_sha": head,
                "reviewed_base_sha": base,
                "reviewer": "sonarqube-cloud",
                "path": path,
                "line": None,
                "message": f"Sonar new-code duplication gate failed: {density}% > 3.0% ({count} duplicated lines in file)",
                "availability": "available",
                "classification": None,
                "invariant": None,
                "affected_paths": [path],
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
