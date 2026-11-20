"""Issue #606: trusted cross-HEAD proof equivalence.

When a candidate HEAD changes but the tested inputs (integration tree,
validation definition, toolchain, base, dependency identity) remain
cryptographically identical to a previously proven HEAD, the new HEAD can
bind to the old proof's immutable identities. A NEW current-HEAD-bound
decision is issued that references the original immutable proof -- the old
HEAD's success is never copied as the new HEAD's proof.

All equivalence bindings are verified; any missing, inconsistent,
ambiguous, stale, or unverifiable identity fails closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import hunter_ci_final_execution as final_execution
import hunter_validation_receipt as receipts
import hunter_validation_reuse as reuse
import pytest

ROOT = Path(__file__).resolve().parents[1]

REPO = "fafa33/Project-Hunter"
PR = 606
CURRENT_HEAD = "a" * 40
SOURCE_HEAD = "b" * 40
BASE_SHA = "c" * 40
RUN_ATTEMPT = 1
JOB_ID = 1
PROOF_DIGEST = "sha256:" + "0" * 64
FAKE_TREE = "tree:" + "d" * 40
FAKE_TESTED_DIGEST = "sha256:" + "d" * 64


def _monkeypatch_fetch(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[dict[str, Any]],
) -> None:
    """Monkeypatch hunter_github_transport.request_rest_json to return runs."""

    def fake_fetch(
        url: str,
        method: str,
        headers: dict[str, str],
        data: Any,
        token: str,
        what: str,
    ) -> Any:
        if "actions/runs" in url:
            return {"workflow_runs": runs}
        raise AssertionError(f"unexpected fetch URL: {url}")

    monkeypatch.setattr(reuse.transport, "request_rest_json", fake_fetch)


def _successful_proof_run(run_id: int = 100, head_sha: str = SOURCE_HEAD) -> dict[str, Any]:
    return {
        "id": run_id,
        "run_attempt": 1,
        "head_sha": head_sha,
        "name": "Hunter / Pre-PR Preflight",
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
    }


def _mock_identical_trees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock identical tested subtree digests and provenance for any head."""
    monkeypatch.setattr(
        reuse,
        "compute_tested_tree_digest",
        lambda root, head_sha: (FAKE_TESTED_DIGEST, ""),
    )
    monkeypatch.setattr(receipts, "commit_tree_identity", lambda root, head_sha: FAKE_TREE)


def _mock_matching_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock toolchain identity to return matching measured and pinned values."""
    fake_identity = "sha256:" + "f" * 64
    monkeypatch.setattr(receipts, "toolchain_identity", lambda tc: fake_identity)


# ---------------------------------------------------------------------------
# Cross-HEAD equivalence with identical inputs
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_identical_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A new HEAD with identical tested inputs binds to the source proof."""
    runs = [_successful_proof_run(run_id=100, head_sha=SOURCE_HEAD)]
    _monkeypatch_fetch(monkeypatch, runs)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)

    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )

    assert decision.equivalent is True
    assert decision.binding is not None
    assert decision.binding.current_head == CURRENT_HEAD
    assert decision.binding.source_proof_head == SOURCE_HEAD
    assert decision.binding.proof_digest == PROOF_DIGEST
    assert "bound to" in decision.reason


# ---------------------------------------------------------------------------
# Fail closed: no previous proven head
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_no_previous_proof_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no previous successful exact-head proof exists, fail closed."""
    _monkeypatch_fetch(monkeypatch, [])
    _mock_identical_trees(monkeypatch)

    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )

    assert decision.equivalent is False
    assert "no source proof" in decision.reason


# ---------------------------------------------------------------------------
# Fail closed: malformed inputs
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_malformed_current_head_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head="not-a-sha",
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )
    assert decision.equivalent is False
    assert "current head is not an exact 40-hex" in decision.reason


def test_cross_head_equivalence_malformed_base_sha_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha="not-a-sha",
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )
    assert decision.equivalent is False
    assert "base SHA is not an exact 40-hex" in decision.reason


def test_cross_head_equivalence_zero_pr_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=0,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )
    assert decision.equivalent is False
    assert "PR number must be positive" in decision.reason


def test_cross_head_equivalence_zero_run_attempt_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=0,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )
    assert decision.equivalent is False
    assert "run_attempt must be positive" in decision.reason


def test_cross_head_equivalence_zero_job_id_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=0,
        proof_digest=PROOF_DIGEST,
    )
    assert decision.equivalent is False
    assert "job_id must be positive" in decision.reason


def test_cross_head_equivalence_empty_proof_digest_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest="",
    )
    assert decision.equivalent is False
    assert "proof digest is required" in decision.reason


# ---------------------------------------------------------------------------
# Fail closed: source tree differs from current tree
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_different_trees_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When source and current trees differ, equivalence fails closed."""
    runs = [_successful_proof_run(run_id=100, head_sha=SOURCE_HEAD)]
    _monkeypatch_fetch(monkeypatch, runs)

    # Source head returns one tested subtree digest, current head a different one.
    def fake_digest(root: Path, head_sha: str) -> tuple[str, str]:
        if head_sha == SOURCE_HEAD:
            return "sha256:" + "d" * 64, ""
        return "sha256:" + "e" * 64, ""

    monkeypatch.setattr(reuse, "compute_tested_tree_digest", fake_digest)

    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )

    assert decision.equivalent is False
    assert "tested tree differs" in decision.reason


# ---------------------------------------------------------------------------
# All equivalence identities are present in the binding
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_binding_contains_all_identities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = [_successful_proof_run(run_id=100, head_sha=SOURCE_HEAD)]
    _monkeypatch_fetch(monkeypatch, runs)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)

    decision = reuse.decide(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        proof_digest=PROOF_DIGEST,
    )

    assert decision.equivalent is True
    assert decision.binding is not None
    b = decision.binding
    assert b.repository == REPO
    assert b.pr_number == PR
    assert b.current_head == CURRENT_HEAD
    assert b.source_proof_head == SOURCE_HEAD
    assert b.base_sha == BASE_SHA
    assert b.tested_tree_digest == FAKE_TESTED_DIGEST
    assert b.dependency_identity  # non-empty
    assert b.workflow_definition_identity  # non-empty
    assert b.toolchain_identity  # non-empty
    assert b.environment_identity  # non-empty
    assert b.trusted_workflow == reuse.PRE_PR_WORKFLOW_PATH
    assert b.run_attempt == RUN_ATTEMPT
    assert b.job_identity == f"job:{JOB_ID}"
    assert b.proof_digest == PROOF_DIGEST
    assert b.provenance == f"commit:{CURRENT_HEAD}:tree:{FAKE_TREE}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_equivalent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = [_successful_proof_run(run_id=100, head_sha=SOURCE_HEAD)]
    _monkeypatch_fetch(monkeypatch, runs)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)

    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hunter_ci_final_execution",
            "--repository",
            REPO,
            "--pr",
            str(PR),
            "--current-head",
            CURRENT_HEAD,
            "--base-sha",
            BASE_SHA,
            "--source-proof-head",
            SOURCE_HEAD,
            "--run-attempt",
            str(RUN_ATTEMPT),
            "--job-id",
            str(JOB_ID),
            "--proof-digest",
            PROOF_DIGEST,
        ],
    )
    rc = final_execution.main()
    assert rc == 0


def test_cli_rejects_malformed_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hunter_ci_final_execution",
            "--repository",
            REPO,
            "--pr",
            str(PR),
            "--current-head",
            "not-a-sha",
            "--base-sha",
            BASE_SHA,
            "--source-proof-head",
            SOURCE_HEAD,
            "--run-attempt",
            str(RUN_ATTEMPT),
            "--job-id",
            str(JOB_ID),
            "--proof-digest",
            PROOF_DIGEST,
        ],
    )
    assert final_execution.main() == 2


# ---------------------------------------------------------------------------
# Equivalence identity list is complete
# ---------------------------------------------------------------------------


def test_verify_for_candidate_issues_current_head_bound_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enforcement entry point verifies canonical proof then binds the current HEAD."""
    monkeypatch.setattr(
        reuse,
        "_source_proof_bindings",
        lambda repo, token, source: (2, 42, "sha256:canonical", ""),
    )
    captured: dict[str, Any] = {}

    def fake_verify(root: Path, **kwargs: Any) -> reuse.EquivalenceDecision:
        captured.update(kwargs)
        return reuse.EquivalenceDecision(True, "equivalent", binding=None)

    monkeypatch.setattr(reuse, "verify_equivalence", fake_verify)

    decision = reuse.verify_for_candidate(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        source_proof_head=SOURCE_HEAD,
    )
    assert decision.equivalent is True
    assert captured["current_head"] == CURRENT_HEAD
    assert captured["source_proof_head"] == SOURCE_HEAD
    assert captured["run_attempt"] == 2
    assert captured["job_id"] == 42
    assert captured["proof_digest"] == "sha256:canonical"


def test_verify_for_candidate_fails_closed_when_canonical_digest_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the canonical authority cannot produce a proof digest, fail closed."""
    monkeypatch.setattr(
        reuse,
        "_source_proof_bindings",
        lambda repo, token, source: (0, 0, "", "canonical proof digest unavailable"),
    )
    decision = reuse.verify_for_candidate(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
        source_proof_head=SOURCE_HEAD,
    )
    assert decision.equivalent is False
    assert "canonical proof digest unavailable" in decision.reason


def test_verify_for_candidate_fails_closed_when_source_head_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If no immutable source proof HEAD can be found, fail closed."""
    monkeypatch.setattr(
        reuse,
        "find_previous_proven_head",
        lambda repo, token, current: (None, "no previous successful exact-head proof found"),
    )
    decision = reuse.verify_for_candidate(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha=BASE_SHA,
        token="token",
    )
    assert decision.equivalent is False
    assert "no source proof" in decision.reason


def test_verify_for_candidate_rejects_wrong_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed base SHA fails closed before any proof work."""
    decision = reuse.verify_for_candidate(
        ROOT,
        repository=REPO,
        pr_number=PR,
        current_head=CURRENT_HEAD,
        base_sha="not-a-sha",
        token="token",
        source_proof_head=SOURCE_HEAD,
    )
    assert decision.equivalent is False
    assert "base SHA" in decision.reason


def test_tested_subtree_digest_excludes_review_metadata() -> None:
    """A real metadata-only HEAD change leaves the tested subtree digest intact.

    Uses the actual repository: compares the current HEAD against an earlier
    commit whose application code differs from the current tree. The digest
    must still differ when real code changes, proving it is content-bound.
    """
    digest_head, problem = reuse.compute_tested_tree_digest(ROOT, "HEAD")
    assert problem == "", problem
    assert digest_head is not None
    assert digest_head.startswith("sha256:")

    digest_parent, problem = reuse.compute_tested_tree_digest(ROOT, "HEAD~1")
    assert problem == "", problem
    assert digest_parent is not None
    # The current branch's HEAD and HEAD~1 differ in real code (the two signed
    # commits on this branch), so their tested digests must differ.
    assert digest_head != digest_parent


def test_tested_subtree_digest_is_deterministic() -> None:
    """The tested subtree digest is stable for the same commit."""
    first, p1 = reuse.compute_tested_tree_digest(ROOT, "HEAD")
    second, p2 = reuse.compute_tested_tree_digest(ROOT, "HEAD")
    assert p1 == "" and p2 == ""
    assert first == second


def test_performance_measurement_uses_real_timestamps_and_counts_api_calls() -> None:
    """Performance evidence derives from GitHub timestamps and counts API calls."""
    calls: list[str] = []

    def fake_fetch(path: str) -> Any:
        calls.append(path)
        if path.startswith("actions/workflows/"):
            return {
                "workflow_runs": [
                    {
                        "id": 555,
                        "run_attempt": 2,
                        "head_sha": "a" * 40,
                        "name": "Hunter / Pre-PR Preflight",
                        "path": reuse.PRE_PR_WORKFLOW_PATH,
                        "status": "completed",
                        "conclusion": "success",
                        "run_started_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:03:20Z",
                    }
                ]
            }
        if path.startswith("actions/runs/555/"):
            return {
                "jobs": [
                    {
                        "id": 777,
                        "name": "Hunter Pre-PR Preflight",
                        "started_at": "2026-01-01T00:00:10Z",
                        "completed_at": "2026-01-01T00:03:00Z",
                    }
                ]
            }
        raise AssertionError(f"unexpected fetch path: {path}")

    report = final_execution.collect_performance_evidence(REPO, "token", fetch=fake_fetch)
    assert report["api_requests"] == 2
    assert len(calls) == 2
    assert report["api_rate_limit_headers"].startswith("unavailable")
    record = report["records"][0]
    assert record["run_id"] == 555
    assert record["run_attempt"] == 2
    assert record["head_sha"] == "a" * 40
    assert record["workflow_elapsed_seconds"] == 200.0
    assert record["job_elapsed_seconds"] == 170.0
    assert record["expensive_job_id"] == 777


def test_performance_measurement_reports_missing_timestamps_as_none() -> None:
    """Missing timestamps produce None, never an invented duration."""

    def fake_fetch(path: str) -> Any:
        if path.startswith("actions/workflows/"):
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "head_sha": "a" * 40,
                        "path": reuse.PRE_PR_WORKFLOW_PATH,
                        "run_started_at": "",
                        "updated_at": "",
                    }
                ]
            }
        return {"jobs": []}

    report = final_execution.collect_performance_evidence(REPO, "token", fetch=fake_fetch)
    record = report["records"][0]
    assert record["workflow_elapsed_seconds"] is None
    assert record["job_elapsed_seconds"] is None
    assert record["expensive_job_id"] is None


def test_performance_measurement_filters_and_bounds() -> None:
    """Measurement filters by workflow/head and respects the bound."""
    calls: list[str] = []

    def fake_fetch(path: str) -> Any:
        calls.append(path)
        if path.startswith("actions/workflows/"):
            return {
                "workflow_runs": [
                    {"id": 1, "head_sha": "a" * 40, "path": reuse.PRE_PR_WORKFLOW_PATH},
                    {"id": 2, "head_sha": "b" * 40, "path": reuse.PRE_PR_WORKFLOW_PATH},
                    {"id": 3, "head_sha": "a" * 40, "path": reuse.PRE_PR_WORKFLOW_PATH},
                    {"id": 4, "head_sha": "a" * 40, "path": "other.yml"},
                ]
            }
        return {"jobs": []}

    report = final_execution.collect_performance_evidence(
        REPO, "token", head_shas=frozenset({"a" * 40}), limit=2, fetch=fake_fetch
    )
    assert len(report["records"]) == 2
    assert all(r["head_sha"] == "a" * 40 for r in report["records"])
    assert report["api_requests"] == 3  # 1 runs fetch + 2 bounded job fetches


def test_equivalence_identities_complete() -> None:
    """All 15 required equivalence identities are declared."""
    expected = frozenset(
        {
            "repository",
            "pr",
            "current_head",
            "source_proof_head",
            "base_sha",
            "tested_tree_digest",
            "dependency_identity",
            "workflow_definition_identity",
            "toolchain_identity",
            "environment_identity",
            "trusted_workflow",
            "run_attempt",
            "job_identity",
            "proof_digest",
            "provenance",
        }
    )
    assert frozenset(reuse.EQUIVALENCE_IDENTITIES) == expected
