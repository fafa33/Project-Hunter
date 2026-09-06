"""Issue #415: every validation stage owns one thing, and no stage duplicates another.

``docs/VALIDATION_STAGE_CONTRACT.json`` is the executable half of that contract.
These fixtures hold the wiring to it: a document that says pre-push never runs
the full repository suite is worth nothing unless pre-push actually does not,
and a document naming one authoritative full proof is worth nothing unless
exactly one stage claims it.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


#: The lane may change how the suite is distributed across workers. It may not
#: change which tests are selected: an "accelerator" that quietly narrowed the
#: run would be a weaker proof wearing the same name.
#: The lane exactly as the hosted steps declare it, in order, so the fixtures
#: below execute it rather than paraphrase it.
DISTRIBUTION_ONLY_TOKENS_ORDERED = ("-n", "auto", "--dist", "loadfile")
DISTRIBUTION_ONLY_TOKENS = frozenset(DISTRIBUTION_ONLY_TOKENS_ORDERED)

#: How a lane that owns parallel execution provisions the plugin it needs.
DEV_EXTRA_INSTALL = '-e ".[dev]"'
PINNED_CONSTRAINTS = "requirements/ci-constraints.txt"
WORKER_PLUGIN_DISTRIBUTION = "pytest-xdist"


def _jobs_declaring_the_parallel_lane() -> list[tuple[Path, list[dict[str, Any]], int]]:
    """Every (workflow, steps, index) where a step declares the parallel lane."""
    import yaml

    found: list[tuple[Path, list[dict[str, Any]], int]] = []
    workflows = ROOT / ".github" / "workflows"
    for path in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml"))):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        for job in (document.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            steps = [step for step in (job.get("steps") or []) if isinstance(step, dict)]
            for index, step in enumerate(steps):
                if "-n" in str((step.get("env") or {}).get("PYTEST_ADDOPTS", "")).split():
                    found.append((path, steps, index))
    return found


def test_the_parallel_lane_provisions_its_worker_plugin_before_using_it() -> None:
    """A lane may only ask for workers if it is the lane that installs them.

    This is the other half of the placement rule. Keeping the flags out of the
    repository's pytest configuration stops a candidate from imposing the plugin
    on environments that never asked for it; requiring the declaring job to
    install the dev extra first is what stops the flags from being declared
    somewhere that cannot honour them either.
    """
    declaring = _jobs_declaring_the_parallel_lane()

    assert declaring, "expected at least one hosted job to own the parallel full lane"
    for path, steps, index in declaring:
        installs = [
            step
            for step in steps[:index]
            if DEV_EXTRA_INSTALL in str(step.get("run", "")) and PINNED_CONSTRAINTS in str(step.get("run", ""))
        ]
        assert installs, f"{path}: the parallel lane is declared without a preceding pinned dev-extra install"

    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert WORKER_PLUGIN_DISTRIBUTION in configuration, "the worker plugin must be a declared dev dependency"
    constraints = (ROOT / "requirements" / PINNED_CONSTRAINTS.split("/", 1)[1]).read_text(encoding="utf-8")
    assert f"{WORKER_PLUGIN_DISTRIBUTION}==" in constraints, "the worker plugin must be pinned for the hosted lanes"


def test_the_parallel_lane_changes_distribution_and_never_test_selection() -> None:
    """Serial and parallel runs must select the same tests.

    Asserted over the declaration rather than by running the suite twice: the
    tokens are the whole surface, so an option that narrows selection -- `-k`,
    `-m`, `--ignore`, `--deselect`, `-x`, `--lf` or a path -- cannot reach the
    lane without failing here first.
    """
    for path, steps, index in _jobs_declaring_the_parallel_lane():
        tokens = str(steps[index]["env"]["PYTEST_ADDOPTS"]).split()
        assert set(tokens) <= DISTRIBUTION_ONLY_TOKENS, (path, tokens)


def test_an_unavailable_worker_plugin_fails_loudly_rather_than_running_serially() -> None:
    """No silent fallback from the parallel lane to a weaker path.

    `-p no:xdist` reproduces exactly what an environment without the plugin
    does: the options are registered by the plugin, so pytest rejects the
    command line instead of quietly ignoring the flags and running something
    other than the lane that was asked for. This is the same failure the trusted
    controller reported when the flags were pinned repository-wide, and it is
    the behaviour that makes that placement rule enforceable rather than
    advisory.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "--collect-only",
            "-q",
            *DISTRIBUTION_ONLY_TOKENS_ORDERED,
            "tests/test_validation_stage_contract.py",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unrecognized arguments" in (completed.stderr + completed.stdout)


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
