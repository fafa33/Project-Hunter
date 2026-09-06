"""Issue #419: the trusted lane runs the same proof faster, and owns what runs it.

``Trusted Candidate Preflight Validation`` was the last full-suite boundary still
executing serially -- 529.8s of Pytest inside a 599s job on PR #418 -- because the
trusted default-branch environment did not yet own the worker plugin. It does
now, so the lane it declares is its own.

Two things have to be true at once for that to be safe, and every fixture here
holds one of them:

*The trusted environment owns the runner.* It installs ``pytest-xdist`` by name,
under its own pinned constraints, from its own checkout, and verifies it before
declaring anything. The candidate is read into ``./candidate`` and is never
installed from, so no candidate manifest, pin, pytest configuration or plugin can
add, remove, downgrade or substitute what executes its validation.

*The proof does not change.* Parallelism is allowed to change how the suite is
distributed across workers and nothing else. The gate commands are untouched, so
the executed command line is byte-identical to the serial one; the only
difference is an environment variable carrying distribution controls
exclusively, and a declaration that is anything else is refused before a gate
runs -- as is a declaration this environment cannot honour, which would
otherwise degrade into a different execution published under the same name.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hunter_defect_prevention_preflight as prevention
import hunter_governance_review_v2 as governance
import hunter_merge_readiness_v2 as readiness
import hunter_pr_preflight as preflight
import hunter_validation_receipt as receipts
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_WORKFLOW = ROOT / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
CANDIDATE_CHECKOUT_PATH = "candidate"
PINNED_CONSTRAINTS = "requirements/ci-constraints.txt"

#: Everything a distribution control may be. The lane is checked against this
#: rather than against a blocklist: an unknown token is refused by construction,
#: so a future option that narrows selection cannot arrive unnoticed.
DISTRIBUTION_ONLY_TOKENS = frozenset({"-n", "auto", "--dist", "loadfile"})

#: Options that decide *which* tests run. None may reach the trusted lane.
SELECTION_CHANGING_OPTIONS = (
    "-k",
    "-m",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--lf",
    "--last-failed",
    "--ff",
    "--failed-first",
    "--nf",
    "--new-first",
    "--sw",
    "--stepwise",
    "-x",
    "--exitfirst",
    "--maxfail",
    "--collect-only",
    "--pyargs",
)


def _workflow() -> dict[Any, Any]:
    """The parsed trusted workflow.

    Keyed by ``Any`` deliberately: YAML resolves the bare ``on:`` trigger block to
    the boolean ``True``, so a str-keyed mapping would not describe this document.
    """
    document = yaml.safe_load(TRUSTED_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _validation_steps() -> list[dict[str, Any]]:
    job = _workflow()["jobs"]["validate-candidate"]
    steps = [step for step in job["steps"] if isinstance(step, dict)]
    assert steps
    return steps


def _step_index(predicate: Any) -> int:
    steps = _validation_steps()
    matches = [index for index, step in enumerate(steps) if predicate(step)]
    assert len(matches) == 1, matches
    return matches[0]


def _declares_lane(step: dict[str, Any]) -> bool:
    return "PYTEST_ADDOPTS" in (step.get("env") or {})


def _provisions_runner(step: dict[str, Any]) -> bool:
    run = str(step.get("run", ""))
    return "pip install" in run and prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION in run


def _verifies_runner(step: dict[str, Any]) -> bool:
    return "--verify-parallel-runner" in str(step.get("run", ""))


def _environment_without_the_lane() -> dict[str, str]:
    """A child environment that inherits nothing about parallelism.

    These fixtures run inside a suite that may itself have been launched with the
    lane declared, and an inherited declaration would quietly decide the very
    thing being measured.
    """
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    return env


def _collected_node_ids(*arguments: str, cwd: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
        cwd=cwd,
        env=_environment_without_the_lane(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return tuple(line.strip() for line in completed.stdout.splitlines() if "::" in line)


# ---------------------------------------------------------------------------
# A. the trusted lane owns and provisions its runner
# ---------------------------------------------------------------------------


def test_the_trusted_workflow_provisions_the_runner_by_name_from_its_own_checkout() -> None:
    """Named explicitly, pinned by the trusted tree, installed at the trusted root.

    Arriving incidentally with the dev extra is not ownership: it would make the
    lane depend on a transitive detail nobody declared, and would say nothing
    about *which* runner the trusted environment intends to execute under.
    """
    step = _validation_steps()[_step_index(_provisions_runner)]
    run = str(step["run"])

    assert PINNED_CONSTRAINTS in run
    assert "working-directory" not in step
    assert CANDIDATE_CHECKOUT_PATH not in run

    constraints = (ROOT / PINNED_CONSTRAINTS).read_text(encoding="utf-8")
    assert f"{prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION}==" in constraints
    assert prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_trusted_workflow_verifies_the_runner_before_declaring_the_lane() -> None:
    """Verification is a step of its own, ahead of the gate chain.

    Discovering the absence as an argument-parsing error inside the gate chain
    would still fail closed, but it would fail as though the candidate's suite
    were at fault. The job says what is actually wrong, before it starts.
    """
    assert _step_index(_verifies_runner) < _step_index(_declares_lane)
    assert _step_index(_provisions_runner) <= _step_index(_verifies_runner)


def test_the_trusted_gate_chain_step_declares_the_canonical_lane() -> None:
    step = _validation_steps()[_step_index(_declares_lane)]

    assert "--run-candidate-gates" in str(step["run"])
    assert tuple(str(step["env"]["PYTEST_ADDOPTS"]).split()) == prevention.TRUSTED_PARALLEL_LANE


def test_a_provisioned_runner_reports_its_resolved_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert prevention.verify_trusted_parallel_runner() == 0

    reported = capsys.readouterr().out
    assert "PASS" in reported
    assert metadata.version(prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION) in reported


# ---------------------------------------------------------------------------
# B / C. the repository's own configuration stays runnable without the runner
# ---------------------------------------------------------------------------


def test_repository_pytest_configuration_forces_no_worker_flags() -> None:
    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = configuration.split("[tool.pytest.ini_options]", 1)[1].split("\n[", 1)[0]
    declarations = [
        line for line in section.splitlines() if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]

    assert not [line for line in declarations if line.lstrip().startswith("addopts")], declarations
    for token in ("-n", "--dist", "-p xdist"):
        assert token not in " ".join(declarations), declarations


def test_a_clean_environment_without_the_runner_can_still_run_this_repository() -> None:
    """The trusted lane is opt-in, so the repository must not require it.

    ``-p no:xdist`` reproduces an environment where the plugin was never
    installed: its options are unregistered, so anything demanding them fails the
    command line. Collection succeeding here is what proves the demand lives in
    the boundaries that install the plugin and nowhere else.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:xdist", "--collect-only", "-q", "tests/test_hunter_pre_push.py"],
        cwd=ROOT,
        env=_environment_without_the_lane(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------
# D / E. the lane requires the runner, and its absence is loud
# ---------------------------------------------------------------------------


def test_the_trusted_lane_tokens_require_the_worker_plugin() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "--collect-only",
            "-q",
            *prevention.TRUSTED_PARALLEL_LANE,
            "tests/test_hunter_pre_push.py",
        ],
        cwd=ROOT,
        env=_environment_without_the_lane(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "unrecognized arguments" in (completed.stdout + completed.stderr)


def _without_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make only the worker plugin look absent, leaving every other import alone."""
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == prevention.TRUSTED_PARALLEL_RUNNER_PLUGIN:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(prevention.importlib.util, "find_spec", find_spec)


def test_a_missing_trusted_runner_fails_verification_loudly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _without_the_runner(monkeypatch)

    assert prevention.verify_trusted_parallel_runner() == 2

    reported = capsys.readouterr().out
    assert prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION in reported
    assert "must not silently run serially" in reported


def test_a_runner_that_imports_but_is_not_installed_is_still_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An importable module with no distribution is not a provisioned dependency."""
    real_version = metadata.version

    def version(name: str) -> str:
        if name == prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION:
            raise metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(prevention.metadata, "version", version)

    problem = prevention.trusted_parallel_runner_problem()

    assert problem is not None and "not an installed distribution" in problem


def test_a_missing_trusted_runner_fails_the_declared_lane_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No gate runs at all, rather than the whole chain running a different way."""
    _without_the_runner(monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", " ".join(prevention.TRUSTED_PARALLEL_LANE))

    def must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a lane the environment cannot honour must not execute any gate")

    monkeypatch.setattr(prevention.subprocess, "run", must_not_run)

    assert prevention.run_candidate_quality_gates(tmp_path) == 2

    reported = capsys.readouterr().out
    assert "does not fall back to a different execution" in reported


# ---------------------------------------------------------------------------
# F / G / H. distribution changes, selection does not
# ---------------------------------------------------------------------------


def test_the_trusted_lane_carries_distribution_controls_only() -> None:
    assert set(prevention.TRUSTED_PARALLEL_LANE) <= DISTRIBUTION_ONLY_TOKENS

    lane = set(prevention.TRUSTED_PARALLEL_LANE)
    for option in SELECTION_CHANGING_OPTIONS:
        assert option not in lane, option
    assert not [token for token in prevention.TRUSTED_PARALLEL_LANE if token.endswith(".py") or "/" in token]


@pytest.mark.parametrize(
    "declared",
    [
        "-n auto --dist loadfile -k governance",
        "-n auto --dist loadfile -m 'not slow'",
        "-n auto --dist loadfile --ignore=tests/test_market_validation.py",
        "-n auto --dist loadfile --deselect tests/test_hunter_pre_push.py::test_x",
        "-n auto --dist loadfile -x",
        "-n auto --dist loadfile --maxfail=1",
        "-n auto --dist loadfile --lf",
        "-n auto --dist loadfile tests/test_hunter_pre_push.py",
        "-n 1 --dist loadfile",
        "--dist loadfile",
        "-p no:randomly",
    ],
)
def test_a_lane_that_is_not_the_canonical_distribution_set_is_refused(declared: str) -> None:
    """Selection can only narrow through this one surface, so the surface is exact.

    Equality against the canonical sequence, not a blocklist: a lane assembled
    from options nobody thought to forbid is refused for the same reason as one
    assembled from options everybody would.
    """
    problem = prevention.trusted_lane_problem(declared)

    assert problem is not None
    assert "canonical trusted" in problem


def test_the_canonical_lane_is_accepted_in_a_provisioned_environment() -> None:
    assert prevention.trusted_lane_problem(" ".join(prevention.TRUSTED_PARALLEL_LANE)) is None
    assert prevention.trusted_lane_problem("") is None


def test_the_trusted_lane_selects_exactly_what_the_serial_lane_selects(tmp_path: Path) -> None:
    """Measured, not asserted: collection is run both ways and compared.

    A tree of its own rather than this repository's, so the comparison is cheap
    and deterministic -- what is under test is the effect of the lane's tokens on
    selection, which does not depend on how many tests exist.
    """
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    (tests / "test_first.py").write_text(
        "import pytest\n\n\ndef test_one() -> None:\n    pass\n\n\n"
        '@pytest.mark.parametrize("value", [1, 2, 3])\ndef test_many(value: int) -> None:\n    pass\n',
        encoding="utf-8",
    )
    (tests / "test_second.py").write_text(
        "def test_alpha() -> None:\n    pass\n\n\ndef test_beta() -> None:\n    pass\n",
        encoding="utf-8",
    )

    serial = _collected_node_ids(cwd=tmp_path)
    parallel = _collected_node_ids(*prevention.TRUSTED_PARALLEL_LANE, cwd=tmp_path)

    assert serial
    assert set(parallel) == set(serial)
    assert len(parallel) == len(serial)


def test_the_trusted_gate_commands_are_unchanged_by_the_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The lane is an environment variable, so proof scope cannot drift with it.

    Every gate the trusted controller launches under the declared lane is the
    same command it launches without one -- including ``pytest`` itself, which is
    still exactly the gate the candidate definition has to match.
    """
    executed: list[tuple[str, ...]] = []

    def fake_run(command: Any, *, cwd: Any, env: Any, check: Any) -> Any:
        executed.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prevention.subprocess, "run", fake_run)

    monkeypatch.setenv("PYTEST_ADDOPTS", " ".join(prevention.TRUSTED_PARALLEL_LANE))
    assert prevention.run_candidate_quality_gates(tmp_path) == 0
    parallel = list(executed)

    executed.clear()
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert prevention.run_candidate_quality_gates(tmp_path) == 0

    assert parallel == executed
    assert parallel == [command for _name, command in prevention.TRUSTED_CANDIDATE_QUALITY_GATES]
    assert parallel[-1] == ("pytest",)
    assert prevention.TRUSTED_CANDIDATE_QUALITY_GATES[-1] == preflight.NORMAL_QUALITY_GATES[-1]


def test_the_trusted_and_candidate_gate_definitions_still_have_to_agree() -> None:
    """Parallelism must not become a reason for the two definitions to diverge."""
    assert prevention.TRUSTED_CANDIDATE_QUALITY_GATES == preflight.NORMAL_QUALITY_GATES
    assert tuple(name for name, _command in prevention.TRUSTED_CANDIDATE_QUALITY_GATES) == (
        prevention.REQUIRED_PREFLIGHT_GATES
    )


# ---------------------------------------------------------------------------
# B (enforced). the candidate's own pytest configuration cannot rewrite its proof
# ---------------------------------------------------------------------------


def _candidate_tree(tmp_path: Path) -> Path:
    """A minimal candidate whose preflight definition is already valid.

    So that anything these fixtures report comes from the configuration under
    test and not from an incomplete tree.
    """
    root = tmp_path / "candidate"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".github" / "workflows" / "hunter-pre-pr-preflight.yml").write_text(
        "run: python scripts/hunter_pr_preflight.py --mode normal\n", encoding="utf-8"
    )
    (root / "scripts" / "hunter_pr_preflight.py").write_text(
        f"NORMAL_QUALITY_GATES = {prevention.TRUSTED_CANDIDATE_QUALITY_GATES!r}\n"
        "def run_preflight():\n"
        "    return run_quality_gates(NORMAL_QUALITY_GATES)\n",
        encoding="utf-8",
    )
    return root


def _pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ('"-n auto"', "-n"),
        ('"-nauto"', "-n"),
        ('"--numprocesses=4"', "--numprocesses"),
        ('"--dist loadfile"', "--dist"),
        ('"-k governance"', "-k"),
        ('"-kgovernance"', "-k"),
        ("\"-m 'not slow'\"", "-m"),
        ('"--ignore=tests/test_market_validation.py"', "--ignore"),
        ('"--deselect tests/test_x.py::test_y"', "--deselect"),
        ('"--maxfail=1"', "--maxfail"),
        ('"-x"', "-x"),
        ('"--lf"', "--lf"),
        ('"--collect-only"', "--collect-only"),
        ('["-ra", "-n", "auto"]', "-n"),
        ('"-ra --strict-markers -k slow"', "-k"),
        ('"-m_not_an_option"', "-m"),
    ],
)
def test_a_candidate_addopt_that_rewrites_its_own_validation_is_refused(
    tmp_path: Path, declared: str, expected: str
) -> None:
    """Worker demands and selection narrowing, in every spelling pytest accepts.

    Including the attached forms. ``-m_not_an_option`` reads as a marker
    expression to pytest, not as an unknown option, so it is a declaration like
    any other and is refused like one.
    """
    root = _candidate_tree(tmp_path)
    _pyproject(root, f"[tool.pytest.ini_options]\naddopts = {declared}\n")

    errors = prevention.validate_candidate_preflight_definition(root)

    assert [error for error in errors if f"addopts declares {expected}" in error], errors


@pytest.mark.parametrize(
    "declared",
    [
        '"-ra"',
        '"--strict-markers --strict-config"',
        '"--durations=25"',
        '"-p no:cacheprovider"',
        '"--color=yes -q"',
        '["-ra", "--strict-markers"]',
        '"--markers"',
        '"--no-header"',
        '"--maxprocesses"',
    ],
)
def test_a_candidate_addopt_that_changes_nothing_selective_is_allowed(tmp_path: Path, declared: str) -> None:
    """The refusal must not become a ban on having any addopts at all.

    A guard that rejected canonically valid configuration would be a defect of
    its own, so the allowed cases are pinned as tightly as the refused ones. The
    last three are deliberate near-misses: long options are matched whole, so
    sharing a prefix with ``--maxfail``, ``--nf`` or ``--numprocesses`` is not
    sharing an option with them.
    """
    root = _candidate_tree(tmp_path)
    _pyproject(root, f"[tool.pytest.ini_options]\naddopts = {declared}\n")

    assert prevention.validate_candidate_preflight_definition(root) == []


def test_the_declaration_is_found_in_every_configuration_source_pytest_reads(tmp_path: Path) -> None:
    """Moving the declaration one file sideways is not a way past the rule."""
    for relative, body in (
        ("pytest.ini", "[pytest]\naddopts = -k governance\n"),
        ("tox.ini", "[pytest]\naddopts = -n auto\n"),
        ("setup.cfg", "[tool:pytest]\naddopts = --ignore=tests\n"),
    ):
        root = _candidate_tree(tmp_path / relative.replace(".", "_"))
        (root / relative).write_text(body, encoding="utf-8")

        errors = prevention.validate_candidate_preflight_definition(root)

        assert [error for error in errors if "addopts declares" in error], (relative, errors)


def test_a_declaration_bound_to_another_tool_or_section_is_not_this_rule(tmp_path: Path) -> None:
    """Wrong-section and wrong-key structure that merely resembles the real one."""
    root = _candidate_tree(tmp_path)
    _pyproject(
        root,
        '[tool.other]\naddopts = "-n auto"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\nmarkers = ["slow: -k slow is not an addopt"]\n',
    )
    (root / "setup.cfg").write_text("[flake8]\naddopts = -k governance\n", encoding="utf-8")

    assert prevention.validate_candidate_preflight_definition(root) == []


def test_a_value_that_merely_looks_like_an_option_is_not_one(tmp_path: Path) -> None:
    """Quoted text is a value, not an argument position.

    ``-m`` inside a quoted marker expression, or a path containing an option-like
    substring, must not be read as a declaration -- a rule that matched raw text
    would report both.
    """
    root = _candidate_tree(tmp_path)
    _pyproject(root, '[tool.pytest.ini_options]\naddopts = "--color=yes --rootdir=/tmp/-k-not-an-option"\n')

    assert prevention.validate_candidate_preflight_definition(root) == []


def test_unreadable_candidate_pytest_configuration_fails_closed(tmp_path: Path) -> None:
    """A source that cannot be parsed is an error, never a silent pass."""
    root = _candidate_tree(tmp_path)
    _pyproject(root, "[tool.pytest.ini_options\naddopts = broken\n")

    errors = prevention.validate_candidate_preflight_definition(root)

    assert [error for error in errors if "unreadable" in error], errors


def test_a_candidate_with_no_pytest_configuration_at_all_is_accepted(tmp_path: Path) -> None:
    assert prevention.validate_candidate_preflight_definition(_candidate_tree(tmp_path)) == []


def test_this_repository_declares_nothing_the_trusted_controller_would_refuse() -> None:
    """The rule is checked against the tree it will actually be applied to."""
    assert prevention.validate_candidate_pytest_configuration(ROOT) == []


# ---------------------------------------------------------------------------
# I / J. the candidate cannot reach the trusted runner
# ---------------------------------------------------------------------------


def test_no_candidate_file_can_reach_the_trusted_runner_provisioning() -> None:
    """Everything installed comes from the trusted root; the candidate is read-only.

    The candidate is checked out to a subdirectory and is only ever passed as an
    argument to trusted code. No step installs from it, changes into it, or runs
    with it as a working directory, so its manifests, pins and pytest
    configuration cannot decide what executes its own validation.
    """
    steps = _validation_steps()
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")]
    assert [str((step.get("with") or {}).get("path", "")) for step in checkouts] == ["", CANDIDATE_CHECKOUT_PATH]

    for step in steps:
        run = str(step.get("run", ""))
        assert "working-directory" not in step, step.get("name")
        assert f"cd {CANDIDATE_CHECKOUT_PATH}" not in run, step.get("name")
        if "pip install" in run:
            assert CANDIDATE_CHECKOUT_PATH not in run, step.get("name")
            assert PINNED_CONSTRAINTS in run, step.get("name")


def test_a_candidate_cannot_override_the_runner_its_own_validation_uses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hostile candidate tree changes nothing about the trusted decision.

    The runner is resolved from the interpreter that is about to launch the gate
    chain -- the trusted environment -- so a candidate that pins another version,
    removes the dependency, disables the plugin in its own configuration or ships
    a same-named module of its own is answered identically to one that does none
    of it.
    """
    hostile = tmp_path / "candidate"
    (hostile / "requirements").mkdir(parents=True)
    (hostile / "pyproject.toml").write_text(
        '[project.optional-dependencies]\ndev = ["pytest"]\n\n' '[tool.pytest.ini_options]\naddopts = "-p no:xdist"\n',
        encoding="utf-8",
    )
    (hostile / "requirements" / "ci-constraints.txt").write_text("pytest-xdist==0.0.1\n", encoding="utf-8")
    (hostile / "xdist.py").write_text("raise RuntimeError('candidate runner')\n", encoding="utf-8")

    baseline = prevention.trusted_parallel_runner_problem()
    monkeypatch.chdir(hostile)

    assert prevention.trusted_parallel_runner_problem() == baseline
    assert prevention.trusted_lane_problem(" ".join(prevention.TRUSTED_PARALLEL_LANE)) is None
    assert metadata.version(prevention.TRUSTED_PARALLEL_RUNNER_DISTRIBUTION) != "0.0.1"


def test_the_runner_decision_reads_no_candidate_state() -> None:
    """Signature-level: neither entry point is even given the candidate to read."""
    assert not inspect.signature(prevention.trusted_parallel_runner_problem).parameters
    assert list(inspect.signature(prevention.trusted_lane_problem).parameters) == ["addopts"]


# ---------------------------------------------------------------------------
# K / L. proof identity and fail-closed refusals are untouched
# ---------------------------------------------------------------------------


def test_exact_head_proof_identity_is_unchanged() -> None:
    """Speed changed; what a proof is a proof *of* did not."""
    assert receipts.RECEIPT_SCHEMA == "hunter.validation-receipt/1"
    assert receipts.FULL_LANE == "full"
    assert receipts.TOOLCHAIN_DISTRIBUTIONS == ("black", "mypy", "pytest", "ruff")
    assert receipts.DEFINITION_PATHS == (
        ".github/workflows/ci.yml",
        ".github/workflows/hunter-pre-pr-preflight.yml",
        "pyproject.toml",
        "requirements/ci-constraints.txt",
        "scripts/hunter_architecture_index_preflight.py",
        "scripts/hunter_artifact_preflight.py",
        "scripts/hunter_defect_prevention_preflight.py",
        "scripts/hunter_pr_preflight.py",
        "scripts/hunter_validation_receipt.py",
        "tests/conftest.py",
    )

    head_sha = inspect.signature(receipts.verify).parameters["head_sha"]
    assert head_sha.kind is inspect.Parameter.KEYWORD_ONLY
    assert head_sha.default is inspect.Parameter.empty


def test_the_trusted_workflow_still_binds_the_exact_candidate_head() -> None:
    text = TRUSTED_WORKFLOW.read_text(encoding="utf-8")

    assert "ref: ${{ github.event.pull_request.head.sha }}" in text
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in text
    assert _workflow()["name"] == governance.TRUSTED_UPGRADE_WORKFLOW_NAME
    assert TRUSTED_WORKFLOW.relative_to(ROOT).as_posix() == governance.TRUSTED_UPGRADE_WORKFLOW_PATH
    assert list(_workflow()[True]["pull_request_target"]["branches"]) == ["main"]
    assert _workflow()["jobs"]["publish-proof"]["permissions"]["statuses"] == "write"
    assert _workflow()["jobs"]["validate-candidate"]["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"head_sha": "b" * 40}, "foreign"),
        ({"content_identity": "tree:" + "c" * 40}, "content identity"),
        ({"definition_identity": "sha256:mismatch"}, "definition changed"),
        ({"toolchain_identity": "sha256:mismatch"}, "toolchain changed"),
        ({"result": receipts.FAILED}, "not a proof"),
        ({"lane": "push-safety"}, "not the full repository lane"),
    ],
)
def test_a_proof_that_is_not_this_candidates_still_fails_closed(mutation: dict[str, Any], expected: str) -> None:
    from datetime import UTC, datetime

    expected_identity = receipts.ValidationIdentity(
        content="tree:" + "a" * 40,
        definition="sha256:definition",
        toolchain="sha256:toolchain",
    )
    fields: dict[str, Any] = {
        "lane": receipts.FULL_LANE,
        "result": receipts.PASSED,
        "head_sha": "a" * 40,
        "content_identity": expected_identity.content,
        "definition_identity": expected_identity.definition,
        "toolchain_identity": expected_identity.toolchain,
        "produced_at": datetime.now(UTC),
        "produced_by": "trusted-lane",
    }
    fields.update(mutation)

    refusal = receipts.verify(receipts.ValidationReceipt(**fields), expected_identity, head_sha="a" * 40)

    assert refusal is not None and expected in refusal


# ---------------------------------------------------------------------------
# M / N. the controllers downstream behave exactly as before
# ---------------------------------------------------------------------------

HEAD = "a" * 40
PR = 419
REPO = "fafa33/Project-Hunter"
RUN_ID = 34032068648


def _active_run(**overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": RUN_ID,
        "name": governance.TRUSTED_UPGRADE_WORKFLOW_NAME,
        "path": governance.TRUSTED_UPGRADE_WORKFLOW_PATH,
        "event": "pull_request_target",
        "head_sha": HEAD,
        "status": "in_progress",
        "conclusion": None,
        "pull_requests": [{"number": PR, "head": {"sha": HEAD}}],
    }
    run.update(overrides)
    return run


def _install(monkeypatch: pytest.MonkeyPatch, *, statuses: Any, runs: Any) -> None:
    def fake_request_json(repository: str, token: str, method: str, path: str, payload: Any = None) -> Any:
        if path.startswith("commits/"):
            return statuses
        if path.startswith("actions/runs?"):
            return runs
        raise AssertionError(f"unexpected request path: {path}")

    monkeypatch.setattr(governance, "request_json", fake_request_json)


def test_a_running_parallel_trusted_proof_is_still_reported_as_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #417 semantics survive: the lane got faster, not differently shaped.

    The trusted workflow still publishes its status from its final job, so the
    window in which no status exists is shorter but not gone. It must still read
    as a dependency wait rather than as a defect.
    """
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_active_run()]})

    assert governance.read_trusted_upgrade_status(REPO, "token", HEAD, PR) == (
        "pending",
        governance.TRUSTED_PROOF_WAITING_DESCRIPTION,
    )


def test_waiting_still_satisfies_neither_admission_nor_merge_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_active_run()]})
    state, _description = governance.read_trusted_upgrade_status(REPO, "token", HEAD, PR)

    assert state != "success"

    decision = readiness.evaluate(
        readiness.StaticReadinessObservation(
            draft=False,
            mergeable=True,
            check_runs=tuple(
                {"id": index, "name": name, "status": "completed", "conclusion": "success"}
                for index, name in enumerate(readiness.REQUIRED_CHECKS, start=1)
            ),
            governance_status={"id": 9, "state": state},
        )
    )

    assert decision.state == "pending"
    assert readiness.GOVERNANCE_CONTEXT in decision.description


def test_no_eligible_trusted_run_is_still_a_missing_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, statuses=[], runs={"workflow_runs": []})

    state, description = governance.read_trusted_upgrade_status(REPO, "token", HEAD, PR)

    assert state == "missing"
    assert "missing" in description


def test_a_foreign_head_run_never_becomes_a_wait_for_this_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, statuses=[], runs={"workflow_runs": [_active_run(head_sha="b" * 40)]})

    state, _description = governance.read_trusted_upgrade_status(REPO, "token", HEAD, PR)

    assert state == "missing"


def test_the_governance_identity_predicate_still_matches_this_workflow() -> None:
    """The workflow edit must not have moved it out of what governance recognises."""
    assert governance.is_trusted_upgrade_run(_active_run(), HEAD)
    assert governance.is_trusted_upgrade_run_bound_to(_active_run(), HEAD, PR)
    assert governance.TRUSTED_RUN_ACTIVE_STATES == frozenset(
        {"queued", "in_progress", "waiting", "requested", "pending"}
    )
    assert governance.TRUSTED_PROOF_WAITING_DESCRIPTION == "Waiting for trusted exact-head preflight proof"
