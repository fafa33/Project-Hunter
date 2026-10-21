"""Fail-closed checks for the existing Issue-agent knowledge context CLI boundary."""

from __future__ import annotations

import argparse

import hunter_issue_agent_lifecycle as cli
import pytest


@pytest.mark.parametrize("path", ["/etc/passwd", "../secrets", "scripts/../../secrets", "scripts\\foo.py", ""])
def test_context_check_refuses_unsafe_paths_before_network(path: str) -> None:
    args = argparse.Namespace(path=path, checkout=".", base_sha="a" * 40)
    with pytest.raises(cli.LifecycleRefused, match="INVALID_SCOPE"):
        cli.cmd_knowledge_context_check(None, args)


def test_context_check_is_wired_to_existing_lifecycle_parser() -> None:
    args = cli._parser().parse_args(
        ["knowledge-context-check", "--path", "scripts/hunter_preflight_reuse_decision.py", "--base-sha", "a" * 40]
    )
    assert args.handler is cli.cmd_knowledge_context_check


def test_signed_historical_assessment_reaches_issue_agent_dpm_without_fabricating_proof(tmp_path) -> None:
    """Same production compiler route as authorize.prepare; no mock context or fake family."""
    import json

    from hunter.automation import issue_agent_knowledge as knowledge
    from hunter.evidence_intelligence.engineering_context_authority import (
        ENGINEERING_IMPLEMENT_TASK_KEY,
        EngineeringContextAuthority,
    )
    from hunter.task_scope import TaskScopeContract

    view = knowledge.KnowledgeView(repository_id=1)
    identity = knowledge.finding_id(1, 582, 4224886962)
    view.findings[identity] = knowledge.Finding(
        {
            "claim": "exact-head race must fail closed",
            "fingerprint": knowledge.fingerprint(
                "scripts/hunter_preflight_reuse_decision.py", "exact-head race must fail closed"
            ),
            "provenance": {
                "pull_request_number": 582,
                "comment_id": 4224886962,
                "path": "scripts/hunter_preflight_reuse_decision.py",
                "reviewed_head_sha": "a" * 40,
            },
        }
    )
    assessment = {
        "finding_id": identity,
        "pull_request_number": 582,
        "comment_id": 4224886962,
        "reviewed_head_sha": "a" * 40,
        "remediation_commit_sha": "b" * 40,
        "final_head_sha": "c" * 40,
        "merge_commit_sha": "d" * 40,
        "hosted_preflight_run_id": 300,
        "regression_tests": ["tests/test_preflight_reuse_decision.py::test_changed_pr_head_refuses_reuse"],
        "disposition": "historical-fix-evidence",
        "source": "github-authenticated-historical-review",
    }
    knowledge.EVIDENCE_SCHEMAS["historical_evidence_assessed"](assessment, "test")
    knowledge._fold(view, "historical_evidence_assessed", assessment)
    assert view.findings[identity].proven is None
    assert view.findings[identity].open
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"families": []}))
    scope = TaskScopeContract(
        task_id="historical-spm-boundary-rehearsal",
        branch_pattern="issue-586-*",
        base_ref="main",
        base_sha="e" * 40,
        allowed_paths=("scripts/hunter_preflight_reuse_decision.py",),
        prohibited_paths=(),
    )
    context = EngineeringContextAuthority(
        registry_path=registry,
        knowledge_overlay=knowledge.overlay_families(view),
    ).compile(ENGINEERING_IMPLEMENT_TASK_KEY, scope=scope)
    assert f"KF-{identity[:12]}" in [entry["id"] for entry in context["applicable_defect_families"]]
    assert context["selected_prevention_rules_sha256"]
