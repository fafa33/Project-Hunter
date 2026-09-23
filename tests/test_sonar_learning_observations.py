from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path("scripts/hunter_collect_sonar_observations.py")
spec = importlib.util.spec_from_file_location("sonar_observations", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HEAD = "a" * 40
BASE = "b" * 40


def getter(*, sha=HEAD, issues=None, total=None):
    issue_list = [] if issues is None else issues

    def get(url: str):
        if "project_pull_requests" in url:
            return {"pullRequests": [{"key": "494", "commit": {"sha": sha}}]}
        return {"issues": issue_list, "total": len(issue_list) if total is None else total}

    return get


def test_exact_head_clean_analysis_is_available_but_non_authoritative():
    rows = module.collect("fafa33_Project-Hunter", 494, HEAD, BASE, get_json=getter())
    assert rows[0]["availability"] == "available"
    assert rows[0]["classification"] is None
    assert rows[0]["event_id"] == f"sonar-clean-{HEAD}"


def test_stale_sonar_analysis_degrades_to_unavailable():
    rows = module.collect("fafa33_Project-Hunter", 494, HEAD, BASE, get_json=getter(sha="c" * 40))
    assert rows[0]["availability"] == "unavailable"
    assert "exact-head" in rows[0]["message"]


def test_raw_sonar_issue_never_assigns_defect_authority():
    issue = {
        "key": "abc",
        "rule": "python:S123",
        "severity": "MAJOR",
        "component": "fafa33_Project-Hunter:src/x.py",
        "line": 7,
        "message": "problem",
    }
    rows = module.collect("fafa33_Project-Hunter", 494, HEAD, BASE, get_json=getter(issues=[issue]))
    assert rows[0]["path"] == "src/x.py"
    assert rows[0]["classification"] is None
    assert rows[0]["invariant"] is None
    assert rows[0]["claimed_family_id"] is None


def test_provider_failure_is_nonblocking_unavailability_evidence():
    def broken(_url: str):
        raise TimeoutError("offline")

    rows = module.collect("fafa33_Project-Hunter", 494, HEAD, BASE, get_json=broken)
    assert rows[0]["availability"] == "unavailable"
    assert rows[0]["classification"] is None


def test_oversized_issue_set_fails_closed_without_partial_learning():
    rows = module.collect("fafa33_Project-Hunter", 494, HEAD, BASE, get_json=getter(total=101))
    assert rows[0]["availability"] == "unavailable"
    assert "bounded limit" in rows[0]["message"]
