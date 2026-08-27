from __future__ import annotations

from pathlib import Path

import hunter_governance_review_v2 as core


def _pr(mergeable: bool | None) -> dict:
    return {
        "state": "open",
        "mergeable": mergeable,
        "title": "no canonical title or issue identity",
        "body": "no matrix, no readiness declaration, no reaction ceremony",
        "head": {"sha": "b" * 40, "ref": "feature/no-governance-metadata"},
        "base": {"sha": "c" * 40, "ref": "main"},
    }


def _allow_admission(monkeypatch) -> None:
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(core, "ruleset_conformance", lambda *_args: ("success", "protected"))


def test_governance_review_ignores_process_metadata(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))
    _allow_admission(monkeypatch)

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


def test_candidate_admission_requires_exact_head_push_preflight(monkeypatch):
    head = "a" * 40

    def fake_request(_repo, _token, _method, path, _payload=None):
        assert f"head_sha={head}" in path
        assert "event=push" in path
        return {
            "workflow_runs": [
                {
                    "id": 1,
                    "name": core.PRE_PR_WORKFLOW_NAME,
                    "path": core.PRE_PR_WORKFLOW_PATH,
                    "head_sha": "b" * 40,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "failure"
    assert "missing" in description


def test_candidate_admission_uses_latest_exact_head_run(monkeypatch):
    head = "a" * 40

    def fake_request(_repo, _token, _method, _path, _payload=None):
        return {
            "workflow_runs": [
                {
                    "id": 10,
                    "name": core.PRE_PR_WORKFLOW_NAME,
                    "path": core.PRE_PR_WORKFLOW_PATH,
                    "head_sha": head,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 11,
                    "name": core.PRE_PR_WORKFLOW_NAME,
                    "path": core.PRE_PR_WORKFLOW_PATH,
                    "head_sha": head,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "failure"
    assert "failure" in description


def test_governance_blocks_when_candidate_was_not_admitted(monkeypatch):
    published = []
    monkeypatch.setattr(core, "read_mergeability", lambda _repo, _token, _number: _pr(True))
    monkeypatch.setattr(core, "candidate_admission", lambda *_args: ("failure", "missing exact-head preflight"))
    monkeypatch.setattr(core, "ruleset_conformance", lambda *_args: ("success", "protected"))
    monkeypatch.setattr(core, "publish", lambda *args: published.append(args))

    assert core.review("fafa33/Project-Hunter", "token", 501) == 0
    assert published[0][3] == "failure"
    assert "exact-head" in published[0][4]


def test_ruleset_conformance_requires_all_canonical_statuses(monkeypatch):
    def fake_request(_repo, _token, _method, path, _payload=None):
        if path.startswith("rulesets?"):
            return [{"id": 7, "enforcement": "active"}]
        assert path == "rulesets/7"
        return {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [
                            {"context": "Quality Gates"},
                            {"context": "dependency-review"},
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.ruleset_conformance("fafa33/Project-Hunter", "token")
    assert state == "failure"
    assert "Hunter Merge Readiness" in description


def test_ruleset_conformance_accepts_complete_main_protection(monkeypatch):
    required = sorted(core.REQUIRED_RULESET_CHECKS)

    def fake_request(_repo, _token, _method, path, _payload=None):
        if path.startswith("rulesets?"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": item} for item in required]},
                }
            ],
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    assert core.ruleset_conformance("fafa33/Project-Hunter", "token")[0] == "success"


def test_workflow_uses_only_trusted_v2_controller_without_bootstrap():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-review.yml"
    ).read_text(encoding="utf-8")

    assert "Checkout installation controller" not in workflow
    assert "installation-engine" not in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions: read" in workflow
    assert 'PR_NUMBER} = "283"' not in workflow
    assert "python -m hunter_governance_review" not in workflow
    assert "bootstrap" not in workflow.lower()
    assert "hunter_governance_review_v2.py" in workflow


def test_reconcile_continues_after_one_pr_failure_and_drops_checkout_credentials():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-governance-reconcile.yml"
    ).read_text(encoding="utf-8")

    assert "persist-credentials: false" in workflow
    assert "Hunter / Pre-PR Preflight" in workflow
    assert "workflow_run:" in workflow
    assert "actions: read" in workflow
    assert "failures=0" in workflow
    assert "if ! python scripts/hunter_governance_review_v2.py" in workflow
    assert "failures=1" in workflow
    assert 'exit "${failures}"' in workflow
