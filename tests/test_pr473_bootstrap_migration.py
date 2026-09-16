"""PR #473 trusted-controller bootstrap migration.

PR #473 installs ``scripts/hunter_review_orchestrator.py``. Until that file
exists on the default branch, no trusted path can orchestrate exact-head review
for it: the candidate ``pull_request`` run publishes an explicit bootstrap
pending state, and the trusted ``workflow_run``/reconcile runs -- which execute
the default-branch bridge -- would otherwise overwrite that same exact head with
``MISSING_REVIEW_AUTHORITY``.

These tests exercise that sequence, not the wording of either file.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/hunter-governance-review.yml"
BRIDGE_PATH = ROOT / "scripts/hunter_governance_review/bootstrap_external_review_469.py"

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load_bridge() -> Any:
    spec = importlib.util.spec_from_file_location("pr473_bootstrap_bridge", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()
governance = sys.modules["hunter_governance_review_v2"]
candidate = sys.modules["hunter_candidate_admission"]

REPOSITORY = bridge.TARGET_REPOSITORY
HEAD = "0a82eded84c42f407c185955495399557bc2643b"


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Capture every status this bridge publishes."""
    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        governance,
        "publish",
        lambda _repo, _token, sha, state, description: records.append((sha, state, description)),
    )
    return records


@pytest.fixture
def legacy_review(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Capture every delegation to the legacy default-branch controller."""
    calls: list[int] = []

    def _review(_repo: str, _token: str, pr_number: int) -> int:
        calls.append(pr_number)
        return 0

    monkeypatch.setattr(governance, "review", _review)
    return calls


def _pull_request(state: str = "open", base: str = "main", head: str = HEAD) -> dict[str, Any]:
    return {"state": state, "base": {"ref": base}, "head": {"sha": head}}


def _mergeability(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(governance, "read_mergeability", lambda *_args, **_kwargs: payload)


def _controller_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bridge, "TRUSTED_CONTROLLER_PATH", tmp_path / "hunter_review_orchestrator.py")


def _controller_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    controller = tmp_path / "hunter_review_orchestrator.py"
    controller.write_text("# trusted controller\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "TRUSTED_CONTROLLER_PATH", controller)


def test_trusted_bridge_publishes_bootstrap_pending_instead_of_missing_review_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
    legacy_review: list[int],
) -> None:
    """The observed production overwrite: trusted main runs this bridge for #473."""
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())

    assert bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR) == 0

    assert legacy_review == [], "the legacy controller must not run and republish MISSING_REVIEW_AUTHORITY"
    assert published == [(HEAD, "pending", bridge.BOOTSTRAP_PENDING_DESCRIPTION)]
    assert bridge.BOOTSTRAP_PENDING_STATE in published[0][2]


def test_bootstrap_pending_is_never_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
    legacy_review: list[int],
) -> None:
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())

    bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR)

    assert {state for _sha, state, _description in published} == {"pending"}


def test_exact_head_authority_resumes_once_the_controller_is_on_the_default_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
    legacy_review: list[int],
) -> None:
    """The migration state retires itself; it does not linger as a standing exemption."""
    _controller_present(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())

    assert bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR) == 0

    assert legacy_review == [bridge.BOOTSTRAP_CONTROLLER_PR]
    assert published == []


@pytest.mark.parametrize(
    ("repository", "pr_number"),
    [
        (REPOSITORY, 999),
        (REPOSITORY, bridge.TARGET_PR),
        ("attacker/Project-Hunter", 473),
    ],
)
def test_bootstrap_pending_is_scoped_to_the_controller_migration_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
    legacy_review: list[int],
    repository: str,
    pr_number: int,
) -> None:
    """A missing controller cannot excuse any other pull request from review."""
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())
    monkeypatch.setattr(bridge, "_install_bootstrap_patch", lambda *_args, **_kwargs: False)

    assert bridge.bootstrap_pending_mode(repository, pr_number) is False
    assert bridge.governance_mode(repository, "token", pr_number) == 0
    assert legacy_review == [pr_number]
    assert published == []


def test_bootstrap_pending_fails_closed_when_head_evidence_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
) -> None:
    """No head, no status: the run errors rather than publishing against nothing."""
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request(head=""))

    with pytest.raises(RuntimeError):
        bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR)
    assert published == []


@pytest.mark.parametrize("payload", [_pull_request(state="closed"), _pull_request(base="release")])
def test_bootstrap_pending_publishes_nothing_outside_the_open_main_targeted_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
    payload: dict[str, Any],
) -> None:
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, payload)

    assert bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR) == 0
    assert published == []


def test_bootstrap_pending_does_not_admit_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    published: list[tuple[str, str, str]],
) -> None:
    """Candidate admission still runs and still fails closed during the migration."""
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())
    calls: list[tuple[int, str | None]] = []

    def _enforce(_repo: str, _token: str, pr_number: int, expected_head: str | None) -> int:
        calls.append((pr_number, expected_head))
        return 1

    monkeypatch.setattr(candidate, "enforce_candidate_admission", _enforce)
    monkeypatch.setattr(bridge, "_install_bootstrap_patch", lambda *_args, **_kwargs: False)

    assert bridge.candidate_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR, HEAD) == 1
    assert calls == [(bridge.BOOTSTRAP_CONTROLLER_PR, HEAD)]
    assert published == []


def test_bootstrap_controller_path_is_resolved_from_the_executing_trusted_tree() -> None:
    """Trust is a property of the tree running the bridge, not of a caller flag."""
    assert bridge.TRUSTED_CONTROLLER_PATH == BRIDGE_PATH.parents[1] / "hunter_review_orchestrator.py"


# --- candidate workflow -------------------------------------------------------


def _bootstrap_step_script() -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    scripts = [
        step["run"]
        for job in document["jobs"].values()
        for step in job["steps"]
        if isinstance(step.get("run"), str) and "BOOTSTRAP_PENDING_TRUSTED_CONTROLLER" in step["run"]
    ]
    assert len(scripts) == 1
    return scripts[0]


def _run_bootstrap_step(
    tmp_path: Path, env: dict[str, str], resolved_head: str = HEAD
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Execute the workflow's bootstrap step with `gh` and `python` stubbed."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    calls = tmp_path / "gh-calls.txt"
    (stub_dir / "gh").write_text(
        textwrap.dedent(f"""\
            #!/bin/sh
            printf '%s\\n' "$*" >> {calls}
            case "$*" in
              *"pulls/473"*) printf '%s\\n' "{resolved_head}" ;;
            esac
            """),
        encoding="utf-8",
    )
    (stub_dir / "python").write_text(
        textwrap.dedent(f"""\
            #!/bin/sh
            printf 'python %s\\n' "$*" >> {calls}
            """),
        encoding="utf-8",
    )
    for name in ("gh", "python"):
        (stub_dir / name).chmod(0o755)

    workspace = tmp_path / "workspace"
    (workspace / "engine" / "scripts" / "hunter_governance_review").mkdir(parents=True)
    process = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", _bootstrap_step_script()],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}",
            "GITHUB_WORKSPACE": str(workspace),
            "GH_REPO": REPOSITORY,
            "PR_NUMBER": "473",
            "GITHUB_RUN_ID": "35102465470",
            "GITHUB_SERVER_URL": "https://github.com",
            **env,
        },
    )
    return process, calls


def test_candidate_bootstrap_step_publishes_pending_for_a_pull_request_event(tmp_path: Path) -> None:
    process, calls = _run_bootstrap_step(tmp_path, {"PR_HEAD_SHA": HEAD})

    assert process.returncode == 0, process.stderr
    recorded = calls.read_text(encoding="utf-8")
    assert f"statuses/{HEAD}" in recorded
    assert "state=pending" in recorded
    assert "BOOTSTRAP_PENDING_TRUSTED_CONTROLLER" in recorded
    assert "python " not in recorded, "the legacy controller must not run while bootstrap is pending"


def test_bootstrap_step_resolves_the_head_when_the_event_carries_no_pull_request(tmp_path: Path) -> None:
    """workflow_run and workflow_dispatch have no pull_request payload."""
    process, calls = _run_bootstrap_step(tmp_path, {"PR_HEAD_SHA": ""})

    assert process.returncode == 0, process.stderr
    recorded = calls.read_text(encoding="utf-8")
    assert f"repos/{REPOSITORY}/pulls/473" in recorded
    assert f"statuses/{HEAD}" in recorded
    assert "statuses/ " not in recorded and "statuses/\n" not in recorded
    assert "state=pending" in recorded


def test_bootstrap_step_never_posts_a_status_against_an_empty_head(tmp_path: Path) -> None:
    """An unresolvable head is a failure, not a status posted at `statuses/`."""
    process, calls = _run_bootstrap_step(tmp_path, {"PR_HEAD_SHA": "", "PR_NUMBER": ""})

    assert process.returncode != 0
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "statuses/" not in recorded


def test_bootstrap_step_rejects_a_null_head_rendering(tmp_path: Path) -> None:
    """`--jq` renders an absent field as the literal text `null`, not as nothing."""
    process, calls = _run_bootstrap_step(tmp_path, {"PR_HEAD_SHA": ""}, resolved_head="null")

    assert process.returncode != 0
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "statuses/" not in recorded


def test_candidate_workflow_keeps_actions_read_only_during_bootstrap() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    blocks = [document.get("permissions")] + [job.get("permissions") for job in document["jobs"].values()]
    present = [block for block in blocks if isinstance(block, dict)]
    assert present
    for block in present:
        assert str(block.get("actions", "read")).strip() == "read"
