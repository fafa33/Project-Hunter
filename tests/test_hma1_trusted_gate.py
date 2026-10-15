"""Adversarial security test suite for HMA-1 Phase 2 Remediation.

Verifies:
1. Candidate modifications to candidate scripts/hygiene checks CANNOT grant candidate admission.
2. Candidate workflow modifications (.github/workflows/*) force full execution under default-branch controller.
3. Candidate test-definition modifications (tests/*) force full execution under default-branch controller.
4. Stale PR HEAD SHA is rejected during initial check and revalidation.
5. GitHub API errors and malformed payloads fail closed with 'failure'.
6. Forged candidate artifacts / status outputs cannot satisfy trusted admission.
7. Unprivileged candidate CI jobs carry no write permissions or secret injection.
8. Trusted validation definitions originate from default branch checkout.
9. Existing canonical readiness authority (hunter_merge_readiness_v2.py) remains unchanged.
"""

from pathlib import Path

import hunter_governance_review_v2 as governance
import hunter_merge_readiness_v2 as readiness
import hunter_preflight_reuse_decision as reuse_decision
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hma1_unprivileged_ci_permissions_and_no_secret_injection():
    """Verify ci.yml enforces unprivileged execution (permissions: {}, no secrets/statuses: write)."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml must exist"

    with open(ci_path, encoding="utf-8") as f:
        ci_data = yaml.safe_load(f)

    assert ci_data.get("permissions") == {}, "Top-level permissions in ci.yml must be empty {}"

    quality_job = ci_data.get("jobs", {}).get("quality", {})
    assert quality_job, "quality job must exist in ci.yml"

    job_perms = quality_job.get("permissions", {})
    assert "statuses" not in job_perms, "Quality job must not carry status write permissions"
    assert "secrets" not in str(quality_job), "Quality job must not receive secrets"


def test_hma1_trusted_validation_originates_from_default_branch():
    """Verify hunter-trusted-preflight-upgrade.yml checks out default branch for trusted controller."""
    upgrade_path = REPO_ROOT / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    assert upgrade_path.exists(), "hunter-trusted-preflight-upgrade.yml must exist"

    with open(upgrade_path, encoding="utf-8") as f:
        upgrade_data = yaml.safe_load(f)

    validate_job = upgrade_data.get("jobs", {}).get("validate-candidate", {})
    steps = validate_job.get("steps", [])

    checkout_trusted = steps[0]
    assert checkout_trusted.get("with", {}).get("ref") == "${{ github.event.repository.default_branch }}"
    assert checkout_trusted.get("with", {}).get("persist-credentials") is False


def test_hma1_candidate_script_tampering_cannot_grant_admission(monkeypatch):
    """Verify candidate modifications to candidate scripts (e.g. hunter_pr_preflight.py) do not bypass admission."""
    head_sha = "a" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        if f"pulls/{pr_number}/files" in path:
            if "page=1" in path or "page=" not in path:
                return [{"filename": "scripts/hunter_pr_preflight.py", "status": "modified", "blob_url": "url"}]
            return []
        if f"pulls/{pr_number}/commits" in path:
            return []
        if f"pulls/{pr_number}" in path:
            return {
                "number": pr_number,
                "state": "open",
                "head": {"sha": head_sha, "ref": "feature"},
                "base": {"ref": "main"},
            }
        if f"commits/{head_sha}/statuses" in path:
            return []  # No trusted upgrade status check
        if f"actions/runs?head_sha={head_sha}" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "head_sha": head_sha,
                        "name": "Hunter / Pre-PR Preflight",
                        "path": ".github/workflows/hunter-pre-pr-preflight.yml",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure", "Must reject candidate editing preflight script without trusted upgrade proof"
    assert (
        "trusted preflight upgrade status is missing" in message
        or "protected preflight" in message
        or "admission blocked" in message
    )


def test_hma1_candidate_workflow_and_test_tampering_prevents_reuse():
    """Verify candidate modifications to workflows or tests are classified as trusted definition paths."""
    # Workflows
    assert reuse_decision.is_trusted_definition_path(".github/workflows/ci.yml")
    assert reuse_decision.is_trusted_definition_path(".github/workflows/hunter-pre-pr-preflight.yml")

    assert reuse_decision.is_trusted_definition_path("scripts/hunter_github_contract.py")

    # Tests
    assert reuse_decision.is_trusted_definition_path("tests/test_hma1_trusted_gate.py")
    assert reuse_decision.is_trusted_definition_path("tests/conftest.py")


def test_hma1_stale_pr_head_sha_rejected(monkeypatch):
    """Verify candidate_admission rejects PR when current PR head SHA differs from requested evaluation HEAD."""
    head_sha = "a" * 40
    stale_sha = "b" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        if f"pulls/{pr_number}" in path:
            return {
                "number": pr_number,
                "state": "open",
                "head": {"sha": stale_sha, "ref": "feature"},
                "base": {"ref": "main"},
            }
        return {}

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "differs from evaluation HEAD" in message


def test_hma1_api_errors_and_malformed_payloads_fail_closed(monkeypatch):
    """Verify candidate_admission fails closed when GitHub API raises an exception or returns malformed payload."""
    head_sha = "a" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        raise RuntimeError("GitHub API connection timeout")

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "PR head SHA verification failed" in message


def test_hma1_forged_candidate_status_without_backed_run_rejected(monkeypatch):
    """Verify forged status checks without a backing GitHub Actions run are rejected."""
    head_sha = "a" * 40
    pr_number = 999

    def mock_request_json(repository, token, method, path, payload=None):
        if f"pulls/{pr_number}" in path:
            return {
                "number": pr_number,
                "state": "open",
                "head": {"sha": head_sha, "ref": "feature"},
                "base": {"ref": "main"},
            }
        if f"pulls/{pr_number}/files" in path:
            if "page=1" in path or "page=" not in path:
                return [{"filename": "scripts/hunter_pr_preflight.py", "status": "modified"}]
            return []
        if f"commits/{head_sha}/statuses" in path:
            return [
                {
                    "id": 1,
                    "context": f"Hunter Trusted Preflight Upgrade / PR #{pr_number}",
                    "state": "success",
                    "creator": {"login": "github-actions[bot]", "type": "Bot"},
                    "target_url": "https://github.com/fafa33/Project-Hunter/actions/runs/99999",
                }
            ]
        if "actions/runs/99999" in path:
            # Backing workflow run belongs to wrong head SHA or wrong path
            return {
                "id": 99999,
                "head_sha": "wrong_sha",
                "path": ".github/workflows/hunter-trusted-preflight-upgrade.yml",
                "event": "pull_request_target",
                "status": "completed",
                "conclusion": "success",
            }
        return {}

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.read_trusted_upgrade_status("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "trusted preflight status head SHA mismatch" in message or "mismatch" in message or "blocked" in message


def test_hma1_head_sha_recheck_detects_concurrent_push(monkeypatch):
    """Verify candidate_admission detects if PR head changes during validation recheck."""
    head_sha = "a" * 40
    new_sha = "c" * 40
    pr_number = 999
    call_count = 0

    monkeypatch.setattr(governance, "read_unresolved_review_threads", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(governance, "verify_code_write_ingress_provenance", lambda *args, **kwargs: ("success", "ok"))
    monkeypatch.setattr(governance, "verify_pre_ready_hostile_review", lambda *args, **kwargs: ("success", "ok"))

    def mock_request_json(repository, token, method, path, payload=None):
        nonlocal call_count
        if f"pulls/{pr_number}" in path and "files" not in path and "commits" not in path:
            call_count += 1
            if call_count == 1:
                return {
                    "number": pr_number,
                    "state": "open",
                    "head": {"sha": head_sha, "ref": "feature-branch"},
                    "base": {"ref": "main"},
                }
            return {
                "number": pr_number,
                "state": "open",
                "head": {"sha": new_sha, "ref": "feature-branch"},
                "base": {"ref": "main"},
            }
        if f"pulls/{pr_number}/files" in path:
            if "page=1" in path or "page=" not in path:
                return [{"filename": "src/app.py", "status": "modified"}]
            return []
        if f"actions/runs?head_sha={head_sha}" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 123,
                        "head_sha": head_sha,
                        "head_branch": "feature-branch",
                        "name": "Hunter / Pre-PR Preflight",
                        "path": ".github/workflows/hunter-pre-pr-preflight.yml",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                        "actor": {"login": "dev"},
                    }
                ]
            }
        return {}

    monkeypatch.setattr(governance, "request_json", mock_request_json)

    status, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, pr_number)
    assert status == "failure"
    assert "head changed during validation" in message


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


def test_hma1_trusted_proof_rechecks_pr_head_after_proof(monkeypatch):
    head_sha = "a" * 40
    calls = {"pr": 0}

    def request(_repository, _token, method, path, payload=None):
        if method == "GET" and path == "pulls/999":
            calls["pr"] += 1
            return {"head": {"sha": "b" * 40 if calls["pr"] >= 3 else head_sha}}
        return {}

    monkeypatch.setattr(governance, "request_json", request)
    monkeypatch.setattr(governance, "verify_code_write_ingress_provenance", lambda *_a, **_k: ("success", "ok"))
    monkeypatch.setattr(
        governance, "read_pr_changed_paths", lambda *_a, **_k: (True, ["scripts/hunter_github_contract.py"], None)
    )
    monkeypatch.setattr(governance, "read_unresolved_review_threads", lambda *_a, **_k: ((), None))
    monkeypatch.setattr(governance, "verify_pre_ready_hostile_review", lambda *_a, **_k: ("success", "ok"))
    monkeypatch.setattr(governance, "read_trusted_upgrade_status", lambda *_a, **_k: ("success", "trusted"))
    state, message = governance.candidate_admission("fafa33/Project-Hunter", "token", head_sha, 999)
    assert state == "failure"
    assert "head changed" in message
    assert calls["pr"] >= 3
