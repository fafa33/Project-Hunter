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
            "bypass_actors": [],
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


def test_ruleset_conformance_rejects_main_ruleset_with_bypass_actor(monkeypatch):
    required = sorted(core.REQUIRED_RULESET_CHECKS)

    def fake_request(_repo, _token, _method, path, _payload=None):
        if path.startswith("rulesets?"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "enforcement": "active",
            "bypass_actors": [{"actor_id": 1, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": item} for item in required]},
                }
            ],
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.ruleset_conformance("fafa33/Project-Hunter", "token")
    assert state == "failure"
    assert "bypass actors" in description


def test_ruleset_conformance_rejects_missing_bypass_configuration(monkeypatch):
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
    state, description = core.ruleset_conformance("fafa33/Project-Hunter", "token")
    assert state == "failure"
    assert "bypass configuration" in description


def test_ruleset_conformance_accepts_complete_main_protection(monkeypatch):
    required = sorted(core.REQUIRED_RULESET_CHECKS)

    def fake_request(_repo, _token, _method, path, _payload=None):
        if path.startswith("rulesets?"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "enforcement": "active",
            "bypass_actors": [],
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


def test_candidate_admission_blocks_candidate_modified_preflight(monkeypatch):
    head = "a" * 40

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "pulls/371/files" in path:
            return [{"filename": "scripts/hunter_pr_preflight.py"}]
        if f"head_sha={head}" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "head_sha": head,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head, pr_number=371)
    assert state == "failure"
    assert "preflight definition was modified by candidate" in description


def test_ruleset_applies_to_ref_include_and_exclude_main():
    detail = {
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main"],
                "exclude": ["refs/heads/main"],
            }
        }
    }
    applies, error = core.ruleset_applies_to_ref(detail, "refs/heads/main")
    assert applies is False
    assert error is None


def test_ruleset_applies_to_ref_include_main_exclude_dev():
    detail = {
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main"],
                "exclude": ["refs/heads/dev"],
            }
        }
    }
    applies, error = core.ruleset_applies_to_ref(detail, "refs/heads/main")
    assert applies is True
    assert error is None


def test_ruleset_applies_to_ref_wildcard_include_explicit_main_exclusion():
    detail = {
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/*"],
                "exclude": ["refs/heads/main"],
            }
        }
    }
    applies, error = core.ruleset_applies_to_ref(detail, "refs/heads/main")
    assert applies is False
    assert error is None


def test_ruleset_applies_to_ref_malformed_missing_condition():
    applies, error = core.ruleset_applies_to_ref({}, "refs/heads/main")
    assert applies is False
    assert "missing" in error

    applies, error = core.ruleset_applies_to_ref({"conditions": {"ref_name": "not-a-dict"}}, "refs/heads/main")
    assert applies is False
    assert "malformed" in error


def test_ruleset_conformance_non_applicable_ruleset_with_all_checks_is_insufficient(monkeypatch):
    required = sorted(core.REQUIRED_RULESET_CHECKS)

    def fake_request(_repo, _token, _method, path, _payload=None):
        if path.startswith("rulesets?"):
            return [{"id": 7, "enforcement": "active"}]
        return {
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {
                "ref_name": {
                    "include": ["refs/heads/*"],
                    "exclude": ["refs/heads/main"],
                }
            },
            "rules": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": item} for item in required]},
                }
            ],
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.ruleset_conformance("fafa33/Project-Hunter", "token")
    assert state == "failure"
    assert "no active ruleset protects refs/heads/main" in description


def test_candidate_admission_normal_exact_head_green_authorizes_ready(monkeypatch):
    head = "a" * 40

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "contents/.hunter-preflight-mode" in path:
            raise core.transport.GitHubRequestError("not found", category="permanent", status_code=404)
        if f"head_sha={head}" in path:
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
                        "mode": "normal",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "success"


def test_candidate_admission_tests_first_red_success_stays_draft(monkeypatch):
    head = "a" * 40
    import base64

    mode_b64 = base64.b64encode(b"tests-first-red").decode("utf-8")

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "contents/.hunter-preflight-mode" in path:
            return {"content": mode_b64}
        if f"head_sha={head}" in path:
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
                        "mode": "tests-first-red",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "failure"
    assert "tests-first-red work must remain in Draft" in description


def test_candidate_admission_stale_mode_proof_cannot_authorize_new_head(monkeypatch):
    head = "a" * 40
    stale_head = "b" * 40

    def fake_request(_repo, _token, _method, path, _payload=None):
        if f"head_sha={head}" in path:
            return {
                "workflow_runs": [
                    {
                        "id": 10,
                        "name": core.PRE_PR_WORKFLOW_NAME,
                        "path": core.PRE_PR_WORKFLOW_PATH,
                        "head_sha": stale_head,
                        "event": "push",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "failure"
    assert "exact-head branch preflight is missing" in description


def test_candidate_admission_forged_mismatched_mode_cannot_authorize_ready(monkeypatch):
    head = "a" * 40

    def fake_request(_repo, _token, _method, path, _payload=None):
        if "contents/.hunter-preflight-mode" in path:
            raise core.transport.GitHubRequestError("not found", category="permanent", status_code=404)
        if f"head_sha={head}" in path:
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
                        "mode": "tests-first-red",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(core, "request_json", fake_request)
    state, description = core.candidate_admission("fafa33/Project-Hunter", "token", head)
    assert state == "failure"
    assert "mismatched" in description
