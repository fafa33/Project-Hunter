from hunter_ci_impact import select_tests


def test_changed_test_maps_to_itself():
    assert select_tests(["tests/test_foo.py"], {"tests/test_foo.py"})[1] == ("tests/test_foo.py",)


def test_script_maps_to_focused_test():
    assert select_tests(["scripts/hunter_pr_preflight.py"], {"tests/test_hunter_pr_preflight.py"})[1] == (
        "tests/test_hunter_pr_preflight.py",
    )


def test_unknown_script_fails_closed():
    assert select_tests(["scripts/unknown.py"], set())[0]


def test_workflow_requires_full():
    assert select_tests([".github/workflows/ci.yml"], {"tests/test_ci.py"})[0]


def test_empty_diff_requires_full():
    assert select_tests([], set())[0]


def test_deleted_test_requires_full():
    assert select_tests(["tests/test_removed.py"], set())[0]


def test_dependency_change_requires_full():
    assert select_tests(["requirements/ci-constraints.txt"], set())[0]


def test_cli_focused_executes_and_propagates_failure(monkeypatch, tmp_path):
    import subprocess
    import sys

    import hunter_ci_impact as impact

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("def test_example(): pass\n")
    monkeypatch.setattr(impact, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["impact", "--base", "a" * 40, "--head", "b" * 40, "--run-focused"])
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n auto")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, stdout="tests/test_example.py\n")
        assert command == [sys.executable, "-m", "pytest", "-q", "tests/test_example.py"]
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(impact.subprocess, "run", fake_run)
    assert impact.main() == 1
    assert len(calls) == 2


def test_cli_diff_failure_fails_closed(monkeypatch, tmp_path):
    import subprocess
    import sys

    import hunter_ci_impact as impact

    monkeypatch.setattr(impact, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["impact", "--base", "a" * 40, "--head", "b" * 40, "--run-focused"])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 128, stderr="invalid revision")

    monkeypatch.setattr(impact.subprocess, "run", fake_run)
    assert impact.main() == 2
    assert len(calls) == 1


def test_cli_full_required_never_runs_focused(monkeypatch, tmp_path):
    import subprocess
    import sys

    import hunter_ci_impact as impact

    monkeypatch.setattr(impact, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["impact", "--base", "a" * 40, "--head", "b" * 40, "--run-focused"])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="pyproject.toml\n")

    monkeypatch.setattr(impact.subprocess, "run", fake_run)
    assert impact.main() == 0
    assert len(calls) == 1


def test_cli_rejects_untrusted_git_revision_arguments(monkeypatch):
    import sys

    import hunter_ci_impact as impact

    monkeypatch.setattr(sys, "argv", ["impact", "--base", "HEAD~1", "--head", "b" * 40])
    assert impact.main() == 2
