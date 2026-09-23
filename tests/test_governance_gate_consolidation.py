from __future__ import annotations

import json
from pathlib import Path

import hunter_merge_readiness_v2 as readiness

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "configs" / "governance_cutover_target.json"


def _target() -> dict:
    return json.loads(TARGET.read_text(encoding="utf-8"))


def test_cutover_target_has_one_final_aggregator_and_no_legacy_governance_gate() -> None:
    target = _target()
    required = target["required_status_checks_after_cutover"]
    assert required == ["Quality Gates", "dependency-review", "CodeQL", "Hunter Merge Readiness"]
    assert "Hunter Governance Review" not in required
    assert target["retired_required_status_checks"] == ["Hunter Governance Review"]


def test_internal_orchestration_is_not_promoted_to_required_merge_gates() -> None:
    target = _target()
    assert not set(target["internal_workflows_not_merge_gates"]) & set(target["required_status_checks_after_cutover"])


def test_cutover_target_removes_ruleset_bypass_actors() -> None:
    assert _target()["bypass_actors_after_cutover"] == []


def test_merge_readiness_directly_owns_code_security_gate_inputs() -> None:
    assert readiness.REQUIRED_CHECKS == ("Quality Gates", "dependency-review", "CodeQL")


def test_legacy_governance_status_is_compatibility_only() -> None:
    observation = readiness.StaticReadinessObservation(
        check_runs=tuple(
            {"id": i, "name": name, "status": "completed", "conclusion": "success"}
            for i, name in enumerate(readiness.REQUIRED_CHECKS, start=1)
        ),
        governance_status={"id": 999, "state": "failure"},
        review_authority=("success", "VALID_AGENT_REVIEW: exact-head authority"),
    )
    assert readiness.evaluate(observation).state == "success"
