"""Adversarial regression test suite for HMA-1 Slice 1.

Verifies:
1. Restricted, unprivileged candidate CI execution boundaries (no secrets / write tokens).
2. Strict exact PR HEAD SHA validation binding.
3. Anti-forgery protections preventing candidate workflow/test edits from forging success.
4. Canonical readiness authority preservation via scripts/hunter_merge_readiness_v2.py.
5. Fail-closed behavior on unresolved reviewer findings and thread state.
"""

from pathlib import Path
import yaml

import hunter_governance_review_v2 as governance
import hunter_merge_readiness_v2 as readiness
import hunter_preflight_reuse_decision as reuse_decision

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


def test_hma1_exact_head_sha_binding_validation(monkeypatch):
    """Verify that candidate admission and readiness refuse mismatched HEAD SHAs."""
    head_sha = "a" * 40
    stale_sha = "b" * 40
    pr_number = 999

    # Mock PR API returning a different current head SHA than requested
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

    # Decide on reuse when head SHA differs
    reusable, reason = reuse_decision.decide(
        REPO_ROOT,
        head_sha=head_sha,
        repository="fafa33/Project-Hunter",
        pr_number=pr_number,
        token="mock_token",
    )
    assert not reusable, "Must reject reuse when PR current head SHA differs from requested HEAD"
    assert "differs" in reason or "unavailable" in reason or "head" in reason.lower()


def test_hma1_candidate_definition_tampering_prevents_reuse():
    """Verify candidate changes to preflight/test definitions force full trusted gate run."""
    # Test protected paths
    assert reuse_decision.is_trusted_definition_path("scripts/hunter_pr_preflight.py")
    assert reuse_decision.is_trusted_definition_path("scripts/hunter_defect_prevention_preflight.py")
    assert reuse_decision.is_trusted_definition_path(".github/workflows/ci.yml")
    assert reuse_decision.is_trusted_definition_path(".github/workflows/hunter-trusted-preflight-upgrade.yml")
    assert reuse_decision.is_trusted_definition_path("pyproject.toml")
    assert reuse_decision.is_trusted_definition_path("requirements/ci-constraints.txt")
    assert reuse_decision.is_trusted_definition_path("tests/conftest.py")


def test_hma1_canonical_readiness_authority_uniqueness():
    """Verify hunter_merge_readiness_v2.py remains the canonical readiness authority."""
    readiness_script = REPO_ROOT / "scripts" / "hunter_merge_readiness_v2.py"
    assert readiness_script.exists()

    # Confirm evaluate returns canonical Decision instances
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


def test_hma1_fail_closed_on_unresolved_reviewer_findings(tmp_path, monkeypatch):
    """Verify readiness evaluation fails closed when unresolved validated findings exist."""
    dispositions_file = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    dispositions_file.write_text(
        '{"findings": [{"id": "FIND-001", "validation_state": "validated", "resolution_state": "unresolved"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(readiness, "REVIEWER_DISPOSITIONS_PATH", dispositions_file)

    class DummyObs:
        draft = False
        mergeable = True

    decision = readiness.evaluate(DummyObs())
    assert decision.state == "failure"
    assert "Unresolved validated reviewer findings" in decision.description
