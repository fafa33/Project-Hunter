"""Regression coverage for PR #609 exact candidate and owner-token boundaries."""

from pathlib import Path
from types import SimpleNamespace

import hunter_defect_prevention_preflight as prevention
import hunter_merge_readiness_v2 as readiness


def test_trusted_gate_targets_candidate_data(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        prevention,
        "TRUSTED_CANDIDATE_QUALITY_GATES",
        (
            ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
            ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
            ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
        ),
    )
    monkeypatch.setattr(prevention, "trusted_lane_problem", lambda _: None)

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prevention.subprocess, "run", run)
    assert prevention.run_candidate_quality_gates(tmp_path) == 0
    assert len(seen) == 3
    for command, kwargs in seen:
        assert Path(command[1]).is_absolute()
        assert kwargs["env"]["HUNTER_TRUSTED_VALIDATION_ROOT"] == str(tmp_path.resolve())
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["GH_TOKEN"] == ""


def test_authenticated_contract_reads_use_explicit_token(monkeypatch):
    seen = []

    def request(**kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr(readiness, "REPO", "fafa33/Project-Hunter")
    monkeypatch.setattr(readiness, "TOKEN", "")
    monkeypatch.setattr(readiness.transport, "request_rest_json", request)
    assert readiness.paged("commits/a/statuses", token="gh-authenticated") == []
    assert seen[0]["token"] == "gh-authenticated"
    readiness.request_json("GET", "rulesets", token="gh-authenticated")
    assert seen[-1]["token"] == "gh-authenticated"
