from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "DEFECT_REGISTRY.json"
INSTRUCTION_PATHS = (
    ROOT / ".github" / "instructions" / "project-hunter.instructions.md",
    ROOT / "CLAUDE.md",
)
CANONICAL_PREFLIGHT_COMMAND = "python scripts/hunter_pr_preflight.py --mode normal"


def test_recent_preflight_failures_remain_registered() -> None:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ids = {item["id"] for item in data["defects"]}

    assert {"PRH-007", "PRH-008", "PRH-009", "PRH-010", "PRH-011"} <= ids


def test_agents_reference_canonical_preflight_command() -> None:
    for path in INSTRUCTION_PATHS:
        text = path.read_text(encoding="utf-8")
        assert CANONICAL_PREFLIGHT_COMMAND in text, path
