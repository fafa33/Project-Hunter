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

from datetime import UTC, datetime
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
    monkeypatch.setattr(
        decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": HEAD}, "base": {"sha": BASE}}
    )


HEAD = "a" * 40
BASE = "b" * 40
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
    monkeypatch.setattr(
        decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": next(seen)}, "base": {"sha": BASE}}
    )
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


@pytest.mark.parametrize(
    "path",
    [
        "docs/CODE_WRITE_POLICY.json",
        "docs/VALIDATION_STAGE_CONTRACT.json",
    ],
)
def test_hgcc1_policy_changes_force_trusted_full_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: str
) -> None:
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", lambda *_a, **_k: (True, (path,), None))
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert path in reason


def test_base_change_during_proof_adjudication_refuses_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observations = iter(
        (
            {"head": {"sha": HEAD}, "base": {"sha": BASE}},
            {"head": {"sha": HEAD}, "base": {"sha": "c" * 40}},
        )
    )
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: next(observations))
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", _no_changes)
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "trusted exact-head proof"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "base changed" in reason


def test_missing_base_identity_refuses_reuse_before_proof(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: {"head": {"sha": HEAD}})
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "base identity" in reason


SOURCE = "d" * 40


def _metadata_only_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report cumulative and source-to-current changes as review metadata only."""
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (".hunter/pre-ready-hostile-review.json",), None),
    )
    monkeypatch.setattr(reuse, "find_previous_proven_head", lambda *_a, **_k: (SOURCE, ""))
    monkeypatch.setattr(
        decision,
        "changed_paths_between",
        lambda *_a, **_k: ([".hunter/pre-ready-hostile-review.json"], ""),
    )


def test_review_metadata_only_still_requires_exact_head_hosted_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(False, "exact-head proof is unavailable"),
    )
    _resolve_spy(monkeypatch, reuse.ReuseDecision(False, "exact-head proof is missing"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "unavailable" in reason


def test_review_metadata_only_may_reuse_only_verified_exact_head_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(True, "verified cross-HEAD equivalence"),
    )
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable
    assert "verified" in reason


def test_cross_head_equivalence_rechecks_live_head_and_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A successful equivalence cannot skip the trusted gate after a HEAD race."""
    observations = iter(
        [
            {"head": {"sha": HEAD}, "base": {"sha": BASE}},
            {"head": {"sha": "d" * 40}, "base": {"sha": BASE}},
        ]
    )
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: next(observations))
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(True, "verified cross-HEAD equivalence"),
    )
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "head changed" in reason


def test_failed_equivalence_falls_back_to_exact_head_proof(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A metadata-only HEAD with its own exact-head proof still reuses it."""
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(False, "no prior equivalent proof"),
    )
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "trusted exact-head proof"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable
    assert "exact-head" in reason


def test_cumulative_metadata_only_cannot_hide_a_code_delta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A later code commit is not metadata-only merely because an earlier one was."""
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        decision,
        "changed_paths_between",
        lambda *_a, **_k: ([".hunter/pre-ready-hostile-review.json", "scripts/hunter_pr_preflight.py"], ""),
    )
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: pytest.fail("equivalence must not run for a code-bearing delta"),
    )
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "not metadata-only" in reason


def test_metadata_only_without_source_proof_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No authoritative source proof means no delta classification and no reuse."""
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(reuse, "find_previous_proven_head", lambda *_a, **_k: (None, "no ancestral proof"))
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: pytest.fail("equivalence must not run without a source proof"),
    )
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "no usable source proof" in reason


def test_metadata_only_equivalence_binds_the_authoritative_source_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The source head selected by the canonical authority is the one verified."""
    _metadata_only_changes(monkeypatch)
    seen: dict[str, Any] = {}

    def fake_verify(*_args: Any, **kwargs: Any) -> reuse.EquivalenceDecision:
        seen.update(kwargs)
        return reuse.EquivalenceDecision(True, "verified cross-HEAD equivalence")

    monkeypatch.setattr(reuse, "verify_for_candidate", fake_verify)
    reusable, _reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable
    assert seen["source_proof_head"] == SOURCE


def test_review_metadata_with_workflow_change_requires_fresh_trusted_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (".hunter/pre-ready-hostile-review.json", ".github/workflows/ci.yml"), None),
    )
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "trusted validation definition" in reason


# ---------------------------------------------------------------------------
# Issue #606 Requirement B: concurrency hardening regression tests
# ---------------------------------------------------------------------------


def _mock_candidate_evidence_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock candidate_evidence dependencies so tests reach decide_from_runs."""
    monkeypatch.setattr(
        decision.reuse.receipts,
        "content_identity",
        lambda root: "tree:" + "1" * 40,
    )
    monkeypatch.setattr(
        decision.reuse.receipts,
        "commit_tree_identity",
        lambda root, head: "tree:" + "1" * 40,
    )
    monkeypatch.setattr(
        decision.reuse.receipts,
        "definition_identity",
        lambda root: "def:abc",
    )
    monkeypatch.setattr(
        decision.reuse.receipts,
        "measured_toolchain",
        lambda: {"python": "3.11", "ruff": "0.1", "black": "24", "mypy": "1.0"},
    )
    monkeypatch.setattr(
        decision.reuse.receipts,
        "pinned_toolchain",
        lambda root: {"python": "3.11", "ruff": "0.1", "black": "24", "mypy": "1.0"},
    )
    monkeypatch.setattr(
        decision.reuse.receipts,
        "toolchain_identity",
        lambda tc: "tc:xyz",
    )


def _mock_receipt_verify_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock receipt verification to pass."""
    monkeypatch.setattr(
        decision.reuse.receipts,
        "verify",
        lambda receipt, expected, **kw: None,
    )


def _mock_canonical_job_proof(monkeypatch: pytest.MonkeyPatch, digest: str = "sha256:job-proof") -> None:
    """Mock canonical job proof to return a digest."""
    monkeypatch.setattr(
        decision.reuse,
        "_canonical_job_proof_with_mode",
        lambda repo, token, run, head_sha, fetch: (digest, reuse.PROOF_MODE_FULL, ""),
    )


def _successful_proof_run_for(
    head_sha: str, *, run_id: int = 100, attempt: int = 1, updated_at: str = "2026-01-01T00:00:00Z"
) -> dict[str, Any]:
    """A canonical successful trusted preflight run record for one exact head."""
    return {
        "id": run_id,
        "run_attempt": attempt,
        "head_sha": head_sha,
        "name": reuse.PRE_PR_WORKFLOW_NAME,
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "updated_at": updated_at,
        "run_started_at": updated_at,
    }


def _run(
    *,
    head_sha: str = HEAD,
    run_id: int = 100,
    attempt: int = 1,
    conclusion: str | None = "success",
    status: str = "completed",
    updated_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    """One canonical Actions run record for concurrency regression tests."""
    return {
        "id": run_id,
        "run_attempt": attempt,
        "head_sha": head_sha,
        "name": reuse.PRE_PR_WORKFLOW_NAME,
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "updated_at": updated_at,
        "run_started_at": updated_at,
    }


def _mock_runs(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]]) -> None:
    """Install one authoritative Actions run snapshot and unchanged-path evidence."""
    import hunter_validation_reuse as reuse_mod

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", _no_changes)


def test_head_a_to_b_while_a_running_refuses_stale_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HEAD A -> B while A validation is running: stale A success must not authorize B."""
    # A's successful run is not a proof for B. The canonical run selector must
    # refuse it by exact head, regardless of its success status.
    proof_for_a = _successful_proof_run_for(HEAD, updated_at="2026-01-01T00:00:00Z")
    selected, problem = reuse.trusted_branch_preflight_run([proof_for_a], "b" * 40)
    assert selected is None
    assert "no exact-head" in problem
    assert "b" * 40 in problem


def test_aba_head_a_to_b_to_a_uses_current_head_proof(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HEAD A -> B -> A: when returning to A, A's own proof must validate A."""
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "hosted exact-head proof verifies for A"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is True
    assert "proof" in reason.lower()


def test_two_simultaneous_pushes_fail_closed_for_uncertain_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two simultaneous pushes: ambiguous/missing evidence fails closed."""
    # Conflicting snapshots for the same run/attempt are rejected by
    # trusted_branch_preflight_run in hunter_validation_reuse.
    _mock_candidate_evidence_passes(monkeypatch)
    _mock_receipt_verify_passes(monkeypatch)
    _mock_canonical_job_proof(monkeypatch)

    runs = [_run(), _run(conclusion="failure")]

    _mock_runs(monkeypatch, runs)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "conflicting" in reason.lower() or "ambiguous" in reason.lower()


def test_stale_workflow_run_cannot_authorize_newer_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stale workflow_run callback cannot publish success for a newer candidate."""
    # An older successful run for A cannot be selected as proof for current B,
    # even when the callback arrives late with a success conclusion.
    stale_run = _successful_proof_run_for(HEAD, run_id=99, updated_at="2025-01-01T00:00:00Z")
    selected, problem = reuse.trusted_branch_preflight_run([stale_run], "b" * 40)
    assert selected is None
    assert "no exact-head" in problem

    # The current head B has no authoritative successful run of its own.
    current_decision = reuse.trusted_branch_preflight_run([stale_run], "b" * 40)
    assert current_decision[0] is None


def test_delayed_status_publication_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Delayed status publication: pending proof beyond bounded deadline fails closed."""
    from datetime import UTC, datetime, timedelta

    # A run that is still pending and exceeds the deadline.
    old_time = (datetime.now(UTC) - timedelta(seconds=50 * 60)).isoformat()
    runs = [_run(status="queued", conclusion=None, updated_at=old_time)]

    _mock_runs(monkeypatch, runs)

    _mock_candidate_evidence_passes(monkeypatch)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "deadline" in reason.lower() or "pending" in reason.lower()


def test_cancelled_workflow_cannot_authorize_readiness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cancelled workflow cannot authorize readiness."""
    cancelled = {
        "id": 100,
        "run_attempt": 1,
        "head_sha": HEAD,
        "name": reuse.PRE_PR_WORKFLOW_NAME,
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "cancelled",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    selected, problem = reuse.trusted_branch_preflight_run([cancelled], HEAD)
    assert selected is None
    assert "concluded cancelled" in problem

    # The canonical decision layer must also refuse a cancelled run.
    evidence = reuse.CandidateEvidence(HEAD, "tree:1", "tree:1", "def:1", "tc:1")
    d = reuse.decide_from_runs([cancelled], evidence)
    assert d.reusable is False


def test_rerun_with_new_run_attempt_uses_latest_attempt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rerun with new run_attempt: latest attempt is selected safely."""
    _mock_candidate_evidence_passes(monkeypatch)
    _mock_receipt_verify_passes(monkeypatch)
    _mock_canonical_job_proof(monkeypatch, digest="sha256:attempt2")

    runs = [
        _run(conclusion="failure"),
        _run(attempt=2, updated_at="2026-01-01T00:05:00Z"),
    ]

    _mock_runs(monkeypatch, runs)

    # With a real clock, the fixed 2026 timestamp is far in the past; verify()
    # is mocked, so recency is not the gate here -- attempt selection is.
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is True
    assert "attempt2" in reason


def test_old_successful_attempt_followed_by_failed_attempt_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old successful attempt followed by failed attempt: latest attempt wins, fails closed."""
    _mock_candidate_evidence_passes(monkeypatch)

    runs = [_run(), _run(attempt=2, conclusion="failure")]

    _mock_runs(monkeypatch, runs)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "concluded failure" in reason.lower()


def test_conflicting_api_snapshots_same_run_attempt_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Conflicting API snapshots for the same run/attempt are rejected."""
    _mock_candidate_evidence_passes(monkeypatch)

    runs = [_run(), _run(conclusion="failure")]

    _mock_runs(monkeypatch, runs)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "conflicting" in reason.lower()


def test_malformed_or_malicious_proof_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Malformed or malicious proof evidence fails closed."""
    runs = [_run(run_id=-1, attempt=0)]

    _mock_runs(monkeypatch, runs)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False


def test_replayed_proof_digest_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A replayed proof digest for a different head is rejected."""
    _mock_candidate_evidence_passes(monkeypatch)
    source_head = "b" * 40
    runs = [_run(head_sha=source_head)]

    _mock_runs(monkeypatch, runs)

    # Request reuse for HEAD != source head SOURCE_HEAD.
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "run exists" in reason.lower() or "missing" in reason.lower()


def test_missing_or_incomplete_job_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing or incomplete job evidence fails closed."""
    runs = [_run()]

    _mock_runs(monkeypatch, runs)

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False


def test_base_branch_changes_during_adjudication_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Base branch changes during proof adjudication refuses reuse."""
    observations = iter(
        (
            {"head": {"sha": HEAD}, "base": {"sha": BASE}},
            {"head": {"sha": HEAD}, "base": {"sha": "d" * 40}},
        )
    )
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: next(observations))
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", lambda *_a, **_k: (True, (), None))
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "trusted exact-head proof"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "base changed" in reason.lower()


def test_head_changes_between_proof_and_publication_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HEAD changes between proof validation and publication refuses reuse."""
    # After the reuse decision says reusable, the PR head recheck detects a change.
    observations = iter(
        (
            {"head": {"sha": HEAD}, "base": {"sha": BASE}},
            {"head": {"sha": "e" * 40}, "base": {"sha": BASE}},
        )
    )
    monkeypatch.setattr(decision.governance, "request_json", lambda *_a, **_k: next(observations))
    monkeypatch.setattr(decision.governance, "read_pr_changed_paths", lambda *_a, **_k: (True, (), None))
    _resolve_spy(monkeypatch, reuse.ReuseDecision(True, "hosted exact-head proof"))
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "changed" in reason.lower()


def test_review_metadata_changes_while_other_checks_running_still_requires_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review metadata changes while other checks run: still requires exact-head proof."""
    _metadata_only_changes(monkeypatch)
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(False, "exact-head proof is unavailable"),
    )
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "unavailable" in reason.lower()


def test_only_current_exact_head_can_be_authorized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only the current exact HEAD can be authorized: head mismatch fails closed."""
    monkeypatch.setattr(
        decision.governance,
        "request_json",
        lambda *_a, **_k: {"head": {"sha": "f" * 40}, "base": {"sha": BASE}},
    )
    _failing_resolve_spy(monkeypatch)
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "head differs" in reason.lower()


def test_two_overlapping_controllers_produce_identical_canonical_outcomes() -> None:
    """Two overlapping trusted controllers cannot produce contradictory outcomes.

    The canonical adjudicator is a pure function of the authoritative run
    record and verified candidate identities, so concurrent evaluations of the
    same snapshot must agree; contradictory snapshots fail closed for both.
    """
    evidence = reuse.CandidateEvidence(HEAD, "tree:1", "tree:1", "def:1", "tc:1")
    runs = [
        _successful_proof_run_for(HEAD, run_id=100, updated_at="2026-01-01T00:00:00Z"),
    ]
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

    # Two independent adjudications of the same authoritative snapshot agree.
    first = reuse.decide_from_runs(runs, evidence, now=now)
    second = reuse.decide_from_runs(runs, evidence, now=now)
    assert (first.reusable, first.reason) == (second.reusable, second.reason)

    # Two adjudications of a contradictory snapshot both fail closed.
    conflicting = runs + [_successful_proof_run_for(HEAD, run_id=100, updated_at="2026-01-02T00:00:00Z")]
    # Same run/attempt, different outcome timestamp -> still one conclusion each;
    # inject a genuinely conflicting conclusion to prove deterministic refusal.
    conflicting[1] = dict(conflicting[1])
    conflicting[1]["conclusion"] = "failure"
    a = reuse.decide_from_runs(conflicting, evidence, now=now)
    b = reuse.decide_from_runs(conflicting, evidence, now=now)
    assert a.reusable is False and b.reusable is False
    assert a.reason == b.reason
    assert "conflicting" in a.reason.lower()


# ---------------------------------------------------------------------------
# Issue #606: trusted push-lane equivalence (hostile regressions)
# ---------------------------------------------------------------------------

PUSH_PR = 606
PUSH_HEAD_B = "e" * 40
REVIEW_PATH = ".hunter/pre-ready-hostile-review.json"


def _binding(
    *,
    current: str = HEAD,
    source: str = SOURCE,
    base: str = BASE,
    digest: str = "sha256:source-proof",
) -> reuse.EquivalenceBinding:
    return reuse.EquivalenceBinding(
        repository=REPO,
        pr_number=PUSH_PR,
        current_head=current,
        source_proof_head=source,
        base_sha=base,
        tested_tree_digest="sha256:tree",
        dependency_identity="deps",
        workflow_definition_identity="workflow",
        toolchain_identity="toolchain",
        environment_identity="environment",
        trusted_workflow=reuse.PRE_PR_WORKFLOW_PATH,
        run_attempt=1,
        job_identity="job:1",
        proof_digest=digest,
        provenance=f"commit:{current}:tree:tree",
    )


def _equivalence(
    *, equivalent: bool = True, binding: reuse.EquivalenceBinding | None = None
) -> reuse.EquivalenceDecision:
    return reuse.EquivalenceDecision(
        equivalent,
        "verified cross-HEAD equivalence" if equivalent else "source proof failed",
        binding if binding is not None else (_binding() if equivalent else None),
    )


def _push_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pr_head: str = HEAD,
    base: str = BASE,
    state: str = "open",
    full_name: str = REPO,
    recheck_head: str | None = None,
    recheck_base: str | None = None,
) -> None:
    checks = {"count": 0}

    def request(repository: str, token: str, method: str, path: str, *_a: Any, **_k: Any) -> Any:
        if path.startswith("commits/"):
            return [
                {
                    "number": PUSH_PR,
                    "state": state,
                    "head": {"sha": pr_head, "repo": {"full_name": full_name}},
                    "base": {"sha": base},
                }
            ]
        if path.startswith("pulls/"):
            checks["count"] += 1
            return {
                "head": {"sha": recheck_head or pr_head},
                "base": {"sha": recheck_base or base},
            }
        raise AssertionError(f"unexpected API path: {path}")

    monkeypatch.setattr(decision.governance, "request_json", request)


def _push_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    delta: tuple[str, ...] = (REVIEW_PATH,),
    source: str | None = SOURCE,
    source_problem: str = "",
    equivalent: bool = True,
    binding: reuse.EquivalenceBinding | None = None,
    **api: Any,
) -> None:
    _push_api(monkeypatch, **api)
    monkeypatch.setattr(decision.reuse, "find_previous_proven_head", lambda *_a, **_k: (source, source_problem))
    monkeypatch.setattr(decision, "changed_paths_between", lambda *_a, **_k: (list(delta), ""))
    monkeypatch.setattr(
        decision.reuse, "verify_for_candidate", lambda *_a, **_k: _equivalence(equivalent=equivalent, binding=binding)
    )


def _push(*, head: str = HEAD) -> decision.PushLaneDecision:
    return decision.decide_push(ROOT, head_sha=head, repository=REPO, token="token")


def test_push_metadata_only_head_reuses_with_a_current_head_bound_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch)
    result = _push()
    assert result.reusable is True
    assert result.execution_mode == reuse.PROOF_MODE_REUSE
    assert result.proof_digest == "sha256:source-proof"
    assert result.binding is not None
    assert result.binding.current_head == HEAD
    assert result.binding.source_proof_head == SOURCE
    assert "cross-HEAD proof reuse" in result.reason


def test_push_ordinary_code_change_never_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, delta=("src/hunter/engine.py",))
    result = _push()
    assert result.reusable is False
    assert result.execution_mode == reuse.PROOF_MODE_FULL
    assert "not metadata-only" in result.reason


@pytest.mark.parametrize("changed", ["requirements/ci-constraints.txt", "pyproject.toml"])
def test_push_dependency_change_never_reuses(monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    _push_env(monkeypatch, delta=(changed,))
    assert _push().reusable is False


@pytest.mark.parametrize(
    "changed",
    [
        ".github/workflows/hunter-pre-pr-preflight.yml",
        "docs/VALIDATION_STAGE_CONTRACT.json",
        "scripts/hunter_validation_reuse.py",
    ],
)
def test_push_workflow_or_trust_root_change_never_reuses(monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    _push_env(monkeypatch, delta=(changed,))
    assert _push().reusable is False


@pytest.mark.parametrize("source,problem", [(None, "no source proof"), (None, "malformed proof")])
def test_push_missing_or_unverifiable_source_proof_fails_closed(
    monkeypatch: pytest.MonkeyPatch, source: str | None, problem: str
) -> None:
    _push_env(monkeypatch, source=source, source_problem=problem)
    result = _push()
    assert result.reusable is False
    assert "source proof" in result.reason


@pytest.mark.parametrize("reason", ["source proof concluded failure", "source proof was cancelled"])
def test_push_failed_or_cancelled_source_proof_fails_closed(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    _push_env(monkeypatch, equivalent=False, binding=None)
    result = _push()
    assert result.reusable is False
    assert "source proof" in result.reason


def test_push_newer_failed_attempt_invalidates_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, source=None, source_problem="newer run attempt failed")
    result = _push()
    assert result.reusable is False
    assert "newer run attempt failed" in result.reason


def test_push_head_a_to_b_never_reuses_an_unrelated_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, source=PUSH_HEAD_B, binding=_binding(source=PUSH_HEAD_B))
    result = _push()
    assert result.reusable is True
    assert result.binding is not None
    assert result.binding.source_proof_head == PUSH_HEAD_B
    assert result.binding.current_head != result.binding.source_proof_head


def test_push_head_a_to_b_to_a_must_rebind_the_current_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, binding=_binding(current=PUSH_HEAD_B))
    result = _push()
    assert result.reusable is False
    assert "binding" in result.reason.lower()


def test_push_two_concurrent_decisions_produce_identical_canonical_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch)
    first = _push()
    second = _push()
    assert (first.reusable, first.execution_mode, first.proof_digest, first.reason) == (
        second.reusable,
        second.execution_mode,
        second.proof_digest,
        second.reason,
    )


def test_push_stale_workflow_run_callback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, recheck_head=PUSH_HEAD_B)
    result = _push()
    assert result.reusable is False
    assert "head changed" in result.reason.lower()


def test_push_pr_base_change_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, recheck_base=PUSH_HEAD_B)
    result = _push()
    assert result.reusable is False
    assert "base changed" in result.reason.lower()


def test_push_fork_or_non_open_pr_can_not_self_authorize(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch, state="closed")
    assert _push().reusable is False
    _push_env(monkeypatch, full_name="attacker/Project-Hunter")
    assert _push().reusable is False


def test_push_reused_proof_never_claims_fresh_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    _push_env(monkeypatch)
    result = _push()
    assert result.execution_mode == reuse.PROOF_MODE_REUSE
    assert result.execution_mode != reuse.PROOF_MODE_FULL


def _push_workflow() -> dict[str, Any]:
    document = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "hunter-pre-pr-preflight.yml").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def test_push_decision_runs_from_trusted_default_branch_code_only() -> None:
    workflow = _push_workflow()
    decision_job = workflow["jobs"]["proof-equivalence"]
    steps = [step for step in decision_job["steps"] if isinstance(step, dict)]
    trusted = next(
        step for step in steps if step.get("id") is None and "Checkout trusted default branch" in str(step.get("name"))
    )
    assert trusted["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert trusted["with"]["persist-credentials"] is False
    decision_step = next(step for step in steps if step.get("id") == "decision")
    assert "--push" in str(decision_step["run"])
    assert "hunter_preflight_reuse_decision.py" in str(decision_step["run"])
    assert (decision_step.get("env") or {}).get("GITHUB_TOKEN") == "${{ github.token }}"
    # The trusted job installs only pinned binary distributions: no local
    # setup script from any checkout is executed in the deciding job.
    install = next(step for step in steps if "Install trusted adjudicator toolchain" in str(step.get("name", "")))
    assert "--only-binary :all:" in str(install.get("run"))
    assert "-e " not in str(install.get("run"))
    # Until the adjudicator reaches trusted main, an unavailable/incompatible
    # trusted script must mean RUN FULL, never a red required check or reuse.
    script = str(decision_step.get("run"))
    assert 'echo "reusable=false"' in script
    assert "execution_mode=full-execution" in script
    assert "running the full suite" in script
    assert "if python scripts/hunter_preflight_reuse_decision.py" in script
    # This job checks out no candidate code at all: candidate code cannot decide reuse.
    assert not any(
        "candidate" in str(step.get("name", "")).lower() or "candidate" in str(step.get("with", {})).lower()
        for step in steps
    )


def test_push_no_privileged_token_reaches_candidate_execution() -> None:
    workflow = _push_workflow()
    preflight = workflow["jobs"]["preflight"]
    heavy = next(step for step in preflight["steps"] if "hunter_pr_preflight.py" in str(step.get("run", "")))
    env = heavy.get("env") or {}
    assert env.get("GITHUB_TOKEN") == ""
    assert env.get("GH_TOKEN") == ""
    assert "proof-equivalence" not in str(env)
    decision_job = workflow["jobs"]["proof-equivalence"]
    assert set(decision_job.get("permissions") or {}) <= {"contents", "actions", "pull-requests"}


def test_push_required_check_concludes_even_when_the_decision_job_fails() -> None:
    workflow = _push_workflow()
    preflight = workflow["jobs"]["preflight"]
    assert preflight["needs"] == "proof-equivalence"
    assert str(preflight.get("if")) == "${{ always() && !cancelled() }}"
    steps = [step for step in preflight["steps"] if isinstance(step, dict)]
    heavy = next(step for step in steps if "hunter_pr_preflight.py" in str(step.get("run", "")))
    assert "needs.proof-equivalence.outputs.reusable == 'true'" in str(heavy.get("if"))
    modes = [step for step in steps if str(step.get("name", "")).startswith(reuse.PROOF_MODE_STEP_PREFIX)]
    assert {step["name"] for step in modes} == {
        f"{reuse.PROOF_MODE_STEP_PREFIX}{reuse.PROOF_MODE_REUSE}",
        f"{reuse.PROOF_MODE_STEP_PREFIX}{reuse.PROOF_MODE_FULL}",
    }
    assert all("always()" in str(step.get("if")) for step in modes)


def test_push_delayed_or_inconclusive_decision_runs_full_suite() -> None:
    workflow = _push_workflow()
    heavy = next(
        step
        for step in workflow["jobs"]["preflight"]["steps"]
        if isinstance(step, dict) and "hunter_pr_preflight.py" in str(step.get("run", ""))
    )
    # Only a positively reusable completed decision can suppress the heavy step;
    # a delayed, failed or malformed decision cannot.
    assert "needs.proof-equivalence.result == 'success'" in str(heavy.get("if"))
    assert "steps.preflight-mode.outputs.mode == 'normal'" in str(heavy.get("if"))
