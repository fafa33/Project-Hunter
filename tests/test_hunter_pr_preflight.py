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
