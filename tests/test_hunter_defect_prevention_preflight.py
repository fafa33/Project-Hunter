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
