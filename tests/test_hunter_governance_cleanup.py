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
    text = (ROOT / ".github/instructions/project-hunter.instructions.md").read_text(encoding="utf-8")
    assert "hunter_governance_preflight.py" not in text
    assert "owner-`+1`" in text
    assert "not merge authority" in text
    assert "Quality Gates" in text
    assert "CodeQL" in text


def test_governance_review_has_no_bootstrap_fallback() -> None:
    text = (ROOT / ".github/workflows/hunter-governance-review.yml").read_text(encoding="utf-8")
    assert "hunter_governance_review_v2.py" in text
    assert "PR_NUMBER} = \"283\"" not in text
    assert "bootstrap" not in text.lower()


def test_merge_readiness_docs_name_only_current_risk_inputs() -> None:
    text = (ROOT / "docs/MERGE_READINESS_GATE.md").read_text(encoding="utf-8")
    assert "Quality Gates" in text
    assert "dependency-review" in text
    assert "CodeQL" in text
    assert "CHANGES_REQUESTED" in text
    assert "Non-blocking recommendations do not" in text
