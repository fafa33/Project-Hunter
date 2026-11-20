"""Issue #606: trusted cross-HEAD proof equivalence.

When a candidate HEAD changes but the tested inputs (integration tree,
validation definition, toolchain, base, dependency identity) remain
cryptographically identical to a previously proven HEAD, the new HEAD can
bind to the old proof's immutable identities. A NEW current-HEAD-bound
decision is issued that references the original immutable proof -- the old
HEAD's success is never copied as the new HEAD's proof.

All equivalence bindings are verified; any missing, inconsistent,
ambiguous, stale, or unverifiable identity fails closed. Caller-supplied
run/attempt/job/digest labels are never accepted as proof: the canonical
authority derives every one from immutable hosted evidence.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
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
RUN_ATTEMPT = 2
JOB_ID = 42
PROOF_DIGEST = "sha256:" + "0" * 64
FAKE_TREE = "tree:" + "d" * 40
FAKE_TESTED_DIGEST = "sha256:" + "d" * 64


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _successful_proof_run(run_id: int = 100, head_sha: str = SOURCE_HEAD) -> dict[str, Any]:
    return {
        "id": run_id,
        "run_attempt": RUN_ATTEMPT,
        "head_sha": head_sha,
        "name": "Hunter / Pre-PR Preflight",
        "path": reuse.PRE_PR_WORKFLOW_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _mock_canonical_bindings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    attempt: int = RUN_ATTEMPT,
    job_id: int = JOB_ID,
    digest: str = PROOF_DIGEST,
    problem: str = "",
) -> None:
    monkeypatch.setattr(
        reuse,
        "_source_proof_bindings",
        lambda repo, token, source: (attempt, job_id, digest, problem),
    )


def _mock_identical_trees(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reuse,
        "compute_tested_tree_digest",
        lambda root, head_sha: (FAKE_TESTED_DIGEST, ""),
    )
    monkeypatch.setattr(receipts, "commit_tree_identity", lambda root, head_sha: FAKE_TREE)


def _mock_matching_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_identity = "sha256:" + "f" * 64
    monkeypatch.setattr(receipts, "toolchain_identity", lambda tc: fake_identity)


def _verify(
    root: Path = ROOT,
    **overrides: Any,
) -> reuse.EquivalenceDecision:
    kwargs: dict[str, Any] = {
        "repository": REPO,
        "pr_number": PR,
        "current_head": CURRENT_HEAD,
        "base_sha": BASE_SHA,
        "token": "token",
        "source_proof_head": SOURCE_HEAD,
    }
    kwargs.update(overrides)
    return reuse.verify_for_candidate(root, **kwargs)


# ---------------------------------------------------------------------------
# Cross-HEAD equivalence with identical inputs
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_identical_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new HEAD with identical tested inputs binds to the source proof."""
    _mock_canonical_bindings(monkeypatch)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)

    decision = _verify()

    assert decision.equivalent is True
    assert decision.binding is not None
    assert decision.binding.current_head == CURRENT_HEAD
    assert decision.binding.source_proof_head == SOURCE_HEAD
    assert decision.binding.proof_digest == PROOF_DIGEST
    assert "bound to" in decision.reason


def test_cross_head_equivalence_binding_contains_all_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every declared equivalence identity is present in the new binding."""
    _mock_canonical_bindings(monkeypatch)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)

    decision = _verify()
    assert decision.equivalent is True
    assert decision.binding is not None
    b = decision.binding
    assert b.repository == REPO
    assert b.pr_number == PR
    assert b.current_head == CURRENT_HEAD
    assert b.source_proof_head == SOURCE_HEAD
    assert b.base_sha == BASE_SHA
    assert b.tested_tree_digest == FAKE_TESTED_DIGEST
    assert b.dependency_identity
    assert b.workflow_definition_identity
    assert b.toolchain_identity
    assert b.environment_identity
    assert b.trusted_workflow == reuse.PRE_PR_WORKFLOW_PATH
    assert b.run_attempt == RUN_ATTEMPT
    assert b.job_identity == f"job:{JOB_ID}"
    assert b.proof_digest == PROOF_DIGEST
    assert b.provenance == f"commit:{CURRENT_HEAD}:tree:{FAKE_TREE}"


# ---------------------------------------------------------------------------
# Fail closed: malformed inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"current_head": "not-a-sha"}, "current head is not an exact 40-hex"),
        ({"base_sha": "not-a-sha"}, "base SHA is not an exact 40-hex"),
        ({"pr_number": 0}, "PR number must be positive"),
        ({"source_proof_head": "not-a-sha"}, "source proof head is not an exact 40-hex"),
    ],
)
def test_malformed_inputs_fail_closed(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], expected: str
) -> None:
    decision = _verify(**overrides)
    assert decision.equivalent is False
    assert expected in decision.reason


# ---------------------------------------------------------------------------
# Fail closed: source proof evidence
# ---------------------------------------------------------------------------


def test_no_previous_source_proof_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reuse,
        "find_previous_proven_head",
        lambda repo, token, current: (None, "no previous successful exact-head proof found"),
    )
    decision = _verify(source_proof_head=None)
    assert decision.equivalent is False
    assert "no source proof" in decision.reason


def test_canonical_digest_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_canonical_bindings(monkeypatch, digest="", problem="canonical proof digest unavailable")
    decision = _verify()
    assert decision.equivalent is False
    assert "canonical proof digest unavailable" in decision.reason


def _source_bindings_fetch(conclusion: str) -> tuple[Any, list[str]]:
    """A single job snapshot for source-binding tests; returns fetch and call log."""
    calls: list[str] = []

    def fake_fetch(repository: str, token: str, path: str) -> Any:
        calls.append(path)
        if "actions/runs?" in path:
            return {"workflow_runs": [_successful_proof_run()]}
        if "/jobs" in path:
            return {
                "jobs": [
                    {
                        "id": JOB_ID,
                        "run_id": 100,
                        "name": "Hunter Pre-PR Preflight",
                        "status": "completed",
                        "conclusion": conclusion,
                    }
                ]
            }
        raise AssertionError(f"unexpected fetch path: {path}")

    return fake_fetch, calls


def test_source_proof_bindings_use_one_job_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job identity and canonical digest derive from one validated API snapshot."""
    fake_fetch, calls = _source_bindings_fetch("success")
    monkeypatch.setattr(reuse, "_fetch", fake_fetch)

    attempt, job_id, digest, problem = reuse._source_proof_bindings(REPO, "token", SOURCE_HEAD)
    assert problem == ""
    assert attempt == RUN_ATTEMPT
    assert job_id == JOB_ID
    assert digest.startswith("sha256:")
    assert len([path for path in calls if "/jobs" in path]) == 1


def test_failed_job_snapshot_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed authoritative job in the single snapshot is never proven."""
    fake_fetch, calls = _source_bindings_fetch("failure")
    monkeypatch.setattr(reuse, "_fetch", fake_fetch)

    _attempt, _job_id, digest, problem = reuse._source_proof_bindings(REPO, "token", SOURCE_HEAD)
    assert digest == ""
    assert "did not succeed" in problem
    assert len([path for path in calls if "/jobs" in path]) == 1


def test_stale_source_proof_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful source proof older than the receipt bound is never reusable."""
    stale = (datetime.now(UTC) - timedelta(seconds=receipts.DEFAULT_MAX_AGE_SECONDS + 60)).isoformat()
    run = _successful_proof_run()
    run["updated_at"] = stale.replace("+00:00", "Z")

    def fake_fetch(repository: str, token: str, path: str) -> Any:
        if "actions/runs?" in path:
            return {"workflow_runs": [run]}
        raise AssertionError("stale proof must not fetch job evidence")

    monkeypatch.setattr(reuse, "_fetch", fake_fetch)
    attempt, job_id, digest, problem = reuse._source_proof_bindings(REPO, "token", SOURCE_HEAD)
    assert (attempt, job_id, digest) == (0, 0, "")
    assert "stale" in problem
    assert str(receipts.DEFAULT_MAX_AGE_SECONDS) in problem


def test_source_proof_without_completion_time_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run with no canonical timestamp cannot establish freshness."""
    run = _successful_proof_run()
    run.pop("updated_at")

    def fake_fetch(repository: str, token: str, path: str) -> Any:
        if "actions/runs?" in path:
            return {"workflow_runs": [run]}
        raise AssertionError("untimed proof must not fetch job evidence")

    monkeypatch.setattr(reuse, "_fetch", fake_fetch)
    attempt, job_id, digest, problem = reuse._source_proof_bindings(REPO, "token", SOURCE_HEAD)
    assert (attempt, job_id, digest) == (0, 0, "")
    assert "completion time is unavailable" in problem


def test_source_proof_freshness_uses_canonical_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canonical completion time is checked against the canonical age bound."""
    now = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    run = _successful_proof_run()
    run["updated_at"] = "2026-01-01T00:00:00Z"

    def fake_fetch(repository: str, token: str, path: str) -> Any:
        if "actions/runs?" in path:
            return {"workflow_runs": [run]}
        return {
            "jobs": [
                {
                    "id": JOB_ID,
                    "run_id": 100,
                    "name": "Hunter Pre-PR Preflight",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(reuse, "_fetch", fake_fetch)
    attempt, job_id, digest, problem = reuse._source_proof_bindings(REPO, "token", SOURCE_HEAD, now=now)
    assert problem == ""
    assert attempt == RUN_ATTEMPT and job_id == JOB_ID and digest.startswith("sha256:")


def test_previous_proven_head_ignores_unrelated_global_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    """The globally newest successful proof on an unrelated commit is not a source."""
    unrelated = "e" * 40
    ancestral = "f" * 40
    runs = [
        _successful_proof_run(run_id=999, head_sha=unrelated),
        _successful_proof_run(run_id=100, head_sha=ancestral),
    ]
    monkeypatch.setattr(reuse, "_fetch", lambda *_a, **_k: {"workflow_runs": runs})
    monkeypatch.setattr(reuse, "_is_ancestor", lambda root, a, d: a == ancestral)
    monkeypatch.setattr(reuse, "compute_tested_tree_digest", lambda root, head: (FAKE_TESTED_DIGEST, ""))
    checked: list[str] = []

    def fake_bindings(repository: str, token: str, head: str, **kwargs: Any) -> tuple[int, int, str, str]:
        checked.append(head)
        return RUN_ATTEMPT, JOB_ID, PROOF_DIGEST, ""

    monkeypatch.setattr(reuse, "_source_proof_bindings", fake_bindings)
    source, problem = reuse.find_previous_proven_head(REPO, "token", CURRENT_HEAD)
    assert problem == ""
    assert source == ancestral
    assert checked == [ancestral]


def test_previous_proven_head_requires_tree_and_canonical_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ancestral proof with a different tree or unverifiable bindings is skipped."""
    wrong_tree = "1" * 40
    wrong_binding = "2" * 40
    good = "3" * 40
    runs = [
        _successful_proof_run(run_id=300, head_sha=wrong_tree),
        _successful_proof_run(run_id=200, head_sha=wrong_binding),
        _successful_proof_run(run_id=100, head_sha=good),
    ]
    monkeypatch.setattr(reuse, "_fetch", lambda *_a, **_k: {"workflow_runs": runs})
    monkeypatch.setattr(reuse, "_is_ancestor", lambda root, a, d: True)

    def fake_digest(root: Path, head: str) -> tuple[str, str]:
        return ("sha256:" + "9" * 64, "") if head == wrong_tree else (FAKE_TESTED_DIGEST, "")

    monkeypatch.setattr(reuse, "compute_tested_tree_digest", fake_digest)
    checked: list[str] = []

    def fake_bindings(repository: str, token: str, head: str, **kwargs: Any) -> tuple[int, int, str, str]:
        checked.append(head)
        if head == wrong_binding:
            return 0, 0, "", "source proof freshness refused: stale"
        return RUN_ATTEMPT, JOB_ID, PROOF_DIGEST, ""

    monkeypatch.setattr(reuse, "_source_proof_bindings", fake_bindings)
    source, problem = reuse.find_previous_proven_head(REPO, "token", CURRENT_HEAD)
    assert problem == ""
    assert source == good
    assert set(checked) == {wrong_binding, good}


def test_previous_proven_head_fails_closed_without_eligible_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ancestral, tree-identical, canonically proven source means no equivalence."""
    runs = [_successful_proof_run(run_id=999, head_sha="e" * 40)]
    monkeypatch.setattr(reuse, "_fetch", lambda *_a, **_k: {"workflow_runs": runs})
    monkeypatch.setattr(reuse, "_is_ancestor", lambda root, a, d: False)
    monkeypatch.setattr(reuse, "compute_tested_tree_digest", lambda root, head: (FAKE_TESTED_DIGEST, ""))
    source, problem = reuse.find_previous_proven_head(REPO, "token", CURRENT_HEAD)
    assert source is None
    assert "no ancestral source proof" in problem


# ---------------------------------------------------------------------------
# Fail closed: tested tree differs
# ---------------------------------------------------------------------------


def test_cross_head_equivalence_different_trees_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When source and current tested trees differ, equivalence fails closed."""
    _mock_canonical_bindings(monkeypatch)

    def fake_digest(root: Path, head_sha: str) -> tuple[str, str]:
        if head_sha == SOURCE_HEAD:
            return "sha256:" + "d" * 64, ""
        return "sha256:" + "e" * 64, ""

    monkeypatch.setattr(reuse, "compute_tested_tree_digest", fake_digest)

    decision = _verify()
    assert decision.equivalent is False
    assert "tested tree differs" in decision.reason


# ---------------------------------------------------------------------------
# Enforcement entry point bindings
# ---------------------------------------------------------------------------


def test_verify_for_candidate_issues_current_head_bound_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """The enforcement entry point derives canonical proof then binds the current HEAD."""
    _mock_canonical_bindings(monkeypatch, attempt=2, job_id=42, digest="sha256:canonical")
    captured: dict[str, Any] = {}

    def fake_verify(root: Path, **kwargs: Any) -> reuse.EquivalenceDecision:
        captured.update(kwargs)
        return reuse.EquivalenceDecision(True, "equivalent", binding=None)

    monkeypatch.setattr(reuse, "verify_equivalence", fake_verify)
    decision = _verify()

    assert decision.equivalent is True
    assert captured["current_head"] == CURRENT_HEAD
    assert captured["source_proof_head"] == SOURCE_HEAD
    assert captured["run_attempt"] == 2
    assert captured["job_id"] == 42
    assert captured["proof_digest"] == "sha256:canonical"


# ---------------------------------------------------------------------------
# Tested-subtree digest is content-bound and metadata-independent
# ---------------------------------------------------------------------------


def test_tested_subtree_digest_excludes_review_metadata(tmp_path: Path) -> None:
    """A metadata-only commit leaves the digest intact; a code commit changes it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / ".hunter").mkdir()
    metadata = repo / ".hunter" / "pre-ready-hostile-review.json"
    metadata.write_text('{"v": 1}', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "source")
    source_head = _git(repo, "rev-parse", "HEAD")
    source_digest, problem = reuse.compute_tested_tree_digest(repo, source_head)
    assert problem == ""
    assert source_digest is not None

    metadata.write_text('{"v": 2}', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "metadata")
    metadata_head = _git(repo, "rev-parse", "HEAD")
    metadata_digest, problem = reuse.compute_tested_tree_digest(repo, metadata_head)
    assert problem == ""
    assert metadata_digest == source_digest

    (repo / "app.py").write_text("print(2)\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "code")
    code_head = _git(repo, "rev-parse", "HEAD")
    code_digest, problem = reuse.compute_tested_tree_digest(repo, code_head)
    assert problem == ""
    assert code_digest != metadata_digest


def test_tested_subtree_digest_is_deterministic() -> None:
    first, p1 = reuse.compute_tested_tree_digest(ROOT, "HEAD")
    second, p2 = reuse.compute_tested_tree_digest(ROOT, "HEAD")
    assert p1 == "" and p2 == ""
    assert first == second


# ---------------------------------------------------------------------------
# Operator CLI (authenticated through the same enforcement path)
# ---------------------------------------------------------------------------


def _cli_argv(**overrides: Any) -> list[str]:
    argv = [
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
    ]
    for key, value in overrides.items():
        argv.extend([f"--{key.replace('_', '-')}", str(value)])
    return argv


def test_cli_equivalent(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    _mock_canonical_bindings(monkeypatch)
    _mock_identical_trees(monkeypatch)
    _mock_matching_toolchain(monkeypatch)
    monkeypatch.setattr(sys, "argv", _cli_argv())
    assert final_execution.main() == 0


def test_cli_rejects_malformed_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", _cli_argv(current_head="not-a-sha"))
    assert final_execution.main() == 2


def test_cli_rejects_unproven_source_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI no longer accepts caller-supplied run/job/digest proof labels."""
    import sys

    monkeypatch.setattr(sys, "argv", _cli_argv(proof_digest="sha256:invented"))
    with pytest.raises(SystemExit):
        final_execution.main()


def test_performance_cli_has_no_caller_selected_write_target() -> None:
    """The measurement CLI cannot write to a caller-chosen path (Issue #606)."""
    script = (ROOT / "scripts" / "hunter_ci_final_execution.py").read_text(encoding="utf-8")
    assert 'add_argument("--output"' not in script
    assert "write_text" not in script


def test_performance_cli_emits_json_without_file_writes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        final_execution,
        "collect_performance_evidence",
        lambda *args, **kwargs: {"repository": REPO, "records": []},
    )
    assert final_execution.performance_main(["--repository", REPO]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == {"repository": REPO, "records": []}


# ---------------------------------------------------------------------------
# Hosted performance evidence
# ---------------------------------------------------------------------------


def test_performance_measurement_uses_real_timestamps_and_counts_api_calls() -> None:
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
    assert report["api_requests"] == 3


def test_equivalence_identities_complete() -> None:
    identities = frozenset(reuse.EQUIVALENCE_IDENTITIES)
    assert len(identities) == 15
    assert {"repository", "pr", "current_head", "source_proof_head", "base_sha"} <= identities
    assert {"run_attempt", "job_identity", "proof_digest", "provenance"} <= identities
