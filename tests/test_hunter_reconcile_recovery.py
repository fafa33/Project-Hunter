"""Bounded recovery for a failed post-push Governance Reconcile (Issue #585).

Focused regression coverage: exact run/job detection bound to the exact PR
head, stale-request recovery through the existing writer finalizer, transient
waiting without an attempt, permanent blockage, duplicate-event idempotence,
stale-HEAD refusal, budget exhaustion, publisher-candidate immutability, and
containment of the externally supplied recovery checkpoint path.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import hunter_pre_ready_review as review
import hunter_reconcile_recovery as recovery
import pytest
from review_request_fixture import CRITERION, ISSUE, JUDGEMENT, REGISTRY

PR = 584
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
WORKFLOW = {"workflows": [{"id": 7, "path": recovery.RECONCILE_WORKFLOW_FILE}]}
RUN = {"id": 111, "conclusion": "success"}
JOB = {"id": 222, "name": recovery.AUTHORITATIVE_JOB_NAME, "conclusion": "success"}
STALE_LOG = (
    "::warning::WAITING_FOR_EXACT_HEAD_REVIEW: exact-head review orchestration cannot start for "
    f"PR #{PR} at {HEAD[:10]}: the exact-head pre-ready review request is stale. "
    "Reconcile will start orchestration as soon as a usable one is committed.\n"
)
PERMANENT_LOG = f"::error::Governance refresh failed for PR #{PR}; continuing.\n"
TRANSIENT_LOG = (
    "Governance review infrastructure unavailable: GitHubUnavailable: transient\n"
    f"::error::Governance refresh failed for PR #{PR}; continuing.\n"
)


@pytest.fixture(autouse=True)
def _sandbox_state_roots(tmp_path, monkeypatch):
    """Confine the sanctioned checkpoint roots to this test's sandbox."""

    monkeypatch.setattr(recovery, "STATE_ROOTS", (tmp_path.resolve(strict=False),))


def _fake_github(monkeypatch, *, run=RUN, log=STALE_LOG):
    """A read-only fake of the exact reconcile evidence this recovery consumes."""

    def fake_request(repository, token, method, path):
        assert method == "GET"
        if path == "actions/workflows?per_page=100":
            return WORKFLOW
        if path == "actions/workflows/7/runs?per_page=5":
            return {"workflow_runs": [run]}
        if path == "actions/runs/111/jobs?per_page=100":
            return {"jobs": [JOB]}
        if path == f"pulls/{PR}":
            return {"state": "open", "head": {"sha": HEAD}}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(recovery, "request_json", fake_request)
    monkeypatch.setattr(recovery, "default_job_log", lambda repository, token: lambda job_id: log)


def _fake_issue_api(monkeypatch):
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "gh":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"number": 585, "body": f"## Acceptance Criteria\n- {CRITERION}"})
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(review.subprocess, "run", fake_run)


def _resolve_judgement(value):
    path = review.Path(value).resolve(strict=True)
    if not path.is_file():
        raise ValueError("review judgement must be a regular file inside the authorized checkout")
    return path


def _git(tmp_path, *args, check=True):
    result = subprocess.run(("git", *args), cwd=tmp_path, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def _stale_candidate_repo(tmp_path, monkeypatch):
    """A governed candidate whose remediation commit left its review request stale."""

    _fake_issue_api(monkeypatch)
    monkeypatch.setattr(
        review,
        "_trusted_judgement_path",
        lambda value: _resolve_judgement(value),
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Hunter Test")
    _git(tmp_path, "config", "user.email", "hunter@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text(json.dumps(REGISTRY))
    # The judgement source is a writer-local input; keep it out of the candidate.
    info_exclude = tmp_path / ".git" / "info" / "exclude"
    info_exclude.write_text("review-judgement.json\n")
    _git(tmp_path, "add", "docs/DEFECT_REGISTRY.json")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("v1\n")
    _git(tmp_path, "add", "src/module.py")
    judgement_path = tmp_path / "review-judgement.json"
    judgement_path.write_text(json.dumps(JUDGEMENT))
    monkeypatch.setenv("HUNTER_REVIEW_JUDGEMENT", str(judgement_path))
    monkeypatch.setenv("HUNTER_GOVERNING_ISSUE", ISSUE)
    import hunter_writer_commit_finalizer as finalizer

    assert finalizer.main(tmp_path) == 0
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "governed candidate")
    # Remediation committed without refreshing the request: the exact defect of Issue #585.
    (tmp_path / "src" / "module.py").write_text("v2\n")
    _git(tmp_path, "add", "src/module.py")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-qm", "remediation")
    return judgement_path, _git(tmp_path, "rev-parse", "HEAD")


def _stale_failure(head_sha: str = HEAD):
    return recovery.ReconcileFailure(
        recovery.KIND_STALE_REQUEST, run_id=111, job_id=222, head_sha=head_sha, detail=STALE_LOG.strip()
    )


def test_exact_failed_run_and_job_are_detected_and_head_bound(monkeypatch):
    _fake_github(monkeypatch)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: STALE_LOG)
    assert failure is not None
    assert failure.kind == recovery.KIND_STALE_REQUEST
    assert (failure.run_id, failure.job_id) == (111, 222)
    assert failure.head_sha == HEAD


def test_stale_request_failure_recovers_through_existing_finalizer(tmp_path, monkeypatch):
    """Test 5: a recoverable reconcile failure produces a bounded, executed recovery."""

    judgement_path, head = _stale_candidate_repo(tmp_path, monkeypatch)
    assert review.read_review_document(tmp_path / review.REVIEW_RELATIVE_PATH)["claims"]["findings"] == []
    _fake_github(monkeypatch)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: STALE_LOG)
    assert failure is not None
    state_path = tmp_path / "recovery-state.json"
    document = recovery.load_state(state_path)
    plan = recovery.plan_recovery(_stale_failure(head), document, pr_number=PR, head_sha=head)
    assert isinstance(plan, recovery.RecoveryPlan)
    assert plan.attempt == 1
    judgement = json.loads(judgement_path.read_text(encoding="utf-8"))
    attempt = recovery.execute_recovery(plan, root=tmp_path, issue=ISSUE, judgement=judgement)
    assert attempt["regenerated"] is True
    bound = {
        item["path"]: item["blob_sha"]
        for item in review.read_review_document(tmp_path / review.REVIEW_RELATIVE_PATH)["claims"]["review_target"]
    }
    assert bound["src/module.py"] == _git(tmp_path, "rev-parse", "HEAD:src/module.py")
    recovery.record_event(
        document,
        {
            "observed_at": "2026-11-16T00:00:00Z",
            "pr_number": PR,
            "head_sha": head,
            "kind": failure.kind,
            "run_id": failure.run_id,
            "job_id": failure.job_id,
            "attempt": plan.attempt,
            "regenerated": True,
        },
        state_path,
    )
    events = recovery.load_state(state_path)["events"]
    assert any(item.get("attempt") == 1 for item in events)


def test_permanent_failure_remains_blocked(tmp_path, monkeypatch):
    """Test 6: a permanent reconcile failure is recorded and never recovered."""

    _fake_github(monkeypatch, run={"id": 111, "conclusion": "failure"}, log=PERMANENT_LOG)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: PERMANENT_LOG)
    assert failure is not None and failure.kind == recovery.KIND_PERMANENT
    plan = recovery.plan_recovery(failure, recovery.load_state(tmp_path / "s.json"), pr_number=PR, head_sha=HEAD)
    assert isinstance(plan, recovery.RecoveryRefusal)
    assert plan.code == "PERMANENT"


def test_transient_failure_waits_without_spending_an_attempt(tmp_path, monkeypatch):
    """A transient failure is left to the existing reconcile sweeps; no attempt is spent."""

    _fake_github(monkeypatch, run={"id": 111, "conclusion": "failure"}, log=TRANSIENT_LOG)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: TRANSIENT_LOG)
    assert failure is not None and failure.kind == recovery.KIND_TRANSIENT
    plan = recovery.plan_recovery(failure, recovery.load_state(tmp_path / "s.json"), pr_number=PR, head_sha=HEAD)
    assert isinstance(plan, recovery.RecoveryRefusal)
    assert plan.code == "TRANSIENT"


def test_transient_marker_in_another_prs_section_does_not_reclassify_ours(monkeypatch):
    """A transient diagnosis far from this PR's failure marker belongs to another PR."""

    filler = "\n".join(
        f"unrelated reconcile output line {index}" for index in range(recovery.TRANSIENT_CONTEXT_LINES + 2)
    )
    log = (
        "Governance review infrastructure unavailable: GitHubUnavailable: transient\n"
        f"{filler}\n"
        f"::error::Governance refresh failed for PR #{PR}; continuing.\n"
    )
    _fake_github(monkeypatch, run={"id": 111, "conclusion": "failure"}, log=log)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: log)
    assert failure is not None and failure.kind == recovery.KIND_PERMANENT


def test_older_runs_are_searched_when_the_newest_run_is_unrelated(monkeypatch):
    """A newest run whose log never mentions this PR did not reconcile it; keep searching."""

    def fake_request(repository, token, method, path):
        if path == "actions/workflows?per_page=100":
            return WORKFLOW
        if path == f"actions/workflows/7/runs?per_page={recovery.MAX_SCANNED_RUNS}":
            return {"workflow_runs": [{"id": 150, "conclusion": "failure"}, RUN]}
        if path == "actions/runs/150/jobs?per_page=100":
            return {"jobs": [{"id": 333, "name": recovery.AUTHORITATIVE_JOB_NAME, "conclusion": "failure"}]}
        if path == "actions/runs/111/jobs?per_page=100":
            return {"jobs": [JOB]}
        raise AssertionError(f"unexpected path {path}")

    logs = {
        333: "::error::Governance refresh failed for PR #999; continuing.\n",
        222: STALE_LOG,
    }
    monkeypatch.setattr(recovery, "request_json", fake_request)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: logs[job_id])
    assert failure is not None
    assert (failure.kind, failure.run_id, failure.job_id) == (recovery.KIND_STALE_REQUEST, 111, 222)


def test_a_run_that_reconciled_the_pr_without_failure_ends_the_scan(monkeypatch):
    """Positive evidence stops the scan: this PR was reconciled with no failure."""

    log = f"PR #{PR}: governance refresh ok; exact-head request current at {HEAD[:10]}.\n"
    _fake_github(monkeypatch, log=log)
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: log)
    assert failure is None


def test_duplicate_recovery_events_create_no_new_attempt(tmp_path):
    """Test 7: the same failure event is idempotent; re-observing it cannot re-attempt."""

    document = recovery.load_state(tmp_path / "s.json")
    first = recovery.plan_recovery(_stale_failure(), document, pr_number=PR, head_sha=HEAD)
    assert isinstance(first, recovery.RecoveryPlan)
    recovery.record_event(
        document,
        {
            "observed_at": "2026-11-16T00:00:00Z",
            "pr_number": PR,
            "head_sha": HEAD,
            "kind": recovery.KIND_STALE_REQUEST,
            "run_id": first.run_id,
            "job_id": 222,
            "attempt": first.attempt,
        },
        tmp_path / "s.json",
    )
    again = recovery.plan_recovery(
        _stale_failure(), recovery.load_state(tmp_path / "s.json"), pr_number=PR, head_sha=HEAD
    )
    assert isinstance(again, recovery.RecoveryRefusal)
    assert again.code == "ALREADY_RECOVERED"
    # A NEW failing run for the same head is a new event and spends the next attempt.
    newer = recovery.plan_recovery(
        recovery.ReconcileFailure(
            recovery.KIND_STALE_REQUEST, run_id=333, job_id=222, head_sha=HEAD, detail="new failing sweep"
        ),
        recovery.load_state(tmp_path / "s.json"),
        pr_number=PR,
        head_sha=HEAD,
    )
    assert isinstance(newer, recovery.RecoveryPlan)
    assert newer.attempt == first.attempt + 1


def test_stale_head_prevents_recovery(tmp_path):
    """Test 8: recovery bound to a head that no longer exists is refused."""

    failure = recovery.ReconcileFailure(
        recovery.KIND_STALE_REQUEST, run_id=111, job_id=222, head_sha=OTHER_HEAD, detail="old failure"
    )
    plan = recovery.plan_recovery(failure, recovery.load_state(tmp_path / "s.json"), pr_number=PR, head_sha=HEAD)
    assert isinstance(plan, recovery.RecoveryRefusal)
    assert plan.code == "STALE_HEAD"


def test_recovery_budget_exhaustion_blocks_further_attempts(tmp_path):
    """Test 9: the bounded per-head budget is enforced against the durable checkpoint."""

    document = recovery.load_state(tmp_path / "s.json")
    for attempt in range(1, recovery.MAX_RECOVERY_ATTEMPTS + 1):
        recovery.record_event(
            document,
            {
                "observed_at": "2026-11-16T00:00:00Z",
                "pr_number": PR,
                "head_sha": HEAD,
                "kind": recovery.KIND_STALE_REQUEST,
                "run_id": 111 + attempt,
                "job_id": 222,
                "attempt": attempt,
            },
            tmp_path / "s.json",
        )
    plan = recovery.plan_recovery(
        _stale_failure(), recovery.load_state(tmp_path / "s.json"), pr_number=PR, head_sha=HEAD
    )
    assert isinstance(plan, recovery.RecoveryRefusal)
    assert plan.code == "RECOVERY_BUDGET_EXHAUSTED"


def test_attempt_counters_survive_history_trimming(tmp_path):
    """Trimming the tracked history never drops a counter or resets the bounded budget."""

    state = tmp_path / "s.json"
    document = recovery.load_state(state)
    recovery.record_event(
        document,
        {
            "observed_at": "2026-11-16T00:00:00Z",
            "pr_number": PR,
            "head_sha": HEAD,
            "kind": recovery.KIND_STALE_REQUEST,
            "run_id": 999,
            "job_id": 222,
            "attempt": 1,
        },
        state,
    )
    for index in range(recovery.MAX_TRACKED_EVENTS + 3):
        recovery.record_event(
            document,
            {
                "observed_at": "2026-11-16T00:00:00Z",
                "pr_number": PR,
                "head_sha": HEAD,
                "kind": recovery.KIND_TRANSIENT,
                "run_id": 500 + index,
                "job_id": 222,
                "refused": "TRANSIENT",
            },
            state,
        )
    events = recovery.load_state(state)["events"]
    counters = [item for item in events if item.get("attempt") is not None]
    assert [item["attempt"] for item in counters] == [1]
    # The durable counter still feeds the bounded budget: the next run plans attempt 2.
    plan = recovery.plan_recovery(_stale_failure(), recovery.load_state(state), pr_number=PR, head_sha=HEAD)
    assert isinstance(plan, recovery.RecoveryPlan)
    assert plan.attempt == 2
    # Idempotence: re-saving the already-saved state changes nothing further.
    before = recovery.load_state(state)
    recovery.save_state(before, state)
    assert recovery.load_state(state) == before


def test_recovery_never_mutates_the_candidate_beyond_the_request(tmp_path, monkeypatch):
    """Test 10: a recovery attempt changes only the review-request artifact."""

    judgement_path, head = _stale_candidate_repo(tmp_path, monkeypatch)
    _fake_github(monkeypatch)
    document = recovery.load_state(tmp_path / "recovery-state.json")
    plan = recovery.plan_recovery(_stale_failure(head), document, pr_number=PR, head_sha=head)
    judgement = json.loads(judgement_path.read_text(encoding="utf-8"))
    head_before = _git(tmp_path, "rev-parse", "HEAD")
    recovery.execute_recovery(plan, root=tmp_path, issue=ISSUE, judgement=judgement)
    assert _git(tmp_path, "rev-parse", "HEAD") == head_before
    changed = _git(tmp_path, "status", "--porcelain").splitlines()
    assert [line.strip() for line in changed] == ["M .hunter/pre-ready-hostile-review.json"]


def test_recovered_request_still_carries_no_review_authority(tmp_path, monkeypatch):
    """A recovery re-requests review for the new content; it fabricates no authority."""

    judgement_path, head = _stale_candidate_repo(tmp_path, monkeypatch)
    _fake_github(monkeypatch)
    document = recovery.load_state(tmp_path / "recovery-state.json")
    plan = recovery.plan_recovery(_stale_failure(head), document, pr_number=PR, head_sha=head)
    judgement = json.loads(judgement_path.read_text(encoding="utf-8"))
    recovery.execute_recovery(plan, root=tmp_path, issue=ISSUE, judgement=judgement)
    regenerated = review.read_review_document(tmp_path / review.REVIEW_RELATIVE_PATH)
    assert "authority" not in regenerated
    assert regenerated["review_request"]["schema"] == "hunter.review-request.v1"
    assert regenerated["review_request"]["claims_id"] == regenerated["review_id"]


def test_main_refuses_a_head_the_live_pr_does_not_carry(monkeypatch, tmp_path):
    """Recovery binds to the live GitHub PR head, not to the caller's claim."""

    _fake_github(monkeypatch)
    code = recovery.main(["--pr", str(PR), "--head", OTHER_HEAD, "--state", str(tmp_path / "state.json")])
    assert code == 2


def test_main_plans_without_executing(monkeypatch, tmp_path):
    _fake_github(monkeypatch)
    state = tmp_path / "state.json"
    code = recovery.main(["--pr", str(PR), "--head", HEAD, "--state", str(state)])
    assert code == 0
    document = recovery.load_state(state)
    assert not any(item.get("attempt") is not None for item in document["events"])


def test_unreadable_reconcile_evidence_fails_closed(monkeypatch):
    _fake_github(monkeypatch, run={"id": 111, "conclusion": "failure"})
    with pytest.raises(recovery.ObservationUnavailable):
        recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: "")


def test_job_log_redirect_is_followed_without_credentials(monkeypatch):
    """The signed blob URL must never receive the API credential (Azure rejects it)."""

    import urllib.error

    seen: list[dict[str, str]] = []

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._body

    class FakeOpener:
        def open(self, request, timeout=0):
            seen.append(dict(request.headers))
            if len(seen) == 1:
                raise urllib.error.HTTPError(
                    request.full_url, 302, "Found", {"Location": "https://signed.blob.example.invalid/log"}, None
                )
            return FakeResponse(b"job log text")

    monkeypatch.setattr(recovery.urllib.request, "build_opener", lambda *a, **k: FakeOpener())
    reader = recovery.default_job_log("fafa33/Project-Hunter", "secret-token")
    assert reader(222) == "job log text"
    assert seen[0]["Authorization"] == "Bearer secret-token"
    second = seen[1]
    assert not any("Authorization" in key for key in second)


def test_state_path_inside_the_sanctioned_roots_is_normalized_and_accepted(tmp_path):
    """A faulty argument that stays inside a sanctioned root is bound, not trusted."""

    nested = tmp_path / "deep" / "sub" / "state.json"
    tricky = tmp_path / "deep" / ".." / "state.json"
    assert recovery.validate_state_path(str(nested)) == nested.resolve(strict=False)
    # `..` that stays inside the root collapses; the bound path is the resolved one.
    assert recovery.validate_state_path(str(tricky)) == (tmp_path / "state.json").resolve(strict=False)


def test_state_path_traversal_outside_the_sanctioned_roots_is_refused(tmp_path):
    """A `..` escape or absolute outside path is refused; recovery fails closed."""

    escape = tmp_path / ".." / "elsewhere.json"
    with pytest.raises(recovery.ObservationUnavailable, match="escapes the sanctioned"):
        recovery.validate_state_path(str(escape))
    with pytest.raises(recovery.ObservationUnavailable, match="escapes the sanctioned"):
        recovery.validate_state_path("/etc/hunter-recovery-state.json")


def test_state_path_must_be_a_json_checkpoint(tmp_path):
    with pytest.raises(recovery.ObservationUnavailable, match="must be a .json file"):
        recovery.validate_state_path(str(tmp_path / "state.yaml"))


def test_load_state_never_reads_outside_the_sanctioned_roots(tmp_path, monkeypatch):
    """The sink itself refuses: an unvalidated caller cannot read foreign files."""

    decoy = tmp_path / "outside" / "state.json"
    decoy.parent.mkdir()
    decoy.write_text('{"schema": "anything", "events": []}')
    monkeypatch.setattr(recovery, "STATE_ROOTS", (tmp_path / "sanctioned",))
    with pytest.raises(recovery.ObservationUnavailable, match="escapes the sanctioned"):
        recovery.load_state(decoy)
    assert decoy.read_text() == '{"schema": "anything", "events": []}'


def test_save_state_never_replaces_outside_the_sanctioned_roots(tmp_path, monkeypatch):
    """The atomic replace is bound to the sanctioned roots even for a direct caller."""

    victim = tmp_path / "outside" / "authorized_keys.json"
    victim.parent.mkdir()
    victim.write_text("original\n")
    document = {"schema": recovery.STATE_SCHEMA, "events": []}
    monkeypatch.setattr(recovery, "STATE_ROOTS", (tmp_path / "sanctioned",))
    with pytest.raises(recovery.ObservationUnavailable, match="escapes the sanctioned"):
        recovery.save_state(document, victim)
    assert victim.read_text() == "original\n"
    # No temporary checkpoint residue was left next to the refused target.
    assert list(victim.parent.iterdir()) == [victim]


def test_main_refuses_an_escaped_state_path_before_any_github_contact(monkeypatch, tmp_path):
    """A faulty `--state` fails closed locally, before any network request."""

    def no_network(*args, **kwargs):
        raise AssertionError("no GitHub request may happen for a refused checkpoint path")

    monkeypatch.setattr(recovery, "request_json", no_network)
    code = recovery.main(["--pr", str(PR), "--head", HEAD, "--state", str(tmp_path / ".." / "escape.json")])
    assert code == 1


def test_pr_number_boundary_excludes_longer_pr_numbers():
    assert recovery._pr_lines("Governance refresh failed for PR #5840\n", 584) == []
    assert recovery._pr_lines("Governance refresh failed for PR #584\n", 584)


def test_unrelated_newest_run_with_similar_pr_number_is_ignored(monkeypatch):
    _fake_github(monkeypatch)
    log = "Governance refresh failed for PR #5840\n"
    assert recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: log) is None


def test_review_orchestration_failure_is_blocking(monkeypatch):
    _fake_github(monkeypatch)
    log = f"::error::Review orchestration failed for PR #{PR}; continuing.\n"
    failure = recovery.observe_failure("fafa33/Project-Hunter", "t", PR, HEAD, read_log=lambda job_id: log)
    assert failure is not None
    assert failure.kind == recovery.KIND_PERMANENT
    assert "Review orchestration failed" in failure.detail
