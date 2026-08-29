from __future__ import annotations

import json
from types import SimpleNamespace

import hunter_defect_prevention_preflight as prevention
import hunter_merge_readiness_v2 as readiness
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

    monkeypatch.setattr(readiness, "REVIEWER_DISPOSITIONS_PATH", path)
    obs = readiness.StaticReadinessObservation(unresolved_review_threads=())
    decision = readiness.evaluate(obs)
    assert decision.state == "failure"
    assert "Unresolved validated reviewer findings" in decision.description


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
    dispositions = {
        "version": 1,
        "purpose": "test",
        "findings": [
            {
                "id": "RFD-TEST-ERR1",
                "source_provenance": {"reviewer": "Reviewer A"},
                "validation_state": "validated",
                "classification": "duplicate",
                "resolution_state": "resolved",
                "permanent_disposition_evidence": "Fixed elsewhere",
            }
        ],
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps(dispositions), encoding="utf-8")
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
            "guard_reference": "scripts/hunter_governance_review_v2.py::candidate_admission",
            "test_reference": "tests/test_hunter_candidate_admission.py::test_admitted_candidate_stays_ready",
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
            "guard_reference": "scripts/hunter_governance_review_v2.py::candidate_admission",
            "test_reference": "tests/test_hunter_candidate_admission.py::test_admitted_candidate_stays_ready",
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
            "test_reference": "tests/test_hunter_candidate_admission.py::test_admitted_candidate_stays_ready",
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
            "guard_reference": "scripts/hunter_governance_review_v2.py::candidate_admission",
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


def test_reference_target_to_nonexistent_file_or_symbol_fails(tmp_path, monkeypatch) -> None:
    nonexistent_file = {
        "id": "RFD-TEST-NOFILE",
        "source_provenance": {"reviewer": "Rev"},
        "validation_state": "validated",
        "classification": "recurrence",
        "mapped_defect_id": "PRH-007",
        "resolution_state": "resolved",
        "permanent_disposition_evidence": "Valid evidence",
        "guard_reference": "scripts/nonexistent_guard.py::guard",
        "test_reference": "tests/test_hunter_candidate_admission.py::test_admitted_candidate_stays_ready",
    }
    path = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    path.write_text(json.dumps({"version": 1, "purpose": "test", "findings": [nonexistent_file]}), encoding="utf-8")
    monkeypatch.setattr(prevention, "REVIEWER_DISPOSITIONS_PATH", path)

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any("file 'scripts/nonexistent_guard.py' does not exist" in error for error in errors)

    nonexistent_symbol = {
        "id": "RFD-TEST-NOSYM",
        "source_provenance": {"reviewer": "Rev"},
        "validation_state": "validated",
        "classification": "recurrence",
        "mapped_defect_id": "PRH-007",
        "resolution_state": "resolved",
        "permanent_disposition_evidence": "Valid evidence",
        "guard_reference": "scripts/hunter_governance_review_v2.py::nonexistent_symbol_func",
        "test_reference": "tests/test_hunter_candidate_admission.py::test_admitted_candidate_stays_ready",
    }
    path.write_text(json.dumps({"version": 1, "purpose": "test", "findings": [nonexistent_symbol]}), encoding="utf-8")

    errors = prevention.validate_reviewer_finding_dispositions()
    assert any(
        "symbol 'nonexistent_symbol_func' not found in 'scripts/hunter_governance_review_v2.py'" in error
        for error in errors
    )


def test_references_require_explicit_selectors_and_python_files() -> None:
    invalid_cases = (
        ("scripts/hunter_governance_review_v2.py", "guard", "must use"),
        ("tests/test_hunter_governance_review_v2.py", "test", "must use"),
        ("scripts/hunter_governance_review_v2.py::", "guard", "selector must be non-empty"),
        ("tests/test_hunter_governance_review_v2.py::   ", "test", "selector must be non-empty"),
        ("README.md::candidate_admission", "guard", "must be a Python file"),
    )

    for reference, role, expected in invalid_cases:
        error = prevention._validate_reference_target(reference, role=role)
        assert error is not None and expected in error


def test_guard_and_test_reference_roles_are_not_interchangeable() -> None:
    production_function_as_test = prevention._validate_reference_target(
        "scripts/hunter_governance_review_v2.py::candidate_admission",
        role="test",
    )
    test_helper_as_guard = prevention._validate_reference_target(
        "tests/test_macro_intelligence_engine.py::point",
        role="guard",
    )

    assert production_function_as_test is not None and "must target the tests tree" in production_function_as_test
    assert test_helper_as_guard is not None and "must not target the tests tree" in test_helper_as_guard


def test_valid_guard_and_pytest_function_references_pass() -> None:
    assert (
        prevention._validate_reference_target(
            "scripts/hunter_governance_review_v2.py::candidate_admission",
            role="guard",
        )
        is None
    )
    assert (
        prevention._validate_reference_target(
            "tests/test_hunter_governance_review_v2.py::test_candidate_admission_tests_first_red_success_stays_draft",
            role="test",
        )
        is None
    )


def test_class_qualified_pytest_reference_passes_static_discovery_rules(tmp_path, monkeypatch) -> None:
    repository_root = tmp_path / "repo"
    test_file = repository_root / "tests" / "test_class_reference.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class TestReference:\n" "    def test_static_target(self):\n" "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prevention, "ROOT", repository_root)

    assert (
        prevention._validate_reference_target(
            "tests/test_class_reference.py::TestReference::test_static_target",
            role="test",
        )
        is None
    )


def test_reference_paths_are_confined_after_symlink_resolution(tmp_path, monkeypatch) -> None:
    repository_root = tmp_path / "repo"
    scripts = repository_root / "scripts"
    scripts.mkdir(parents=True)
    internal_file = scripts / "internal_guard.py"
    internal_file.write_text("def guard():\n    pass\n", encoding="utf-8")
    external_file = tmp_path / "external_guard.py"
    external_file.write_text("def guard():\n    pass\n", encoding="utf-8")
    (scripts / "external_link.py").symlink_to(external_file)
    (scripts / "internal_link.py").symlink_to(internal_file)
    monkeypatch.setattr(prevention, "ROOT", repository_root)

    absolute_error = prevention._validate_reference_target(f"{external_file}::guard", role="guard")
    traversal_error = prevention._validate_reference_target("../external_guard.py::guard", role="guard")
    escape_error = prevention._validate_reference_target("scripts/external_link.py::guard", role="guard")

    assert absolute_error is not None and "repository-relative" in absolute_error
    assert traversal_error is not None and "must not contain '..' traversal" in traversal_error
    assert escape_error is not None and "resolves outside" in escape_error
    assert prevention._validate_reference_target("scripts/internal_link.py::guard", role="guard") is None


def test_all_seeded_registry_references_pass_role_validation() -> None:
    dispositions = json.loads(prevention.REVIEWER_DISPOSITIONS_PATH.read_text(encoding="utf-8"))

    for finding in dispositions["findings"]:
        guard_reference = finding.get("guard_reference")
        test_reference = finding.get("test_reference")
        if guard_reference is not None:
            assert prevention._validate_reference_target(guard_reference, role="guard") is None
        if test_reference is not None:
            assert prevention._validate_reference_target(test_reference, role="test") is None


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
