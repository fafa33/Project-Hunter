from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BRIDGE = "bootstrap_external_review_469.py"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_legacy_bootstrap_bridge_has_no_runtime_workflow_authority_after_cutover() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        assert BRIDGE not in path.read_text(encoding="utf-8"), path.name
    # Retained for audit/rollback evidence only; deletion is a later post-cutover act.
    assert (ROOT / "scripts" / "hunter_governance_review" / BRIDGE).is_file()


def test_governance_has_one_canonical_runtime_controller() -> None:
    review = _workflow("hunter-governance-review.yml")
    reconcile = _workflow("hunter-governance-reconcile.yml")
    assert "hunter_governance_review_v2.py" in review
    assert "hunter_governance_review_v2.py" in reconcile
    assert BRIDGE not in review
    assert BRIDGE not in reconcile


def test_readiness_has_one_canonical_runtime_projection() -> None:
    readiness = _workflow("hunter-merge-readiness.yml")
    assert "python scripts/hunter_merge_readiness_v2.py" in readiness
    assert BRIDGE not in readiness


def test_privileged_reviewer_orchestration_stays_on_trusted_reconcile_path() -> None:
    review = _workflow("hunter-governance-review.yml")
    reconcile = _workflow("hunter-governance-reconcile.yml")
    assert "actions: write" not in review
    assert "actions: write" in reconcile
    assert "python scripts/hunter_review_orchestrator.py ensure" in reconcile
    assert "python scripts/hunter_review_orchestrator.py ensure" not in review


def test_candidate_admission_uses_canonical_controller() -> None:
    admission = _workflow("hunter-candidate-admission.yml")
    assert "engine/scripts/hunter_candidate_admission.py" in admission
    assert BRIDGE not in admission
