from __future__ import annotations

import json
from pathlib import Path

import hunter_defect_prevention_preflight as prevention

ROOT = Path(__file__).resolve().parents[1]


def test_code_write_policy_forbids_direct_api_code_commits() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    paths = policy["code_write_paths"]

    assert paths["local_git_push"]["allowed"] is True
    assert paths["local_git_push"]["required_boundary"] == ".githooks/pre-push"
    assert paths["github_contents_api"]["allowed"] is False
    assert paths["github_git_data_api"]["allowed"] is False
    assert paths["api_only_agents"]["allowed_role"] == "read-review-metadata-only"


def test_code_write_policy_requires_draft_until_exact_head_admission() -> None:
    policy = json.loads((ROOT / "docs" / "CODE_WRITE_POLICY.json").read_text(encoding="utf-8"))
    progression = policy["review_progression"]

    assert progression["unadmitted_head_state"] == "draft"
    assert "exact-head" in progression["ready_requires"]
    assert "Pre-PR Preflight" in progression["ready_requires"]
    assert progression["auto_ready"] is False


def test_defect_prevention_guard_validates_code_write_policy() -> None:
    assert prevention.validate_code_write_policy() == []
