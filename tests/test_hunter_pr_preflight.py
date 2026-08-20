from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import hunter_pr_preflight

ROOT = Path(__file__).resolve().parents[1]


def test_normal_quality_gate_order_matches_ci_contract() -> None:
    assert hunter_pr_preflight.NORMAL_QUALITY_GATES == (
        ("Ruff", ("ruff", "check", ".")),
        ("Black", ("black", "--check", "--diff", ".")),
        ("Mypy", ("mypy",)),
        ("Pytest", ("pytest",)),
    )
    assert hunter_pr_preflight.QUALITY_GATES == hunter_pr_preflight.NORMAL_QUALITY_GATES


def test_tests_first_red_hygiene_gate_order_excludes_pytest() -> None:
    assert hunter_pr_preflight.TESTS_FIRST_HYGIENE_GATES == (
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
    return_codes = iter((0, 0, 0, 1))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 0
    assert calls == [
        ("ruff", "check", "."),
        ("black", "--check", "--diff", "."),
        ("mypy",),
        ("pytest",),
    ]


def test_tests_first_red_never_launders_hygiene_failure(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    return_codes = iter((0, 8, 0, 1))

    def fake_run(command, *, check):
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(hunter_pr_preflight.subprocess, "run", fake_run)

    result = hunter_pr_preflight.run_preflight(mode="tests-first-red")

    assert result == 8
    assert calls == [
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
    return_codes = iter((0, 0, 0, pytest_exit))

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


def test_workflows_use_node24_compatible_action_majors() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    for path in sorted(workflow_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "actions/checkout@v4" not in text, path
        assert "actions/setup-python@v5" not in text, path


def test_agent_instructions_document_both_preflight_modes() -> None:
    text = (ROOT / ".github" / "instructions" / "project-hunter.instructions.md").read_text(encoding="utf-8")
    assert "tests-first-red" in text
    assert ".hunter-preflight-mode" in text
    assert "python scripts/hunter_pr_preflight.py" in text
