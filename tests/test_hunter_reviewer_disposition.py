from __future__ import annotations

import json
from types import SimpleNamespace

import hunter_defect_prevention_preflight as prevention
import hunter_governance_review_v2 as gov_v2
import hunter_pre_push


def test_unresolved_validated_finding_cannot_be_resolved_by_thread_resolution_alone(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-001",
                "source_provenance": {"reviewer": "CodeRabbit"},
                "validation_state": "validated",
                "classification": "new_systemic_defect",
                "resolution_state": "unresolved",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("unresolved" in error for error in errors)

    disp_ok, disp_msg = gov_v2.check_reviewer_dispositions()
    assert disp_ok is False
    assert "unresolved" in disp_msg


def test_prevented_defect_recurrence_fails_prevention_conformance(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-002",
                "source_provenance": {"reviewer": "Codex"},
                "validation_state": "validated",
                "classification": "recurrence",
                "mapped_defect_id": "PRH-007",
                "resolution_state": "unresolved",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("recurrence" in error or "unresolved" in error for error in errors)


def test_recurrence_becomes_valid_only_after_permanent_corrective_disposition(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-003",
                "source_provenance": {"reviewer": "Codex"},
                "validation_state": "validated",
                "classification": "recurrence",
                "mapped_defect_id": "PRH-007",
                "resolution_state": "resolved",
                "permanent_disposition_evidence": "Added exact-head proof check",
                "guard_reference": "scripts/hunter_governance_review_v2.py::candidate_admission",
                "test_reference": "tests/test_pr376_prevention_regressions.py::test_protected_preflight_requires_exact_head_pr_bound_status",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_duplicate_reviewer_reports_map_to_one_defect_class(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-004A",
                "source_provenance": {"reviewer": "Reviewer A", "pr_number": 100},
                "validation_state": "validated",
                "classification": "duplicate",
                "mapped_defect_id": "PRH-007",
                "resolution_state": "resolved",
                "permanent_disposition_evidence": "Fixed in PRH-007 guard",
            },
            {
                "id": "RFD-TEST-004B",
                "source_provenance": {"reviewer": "Reviewer B", "pr_number": 101},
                "validation_state": "validated",
                "classification": "duplicate",
                "mapped_defect_id": "PRH-007",
                "resolution_state": "resolved",
                "permanent_disposition_evidence": "Fixed in PRH-007 guard",
            },
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_non_automatable_disposition_without_justification_or_manual_control_fails(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-005",
                "source_provenance": {"reviewer": "Reviewer A"},
                "validation_state": "validated",
                "classification": "isolated_non_automatable",
                "resolution_state": "resolved",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("justification" in error or "bounded manual control" in error for error in errors)


def test_unvalidated_reviewer_prose_cannot_create_or_clear_enforcement_state(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-006",
                "source_provenance": {"reviewer": "Reviewer A"},
                "validation_state": "unvalidated",
                "resolution_state": "unresolved",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_existing_valid_normal_pr_behavior_is_not_falsely_blocked(tmp_path, monkeypatch) -> None:
    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_duplicate_without_mapped_defect_id_fails(tmp_path, monkeypatch) -> None:
    invalid_duplicates: list[dict[str, object]] = [
        # missing mapped_defect_id
        {
            "id": "RFD-TEST-ERR1A",
            "source_provenance": {"reviewer": "Reviewer A"},
            "validation_state": "validated",
            "classification": "duplicate",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Fixed elsewhere",
        },
        # whitespace-only mapped_defect_id
        {
            "id": "RFD-TEST-ERR1B",
            "source_provenance": {"reviewer": "Reviewer A"},
            "validation_state": "validated",
            "classification": "duplicate",
            "mapped_defect_id": "   ",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Fixed elsewhere",
        },
        # boolean mapped_defect_id
        {
            "id": "RFD-TEST-ERR1C",
            "source_provenance": {"reviewer": "Reviewer A"},
            "validation_state": "validated",
            "classification": "duplicate",
            "mapped_defect_id": True,
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Fixed elsewhere",
        },
    ]

    for case in invalid_duplicates:
        path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
        path.write_text(json.dumps({"version": 1, "findings": [case]}), encoding="utf-8")
        monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

        errors = prevention.validate_reviewer_finding_dispositions()
        assert any("duplicate classification requires non-empty mapped_defect_id" in error for error in errors)


def test_duplicate_with_unknown_mapped_defect_id_fails(tmp_path, monkeypatch) -> None:
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-ERR2",
                "source_provenance": {"reviewer": "Reviewer A"},
                "validation_state": "validated",
                "classification": "duplicate",
                "mapped_defect_id": "PRH-UNKNOWN-999",
                "resolution_state": "resolved",
                "permanent_disposition_evidence": "Fixed elsewhere",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("mapped_defect_id 'PRH-UNKNOWN-999' not found" in error for error in errors)


def test_escalated_recurrence_with_missing_or_invalid_evidence_or_refs_fails(tmp_path, monkeypatch) -> None:
    invalid_cases: list[dict[str, object]] = [
        # missing permanent_disposition_evidence
        {
            "id": "RFD-ERR-A",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "guard_reference": "scripts/guard.py",
            "test_reference": "tests/test_guard.py",
        },
        # whitespace-only permanent_disposition_evidence
        {
            "id": "RFD-ERR-B",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "   ",
            "guard_reference": "scripts/guard.py",
            "test_reference": "tests/test_guard.py",
        },
        # boolean placeholder for permanent_disposition_evidence
        {
            "id": "RFD-ERR-B2",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": True,
            "guard_reference": "scripts/guard.py",
            "test_reference": "tests/test_guard.py",
        },
        # boolean placeholder for guard_reference
        {
            "id": "RFD-ERR-C",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": True,
            "test_reference": "tests/test_guard.py",
        },
        # whitespace-only guard_reference
        {
            "id": "RFD-ERR-C2",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": "   ",
            "test_reference": "tests/test_guard.py",
        },
        # number placeholder for guard_reference
        {
            "id": "RFD-ERR-C3",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": 123,
            "test_reference": "tests/test_guard.py",
        },
        # missing test_reference
        {
            "id": "RFD-ERR-D",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": "scripts/guard.py",
        },
        # whitespace-only test_reference
        {
            "id": "RFD-ERR-D2",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": "scripts/guard.py",
            "test_reference": "\t \n",
        },
        # list placeholder for test_reference
        {
            "id": "RFD-ERR-D3",
            "source_provenance": {"reviewer": "Rev"},
            "validation_state": "validated",
            "classification": "recurrence",
            "mapped_defect_id": "PRH-007",
            "resolution_state": "resolved",
            "permanent_disposition_evidence": "Valid evidence",
            "guard_reference": "scripts/guard.py",
            "test_reference": ["tests/test_guard.py"],
        },
    ]

    for case in invalid_cases:
        case_id = str(case["id"])
        dispositions = {"version": 1, "purpose": "test", "findings": [case]}
        path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
        path.write_text(json.dumps(dispositions), encoding="utf-8")
        monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

        errors = prevention.validate_reviewer_finding_dispositions()
        assert any(
            "recurrence of" in error and "requires a resolved permanent disposition" in error for error in errors
        ), f"Failed for case {case_id}: {errors}"


def test_deterministic_preflight_failure_cannot_cross_normal_push_boundary(tmp_path, monkeypatch) -> None:
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(hunter_pre_push, "_run_git", fake_git)
    monkeypatch.setattr(hunter_pre_push.os, "chdir", lambda _path: None)
    monkeypatch.setattr(hunter_pre_push, "_select_preflight_mode", lambda _head: hunter_pre_push.NORMAL_MODE)
    monkeypatch.setattr(
        hunter_pre_push.subprocess,
        "run",
        lambda command, *, check: SimpleNamespace(returncode=1),
    )

    update = [f"refs/heads/main {'a'*40} refs/heads/main {'0'*40}\n"]
    exit_code = hunter_pre_push.enforce_pre_push(update)
    assert exit_code == 1
