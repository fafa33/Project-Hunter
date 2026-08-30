from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

candidate_controller = importlib.import_module("hunter_candidate_admission")
prevention = importlib.import_module("hunter_defect_prevention_preflight")
governance = importlib.import_module("hunter_governance_review_v2")


HEAD_A = "a" * 40
HEAD_B = "b" * 40


def _query_page(path: str) -> str:
    return parse_qs(urlparse(f"https://example.invalid/{path}").query).get("page", [""])[0]


def test_changed_path_detection_reads_later_pages(monkeypatch) -> None:
    seen: list[str] = []

    def fake_request(_repo, _token, _method, path, _payload=None):
        seen.append(path)
        page = _query_page(path)
        if page == "1":
            return [{"filename": f"docs/file-{index}.md"} for index in range(100)]
        if page == "2":
            return [{"filename": "scripts/hunter_pr_preflight.py"}]
        raise AssertionError(path)

    monkeypatch.setattr(governance, "request_json", fake_request)
    ok, paths, error = governance.read_pr_changed_paths("fafa33/Project-Hunter", "token", 376)

    assert ok is True
    assert error is None
    assert "scripts/hunter_pr_preflight.py" in paths
    assert any(_query_page(path) == "2" for path in seen)


def test_changed_path_detection_fails_closed_on_unreadable_later_page(monkeypatch) -> None:
    def fake_request(_repo, _token, _method, path, _payload=None):
        page = _query_page(path)
        if page == "1":
            return [{"filename": f"docs/file-{index}.md"} for index in range(100)]
        if page == "2":
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
        if f"commits/{HEAD_A}" in path:
            return {
                "committer": {"login": "fafa33"},
                "commit": {"committer": {"name": "Farhad5778", "email": "fafa33@example.com"}},
            }
        raise AssertionError(path)

    monkeypatch.setattr(governance, "request_json", fake_request)
    state, _description = governance.candidate_admission("fafa33/Project-Hunter", "token", HEAD_A, pr_number=376)

    assert state == "success"
    assert not any("actions/runs" in path for path in requests)


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


def test_trusted_candidate_runner_executes_immutable_gate_chain(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, env, check):
        assert cwd == tmp_path
        assert env["GITHUB_TOKEN"] == ""
        assert env["GH_TOKEN"] == ""
        assert check is False
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prevention.subprocess, "run", fake_run)

    assert prevention.run_candidate_quality_gates(tmp_path) == 0
    assert calls == [command for _name, command in prevention.TRUSTED_CANDIDATE_QUALITY_GATES]
    assert len(calls) == len(prevention.REQUIRED_PREFLIGHT_GATES)


def test_candidate_definition_rejects_dead_gate_tuple(tmp_path) -> None:
    workflow = tmp_path / ".github" / "workflows"
    scripts = tmp_path / "scripts"
    workflow.mkdir(parents=True)
    scripts.mkdir()
    (workflow / "hunter-pre-pr-preflight.yml").write_text(
        "run: python scripts/hunter_pr_preflight.py --mode normal\n",
        encoding="utf-8",
    )
    (scripts / "hunter_pr_preflight.py").write_text(
        "NORMAL_QUALITY_GATES = ()\n"
        'DEAD = (("Architecture Index Guard", ("python", "ignored.py")),)\n'
        "def run_preflight():\n"
        "    return 0\n",
        encoding="utf-8",
    )

    errors = prevention.validate_candidate_preflight_definition(tmp_path)

    assert any("NORMAL_QUALITY_GATES must match" in error for error in errors)
    assert any("does not execute NORMAL_QUALITY_GATES" in error for error in errors)


def test_candidate_workflow_ignores_conditional_or_documented_exit_zero(tmp_path) -> None:
    workflow = tmp_path / ".github" / "workflows"
    scripts = tmp_path / "scripts"
    workflow.mkdir(parents=True)
    scripts.mkdir()
    (workflow / "hunter-pre-pr-preflight.yml").write_text(
        "run: |\n"
        "  # example: exit 0\n"
        "  if false; then\n"
        "    exit 0\n"
        "  fi\n"
        "  python scripts/hunter_pr_preflight.py --mode normal\n",
        encoding="utf-8",
    )
    (scripts / "hunter_pr_preflight.py").write_text(
        "NORMAL_QUALITY_GATES = " + repr(prevention.TRUSTED_CANDIDATE_QUALITY_GATES) + "\n"
        "def run_quality_gates(gates):\n"
        "    return 0\n"
        "def run_preflight():\n"
        "    return run_quality_gates(NORMAL_QUALITY_GATES)\n",
        encoding="utf-8",
    )

    errors = prevention.validate_candidate_preflight_definition(tmp_path)

    assert not any("exit 0" in error for error in errors)


def test_governance_required_status_fails_when_candidate_is_unadmitted(monkeypatch) -> None:
    pr = {
        "state": "open",
        "base": {"ref": "main"},
        "head": {"sha": HEAD_A},
        "mergeable": True,
    }
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(governance, "read_mergeability", lambda *_args: pr)
    monkeypatch.setattr(governance, "candidate_admission", lambda *_args: ("failure", "exact-head proof missing"))
    monkeypatch.setattr(
        governance,
        "publish",
        lambda _repo, _token, _sha, state, description: published.append((state, description)),
    )

    assert governance.review("fafa33/Project-Hunter", "token", 377) == 0
    assert published == [("failure", "exact-head proof missing")]


def test_governance_required_status_succeeds_only_after_admission(monkeypatch) -> None:
    pr = {
        "state": "open",
        "base": {"ref": "main"},
        "head": {"sha": HEAD_A},
        "mergeable": True,
    }
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(governance, "read_mergeability", lambda *_args: pr)
    monkeypatch.setattr(governance, "candidate_admission", lambda *_args: ("success", "admitted"))
    monkeypatch.setattr(
        governance,
        "publish",
        lambda _repo, _token, _sha, state, description: published.append((state, description)),
    )

    assert governance.review("fafa33/Project-Hunter", "token", 377) == 0
    assert published == [
        ("success", "Exact-head candidate admission and current merge-state governance checks passed.")
    ]


def test_trusted_upgrade_separates_untrusted_execution_from_status_write() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    ).read_text(encoding="utf-8")

    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "path: candidate" in workflow
    assert "validate-candidate candidate" in workflow
    assert "--run-candidate-gates candidate" in workflow
    assert "python scripts/hunter_pr_preflight.py --mode normal" not in workflow
    assert "cd candidate" not in workflow
    assert 'GITHUB_TOKEN: ""' in workflow
    assert "needs: validate-candidate" in workflow
    assert "statuses: write" in workflow
    assert "Hunter Trusted Preflight Upgrade / PR #" in workflow
