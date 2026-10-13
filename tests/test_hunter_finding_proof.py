"""Negative security tests for bounded hosted reviewer proof."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import hunter_finding_proof as proof


def finding(**overrides):
    value = {
        "id": "RFD-900-001",
        "validation_state": "validated",
        "resolution_state": "resolved",
        "classification": "new_systemic_defect",
        "executable_proof": {
            "vulnerable_sha": "a" * 40,
            "red_test": "tests/test_example.py::test_regression",
            "green_test": "tests/test_example.py::test_regression",
            "adversarial_tests": ["tests/test_example.py::test_adversarial"],
        },
    }
    value.update(overrides)
    return value


def test_missing_proof_fails_closed():
    assert "missing executable_proof" in str(proof.validate_proof(finding(executable_proof=None)))


def test_forged_or_mismatched_proof_rejected_before_execution():
    bad = finding()
    bad["executable_proof"]["vulnerable_sha"] = "main"
    bad["executable_proof"]["green_test"] = "tests/test_example.py::test_other"
    assert len(proof.validate_proof(bad)) >= 2


def test_path_escape_rejected(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    bad = finding()
    bad["executable_proof"]["red_test"] = "tests/../../secrets.py::test_secret"
    bad["executable_proof"]["green_test"] = bad["executable_proof"]["red_test"]
    assert "invalid pytest target" in str(proof.validate_proof(bad, root=tmp_path))


def test_unavailable_baseline_fails_closed(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("def test_regression(): pass\n")
    with patch.object(proof, "run", return_value=type("Result", (), {"returncode": 1})()):
        assert "vulnerable_sha unavailable" in str(proof.validate_proof(finding(), root=tmp_path))


def test_legacy_ids_are_pinned_not_all_findings_exempt():
    assert proof.validate_proof(finding(id="RFD-372-001", executable_proof=None)) == []
    assert proof.validate_proof(finding(id="RFD-999-001", executable_proof=None))
