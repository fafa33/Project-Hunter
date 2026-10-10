"""Adversarial authorization checks before privileged collector work."""

import hashlib

import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
PROOF = "b" * 64
GEN = orchestrator.BASE_GENERATION_ID


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-proof",
        "replayed-proof",
        "wrong-origin",
        "candidate-origin",
        "wrong-generation",
        "old-run",
        "missing-cycle",
        "wrong-event",
        "wrong-path",
        "duplicate-status",
    ],
)
def test_adversarial_dispatch_rejected_before_reviewer_work(monkeypatch, attack):
    run = {
        "id": 778,
        "event": "repository_dispatch",
        "path": collector.WORKFLOW,
        "head_branch": "main",
        "created_at": "2026-10-10T10:01:00Z",
        "head_sha": "d" * 40,
    }
    origin = {
        "id": 123,
        "event": "schedule",
        "head_branch": "main",
        "path": ".github/workflows/hunter-governance-reconcile.yml",
    }
    cycle = orchestrator.ReviewCycle(472, HEAD, "WAITING_FOR_REVIEWER", "", 123, "2026-10-10T10:00:00Z", "digest", GEN)
    status = {
        "context": f"{orchestrator.DISPATCH_PROOF_CONTEXT_PREFIX}472",
        "description": f"{GEN}|{hashlib.sha256(PROOF.encode()).hexdigest()}",
        "creator": {"login": orchestrator.TRUSTED_STATUS_CREATOR},
        "target_url": "https://github.com/owner/repo/actions/runs/123",
        "created_at": "2026-10-10T10:00:00Z",
    }
    if attack == "wrong-origin":
        origin["path"] = ".github/workflows/candidate.yml"
    if attack == "candidate-origin":
        origin["head_branch"] = "candidate"
    if attack == "wrong-generation":
        cycle = orchestrator.ReviewCycle(
            472, HEAD, "WAITING_FOR_REVIEWER", "", 123, "2026-10-10T10:00:00Z", "digest", "other"
        )
    if attack == "old-run":
        run["created_at"] = "2026-10-10T09:59:00Z"
    if attack == "wrong-event":
        run["event"] = "workflow_dispatch"
    if attack == "wrong-path":
        run["path"] = ".github/workflows/evil.yml"
    responses = {
        "actions/runs/778": run,
        "actions/runs/123": origin,
        "": {"default_branch": "main"},
        "commits/main": {"sha": "d" * 40},
    }
    monkeypatch.setattr(orchestrator, "request_json", lambda _r, _t, _m, path: responses[path])
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_: ("absent", None, None) if attack == "missing-cycle" else ("present", cycle, None),
    )
    monkeypatch.setattr(
        orchestrator, "_all_commit_statuses", lambda *_: [status, status] if attack == "duplicate-status" else [status]
    )
    monkeypatch.setattr(
        orchestrator, "read_atomic_collector_owner", lambda *_: 777 if attack == "replayed-proof" else None
    )
    with pytest.raises(ValueError):
        collector.verify_trusted_dispatch_before_work(
            "owner/repo", "token", 472, HEAD, GEN, "c" * 64 if attack == "wrong-proof" else PROOF, 778
        )


def test_authorized_dispatch_is_accepted(monkeypatch):
    run = {
        "id": 778,
        "event": "repository_dispatch",
        "path": collector.WORKFLOW,
        "head_branch": "main",
        "created_at": "2026-10-10T10:01:00Z",
        "head_sha": "d" * 40,
    }
    origin = {
        "id": 123,
        "event": "schedule",
        "head_branch": "main",
        "path": ".github/workflows/hunter-governance-reconcile.yml",
    }
    cycle = orchestrator.ReviewCycle(472, HEAD, "WAITING_FOR_REVIEWER", "", 123, "2026-10-10T10:00:00Z", "digest", GEN)
    status = {
        "context": f"{orchestrator.DISPATCH_PROOF_CONTEXT_PREFIX}472",
        "description": f"{GEN}|{hashlib.sha256(PROOF.encode()).hexdigest()}",
        "creator": {"login": orchestrator.TRUSTED_STATUS_CREATOR},
        "target_url": "https://github.com/owner/repo/actions/runs/123",
        "created_at": "2026-10-10T10:00:00Z",
    }
    responses = {
        "actions/runs/778": run,
        "actions/runs/123": origin,
        "": {"default_branch": "main"},
        "commits/main": {"sha": "d" * 40},
    }
    monkeypatch.setattr(orchestrator, "request_json", lambda _r, _t, _m, path: responses[path])
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "unique_collector_claimant", lambda *_: 778)
    monkeypatch.setattr(
        orchestrator,
        "read_atomic_collector_owner",
        lambda *_: None if not getattr(orchestrator, "_claim_created", False) else 778,
    )
    monkeypatch.setattr(orchestrator, "_claim_created", False, raising=False)

    def authorized_request(_r, _t, _m, path, payload=None):
        if path == "git/tags":
            return {"sha": "a" * 40}
        if path == "git/refs":
            monkeypatch.setattr(orchestrator, "_claim_created", True)
            return {}
        return responses[path]

    monkeypatch.setattr(orchestrator, "request_json", authorized_request)
    monkeypatch.setattr(orchestrator, "_all_commit_statuses", lambda *_: [status])
    collector.verify_trusted_dispatch_before_work("owner/repo", "token", 472, HEAD, GEN, PROOF, 778)


def test_competing_dispatch_claimants_fail_closed(monkeypatch):
    title = orchestrator.collector_run_name(472, HEAD, GEN)

    def request(_repo, _token, _method, path):
        assert "event=repository_dispatch" in path
        return {
            "workflow_runs": [
                {
                    "id": number,
                    "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
                    "event": "repository_dispatch",
                    "display_title": title,
                    "created_at": "2026-10-10T10:01:00Z",
                    "head_sha": "d" * 40,
                }
                for number in (778, 779)
            ]
        }

    monkeypatch.setattr(orchestrator, "request_json", request)
    assert orchestrator.unique_collector_claimant("owner/repo", "token", title, "2026-10-10T10:00:00Z") is None


def test_atomic_ref_race_has_exactly_one_winner():
    """Simulate GitHub's create-only refs under simultaneous contender writes."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    ref = orchestrator.collector_claim_ref(472, HEAD, GEN, PROOF)
    assert ref == orchestrator.collector_claim_ref(472, HEAD, GEN, PROOF)
    assert ref != orchestrator.collector_claim_ref(472, HEAD, GEN, "c" * 64)
    barrier = Barrier(2)
    lock = Lock()
    refs = {}

    def contender(run_id):
        barrier.wait()
        with lock:
            if ref in refs:
                return False
            refs[ref] = run_id
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contender, (778, 779)))
    assert outcomes.count(True) == 1
    assert len(refs) == 1


def test_atomic_owner_tag_rejects_wrong_run(monkeypatch):
    name = orchestrator.collector_claim_ref(472, HEAD, GEN, PROOF).removeprefix("tags/")

    def request(_repo, _token, _method, path):
        if path.startswith("git/ref/"):
            return {"object": {"type": "tag", "sha": "a" * 40}}
        if path.startswith("git/tags/"):
            return {"tag": name, "message": "hunter-collector-run-id=778"}
        raise AssertionError(path)

    monkeypatch.setattr(orchestrator, "request_json", request)
    assert orchestrator.read_atomic_collector_owner("owner/repo", "token", 472, HEAD, GEN, PROOF) == 778
    assert orchestrator.read_atomic_collector_owner("owner/repo", "token", 472, HEAD, GEN, PROOF) != 779


def test_missing_atomic_claim_404_is_unowned(monkeypatch):
    def missing(*_):
        raise transport.GitHubRequestError("not found", category="permanent", status_code=404)

    monkeypatch.setattr(orchestrator, "request_json", missing)
    assert orchestrator.read_atomic_collector_owner("owner/repo", "token", 472, HEAD, GEN, PROOF) is None


@pytest.mark.parametrize("status,category", [(403, "permanent"), (500, "transient"), (404, "node-resolution")])
def test_atomic_claim_api_failures_are_not_unowned(monkeypatch, status, category):
    def unavailable(*_):
        raise transport.GitHubRequestError("unavailable", category=category, status_code=status)

    monkeypatch.setattr(orchestrator, "request_json", unavailable)
    with pytest.raises(transport.GitHubRequestError):
        orchestrator.read_atomic_collector_owner("owner/repo", "token", 472, HEAD, GEN, PROOF)
