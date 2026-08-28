from __future__ import annotations

import importlib
import json
from pathlib import Path

candidate_controller = importlib.import_module("hunter_candidate_admission")
prevention = importlib.import_module("hunter_defect_prevention_preflight")
governance = importlib.import_module("hunter_governance_review_v2")


HEAD_A = "a" * 40
HEAD_B = "b" * 40


def test_changed_path_detection_reads_later_pages(monkeypatch) -> None:
    seen: list[str] = []

    def fake_request(_repo, _token, _method, path, _payload=None):
        seen.append(path)
        if "page=1" in path:
            return [{"filename": f"docs/file-{index}.md"} for index in range(100)]
        if "page=2" in path:
            return [{"filename": "scripts/hunter_pr_preflight.py"}]
        raise AssertionError(path)

    monkeypatch.setattr(governance, "request_json", fake_request)
    ok, paths, error = governance.read_pr_changed_paths("fafa33/Project-Hunter", "token", 376)

    assert ok is True
    assert error is None
    assert "scripts/hunter_pr_preflight.py" in paths
    assert any("page=2" in path for path in seen)


def test_changed_path_detection_fails_closed_on_unreadable_later_page(monkeypatch) -> None:
    def fake_request(_repo, _token, _method, path, _payload=None):
        if "page=1" in path:
            return [{"filename": f"docs/file-{index}.md"} for index in range(100)]
        if "page=2" in path:
            return {"message": "malformed"}
        raise AssertionError(path)

    monkeypatch.setattr(governance, "request_json", fake_request)
    ok, paths, error = governance.read_pr_changed_paths("fafa33/Project-Hunter", "token", 376)

    assert ok is False
    assert paths == ()
    assert "not a list" in str(error)


def test_trusted_upgrade_status_is_bound_to_exact_pr_context(monkeypatch) -> None:
    context = governance._upgrade_status_context(376)

    def fake_request(_repo, _token, _method, path, _payload=None):
        assert f"commits/{HEAD_A}/statuses" in path
        return [
            {"id": 10, "context": governance._upgrade_status_context(999), "state": "success"},
            {"id": 11, "context": context, "state": "success"},
        ]

    monkeypatch.setattr(governance, "request_json", fake_request)
    state, description = governance.read_trusted_upgrade_status("fafa33/Project-Hunter", "token", HEAD_A, 376)

    assert state == "success"
    assert "Exact-head trusted candidate preflight validation passed" in description


def test_wrong_pr_trusted_upgrade_status_cannot_authorize(monkeypatch) -> None:
    def fake_request(_repo, _token, _method, _path, _payload=None):
        return [
            {
                "id": 10,
                "context": governance._upgrade_status_context(999),
                "state": "success",
            }
        ]

    monkeypatch.setattr(governance, "request_json", fake_request)
    state, _description = governance.read_trusted_upgrade_status("fafa33/Project-Hunter", "token", HEAD_A, 376)

    assert state == "missing"


def test_protected_preflight_requires_exact_head_pr_bound_status(monkeypatch) -> None:
    requests: list[str] = []

    def fake_request(_repo, _token, _method, path, _payload=None):
        requests.append(path)
        if "pulls/376/files" in path:
            return [{"filename": "scripts/hunter_pr_preflight.py"}]
        if "contents/.hunter-preflight-mode" in path:
            return {"message": "Not Found"}
        if f"commits/{HEAD_A}/statuses" in path:
            return [
                {
                    "id": 12,
                    "context": governance._upgrade_status_context(376),
                    "state": "success",
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(governance, "request_json", fake_request)
    state, _description = governance.candidate_admission(
        "fafa33/Project-Hunter", "token", HEAD_A, pr_number=376
    )

    assert state == "success"
    assert not any("actions/runs" in path and "pull_request_target" in path for path in requests)


def test_unknown_code_write_path_is_rejected(monkeypatch, tmp_path) -> None:
    policy = {
        "version": 1,
        "code_write_paths": {
            "local_git_push": {
                "allowed": True,
                "required_boundary": ".githooks/pre-push",
            },
            "github_contents_api": {"allowed": False},
            "github_git_data_api": {"allowed": False},
            "api_only_agents": {"allowed_role": "read-review-metadata-only"},
            "github_graphql_api": {"allowed": True},
        },
        "review_progression": {
            "unadmitted_head_state": "draft",
            "ready_requires": "successful exact-head Hunter / Pre-PR Preflight",
            "auto_ready": False,
        },
    }
    path = tmp_path / "CODE_WRITE_POLICY.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(prevention, "WRITE_POLICY_PATH", path)

    errors = prevention.validate_code_write_policy()

    assert any("unrecognized code-write paths" in error for error in errors)


def test_stale_candidate_admission_event_cannot_draft_newer_head(monkeypatch) -> None:
    initial = {
        "state": "open",
        "draft": False,
        "node_id": "PR_node",
        "base": {"ref": "main"},
        "head": {"sha": HEAD_A},
    }
    newer = {
        "state": "open",
        "draft": False,
        "node_id": "PR_node",
        "base": {"ref": "main"},
        "head": {"sha": HEAD_B},
    }
    reads = iter((initial, newer))
    converted: list[str] = []

    monkeypatch.setattr(candidate_controller.governance, "read_mergeability", lambda *_args: next(reads))
    monkeypatch.setattr(
        candidate_controller.governance,
        "candidate_admission",
        lambda *_args: ("failure", "blocked"),
    )
    monkeypatch.setattr(candidate_controller, "convert_to_draft", lambda _token, node_id: converted.append(node_id))

    assert (
        candidate_controller.enforce_candidate_admission(
            "fafa33/Project-Hunter", "token", 376, expected_head_sha=HEAD_A
        )
        == 0
    )
    assert converted == []


def test_trusted_upgrade_separates_untrusted_execution_from_status_write() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    ).read_text(encoding="utf-8")

    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "path: candidate" in workflow
    assert "validate-candidate candidate" in workflow
    assert "cd candidate" in workflow
    assert "python scripts/hunter_pr_preflight.py --mode normal" in workflow
    assert 'GITHUB_TOKEN: ""' in workflow
    assert "needs: validate-candidate" in workflow
    assert "statuses: write" in workflow
    assert "Hunter Trusted Preflight Upgrade / PR #" in workflow
