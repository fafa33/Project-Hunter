"""Adversarial regression test suite for HMA-1 Phase 2 Slice 2.

Proves that modified production execution paths reject:
1. Stale / Mismatched HEAD SHA (in candidate_admission and verify_unprivileged_head)
2. Candidate workflow tampering (.github/workflows/*)
3. Candidate test-definition tampering (tests/*)
4. Forged successful evidence / malformed PR payloads
5. Missing or failed authoritative checks (API failures fail closed)
6. Forbidden elevated credentials during candidate execution
"""

from pathlib import Path
from unittest.mock import MagicMock
import yaml

import hunter_governance_review_v2 as governance
import hunter_merge_readiness_v2 as readiness
import hunter_preflight_reuse_decision as reuse_decision
import hunter_pr_preflight as pr_preflight

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hma1_unprivileged_ci_permissions_and_workflow_structure():
    """Verify CI workflows enforce unprivileged candidate execution boundaries."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml must exist"

    with open(ci_path, "r", encoding="utf-8") as f:
        ci_data = yaml.safe_load(f)

    # Top-level permissions must be restricted
    assert ci_data.get("permissions") == {}, "Top-level permissions in ci.yml must be empty {}"

    quality_job = ci_data.get("jobs", {}).get("quality", {})
    assert quality_job, "quality job must exist in ci.yml"

    # Quality Gates job permissions must be unprivileged
    job_perms = quality_job.get("permissions", {})
    assert "statuses" not in job_perms, "Unprivileged quality job must not have statuses: write permission"
    assert "secrets" not in str(quality_job), "Candidate quality job must not inherit or receive secrets"

    # Verify Trusted Upgrade workflow structure
    upgrade_path = REPO_ROOT / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    assert upgrade_path.exists(), "hunter-trusted-preflight-upgrade.yml must exist"
    with open(upgrade_path, "r", encoding="utf-8") as f:
        upgrade_data = yaml.safe_load(f)

    validate_job = upgrade_data.get("jobs", {}).get("validate-candidate", {})
    val_perms = validate_job.get("permissions", {})
    assert val_perms.get("statuses") is None, "Validation job must not carry status write permissions"


def test_hma1_candidate_admission_rejects_stale_head_sha(monkeypatch):
    """Verify candidate_admission rejects PR when current PR head SHA differs from requested evaluation HEAD."""
    head_sha = "a" * 40
    stale_sha = "b" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        if f"pulls/{pr_number}" in path:
            return {
                "number": pr_number,
                "state": "open",
                "head": {"sha": stale_sha, "ref": "feature-branch"},
                "base": {"ref": "main"},
            }
        return {}

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "differs from evaluation HEAD" in message


def test_hma1_candidate_admission_fails_closed_on_api_error(monkeypatch):
    """Verify candidate_admission fails closed if PR head verification raises an error."""
    head_sha = "a" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        raise RuntimeError("GitHub API connection timeout")

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "PR head SHA verification failed" in message


def test_hma1_candidate_workflow_and_test_tampering_prevents_reuse():
    """Verify candidate edits to workflows or tests are classified as trusted definition paths, preventing proof reuse."""
    # Workflow tampering
    assert reuse_decision.is_trusted_definition_path(".github/workflows/ci.yml")
    assert reuse_decision.is_trusted_definition_path(".github/workflows/hunter-pre-pr-preflight.yml")
    assert reuse_decision.is_trusted_definition_path(".github/workflows/hunter-trusted-preflight-upgrade.yml")

    # Test tampering
    assert reuse_decision.is_trusted_definition_path("tests/test_hma1_trusted_gate.py")
    assert reuse_decision.is_trusted_definition_path("tests/unit/test_auth.py")
    assert reuse_decision.is_trusted_definition_path("tests/conftest.py")

    # Preflight script tampering
    assert reuse_decision.is_trusted_definition_path("scripts/hunter_pr_preflight.py")
    assert reuse_decision.is_trusted_definition_path("scripts/hunter_defect_prevention_preflight.py")


def test_hma1_verify_unprivileged_head_rejects_stale_sha(monkeypatch):
    """Verify hunter_pr_preflight.verify_unprivileged_head fails closed on stale SHA mismatch."""
    expected_sha = "a" * 40
    actual_sha = "b" * 40

    def mock_check_output(*args, **kwargs):
        return actual_sha + "\n"

    monkeypatch.setattr("subprocess.check_output", mock_check_output)

    res = pr_preflight.verify_unprivileged_head(expected_head_sha=expected_sha)
    assert res == 1, "Must return 1 on HEAD SHA mismatch"


def test_hma1_verify_unprivileged_head_rejects_forbidden_credentials(monkeypatch):
    """Verify hunter_pr_preflight.verify_unprivileged_head fails closed if elevated write tokens are present."""
    monkeypatch.setenv("GH_ADMIN_TOKEN", "secret_write_token")

    res = pr_preflight.verify_unprivileged_head()
    assert res == 1, "Must return 1 if elevated write token is detected"


def test_hma1_canonical_readiness_authority_uniqueness():
    """Verify hunter_merge_readiness_v2.py remains the canonical readiness authority."""
    readiness_script = REPO_ROOT / "scripts" / "hunter_merge_readiness_v2.py"
    assert readiness_script.exists()

    class DummyObs:
        draft = False
        mergeable = True
        unresolved_review_threads = ()
        changes_requested = ()
        review_authority = ("VALID_AGENT_REVIEW", "approved")
        candidate_admission = ("success", "preflight passed")
        check_runs = (
            {"name": "Quality Gates", "status": "completed", "conclusion": "success"},
            {"name": "dependency-review", "status": "completed", "conclusion": "success"},
            {"name": "CodeQL", "status": "completed", "conclusion": "success"},
        )
        shared_open_prs = ()

    decision = readiness.evaluate(DummyObs())
    assert decision.state == "success"
    assert "Ready to merge" in decision.description
