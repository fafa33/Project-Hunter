from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hunter_pr_preflight
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_COMMAND = "python scripts/hunter_pr_preflight.py"


def _load_workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _preflight_checkout_depths(workflow: dict[str, Any]) -> list[object]:
    depths: list[object] = []
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        return depths

    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or PREFLIGHT_COMMAND not in run:
                continue

            checkout: dict[str, Any] | None = None
            for prior in reversed(steps[:index]):
                if not isinstance(prior, dict):
                    continue
                uses = prior.get("uses")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    checkout = prior
                    break

            if checkout is None:
                depths.append(None)
                continue
            with_config = checkout.get("with", {})
            if not isinstance(with_config, dict):
                depths.append(None)
                continue
            depths.append(with_config.get("fetch-depth"))
    return depths


def test_normal_quality_gate_order_matches_ci_contract() -> None:
    assert hunter_pr_preflight.NORMAL_QUALITY_GATES == (
        ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
        ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
        ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
        ("Ruff", ("ruff", "check", ".")),
        ("Black", ("black", "--check", "--diff", ".")),
        ("Mypy", ("mypy",)),
        ("Pytest", ("pytest",)),
    )
    assert hunter_pr_preflight.QUALITY_GATES == hunter_pr_preflight.NORMAL_QUALITY_GATES


def test_tests_first_red_hygiene_gate_order_excludes_only_pytest() -> None:
    assert hunter_pr_preflight.TESTS_FIRST_HYGIENE_GATES == (
        ("Architecture Index Guard", ("python", "scripts/hunter_architecture_index_preflight.py")),
        ("Artifact Guard", ("python", "scripts/hunter_artifact_preflight.py")),
        ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
        ("Ruff", ("ruff", "check", ".")),
        ("Black", ("black", "--check", "--diff", ".")),
        ("Mypy", ("mypy",)),
    )


def test_preflight_fails_fast(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 7, 0))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_quality_gates(
        (
            ("first", ("one",)),
            ("second", ("two",)),
            ("third", ("three",)),
        )
    )

    assert result == 7
    assert calls == [("one",), ("two",)]


def test_preflight_returns_success_when_all_gates_pass(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_quality_gates((("first", ("one",)), ("second", ("two",))))

    assert result == 0
    assert calls == [("one",), ("two",)]


def test_tests_first_red_succeeds_only_for_clean_hygiene_and_red_pytest(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 0, 0, 0, 0, 0, 1))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 0
    assert calls == [
        ("python", "scripts/hunter_architecture_index_preflight.py"),
        ("python", "scripts/hunter_artifact_preflight.py"),
        ("python", "scripts/hunter_defect_prevention_preflight.py"),
        ("ruff", "check", "."),
        ("black", "--check", "--diff", "."),
        ("mypy",),
        ("pytest",),
    ]


def test_tests_first_red_never_launders_architecture_index_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 9
    assert calls == [("python", "scripts/hunter_architecture_index_preflight.py")]


def test_tests_first_red_never_launders_artifact_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 9))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 9
    assert calls == [
        ("python", "scripts/hunter_architecture_index_preflight.py"),
        ("python", "scripts/hunter_artifact_preflight.py"),
    ]


def test_tests_first_red_never_launders_defect_prevention_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 0, 9))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 9
    assert calls == [
        ("python", "scripts/hunter_architecture_index_preflight.py"),
        ("python", "scripts/hunter_artifact_preflight.py"),
        ("python", "scripts/hunter_defect_prevention_preflight.py"),
    ]


def test_tests_first_red_never_launders_hygiene_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 0, 0, 0, 8, 0, 1))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 8
    assert calls == [
        ("python", "scripts/hunter_architecture_index_preflight.py"),
        ("python", "scripts/hunter_artifact_preflight.py"),
        ("python", "scripts/hunter_defect_prevention_preflight.py"),
        ("ruff", "check", "."),
        ("black", "--check", "--diff", "."),
    ]


def test_tests_first_red_fails_closed_when_pytest_is_unexpectedly_green(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    assert hunter_pr_preflight.run_preflight(mode="tests-first-red") != 0
    assert calls[-1] == ("pytest",)


@pytest.mark.parametrize("pytest_exit", [2, 3, 4, 5])
def test_tests_first_red_rejects_non_test_failure_pytest_exits(monkeypatch, pytest_exit: int) -> None:
    return_codes = iter((0, 0, 0, 0, 0, 0, pytest_exit))

    def fake_run(command, *, check):
        assert check is False
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    assert hunter_pr_preflight.run_preflight(mode="tests-first-red") == pytest_exit


def test_unknown_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported preflight mode"):
        hunter_pr_preflight.run_preflight(mode="anything-else")


def test_pre_pr_workflow_reads_machine_readable_mode_marker() -> None:
    text = (ROOT / ".github" / "workflows" / "hunter-pre-pr-preflight.yml").read_text(encoding="utf-8")
    assert ".hunter-preflight-mode" in text
    assert "PREFLIGHT_MODE" in text
    assert "--mode" in text
    assert "tests-first-red" in text


def test_shared_preflight_workflows_fetch_full_history() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    workflow_paths = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    matched: list[Path] = []
    for path in workflow_paths:
        depths = _preflight_checkout_depths(_load_workflow(path))
        if not depths:
            continue
        matched.append(path)
        assert all(str(depth) == "0" for depth in depths), (path, depths)
    assert matched


def test_full_history_must_belong_to_checkout_preceding_preflight() -> None:
    workflow = {
        "jobs": {
            "quality": {
                "steps": [
                    {"uses": "actions/checkout@v7", "with": {"fetch-depth": 1}},
                    {"name": "Unrelated", "with": {"fetch-depth": 0}},
                    {"run": "python scripts/hunter_pr_preflight.py --mode normal"},
                ]
            }
        }
    }
    assert _preflight_checkout_depths(workflow) == [1]


#: The action major each governed workflow must be on.
REQUIRED_ACTION_MAJORS = {"actions/checkout": 7, "actions/setup-python": 6}

#: Either the floating major tag, or an immutable commit pin whose trailing
#: comment names the release it points at. A SHA pin is the stronger form -- a
#: tag can be repointed and a commit cannot -- so the invariant is "on the
#: required major", not "spelled @vN".
_ACTION_REF = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w.-]+)@(?P<ref>\S+)(?:\s*#\s*v(?P<comment_major>\d+)(?:\.\d+)*)?\s*$"
)


def action_major(ref: str, comment_major: str | None) -> int | None:
    """The action major a `uses:` reference resolves to, or None if unreadable.

    A bare `@vN` carries its own major. A 40-hex commit carries none, so the
    version comment beside it is what names the release -- which is why a SHA
    pin without that comment is unreadable here and fails rather than passing
    silently.
    """

    if re.fullmatch(r"v(\d+)(?:\.\d+)*", ref):
        return int(ref[1:].split(".", 1)[0])
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return int(comment_major) if comment_major is not None else None
    return None


def test_workflows_use_current_node24_action_majors() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    workflow_paths = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            match = _ACTION_REF.search(line.strip())
            if match is None:
                continue
            required = REQUIRED_ACTION_MAJORS.get(match.group("action"))
            if required is None:
                continue
            resolved = action_major(match.group("ref"), match.group("comment_major"))
            assert resolved == required, f"{path}: {line.strip()}"


@pytest.mark.parametrize(
    ("ref", "comment_major", "expected"),
    [
        ("v6", None, 6),
        ("v6.3.0", None, 6),
        ("v5", None, 5),
        ("ece7cb06caefa5fff74198d8649806c4678c61a1", "6", 6),
        ("ece7cb06caefa5fff74198d8649806c4678c61a1", "5", 5),
        # A commit pin with no version comment names no release, so it is
        # unreadable rather than assumed current.
        ("ece7cb06caefa5fff74198d8649806c4678c61a1", None, None),
        ("main", None, None),
        ("", None, None),
    ],
)
def test_action_major_resolution(ref: str, comment_major: str | None, expected: int | None) -> None:
    assert action_major(ref, comment_major) == expected


def test_agent_instructions_reference_machine_contract_surfaces() -> None:
    paths = (
        ROOT / ".github" / "instructions" / "project-hunter.instructions.md",
        ROOT / "CLAUDE.md",
    )
    required = (
        "docs/DEFECT_REGISTRY.json",
        ".hunter-preflight-mode",
        "python scripts/hunter_pr_preflight.py --mode normal",
        "python scripts/install_hunter_git_hooks.py",
        ".githooks/pre-push",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for contract_surface in required:
            assert contract_surface in text, (path, contract_surface)


def test_aggregate_mode_runs_all_independent_gates_on_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, capture_output=False, text=False, check=False, timeout=None):
        cmd = tuple(command)
        calls.append(cmd)
        if "ruff" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="ruff error in src/hunter/app.py")
        if "black" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="black diff in src/hunter/app.py")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    exit_code, report = hunter_pr_preflight.run_aggregate_gates(
        gates=(
            ("Ruff", ("ruff", "check", ".")),
            ("Black", ("black", "--check", ".")),
            ("Mypy", ("mypy",)),
        ),
        dependencies={},
    )

    assert exit_code == 1
    assert len([cmd for cmd in calls if cmd[0] != "git"]) == 3
    assert report["overall_outcome"] == "failed"
    outcomes = {g["name"]: g["outcome"] for g in report["gates"]}
    assert outcomes == {"Ruff": "failed", "Black": "failed", "Mypy": "passed"}


def test_aggregate_mode_marks_dependent_gates_blocked(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, capture_output=False, text=False, check=False, timeout=None):
        cmd = tuple(command)
        calls.append(cmd)
        if "defect" in " ".join(cmd):
            return SimpleNamespace(returncode=1, stdout="", stderr="Defect prevention failure")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    exit_code, report = hunter_pr_preflight.run_aggregate_gates(
        gates=(
            ("Defect Prevention Guard", ("python", "scripts/hunter_defect_prevention_preflight.py")),
            ("Ruff", ("ruff", "check", ".")),
            ("Pytest", ("pytest",)),
        ),
        dependencies={"Pytest": ("Defect Prevention Guard",)},
    )

    assert exit_code == 1
    assert [cmd for cmd in calls if cmd[0] != "git"] == [
        ("python", "scripts/hunter_defect_prevention_preflight.py"),
        ("ruff", "check", "."),
    ]
    gate_dict = {g["name"]: g for g in report["gates"]}
    assert gate_dict["Defect Prevention Guard"]["outcome"] == "failed"
    assert gate_dict["Ruff"]["outcome"] == "passed"
    assert gate_dict["Pytest"]["outcome"] == "blocked"
    assert (
        "Blocked by failed or blocked dependent gate(s): Defect Prevention Guard"
        in gate_dict["Pytest"]["error_details"]
    )


def test_aggregate_mode_produces_json_report_file(monkeypatch) -> None:
    def fake_run(command, capture_output=False, text=False, check=False, timeout=None):
        return SimpleNamespace(returncode=0, stdout="pass", stderr="")

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    root = hunter_pr_preflight._repository_root()
    report_rel = "test_aggregate_report_file.json"
    expected_path = hunter_pr_preflight.validate_and_resolve_report_path(report_rel, root)
    if expected_path.exists():
        expected_path.unlink()

    try:
        exit_code = hunter_pr_preflight.run_preflight(
            mode="aggregate",
            report_path=report_rel,
        )

        assert exit_code == 0
        assert expected_path.is_file()

        import json

        data = json.loads(expected_path.read_text(encoding="utf-8"))
        assert data["schema"] == "hunter.preflight-report/1"
        assert data["mode"] == "aggregate"
        assert data["overall_outcome"] == "passed"
        assert len(data["gates"]) == 7
    finally:
        if expected_path.exists():
            expected_path.unlink()


def test_report_path_security_validation(tmp_path) -> None:
    root = hunter_pr_preflight._repository_root()

    # Relative path resolves inside approved root (.hunter/preflight-reports)
    dest = hunter_pr_preflight.validate_and_resolve_report_path("test_out.json", root)
    assert dest.is_relative_to((root / ".hunter" / "preflight-reports").resolve())

    # Rejects absolute path outside approved root
    with pytest.raises(ValueError, match="resolves outside approved report root"):
        hunter_pr_preflight.validate_and_resolve_report_path("/tmp/arbitrary_overwrite.json", root)

    # Rejects .. traversal
    with pytest.raises(ValueError, match="forbidden '..' traversal"):
        hunter_pr_preflight.validate_and_resolve_report_path("../outside.json", root)

    # Rejects target inside protected repository directories like .git or docs
    with pytest.raises(ValueError, match="resolves outside approved report root"):
        hunter_pr_preflight.validate_and_resolve_report_path("/app/docs/CODE_WRITE_POLICY.json", root)


def test_extract_relevant_file_paths_regex_performance_and_linear_time() -> None:
    root = hunter_pr_preflight._repository_root()
    import time

    # Adversarial input with 500,000 chars of nested/overlapping patterns
    adversarial_text = "a/b/c/d/e.f/g/h.i/" * 25000 + "\nscripts/hunter_pr_preflight.py:10\n"

    t0 = time.monotonic()
    extracted = hunter_pr_preflight._extract_relevant_file_paths(adversarial_text, root)
    elapsed = time.monotonic() - t0

    # Must complete in under 0.5s (demonstrably linear)
    assert elapsed < 0.5, f"Extraction took {elapsed:.2f}s, expected linear < 0.5s"
    assert "scripts/hunter_pr_preflight.py" in extracted


def test_generated_preflight_report_directory_is_gitignored() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".hunter/preflight-reports/" in ignored
