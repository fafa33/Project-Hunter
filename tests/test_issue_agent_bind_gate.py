"""Regression for the hosted #577 NO_CHANGES -> terminal FAILED race."""

from pathlib import Path

import yaml


def test_validator_requires_signed_result_bound_transition() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/hunter-issue-agent-trigger.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["bind"]["outputs"]["result_bound"] == "${{ steps.advance.outputs.result_bound }}"
    assert any(step.get("id") == "advance" for step in jobs["bind"]["steps"])
    gate = jobs["validate"]["if"]
    assert "needs.bind.outputs.result_bound == 'true'" in gate
    assert "needs.bind.result == 'success'" in gate


def test_binding_success_is_not_inferred_from_job_success() -> None:
    source = Path("scripts/hunter_issue_agent_lifecycle.py").read_text()
    assert "decision.target_state == state.RESULT_BOUND" in source
    assert "_output(result_bound=" in source
