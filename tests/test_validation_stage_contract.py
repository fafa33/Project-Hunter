"""Issue #415: every validation stage owns one thing, and no stage duplicates another.

``docs/VALIDATION_STAGE_CONTRACT.json`` is the executable half of that contract.
These fixtures hold the wiring to it: a document that says pre-push never runs
the full repository suite is worth nothing unless pre-push actually does not,
and a document naming one authoritative full proof is worth nothing unless
exactly one stage claims it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hunter_pr_preflight as preflight
import hunter_pre_push
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_JSON = ROOT / "docs" / "VALIDATION_STAGE_CONTRACT.json"
CONTRACT_MD = ROOT / "docs" / "VALIDATION_STAGE_CONTRACT.md"
INSTRUCTION_SURFACES = (
    ROOT / "CLAUDE.md",
    ROOT / ".github" / "instructions" / "project-hunter.instructions.md",
)

PIPELINE_ORDER = (
    "focused-development-verification",
    "pre-push-safety",
    "hosted-full-exact-head-proof",
    "candidate-admission",
    "pull-request-integration-compatibility",
    "merge-readiness",
    "human-merge-approval",
)


def _contract() -> dict[str, Any]:
    document = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _stages() -> list[dict[str, Any]]:
    stages = _contract()["stages"]
    assert isinstance(stages, list) and stages
    return stages


def _stage(stage_id: str) -> dict[str, Any]:
    return next(stage for stage in _stages() if stage["id"] == stage_id)


def test_contract_declares_the_whole_pipeline_in_order() -> None:
    assert tuple(stage["id"] for stage in _stages()) == PIPELINE_ORDER


def test_every_stage_names_an_owner_a_proof_and_a_prohibition() -> None:
    for stage in _stages():
        for field in ("owner", "boundary", "proof", "full_repository_suite", "must_not"):
            assert isinstance(stage.get(field), str) and stage[field].strip(), (stage["id"], field)


def test_every_declared_suite_policy_is_a_defined_semantic() -> None:
    semantics = _contract()["full_repository_suite_semantics"]
    for stage in _stages():
        assert stage["full_repository_suite"] in semantics, stage["id"]


def test_exactly_one_stage_owns_the_authoritative_full_repository_proof() -> None:
    """Two stages claiming `always` is the duplication Issue #415 removed."""
    always = [stage["id"] for stage in _stages() if stage["full_repository_suite"] == "always"]

    assert always == ["hosted-full-exact-head-proof"]


def test_reuse_semantics_name_their_authority_and_fail_closed() -> None:
    reuse = _contract()["reuse_semantics"]

    assert reuse["authority"] == "scripts/hunter_validation_receipt.py"
    assert (ROOT / reuse["authority"]).is_file()
    assert "fail_closed" in reuse


def test_documented_contract_covers_every_declared_stage() -> None:
    text = CONTRACT_MD.read_text(encoding="utf-8")

    for stage in _stages():
        assert stage["id"] in text, stage["id"]


def test_agent_instruction_surfaces_point_at_the_stage_contract() -> None:
    """An agent cannot honour an ownership contract it is never pointed at."""
    for path in INSTRUCTION_SURFACES:
        assert "docs/VALIDATION_STAGE_CONTRACT.md" in path.read_text(encoding="utf-8"), path


def test_push_safety_lane_is_the_normal_gate_chain_without_the_full_suite() -> None:
    assert preflight.PUSH_SAFETY_GATES == preflight.NORMAL_QUALITY_GATES[:-1]
    assert preflight.NORMAL_QUALITY_GATES[-1] == preflight.PYTEST_GATE
    assert preflight.PYTEST_GATE not in preflight.PUSH_SAFETY_GATES


def test_pre_push_declares_and_runs_no_full_repository_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """The declaration and the wiring are asserted together, in one fixture.

    Checking only the manifest would let the document drift away from the hook;
    checking only the hook would let the document keep promising something the
    repository no longer honours.
    """
    assert _stage("pre-push-safety")["full_repository_suite"] == "never"

    executed: list[tuple[str, tuple[str, ...]]] = []

    def record(gates: Any) -> int:
        executed.extend(tuple(gate) for gate in gates)
        return 0

    monkeypatch.setattr(preflight, "run_quality_gates", record)

    assert hunter_pre_push._run_push_safety_lane() == 0
    assert executed == [tuple(gate) for gate in preflight.PUSH_SAFETY_GATES]
    assert preflight.PYTEST_GATE not in executed


def test_no_op_push_runs_no_validation_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Everything up-to-date` mutates nothing, so it needs no proof.

    Issue #415 forbids manufacturing a validation identity for an unchanged
    head -- with a synthetic run, and above all with an empty commit.
    """

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a no-op push must not launch any validation")

    monkeypatch.setattr(hunter_pre_push, "_run_git", unexpected)
    monkeypatch.setattr(hunter_pre_push, "_run_push_safety_lane", unexpected)
    monkeypatch.setattr(hunter_pre_push.subprocess, "run", unexpected)

    assert hunter_pre_push.enforce_pre_push([]) == 0
    assert hunter_pre_push.enforce_pre_push(["\n", "   \n"]) == 0


def test_deleting_a_remote_branch_runs_no_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a delete push must not launch any validation")

    monkeypatch.setattr(hunter_pre_push, "_run_git", unexpected)
    monkeypatch.setattr(hunter_pre_push, "_run_push_safety_lane", unexpected)

    deletion = [f"(delete) {hunter_pre_push.ZERO_SHA} refs/heads/feature {'a' * 40}\n"]

    assert hunter_pre_push.enforce_pre_push(deletion) == 0


def test_tests_first_red_still_proves_its_red_result_at_the_push_boundary() -> None:
    """The one lane where Pytest belongs locally: the RED result *is* the proof."""
    assert "exception" in _stage("pre-push-safety")
    assert hunter_pre_push._preflight_command(hunter_pre_push.TESTS_FIRST_RED_MODE) == (
        "python",
        "scripts/hunter_pr_preflight.py",
        "--mode",
        "tests-first-red",
    )


def test_hosted_stage_owns_the_workflow_candidate_admission_reads() -> None:
    stage = _stage("hosted-full-exact-head-proof")

    assert ".github/workflows/hunter-pre-pr-preflight.yml" in stage["owner"]
    assert (ROOT / ".github" / "workflows" / "hunter-pre-pr-preflight.yml").is_file()


def test_integration_stage_reuses_only_on_content_identity() -> None:
    assert _stage("pull-request-integration-compatibility")["full_repository_suite"] == (
        "only-when-content-identity-differs"
    )


PARALLEL_LANE = "-n auto --dist loadfile"
CANONICAL_PREFLIGHT_COMMAND = "python scripts/hunter_pr_preflight.py"


def _canonical_preflight_steps() -> list[tuple[Path, dict[str, Any]]]:
    import yaml

    found: list[tuple[Path, dict[str, Any]]] = []
    workflows = ROOT / ".github" / "workflows"
    for path in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml"))):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        for job in (document.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                if isinstance(step, dict) and CANONICAL_PREFLIGHT_COMMAND in str(step.get("run", "")):
                    found.append((path, step))
    return found


def test_every_canonical_preflight_step_declares_the_same_parallel_lane() -> None:
    """The parallel lane is declared per step, so drift between them is the risk."""
    steps = _canonical_preflight_steps()

    assert steps, "expected the canonical preflight to be invoked by a hosted workflow"
    for path, step in steps:
        assert (step.get("env") or {}).get("PYTEST_ADDOPTS") == PARALLEL_LANE, path


def test_repository_pytest_configuration_does_not_pin_the_parallel_lane() -> None:
    """The candidate may not demand a plugin its own trusted validation lacks.

    The trusted default-branch controller executes this repository's `pytest`
    command inside the candidate tree using the *trusted* environment. A
    candidate that pinned worker flags in its own pytest configuration would
    therefore make its own trusted validation unrunnable -- observed on this
    contribution as `pytest: error: unrecognized arguments: -n --dist loadfile`
    from the Trusted Candidate Preflight Validation job. The lane belongs to the
    boundaries that install the plugin, not to the repository's configuration.
    """
    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = configuration.split("[tool.pytest.ini_options]", 1)[1].split("\n[", 1)[0]
    declarations = [
        line for line in section.splitlines() if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]

    assert not [line for line in declarations if line.lstrip().startswith("addopts")], declarations


def test_merge_control_stages_remain_human_terminated() -> None:
    """No stage after merge readiness may be automated away."""
    human = _stage("human-merge-approval")

    assert human["boundary"] == "human"
    assert "auto-merge" in human["must_not"]
