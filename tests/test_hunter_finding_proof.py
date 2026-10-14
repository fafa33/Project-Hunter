"""Negative security tests for bounded hosted reviewer proof."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import hunter_finding_proof as proof


def finding(**overrides):
    value = {
        "id": "RFD-900-001",
        "source_provenance": {"reviewed_head_sha": "a" * 40},
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


def test_unlisted_ids_are_not_exempt():
    assert proof.validate_proof(finding(id="RFD-999-001", executable_proof=None))


def test_adversarial_selector_must_be_distinct_and_unique():
    bad = finding()
    bad["executable_proof"]["adversarial_tests"] = ["tests/test_example.py::test_regression"]
    assert "adversarial" in str(proof.validate_proof(bad))
    bad["executable_proof"]["adversarial_tests"] = ["tests/test_example.py::test_adversarial"] * 2
    assert "adversarial" in str(proof.validate_proof(bad))


def test_legacy_exemption_is_pinned_to_record_content():
    import json

    records = json.loads((Path(proof.ROOT) / "docs/REVIEWER_FINDING_DISPOSITIONS.json").read_text())["findings"]
    legacy = next(r for r in records if r["id"] == "RFD-372-001")
    assert proof.is_pinned_legacy(legacy)
    mutated = {**legacy, "root_cause": "rewritten to describe a brand-new finding"}
    assert not proof.is_pinned_legacy(mutated)
    assert proof.validate_proof({**mutated, "executable_proof": None})


def test_vulnerable_sha_must_equal_reviewed_head_provenance():
    bad = finding(source_provenance={"reviewed_head_sha": "b" * 40})
    assert "reviewed_head_sha" in str(proof.validate_proof(bad))
    assert "reviewed_head_sha" in str(proof.validate_proof(finding(source_provenance={})))


def test_pytest_runs_with_scrubbed_environment_and_no_network(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, env=kwargs["env"])
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(proof, "sandbox_prefix", lambda: ["sandbox", "--unshare-net"])
    monkeypatch.setattr(proof.subprocess, "run", fake_run)
    proof.run_pytest(["tests/test_example.py::test_regression"], cwd=tmp_path)
    assert seen["command"][:2] == ["sandbox", "--unshare-net"]
    assert "GITHUB_TOKEN" not in seen["env"] and "HTTPS_PROXY" not in seen["env"]


def test_missing_sandbox_fails_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(proof.shutil, "which", lambda name: None)
    try:
        proof.run_pytest(["tests/test_example.py::test_regression"], cwd=tmp_path)
    except proof.IsolationUnavailable:
        return
    raise AssertionError("an unsandboxed proof run must be refused")


def test_unshare_only_fallback_is_refused(monkeypatch):
    monkeypatch.setattr(proof.shutil, "which", lambda name: "/usr/bin/unshare" if name == "unshare" else None)
    try:
        proof.sandbox_prefix()
    except proof.IsolationUnavailable:
        return
    raise AssertionError("a network-only namespace must not be accepted as isolation")
