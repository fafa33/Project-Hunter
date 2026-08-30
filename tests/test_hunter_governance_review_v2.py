from __future__ import annotations

import importlib
from pathlib import Path

core = importlib.import_module("hunter_governance_review_v2")


HEAD = "b" * 40


def _pr(mergeable: bool | None) -> dict:
    return {
        "state": "open",
        "mergeable": mergeable,
        "title": "no canonical title or issue identity",
        "body": "no matrix, no readiness declaration, no reaction ceremony",
        "head": {"sha": HEAD, "ref": "feature/no-governance-metadata"},
        "base": {"sha": "c" * 40, "ref": "main"},
    }


def test_governance_review_ignores_process_metadata(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "success"


def test_governance_review_fails_real_merge_conflict(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(False))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "failure"
    assert "merge conflicts" in published[0][4]


def test_governance_review_waits_for_unknown_mergeability(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(None))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0

    assert published[0][3] == "pending"


def test_governance_review_skips_non_main_target(monkeypatch):
    published = []
    pr = _pr(True)
    pr["base"]["ref"] = "release"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_skips_disposition_check_for_closed_pr(monkeypatch):
    published = []
    pr = _pr(True)
    pr["state"] = "closed"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_skips_disposition_check_for_non_main_pr(monkeypatch):
    published = []
    pr = _pr(True)
    pr["base"]["ref"] = "release"
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published == []


def test_governance_review_fails_closed_for_open_main_pr_with_unresolved_disposition(monkeypatch):
    published = []
    pr = _pr(True)
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: pr)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (False, "Unresolved finding RFD-ERR"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert len(published) == 1
    assert published[0][3] == "failure"
    assert "Unresolved finding RFD-ERR" in published[0][4]


def test_candidate_admission_tests_first_red_success_stays_draft(monkeypatch):
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, (), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("tests-first-red", None))

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "Draft-only" in description


def test_protected_preflight_ordinary_candidate_cannot_self_authorize(monkeypatch):
    monkeypatch.setattr(
        core,
        "read_pr_changed_paths",
        lambda *_args: (True, ("scripts/hunter_pr_preflight.py",), None),
    )
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))
    monkeypatch.setattr(
        core,
        "read_commit_lineage_ingress",
        lambda *_args: (True, "Commit lineage ingress validated."),
    )
    monkeypatch.setattr(
        core,
        "read_trusted_upgrade_status",
        lambda *_args: ("missing", "trusted proof missing"),
    )

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert description == "trusted proof missing"


def test_workflow_uses_only_trusted_v2_controller_without_bootstrap():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")

    assert "Checkout installation controller" not in workflow
    assert "installation-engine" not in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert 'PR_NUMBER} = "283"' not in workflow
    assert "python -m hunter_governance_review" not in workflow
    assert "bootstrap" not in workflow.lower()
    assert "hunter_governance_review_v2.py" in workflow


def test_reconcile_continues_after_one_pr_failure_and_drops_checkout_credentials():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-reconcile.yml"
    ).read_text(encoding="utf-8")

    assert "persist-credentials: false" in workflow
    assert "failures=0" in workflow
    assert "if ! python scripts/hunter_governance_review_v2.py" in workflow
    assert "failures=1" in workflow
    assert 'exit "${failures}"' in workflow


def test_api_only_code_write_candidate_fails_admission(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"commits/{HEAD}" in path:
            return {
                "committer": {"login": "web-flow"},
                "commit": {"committer": {"name": "GitHub", "email": "noreply@github.com"}},
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "prohibited API-only path" in description


def test_api_only_candidate_remains_unadmitted_even_if_hosted_ci_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"commits/{HEAD}" in path:
            return {
                "committer": {"login": "web-flow"},
                "commit": {"committer": {"name": "GitHub", "email": "noreply@github.com"}},
            }
        if "actions/runs" in path:
            return {
                "workflow_runs": [
                    {
                        "head_sha": HEAD,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                        "id": 100,
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "prohibited API-only path" in description


def test_clone_capable_candidate_with_valid_pre_push_and_exact_head_evidence_progresses(monkeypatch) -> None:
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"commits/{HEAD}" in path:
            return {
                "committer": {"login": "fafa33"},
                "commit": {"committer": {"name": "Farhad5778", "email": "fafa33@example.com"}},
            }
        if "actions/runs" in path:
            return {
                "workflow_runs": [
                    {
                        "head_sha": HEAD,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                        "id": 100,
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "success"
    assert "Exact-head branch preflight passed" in description


def test_stale_preflight_proof_cannot_authorize_newer_head(monkeypatch) -> None:
    head_newer = "c" * 40
    monkeypatch.setattr(core, "read_pr_changed_paths", lambda *_args: (True, ("src/hunter/cli.py",), None))
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"commits/{head_newer}" in path:
            return {
                "committer": {"login": "fafa33"},
                "commit": {"committer": {"name": "Farhad5778", "email": "fafa33@example.com"}},
            }
        if "actions/runs" in path:
            return {
                "workflow_runs": [
                    {
                        "head_sha": HEAD,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                        "id": 99,
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head_newer, 501)

    assert state == "failure"
    assert "exact-head branch preflight is missing" in description


def test_import_ordering_recurrence_prh001_cannot_reach_admitted_candidate_without_canonical_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        core, "read_pr_changed_paths", lambda *_args: (True, ("tests/test_issue_agent_trigger.py",), None)
    )
    monkeypatch.setattr(core, "read_head_preflight_mode", lambda *_args: ("normal", None))

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"commits/{HEAD}" in path:
            return {
                "committer": {"login": "web-flow"},
                "commit": {"committer": {"name": "GitHub", "email": "noreply@github.com"}},
            }
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", fake_request)

    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", HEAD, 501)

    assert state == "failure"
    assert "prohibited API-only path" in description


def test_legitimate_api_only_read_review_metadata_operations_allowed(monkeypatch) -> None:
    published = []

    def fake_read_mergeability(_repo, _token, pr_number):
        return _pr(True)

    monkeypatch.setattr(core, "read_mergeability", fake_read_mergeability)
    monkeypatch.setattr(core, "check_reviewer_dispositions", lambda: (True, ""))
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    result = core.review("fafa33/Project-Hunter", "token", 501)

    assert result == 0
    assert len(published) == 1
    assert published[0][3] == "success"
