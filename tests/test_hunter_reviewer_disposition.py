from __future__ import annotations

import json
from types import SimpleNamespace

import hunter_defect_prevention_preflight as prevention
import hunter_governance_review_v2 as gov_v2
import hunter_pre_push


def _valid_recurrence_finding(
    guard_ref: str = "scripts/hunter_governance_review_v2.py::candidate_admission",
    test_ref: str = "tests/test_hunter_governance_review_v2.py::test_candidate_admission_tests_first_red_success_stays_draft",
) -> dict[str, object]:
    return {
        "id": "RFD-TEST-RECURRENCE",
        "source_provenance": {"reviewer": "Codex"},
        "validation_state": "validated",
        "classification": "recurrence",
        "mapped_defect_id": "PRH-007",
        "resolution_state": "resolved",
        "permanent_disposition_evidence": "Valid evidence string for testing",
        "guard_reference": guard_ref,
        "test_reference": test_ref,
    }


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
    dispositions = {"version": 1, "purpose": "test", "findings": [_valid_recurrence_finding()]}
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


def test_nonexistent_guard_file_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(guard_ref="scripts/does_not_exist.py::fake_symbol")
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("references non-existent file 'scripts/does_not_exist.py'" in error for error in errors)


def test_nonexistent_guard_symbol_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(guard_ref="scripts/hunter_governance_review_v2.py::nonexistent_function")
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("symbol 'nonexistent_function' not found" in error for error in errors)


def test_nonexistent_test_file_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(test_ref="tests/does_not_exist.py::test_fake")
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("references non-existent file 'tests/does_not_exist.py'" in error for error in errors)


def test_nonexistent_test_function_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(
        test_ref="tests/test_hunter_governance_review_v2.py::test_completely_fictitious_name"
    )
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("symbol 'test_completely_fictitious_name' not found" in error for error in errors)


def test_malformed_reference_fails(tmp_path, monkeypatch) -> None:
    malformed_refs = [
        "scripts/hunter_governance_review_v2.py",  # missing ::
        "scripts/hunter_governance_review_v2.py::",  # empty symbol
        "::candidate_admission",  # empty path
    ]
    for ref in malformed_refs:
        finding = _valid_recurrence_finding(guard_ref=ref)
        path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
        path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
        monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

        errors = prevention.validate_reviewer_finding_dispositions()
        assert any("guard_reference" in error for error in errors)


def test_absolute_path_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(guard_ref="/app/scripts/hunter_governance_review_v2.py::candidate_admission")
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("uses an absolute path" in error for error in errors)


def test_path_traversal_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(
        guard_ref="scripts/../scripts/hunter_governance_review_v2.py::candidate_admission"
    )
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("contains path traversal ('..')" in error for error in errors)


def test_whitespace_target_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(guard_ref="   ")
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("guard_reference must be a non-empty string" in error for error in errors)


def test_non_string_reference_fails(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(guard_ref=12345)
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("guard_reference must be a non-empty string" in error for error in errors)


def test_valid_guard_reference_passes(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(
        guard_ref="scripts/hunter_defect_prevention_preflight.py::validate_candidate_preflight_definition"
    )
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_valid_pytest_function_reference_passes(tmp_path, monkeypatch) -> None:
    finding = _valid_recurrence_finding(
        test_ref="tests/test_pr376_prevention_regressions.py::test_candidate_definition_rejects_dead_gate_tuple"
    )
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "findings": [finding]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


def test_all_current_seeded_registry_references_pass() -> None:
    errors = prevention.validate_reviewer_finding_dispositions()
    assert errors == []


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
