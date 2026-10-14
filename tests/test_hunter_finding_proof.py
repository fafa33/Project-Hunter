"""Negative security tests for bounded hosted reviewer proof."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import hunter_finding_proof as proof


def finding(**overrides):
    value = {
        "id": "RFD-900-001",
        "source_provenance": {"reviewed_head_sha": "a" * 40},
        "validation_state": "validated",
        "resolution_state": "resolved",
        "classification": "new_systemic_defect",
        "executable_proof": {
            "vulnerable_sha": "a" * 40,
            "red_test": "tests/test_example.py::test_regression",
            "green_test": "tests/test_example.py::test_regression",
            "adversarial_tests": ["tests/test_example.py::test_adversarial"],
        },
    }
    value.update(overrides)
    return value


def test_missing_proof_fails_closed():
    assert "missing executable_proof" in str(proof.validate_proof(finding(executable_proof=None)))


def test_forged_or_mismatched_proof_rejected_before_execution():
    bad = finding()
    bad["executable_proof"]["vulnerable_sha"] = "main"
    bad["executable_proof"]["green_test"] = "tests/test_example.py::test_other"
    assert len(proof.validate_proof(bad)) >= 2


def test_path_escape_rejected(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    bad = finding()
    bad["executable_proof"]["red_test"] = "tests/../../secrets.py::test_secret"
    bad["executable_proof"]["green_test"] = bad["executable_proof"]["red_test"]
    assert "invalid pytest target" in str(proof.validate_proof(bad, root=tmp_path))


def test_unavailable_baseline_fails_closed(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("def test_regression(): pass\n")
    with patch.object(proof, "run", return_value=type("Result", (), {"returncode": 1})()):
        assert "vulnerable_sha unavailable" in str(proof.validate_proof(finding(), root=tmp_path))


def test_unlisted_ids_are_not_exempt():
    assert proof.validate_proof(finding(id="RFD-999-001", executable_proof=None))


def test_adversarial_selector_must_be_distinct_and_unique():
    bad = finding()
    bad["executable_proof"]["adversarial_tests"] = ["tests/test_example.py::test_regression"]
    assert "adversarial" in str(proof.validate_proof(bad))
    bad["executable_proof"]["adversarial_tests"] = ["tests/test_example.py::test_adversarial"] * 2
    assert "adversarial" in str(proof.validate_proof(bad))


def test_legacy_exemption_is_pinned_to_record_content():
    import json

    records = json.loads((Path(proof.ROOT) / "docs/REVIEWER_FINDING_DISPOSITIONS.json").read_text())["findings"]
    legacy = next(r for r in records if r["id"] == "RFD-372-001")
    assert proof.is_pinned_legacy(legacy)
    mutated = {**legacy, "root_cause": "rewritten to describe a brand-new finding"}
    assert not proof.is_pinned_legacy(mutated)
    assert proof.validate_proof({**mutated, "executable_proof": None})


def test_vulnerable_sha_must_equal_reviewed_head_provenance():
    bad = finding(source_provenance={"reviewed_head_sha": "b" * 40})
    assert "reviewed_head_sha" in str(proof.validate_proof(bad))
    assert "reviewed_head_sha" in str(proof.validate_proof(finding(source_provenance={})))


def test_pytest_runs_with_scrubbed_environment_and_no_network(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, env=kwargs["env"])
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(proof, "sandbox_prefix", lambda source, view: ["sandbox", "--unshare-net"])
    monkeypatch.setattr(proof.subprocess, "run", fake_run)
    proof.run_pytest(["tests/test_example.py::test_regression"], source=tmp_path, view=tmp_path)
    assert seen["command"][:2] == ["sandbox", "--unshare-net"]
    assert "GITHUB_TOKEN" not in seen["env"] and "HTTPS_PROXY" not in seen["env"]


def test_missing_sandbox_fails_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(proof, "trusted_bwrap", lambda: None)
    try:
        proof.run_pytest(["tests/test_example.py::test_regression"], source=tmp_path, view=tmp_path)
    except proof.IsolationUnavailable:
        return
    raise AssertionError("an unsandboxed proof run must be refused")


def test_unshare_only_fallback_is_refused(monkeypatch):
    monkeypatch.setattr(proof, "trusted_bwrap", lambda: None)
    try:
        proof.sandbox_prefix(Path("."), Path("."))
    except proof.IsolationUnavailable:
        return
    raise AssertionError("a network-only namespace must not be accepted as isolation")


def test_sandbox_boundary_masks_credentials_and_keeps_baseline_worktree_visible(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(proof, "trusted_bwrap", lambda: "/usr/bin/bwrap")
    worktree = tmp_path / "hunter-finding-red-x" / "repo"
    worktree.mkdir(parents=True)
    argv = proof.sandbox_prefix(worktree, tmp_path)

    def position(*pair):
        return next(i for i in range(len(argv) - 1) if tuple(argv[i : i + 2]) == pair)

    assert argv[1:4] == ["--ro-bind", "/", "/"]  # whole host filesystem read-only
    assert "--unshare-net" in argv and "--unshare-pid" in argv and "--die-with-parent" in argv
    for tree in proof.MASKED_TREES:  # credential-bearing host trees are empty tmpfs mounts
        assert ("--tmpfs", tree) in list(zip(argv, argv[1:], strict=False))
    # a work tree under the (shadowed) /tmp must be re-bound read-only AFTER the private tmpfs mount
    resolved = str(worktree.resolve())
    if resolved.startswith(proof.SANDBOX_TMP):
        assert position("--ro-bind", resolved) > position("--tmpfs", proof.SANDBOX_TMP)
    assert "--bind" not in argv  # nothing is ever mounted writable from the host
    assert proof.scrubbed_env(proof.SANDBOX_TMP)["HOME"] == proof.SANDBOX_TMP


def test_sandbox_blocks_host_writes_and_credential_reads_when_bwrap_is_usable(tmp_path: Path):
    if not proof.trusted_bwrap():
        return
    probe = proof.subprocess.run([*proof.sandbox_prefix(tmp_path, tmp_path), "true"], capture_output=True, check=False)
    if probe.returncode:
        return  # user namespaces unavailable on this host; the structural test above still binds the argv
    script = "\n".join(
        [
            "import os",
            "def writable(path):",
            "    try:",
            "        open(path, 'w').close()",
            "    except OSError:",
            "        return False",
            "    return True",
            "assert not writable('/etc/hunter-probe'), 'root filesystem must be read-only'",
            "assert writable('/tmp/hunter-sandbox-probe'), 'sandbox tmpfs must be writable'",
            "assert not os.listdir('/root'), 'credential tree must be masked'",
        ]
    )
    result = proof.subprocess.run(
        [*proof.sandbox_prefix(tmp_path, tmp_path), sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not Path("/tmp/hunter-sandbox-probe").exists()  # the sandbox tmpfs never reached the host /tmp


def test_bwrap_is_never_resolved_from_ambient_path(monkeypatch, tmp_path: Path):
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert proof.trusted_bwrap() != str(fake)
    monkeypatch.setattr(proof, "TRUSTED_BWRAP_PATHS", (str(fake),))
    assert proof.trusted_bwrap() is None or Path(proof.trusted_bwrap()).stat().st_uid == 0  # test-owned file refused


def _git_repo(path: Path) -> str:
    def git(*args: str) -> str:
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        done = proof.subprocess.run(
            ["git", *args], cwd=path, capture_output=True, text=True, check=True, env={**env, "PATH": "/usr/bin:/bin"}
        )
        return done.stdout.strip()

    git("init", "-q")
    (path / "tests").mkdir()
    (path / "tests/test_example.py").write_text("def test_regression():\n    assert False\n")
    git("add", "-A")
    git("commit", "-q", "-m", "baseline")
    return git("rev-parse", "HEAD")


def test_baseline_and_current_snapshots_carry_no_git_metadata(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = _git_repo(repo)
    red, green = tmp_path / "red", tmp_path / "green"
    red.mkdir()
    green.mkdir()
    assert proof.extract_baseline(repo, sha, red) is None
    proof.snapshot_tracked(repo, green)
    for tree in (red, green):
        assert (tree / "tests/test_example.py").is_file()
        assert not (tree / ".git").exists()  # a test cannot inspect the checked-out SHA


def test_red_and_green_are_presented_at_the_same_path_with_no_phase_marker(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = _git_repo(repo)
    calls = []

    def fake_pytest(selectors, *, source, view):
        calls.append((list(selectors), source, view))
        code = 1 if len(calls) == 1 else 0
        report = (
            "FAILED tests/test_example.py::test_regression - assert False"
            if code
            else "PASSED tests/test_example.py::test_regression\nPASSED tests/test_example.py::test_adversarial"
        )
        return type("Result", (), {"returncode": code, "stdout": report, "stderr": ""})()

    monkeypatch.setattr(proof, "run_pytest", fake_pytest)
    selector = "tests/test_example.py::test_regression"
    adversarial = "tests/test_example.py::test_adversarial"
    (repo / "tests/test_example.py").write_text("def test_regression(): pass\ndef test_adversarial(): pass\n")
    value = finding(
        source_provenance={"reviewed_head_sha": sha},
        executable_proof={
            "vulnerable_sha": sha,
            "red_test": selector,
            "green_test": selector,
            "adversarial_tests": [adversarial],
        },
    )
    assert proof.validate_proof(value, root=repo) == []
    red_call, green_call = calls
    assert red_call[2] == green_call[2] == repo  # identical view path for both phases
    assert red_call[1] != green_call[1]
    assert not (red_call[1] / ".git").exists() and not (green_call[1] / ".git").exists()


def test_snapshot_refuses_a_tracked_symlink_that_would_redirect_a_later_copy(tmp_path: Path):
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), repo / "link")
    (repo / "plain").mkdir()
    (repo / "plain" / "later.txt").write_text("x")
    done = proof.subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    assert done.returncode == 0
    green = tmp_path / "green"
    green.mkdir()
    # a symlinked ancestor of a later tracked path must be refused rather than followed
    os.symlink(str(outside), green / "plain")
    try:
        proof.snapshot_tracked(repo, green)
    except ValueError:
        pass
    else:
        raise AssertionError("a symlink ancestor inside the snapshot must be refused")
    assert not (outside / "later.txt").exists()


def test_a_selector_skipped_or_deselected_by_candidate_hooks_is_not_proven():
    green = "tests/test_example.py::test_regression"
    adversarial = "tests/test_example.py::test_adversarial"
    passed = f"PASSED {green}\nPASSED {adversarial}\n2 passed"
    skipped = f"SKIPPED {green}\nPASSED {adversarial}\n1 passed, 1 skipped"
    deselected = f"PASSED {adversarial}\n1 passed, 1 deselected"
    assert proof.unproven_selectors(passed, [green, adversarial], "PASSED") == []
    assert proof.unproven_selectors(skipped, [green, adversarial], "PASSED") == [green]
    assert proof.unproven_selectors(deselected, [green, adversarial], "PASSED") == [green]
    assert proof.unproven_selectors(f"FAILED {green}", [green], "FAILED") == []
    assert proof.unproven_selectors(f"ERROR {green}", [green], "FAILED") == [green]


def test_candidate_ledger_must_retain_every_trusted_baseline_finding(tmp_path: Path):
    import json

    def ledger(root: Path, ids: list[str]) -> Path:
        (root / "docs").mkdir(parents=True)
        (root / "docs/REVIEWER_FINDING_DISPOSITIONS.json").write_text(
            json.dumps({"findings": [{"id": i} for i in ids]})
        )
        return root

    baseline = ledger(tmp_path / "base", ["RFD-1", "RFD-2"])
    emptied = ledger(tmp_path / "emptied", [])
    kept = ledger(tmp_path / "kept", ["RFD-1", "RFD-2", "RFD-3"])
    assert len(proof.baseline_retention_errors(emptied, baseline)) == 2
    assert proof.baseline_retention_errors(kept, baseline) == []
    assert proof.baseline_retention_errors(baseline, baseline) == []
