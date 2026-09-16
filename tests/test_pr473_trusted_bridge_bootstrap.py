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
import sys
from pathlib import Path
from typing import Any

import pytest

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
    del tmp_path

    def _request(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("GitHub API 404")

    monkeypatch.setattr(governance, "request_json", _request)


def _controller_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del tmp_path
    monkeypatch.setattr(
        governance, "request_json", lambda *_args, **_kwargs: {"type": "file", "path": bridge.TRUSTED_CONTROLLER_PATH}
    )


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


def test_bootstrap_controller_path_targets_the_default_branch_api_path() -> None:
    assert bridge.TRUSTED_CONTROLLER_PATH == "scripts/hunter_review_orchestrator.py"


# --- candidate workflow -------------------------------------------------------


def test_candidate_workspace_file_cannot_end_bootstrap_pending_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Candidate-controlled workspace files are not trusted default-branch evidence."""
    controller = tmp_path / "hunter_review_orchestrator.py"
    controller.write_text("# candidate-controlled\n", encoding="utf-8")

    # Even if the mutable workspace contains a controller, immutable main evidence wins.
    def _request(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("GitHub API 404")

    monkeypatch.setattr(governance, "request_json", _request)

    assert bridge.bootstrap_pending_mode(REPOSITORY, bridge.BOOTSTRAP_CONTROLLER_PR, "token") is True


def test_bootstrap_controller_adopts_exact_head_review_before_controller_lands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    legacy_review: list[int],
) -> None:
    """#473 must have a real trusted pre-merge admission path, not pending-only."""
    _controller_missing(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())
    installed: list[tuple[str, int, str]] = []

    def _install(repo: str, token: str, pr_number: int, head_sha: str) -> bool:
        del token
        installed.append((repo, pr_number, head_sha))
        return True

    monkeypatch.setattr(bridge, "_install_bootstrap_patch", _install)

    assert bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR) == 0
    assert installed == [(REPOSITORY, bridge.BOOTSTRAP_CONTROLLER_PR, HEAD)]
    assert legacy_review == [bridge.BOOTSTRAP_CONTROLLER_PR]


def test_bootstrap_rejects_codex_review_that_reports_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    review = {
        "id": 7,
        "user": {"login": bridge.CODEX_LOGIN},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "### Codex Review\n\nHere are some automated review suggestions.\n\n**Reviewed commit:** `0a82ede`",
    }
    monkeypatch.setattr(bridge, "_paged_reviews", lambda *_args: (review,))
    assert bridge._exact_head_codex_review(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR, HEAD) is None


def test_bootstrap_accepts_only_native_codex_clear_review(monkeypatch: pytest.MonkeyPatch) -> None:
    review = {
        "id": 8,
        "user": {"login": bridge.CODEX_LOGIN},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "### 💡 Codex Review\n\nDidn't find any major issues.\n\n**Reviewed commit:** `0a82ede`",
    }
    monkeypatch.setattr(bridge, "_paged_reviews", lambda *_args: (review,))
    assert bridge._exact_head_codex_review(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR, HEAD) == review


def test_controller_lookup_is_bound_to_checked_out_trusted_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent main advance cannot change controller evidence for an in-flight trusted checkout."""
    checked_out = "a" * 40
    seen: list[str] = []
    monkeypatch.setattr(bridge, "_checked_out_commit_sha", lambda: checked_out)

    def _request(_repo: str, _token: str, _method: str, endpoint: str) -> Any:
        seen.append(endpoint)
        raise RuntimeError("GitHub API 404")

    monkeypatch.setattr(governance, "request_json", _request)
    assert bridge._trusted_controller_on_default_branch(REPOSITORY, "token") is False
    assert seen == [f"contents/{bridge.TRUSTED_CONTROLLER_PATH}?ref={checked_out}"]
    assert "ref=main" not in seen[0]


def test_controller_presence_requires_valid_default_branch_file_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    for payload in ({}, {"type": "dir", "path": bridge.TRUSTED_CONTROLLER_PATH}, {"type": "file", "path": "wrong.py"}):
        monkeypatch.setattr(governance, "request_json", lambda *_args, _payload=payload, **_kwargs: _payload)
        with pytest.raises(RuntimeError, match="malformed"):
            bridge._trusted_controller_on_default_branch(REPOSITORY, "token")

    monkeypatch.setattr(
        governance,
        "request_json",
        lambda *_args, **_kwargs: {"type": "file", "path": bridge.TRUSTED_CONTROLLER_PATH},
    )
    assert bridge._trusted_controller_on_default_branch(REPOSITORY, "token") is True


def test_bootstrap_patch_self_retires_when_trusted_controller_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, legacy_review: list[int]
) -> None:
    _controller_present(monkeypatch, tmp_path)
    _mergeability(monkeypatch, _pull_request())
    monkeypatch.setattr(
        bridge,
        "_install_bootstrap_patch",
        lambda *_args, **_kwargs: pytest.fail("retired bootstrap must not install synthetic authority"),
    )
    assert bridge.governance_mode(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR) == 0
    assert legacy_review == [bridge.BOOTSTRAP_CONTROLLER_PR]


@pytest.mark.parametrize("controller_present", [True, False])
def test_candidate_bootstrap_retires_with_trusted_controller(monkeypatch, controller_present):
    _mergeability(monkeypatch, _pull_request())
    monkeypatch.setattr(bridge, "_trusted_controller_on_default_branch", lambda *_args: controller_present)
    installed = []
    monkeypatch.setattr(bridge, "_install_bootstrap_patch", lambda *args: installed.append(args))
    monkeypatch.setattr(candidate, "enforce_candidate_admission", lambda *_args: 1)
    assert bridge.candidate_mode(REPOSITORY, "token", 473, HEAD) == 1
    assert len(installed) == (0 if controller_present else 1)


@pytest.mark.parametrize(
    "login,accepted", [("chatgpt-codex-connector[bot]", True), ("chatgpt-codex-connector", False), ("fafa33", False)]
)
def test_bootstrap_requires_actual_authenticated_bot_login(monkeypatch, login, accepted):
    review = {
        "id": 8,
        "user": {"login": login},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "### 💡 Codex Review\n\nDidn't find any major issues.\n\n**Reviewed commit:** `0a82ede`",
    }
    monkeypatch.setattr(bridge, "_paged_reviews", lambda *_args: (review,))
    assert (bridge._exact_head_codex_review(REPOSITORY, "token", 473, HEAD) is not None) is accepted


def test_candidate_bootstrap_rejects_unavailable_controller_evidence(monkeypatch):
    _mergeability(monkeypatch, _pull_request())

    def unavailable(*_args):
        raise RuntimeError("GitHub API 503")

    monkeypatch.setattr(bridge, "_trusted_controller_on_default_branch", unavailable)
    monkeypatch.setattr(bridge, "_install_bootstrap_patch", lambda *_args: pytest.fail("must not install authority"))
    with pytest.raises(RuntimeError, match="503"):
        bridge.candidate_mode(REPOSITORY, "token", 473, HEAD)


@pytest.mark.parametrize(
    "state,body", [("COMMENTED", "Blocking finding remains"), ("CHANGES_REQUESTED", "Fix authority"), ("DISMISSED", "")]
)
def test_latest_review_revokes_prior_clear_bootstrap_authority(monkeypatch, state, body):
    clear = {
        "id": 1,
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "### Codex Review\nDidn't find any major issues.\n**Reviewed commit:** `0a82ede`",
    }
    newer = {**clear, "id": 2, "state": state, "body": body}
    monkeypatch.setattr(bridge, "_paged_reviews", lambda *_args: (newer, clear))
    assert bridge._exact_head_codex_review(REPOSITORY, "token", 473, HEAD) is None


def test_later_clear_review_can_restore_bootstrap_authority(monkeypatch):
    finding = {
        "id": 1,
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "body": "Blocking finding",
    }
    clear = {
        **finding,
        "id": 2,
        "body": "### Codex Review\nDidn't find any major issues.\n**Reviewed commit:** `0a82ede`",
    }
    monkeypatch.setattr(bridge, "_paged_reviews", lambda *_args: (clear, finding))
    assert bridge._exact_head_codex_review(REPOSITORY, "token", 473, HEAD) == clear


@pytest.mark.parametrize("path", [None, "", 123])
def test_controller_requires_explicit_path_evidence(monkeypatch, path):
    payload = {"type": "file"}
    if path is not None:
        payload["path"] = path
    monkeypatch.setattr(governance, "request_json", lambda *_args: payload)
    with pytest.raises(RuntimeError, match="malformed"):
        bridge._trusted_controller_on_default_branch(REPOSITORY, "token")


@pytest.mark.parametrize("controller_present", [True, False])
def test_readiness_bootstrap_covers_controller_migration_and_self_retires(monkeypatch, controller_present):
    """Readiness must admit #473 through the same guarded bootstrap and retire once the controller lands."""
    monkeypatch.setattr(bridge, "_trusted_controller_on_default_branch", lambda *_args: controller_present)
    monkeypatch.setattr(governance, "read_mergeability", lambda *_args: _pull_request())
    installed = []
    monkeypatch.setattr(bridge, "_install_bootstrap_patch", lambda *args: installed.append(args) or True)

    import hunter_merge_readiness_v2 as readiness

    monkeypatch.setattr(readiness, "main", lambda: 0)
    assert bridge.readiness_mode(REPOSITORY, "token") == 0
    controller_installs = [call for call in installed if call[2] == bridge.BOOTSTRAP_CONTROLLER_PR]
    expected = [] if controller_present else [(REPOSITORY, "token", bridge.BOOTSTRAP_CONTROLLER_PR, HEAD)]
    assert controller_installs == expected
