from __future__ import annotations

import json

import hunter_defect_prevention_preflight as prevention


def test_current_defect_prevention_lifecycle_is_valid() -> None:
    assert prevention.validate_defect_prevention_lifecycle() == []


def test_legacy_guarded_is_not_treated_as_prevented() -> None:
    lifecycle = json.loads(prevention.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    assert lifecycle["legacy_status_semantics"]["guarded"] == "detected"
    assert lifecycle["stages"][-1] == "prevented"


def test_prh_007_has_local_hosted_merge_and_recurrence_evidence() -> None:
    lifecycle = json.loads(prevention.LIFECYCLE_PATH.read_text(encoding="utf-8"))
    evidence = lifecycle["explicit_enforcement"]["PRH-007"]
    assert evidence["state"] == "merge-enforced"
    for field in prevention.REQUIRED_ENFORCEMENT_FIELDS:
        assert isinstance(evidence[field], str)
        assert evidence[field].strip()


def test_prevented_state_requires_all_enforcement_evidence(monkeypatch, tmp_path) -> None:
    registry = {"defects": [{"id": "X-001", "status": "guarded"}]}
    lifecycle = {
        "version": 1,
        "stages": list(prevention.EXPECTED_STAGES),
        "legacy_status_semantics": {"guarded": "detected"},
        "explicit_enforcement": {
            "X-001": {
                "state": "prevented",
                "local": "hook",
                "hosted": "ci",
                "merge": "",
                "recurrence": "escalate",
            }
        },
    }
    registry_path = tmp_path / "registry.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    lifecycle_path.write_text(json.dumps(lifecycle), encoding="utf-8")
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(prevention, "LIFECYCLE_PATH", lifecycle_path)

    errors = prevention.validate_defect_prevention_lifecycle()

    assert any("requires non-empty merge evidence" in error for error in errors)


def test_unknown_legacy_status_fails_closed(monkeypatch, tmp_path) -> None:
    registry = {"defects": [{"id": "X-001", "status": "mystery"}]}
    lifecycle = {
        "version": 1,
        "stages": list(prevention.EXPECTED_STAGES),
        "legacy_status_semantics": {"guarded": "detected"},
        "explicit_enforcement": {},
    }
    registry_path = tmp_path / "registry.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    lifecycle_path.write_text(json.dumps(lifecycle), encoding="utf-8")
    monkeypatch.setattr(prevention, "REGISTRY_PATH", registry_path)
    monkeypatch.setattr(prevention, "LIFECYCLE_PATH", lifecycle_path)

    errors = prevention.validate_defect_prevention_lifecycle()

    assert "legacy registry status has no prevention semantics: mystery" in errors


def test_validate_candidate_preflight_missing_files(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    errors = prevention.validate_candidate_preflight_definition(candidate)
    assert any("candidate preflight workflow missing" in e for e in errors)
    assert any("candidate preflight script missing" in e for e in errors)


def test_validate_candidate_preflight_hostile_bypasses(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    wf_dir = candidate / ".github" / "workflows"
    scripts_dir = candidate / "scripts"
    wf_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    wf_file = wf_dir / "hunter-pre-pr-preflight.yml"
    script_file = scripts_dir / "hunter_pr_preflight.py"

    # Workflow has unconditional exit 0 and missing invocation
    wf_file.write_text("name: Hostile\nrun: exit 0\n", encoding="utf-8")

    # Script has exit 0 and missing quality gates
    script_file.write_text("import sys\n# exit 0\nsys.exit(0)\n", encoding="utf-8")

    errors = prevention.validate_candidate_preflight_definition(candidate)
    assert "candidate preflight workflow does not invoke scripts/hunter_pr_preflight.py" in errors
    assert "candidate preflight workflow contains unconditional exit 0 bypass" in errors
    assert "candidate preflight script contains unconditional exit 0" in errors
    assert any("candidate preflight script NORMAL_QUALITY_GATES missing required gates" in e for e in errors)


def test_validate_candidate_preflight_valid_definition(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    wf_dir = candidate / ".github" / "workflows"
    scripts_dir = candidate / "scripts"
    wf_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    wf_file = wf_dir / "hunter-pre-pr-preflight.yml"
    script_file = scripts_dir / "hunter_pr_preflight.py"

    wf_file.write_text("name: Hunter Pre-PR Preflight\nrun: python scripts/hunter_pr_preflight.py\n", encoding="utf-8")

    script_content = """from __future__ import annotations
import sys

def run_quality_gates():
    gates = [
        ("Architecture Index Guard", ["python", "scripts/hunter_architecture_index_preflight.py"]),
        ("Artifact Guard", ["python", "scripts/hunter_artifact_preflight.py"]),
        ("Defect Prevention Guard", ["python", "scripts/hunter_defect_prevention_preflight.py"]),
        ("Ruff", ["ruff", "check", "."]),
        ("Black", ["black", "--check", "."]),
        ("Mypy", ["mypy", "src"]),
        ("Pytest", ["pytest"]),
    ]
    return 0

if __name__ == "__main__":
    sys.exit(run_quality_gates())
"""
    script_file.write_text(script_content, encoding="utf-8")

    errors = prevention.validate_candidate_preflight_definition(candidate)
    assert errors == []
