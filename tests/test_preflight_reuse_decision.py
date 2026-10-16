"""Issue #580: the trusted candidate lane reuses the exact-head host proof safely.

The trusted candidate lane exists to verify candidates that change a trusted
validation-definition path -- edits the hosted push proof cannot self-attest.
When a candidate changes none of those paths, re-running the identical full
suite costs fourteen minutes and establishes nothing the hosted exact-head proof
did not. The decision to reuse is trusted default-branch code, every evidence
failure runs the full trusted gates, and the gate-execution step is only skipped
when the reuse step itself declared the proof reusable.

The hosted proof is never candidate-controlled: the reuse adjudicator reads the
``Hunter / Pre-PR Preflight`` run record from the Actions API by immutable head
SHA, exactly as candidate admission does, and identity-matches the tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import hunter_governance_review_v2 as governance
import hunter_preflight_reuse_decision as decision
import hunter_validation_reuse as reuse
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _current_pr_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": HEAD}})


HEAD = "a" * 40
PR = 580
REPO = "fafa33/Project-Hunter"


def _ok(reusable: bool, reason: str) -> tuple[bool, str]:
    return reusable, reason


def _no_changes(*args: Any, **kwargs: Any) -> tuple[bool, tuple[str, ...], str | None]:
    return True, (), None


def _resolve_spy(monkeypatch: pytest.MonkeyPatch, result: reuse.ReuseDecision) -> None:
    monkeypatch.setattr(decision.reuse, "resolve", lambda *_a, **_k: result)


def _failing_resolve_spy(monkeypatch: pytest.MonkeyPatch, decided: bool = False) -> None:
    def resolve(*args: Any, **kwargs: Any) -> reuse.ReuseDecision:
        if decided:
            return reuse.ReuseDecision(False, "exact-head proof is missing")
        raise AssertionError("reuse resolution must not run before fail-closed refusal")

    monkeypatch.setattr(decision.reuse, "resolve", resolve)


# ---------------------------------------------------------------------------
# The decision itself
# ---------------------------------------------------------------------------


def test_candidate_without_definition_changes_reuses_the_exact_head_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", _no_changes)
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "hosted exact-head proof verifies"))

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is True
    assert "hosted exact-head proof" in reason


def test_an_ordinary_candidate_is_fresh_until_the_hosted_proof_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", _no_changes)
    _resolve_spy(monkeypatch, reuse.ReuseDecision(False, "exact-head branch preflight is missing"))

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is False
    assert "missing" in reason


def test_a_protected_definition_change_always_runs_the_full_trusted_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_args, **_kwargs: (True, ("scripts/hunter_pr_preflight.py", "tests/test_x.py"), None),
    )
    _failing_resolve_spy(monkeypatch)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is False
    assert "scripts/hunter_pr_preflight.py" in reason
    assert "trusted validation definition" in reason


def test_a_pytest_configuration_change_also_runs_the_full_trusted_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_args, **_kwargs: (True, ("pyproject.toml",), None),
    )
    _failing_resolve_spy(monkeypatch)

    reusable, _reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is False


def test_unavailable_changed_file_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        decision.governance, "read_pr_changed_paths", lambda *_args, **_kwargs: (False, (), "api unavailable")
    )
    _failing_resolve_spy(monkeypatch)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is False
    assert "unavailable" in reason


def test_a_throwing_changed_file_read_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def throws(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", throws)
    _failing_resolve_spy(monkeypatch)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")

    assert reusable is False
    assert "rate limited" in reason


def test_the_definition_authority_is_superset_of_admission_routing() -> None:
    assert governance.PREFLIGHT_OWNED_PATHS <= decision.TRUSTED_DEFINITION_PATHS
    for candidate_surface in (
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "requirements/ci-constraints.txt",
        "tests/conftest.py",
        ".github/workflows/hunter-trusted-preflight-upgrade.yml",
    ):
        assert candidate_surface in decision.TRUSTED_DEFINITION_PATHS, candidate_surface


def test_the_cli_emits_a_reuse_declaration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        decision, "decide", lambda *_a, **_k: _ok(True, "the hosted exact-head proof verified this tree")
    )
    output = tmp_path / "GITHUB_OUTPUT"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    assert decision.main(["--pr", str(PR), "--repository", REPO, "--head-sha", HEAD, "--candidate-root", "."]) == 0

    assert "reusable=true" in output.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The workflow wiring
# ---------------------------------------------------------------------------


def _workflow() -> dict[Any, Any]:
    document = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _steps() -> list[dict[str, Any]]:
    steps = [step for step in _workflow()["jobs"]["validate-candidate"]["steps"] if isinstance(step, dict)]
    assert steps
    return steps


def test_the_gate_step_only_skips_on_a_positively_reusable_decision() -> None:
    steps = _steps()
    decision_step = next(step for step in steps if step.get("id") == "reuse-decision")
    gate_step = next(step for step in steps if "--run-candidate-gates" in str(step.get("run", "")))

    assert decision_step["name"] == "Decide whether the hosted exact-head proof may be reused"
    assert "hunter_preflight_reuse_decision.py" in str(decision_step["run"])
    assert gate_step.get("if") == "steps.reuse-decision.outputs.reusable != 'true'"
    assert _steps().index(decision_step) < _steps().index(gate_step)


def test_the_decision_never_replaces_the_structure_check_or_the_publisher() -> None:
    steps = _steps()
    structure = next(step for step in steps if "--validate-candidate" in str(step.get("run", "")))
    assert "if" not in structure

    job = _workflow()["jobs"]["publish-proof"]
    assert job["needs"] == "validate-candidate"
    assert str(job.get("if")) == "${{ always() && !cancelled() }}"


def test_the_candidate_executing_step_never_inherits_the_reuse_token() -> None:
    steps = _steps()
    gate_step = next(step for step in steps if "--run-candidate-gates" in str(step.get("run", "")))
    env = gate_step.get("env") or {}
    assert env.get("GITHUB_TOKEN") == ""
    assert env.get("GH_TOKEN") == ""
    decision_step = next(step for step in steps if step.get("id") == "reuse-decision")
    assert (decision_step.get("env") or {}).get("GITHUB_TOKEN") == "${{ github.token }}"


def test_changed_pr_head_refuses_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": "b" * 40}})
    _failing_resolve_spy(monkeypatch)
    allowed, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not allowed
    assert "head" in reason


def test_head_changes_during_adjudication_refuses_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = iter((HEAD, "b" * 40))
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": next(seen)}})
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", _no_changes)
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "hosted proof"))
    allowed, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not allowed
    assert "changed" in reason


def test_malformed_pr_file_listing_refuses_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    original = decision.governance.read_pr_changed_paths
    monkeypatch.setattr(
        decision.governance, "request_json", lambda *_a, **_k: [{"status": "modified"}, {"filename": "tests/test_x.py"}]
    )
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", original)
    ok, files, error = decision.governance.read_pr_changed_files(REPO, "token", PR)
    assert not ok
    assert files == ()
    assert "malformed" in (error or "")


@pytest.mark.parametrize(
    "path",
    [
        "build_backend/project_hunter_build.py",
        "build_backend/new_hook.py",
        "requirements/dev.txt",
        ".github/actions/custom/action.yml",
        "nested/tests/conftest.py",
    ],
)
def test_candidate_controlled_bootstrap_forces_trusted_full_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str
) -> None:
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", lambda *_a, **_k: (True, (path,), None))
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert path in reason
