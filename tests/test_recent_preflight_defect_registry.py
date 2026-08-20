from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "DEFECT_REGISTRY.json"
INSTRUCTION_PATHS = (
    ROOT / ".github" / "instructions" / "project-hunter.instructions.md",
    ROOT / "CLAUDE.md",
)


def test_recent_preflight_failures_remain_registered() -> None:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ids = {item["id"] for item in data["defects"]}

    assert {"PRH-007", "PRH-008"} <= ids


def test_agents_must_run_complete_preflight_before_push() -> None:
    required = (
        "Before pushing",
        "python scripts/hunter_pr_preflight.py --mode normal",
        "Artifact Guard",
        "Ruff",
        "Black",
        "Mypy",
        "Pytest",
    )

    for path in INSTRUCTION_PATHS:
        text = path.read_text(encoding="utf-8")
        for phrase in required:
            assert phrase in text, (path, phrase)


def test_hosted_preflight_evidence_must_match_exact_branch_head() -> None:
    required = (
        "run's commit SHA exactly equals the current branch HEAD",
        "run number",
        "not exact-head evidence",
    )

    for path in INSTRUCTION_PATHS:
        text = path.read_text(encoding="utf-8")
        for phrase in required:
            assert phrase in text, (path, phrase)
