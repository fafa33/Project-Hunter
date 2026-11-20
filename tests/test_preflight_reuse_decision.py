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


def test_review_metadata_only_still_requires_exact_head_hosted_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (".hunter/pre-ready-hostile-review.json",), None),
    )
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(False, "exact-head proof is unavailable"),
    )
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert not reusable
    assert "unavailable" in reason


def test_review_metadata_only_may_reuse_only_verified_exact_head_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (".hunter/pre-ready-hostile-review.json",), None),
    )
    monkeypatch.setattr(
        reuse,
        "verify_for_candidate",
        lambda *_a, **_k: reuse.EquivalenceDecision(True, "verified cross-HEAD equivalence"),
    )
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable
    assert "verified" in reason


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
        "_canonical_job_proof",
        lambda repo, token, run, head_sha, fetch: (digest, ""),
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
    import hunter_validation_reuse as reuse_mod

    _mock_candidate_evidence_passes(monkeypatch)
    _mock_receipt_verify_passes(monkeypatch)
    _mock_canonical_job_proof(monkeypatch)

    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
        },
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

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

    import hunter_validation_reuse as reuse_mod

    # A run that is still pending and exceeds the deadline.
    old_time = (datetime.now(UTC) - timedelta(seconds=50 * 60)).isoformat()
    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "queued",
            "conclusion": None,
            "updated_at": old_time,
            "run_started_at": old_time,
        }
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    # Mock candidate evidence to pass.
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
    import hunter_validation_reuse as reuse_mod

    _mock_candidate_evidence_passes(monkeypatch)
    _mock_receipt_verify_passes(monkeypatch)
    _mock_canonical_job_proof(monkeypatch, digest="sha256:attempt2")

    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": reuse.PRE_PR_WORKFLOW_NAME,
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": 100,
            "run_attempt": 2,
            "head_sha": HEAD,
            "name": reuse.PRE_PR_WORKFLOW_NAME,
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-01-01T00:05:00Z",
        },
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    # With a real clock, the fixed 2026 timestamp is far in the past; verify()
    # is mocked, so recency is not the gate here -- attempt selection is.
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is True
    assert "attempt2" in reason


def test_old_successful_attempt_followed_by_failed_attempt_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old successful attempt followed by failed attempt: latest attempt wins, fails closed."""
    import hunter_validation_reuse as reuse_mod

    _mock_candidate_evidence_passes(monkeypatch)

    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 100,
            "run_attempt": 2,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
        },
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "concluded failure" in reason.lower()


def test_conflicting_api_snapshots_same_run_attempt_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Conflicting API snapshots for the same run/attempt are rejected."""
    import hunter_validation_reuse as reuse_mod

    _mock_candidate_evidence_passes(monkeypatch)

    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
        },
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "conflicting" in reason.lower()


def test_malformed_or_malicious_proof_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Malformed or malicious proof evidence fails closed."""
    import hunter_validation_reuse as reuse_mod

    runs = [
        {
            "id": -1,
            "run_attempt": 0,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False


def test_replayed_proof_digest_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A replayed proof digest for a different head is rejected."""
    import hunter_validation_reuse as reuse_mod

    _mock_candidate_evidence_passes(monkeypatch)
    source_head = "b" * 40
    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": source_head,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

    # Request reuse for HEAD != source head SOURCE_HEAD.
    reusable, reason = decision.decide(tmp_path, head_sha=HEAD, repository=REPO, pr_number=PR, token="token")
    assert reusable is False
    assert "run exists" in reason.lower() or "missing" in reason.lower()


def test_missing_or_incomplete_job_evidence_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Missing or incomplete job evidence fails closed."""
    import hunter_validation_reuse as reuse_mod

    runs = [
        {
            "id": 100,
            "run_attempt": 1,
            "head_sha": HEAD,
            "name": "Hunter / Pre-PR Preflight",
            "path": reuse.PRE_PR_WORKFLOW_PATH,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
    ]

    def fake_fetch(url, method, headers, data, token, what):
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        # No jobs endpoint response - incomplete evidence.
        raise AssertionError("jobs evidence unavailable")

    monkeypatch.setattr(reuse_mod.transport, "request_rest_json", fake_fetch)
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (), None),
    )

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
    monkeypatch.setattr(
        decision.governance,
        "read_pr_changed_paths",
        lambda *_a, **_k: (True, (".hunter/pre-ready-hostile-review.json",), None),
    )
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
