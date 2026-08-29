from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_PATHS = (
    ".github/workflows/hunter-draft-promotion-signal.yml",
    ".github/workflows/coderabbit-hostile-review-adapter.yml",
    "scripts/hunter_draft_promotion_signal.py",
    "scripts/hunter_coderabbit_review_adapter.py",
)


def test_retired_migration_surfaces_are_absent() -> None:
    for relative in RETIRED_PATHS:
        assert not (ROOT / relative).exists(), relative


def test_agent_instructions_use_current_state_governance() -> None:
    path = ROOT / ".github/instructions/project-hunter.instructions.md"
    text = path.read_text(encoding="utf-8")
    assert "generate-pr-body" not in text
    assert "Before a governed branch" not in text
    assert "owner-`+1`" in text
    assert "not merge authority" in text
    assert "Issue identity" in text
    assert "top-level PR comments" in text
    assert "metadata-only edits" in text
    assert "superseded historical runs" in text
    assert "it is Draft" in text
    assert "merge conflict or unresolved mergeability" in text
    assert "substantive inline review thread remains unresolved" in text
    assert "current review is `CHANGES_REQUESTED`" in text
    assert "Quality Gates" in text
    assert "dependency-review" in text
    assert "CodeQL" in text
    assert "missing, pending, cancelled, or failed" in text
    assert "Hunter Governance Review` is missing or non-stale pending" in text
    assert "Hunter Governance Review` is failed or errored" in text
    assert "head cannot be safely attributed" in text
    assert "Hunter Merge Readiness` is the final current-state controller" in text


def test_active_workflows_do_not_invoke_retired_preflight() -> None:
    active_workflows = (
        ".github/workflows/hunter-governance-preflight.yml",
        ".github/workflows/hunter-governance-review.yml",
        ".github/workflows/hunter-merge-readiness.yml",
    )
    for relative in active_workflows:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "hunter_governance_preflight.py" not in text, relative


def test_governance_review_has_no_bootstrap_fallback() -> None:
    path = ROOT / ".github/workflows/hunter-governance-review.yml"
    text = path.read_text(encoding="utf-8")
    assert "github.event.pull_request.number" in text
    assert "github.event.workflow_run.pull_requests[0].number" in text
    assert "github.event.inputs.pr_number" in text
    assert "283" not in text
    assert "bootstrap" not in text.lower()
    assert 'python "${GITHUB_WORKSPACE}/engine/scripts/hunter_governance_review_v2.py"' in text


def test_merge_readiness_docs_name_only_current_risk_inputs() -> None:
    path = ROOT / "docs/MERGE_READINESS_GATE.md"
    text = path.read_text(encoding="utf-8")
    assert "the PR is Draft" in text
    assert "mergeability is unresolved" in text
    assert "substantive unresolved finding" in text
    assert "current review is `CHANGES_REQUESTED`" in text
    assert "Quality Gates" in text
    assert "dependency-review" in text
    assert "CodeQL" in text
    assert "missing, pending, cancelled, or failed" in text
    assert "Hunter Governance Review` is missing or non-stale pending" in text
    assert "Hunter Governance Review` is failed or errored" in text
    assert "same head SHA is shared by another open PR" in text
    assert "Issue identity" in text
    assert "top-level PR comments" in text
    assert "metadata-only edits" in text
    assert "superseded historical runs" in text
    assert "Non-blocking recommendations do not" in text
