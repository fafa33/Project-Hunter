"""Adversarial authorization checks before privileged collector work."""

import hashlib

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
    if attack == "replayed-proof":
        completed = {
            "context": f"{orchestrator.COLLECTOR_CONTEXT_PREFIX}472",
            "state": "success",
            "creator": {"login": orchestrator.TRUSTED_STATUS_CREATOR},
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
    responses = {"actions/runs/778": run, "actions/runs/123": origin, "": {"default_branch": "main"}}
    monkeypatch.setattr(orchestrator, "request_json", lambda _r, _t, _m, path: responses[path])
    monkeypatch.setattr(
        orchestrator,
        "read_cycle",
        lambda *_: ("absent", None, None) if attack == "missing-cycle" else ("present", cycle, None),
    )
    monkeypatch.setattr(
        orchestrator,
        "_all_commit_statuses",
        lambda *_: (
            [status, status]
            if attack == "duplicate-status"
            else ([status, completed] if attack == "replayed-proof" else [status])
        ),
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
    responses = {"actions/runs/778": run, "actions/runs/123": origin, "": {"default_branch": "main"}}
    monkeypatch.setattr(orchestrator, "request_json", lambda _r, _t, _m, path: responses[path])
    monkeypatch.setattr(orchestrator, "read_cycle", lambda *_: ("present", cycle, None))
    monkeypatch.setattr(orchestrator, "_all_commit_statuses", lambda *_: [status])
    collector.verify_trusted_dispatch_before_work("owner/repo", "token", 472, HEAD, GEN, PROOF, 778)
