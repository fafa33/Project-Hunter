from __future__ import annotations

import base64
import hashlib
import json

import pytest

from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorization,
    SignedIssueAgentAuthorization,
    derive_execution_target,
)
from hunter.automation.issue_agent_replacement_executor import (
    REHEARSAL_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    ReplacementExecutorError,
    ReplacementResultLedger,
    assert_rehearsal_has_no_publication_authority,
    publisher_environment_is_safe,
    validate_replacement_result,
    validation_receipt,
)
from hunter.task_scope import TaskScopeContract

SAFETY_TREE = "c" * 40


def _signed():
    a = IssueAgentAuthorization(
        repository="fafa33/Project-Hunter",
        issue_number=523,
        issue_url="https://github.com/fafa33/Project-Hunter/issues/523",
        issue_title="replacement executor",
        issue_body="bounded",
        authorized_by="fafa33",
        authorization_label="hunter-agent-execute",
        issue_updated_at="2026-09-26T00:00:00Z",
        authorization_id="hunter-issue-agent:" + "a" * 64,
    )
    object.__setattr__(a, "authorization_id", a.derived_authorization_id)
    s = TaskScopeContract(
        task_id=a.authorization_id,
        branch_pattern="issue-523-*",
        base_ref="main",
        base_sha="9" * 40,
        allowed_paths=("docs/", "src/hunter/automation/"),
        prohibited_paths=("docs/secret/",),
    )
    return SignedIssueAgentAuthorization(a, s, "00" * 64)


def _result(signed, rehearsal=False, path="docs/x.md"):
    t = derive_execution_target(signed)
    content = b"safe candidate\n"
    return json.dumps(
        {
            "schema_version": REHEARSAL_SCHEMA_VERSION if rehearsal else RESULT_SCHEMA_VERSION,
            "authorization_id": t.authorization_id,
            "base_sha": t.base_sha,
            "branch": t.branch,
            "files": [
                {
                    "path": path,
                    "content_b64": base64.b64encode(content).decode(),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mode": "100644",
                }
            ],
        }
    )


def test_valid_result_is_bound_to_signed_scope_and_target():
    s = _signed()
    r = validate_replacement_result(_result(s), signed_authorization=s, rehearsal=False)
    assert r.branch.startswith("issue-523-") and r.files[0].content == b"safe candidate\n" and r.rehearsal is False


@pytest.mark.parametrize("path", ["outside.txt", "docs/secret/x.md", "../escape", ".git/config"])
def test_hostile_paths_fail_closed(path):
    s = _signed()
    with pytest.raises(ReplacementExecutorError):
        validate_replacement_result(_result(s, path=path), signed_authorization=s, rehearsal=False)


def test_result_cannot_add_prompt_or_evidence_fields():
    s = _signed()
    p = json.loads(_result(s))
    p["exact_prompt"] = "secret"
    with pytest.raises(ReplacementExecutorError, match="schema mismatch"):
        validate_replacement_result(json.dumps(p), signed_authorization=s, rehearsal=False)


def test_rehearsal_and_live_results_are_not_interchangeable():
    s = _signed()
    with pytest.raises(ReplacementExecutorError, match="wrong execution class"):
        validate_replacement_result(_result(s, True), signed_authorization=s, rehearsal=False)


def test_rehearsal_structurally_rejects_publication_credentials():
    assert_rehearsal_has_no_publication_authority({})
    with pytest.raises(ReplacementExecutorError, match="publication authority"):
        assert_rehearsal_has_no_publication_authority({"GITHUB_TOKEN": "secret"})


def test_publisher_rejects_model_authority():
    assert publisher_environment_is_safe({"PATH": "/usr/bin"})
    assert not publisher_environment_is_safe({"OPENAI_API_KEY": "secret"})


def test_validation_receipt_is_exact_result_and_definition_bound():
    from hunter.automation.issue_agent_replacement_executor import validation_receipt, verify_validation_receipt

    s = _signed()
    doc = _result(s)
    receipt = validation_receipt(doc, signed_authorization=s, validation_definition="preflight-v1")
    verified = verify_validation_receipt(
        receipt.to_json(), result_document=doc, signed_authorization=s, expected_validation_definition="preflight-v1"
    )
    assert verified.result_sha256 == receipt.result_sha256
    tampered = json.loads(doc)
    tampered["files"][0]["content_b64"] = base64.b64encode(b"other").decode()
    with pytest.raises(ReplacementExecutorError):
        verify_validation_receipt(
            receipt.to_json(),
            result_document=json.dumps(tampered),
            signed_authorization=s,
            expected_validation_definition="preflight-v1",
        )


def test_validation_receipt_cannot_be_reused_after_definition_change():
    from hunter.automation.issue_agent_replacement_executor import validation_receipt, verify_validation_receipt

    s = _signed()
    doc = _result(s)
    receipt = validation_receipt(doc, signed_authorization=s, validation_definition="preflight-v1")
    with pytest.raises(ReplacementExecutorError, match="validation_definition mismatch"):
        verify_validation_receipt(
            receipt.to_json(),
            result_document=doc,
            signed_authorization=s,
            expected_validation_definition="preflight-v2",
        )


def test_result_rejects_symlink_mode():
    s = _signed()
    p = json.loads(_result(s))
    p["files"][0]["mode"] = "120000"
    with pytest.raises(ReplacementExecutorError, match="invalid mode"):
        validate_replacement_result(json.dumps(p), signed_authorization=s, rehearsal=False)


def test_result_ledger_is_replay_safe(tmp_path):
    s = _signed()
    doc = _result(s)
    receipt = validation_receipt(doc, signed_authorization=s, validation_definition="canonical-v1")
    ledger = ReplacementResultLedger(tmp_path / "ledger.sqlite")
    ledger.record_validated(receipt)
    ledger.record_validated(receipt)
    ledger.record_published(receipt, "a" * 40)
    ledger.record_published(receipt, "a" * 40)
    other = type(receipt)(
        receipt.authorization_id,
        receipt.base_sha,
        receipt.branch,
        "b" * 64,
        receipt.validation_definition,
        receipt.schema_version,
    )
    with pytest.raises(ReplacementExecutorError, match="different replacement result"):
        ledger.record_validated(other)
    with pytest.raises(ReplacementExecutorError, match="different head"):
        ledger.record_published(receipt, "c" * 40)


def test_publisher_environment_rejects_every_provider_command():
    for name in (
        "HUNTER_AGENT_CODEX_COMMAND",
        "HUNTER_AGENT_CLAUDE_COMMAND",
        "HUNTER_AGENT_FREEBUFF_COMMAND",
        "HUNTER_AGENT_OPENCODE_COMMAND",
        "HUNTER_AGENT_JULES_COMMAND",
    ):
        assert not publisher_environment_is_safe({name: "configured"})


def test_rehearsal_result_can_never_reach_publisher(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    validated = core.validate_replacement_result(_result(signed, True), signed_authorization=signed, rehearsal=True)
    receipt = core.ReplacementValidationReceipt(
        validated.authorization_id, validated.base_sha, validated.branch, "0" * 64, "v1"
    )
    with pytest.raises(core.ReplacementExecutorError, match="rehearsal result cannot be published"):
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE)


def test_publisher_refuses_existing_branch_before_building_commit(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.validation_receipt(doc, signed_authorization=signed, validation_definition="v1")
    calls = []

    def fake_git(_repo, *args, **_kwargs):
        calls.append(args)
        if args[0] == "ls-remote":
            return "a" * 40 + " refs/heads/" + validated.branch
        raise AssertionError("publisher must stop before candidate commit construction")

    monkeypatch.setattr(core, "_git_plumbing", fake_git)
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    with pytest.raises(core.ReplacementExecutorError, match="already exists"):
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE)
    assert calls and calls[0][0] == "ls-remote"
    assert calls[0][1] == "https://github.com/fafa33/Project-Hunter.git"


def test_publisher_uses_data_only_git_plumbing_and_create_only_lease(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.validation_receipt(doc, signed_authorization=signed, validation_definition="v1")
    calls = []
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    monkeypatch.setattr(core, "build_signed_candidate_commit", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(core, "candidate_tree", lambda *_a, **_k: SAFETY_TREE)

    def candidate_code_must_not_run(*_a, **_k):
        raise AssertionError("the publisher must never execute the candidate's pre-push scripts")

    monkeypatch.setattr(core, "_run_pre_push_safety", candidate_code_must_not_run)

    def fake_git(_repo, *args, **_kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(core, "_git_plumbing", fake_git)
    publication = core.publish_create_only(
        tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE
    )
    assert publication.head_sha == "b" * 40
    push = calls[-1]
    assert push[:3] == ("push", "--no-verify", f"--force-with-lease=refs/heads/{validated.branch}:")
    assert push[3] == "https://github.com/fafa33/Project-Hunter.git"
    assert not any(x in {"checkout", "commit", "merge"} for call in calls for x in call)


def test_rehearsal_workflow_has_no_publication_or_model_secret():
    from pathlib import Path

    text = Path(".github/workflows/hunter-issue-agent-replacement-rehearsal.yml").read_text(encoding="utf-8")
    assert "permissions: {}" in text and "environment:" not in text
    assert "persist-credentials: false" in text
    for forbidden in (
        "HUNTER_AGENT_GITHUB_PUSH_TOKEN",
        "HUNTER_ISSUE_AGENT_PR_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
    ):
        assert forbidden not in text
    assert "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY" in text
    # The validator imports hunter.automation (package initializer needs the full pinned runtime): the install must
    # be the constraint-pinned, wheel-only project install, never an unpinned or partial one.
    assert (
        "python -m pip install --disable-pip-version-check --only-binary ':all:' -c requirements/ci-constraints.txt ."
        in text
    )


def test_publisher_rejects_receipt_for_different_result(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.ReplacementValidationReceipt(
        validated.authorization_id, validated.base_sha, validated.branch, "0" * 64, "v1"
    )
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    with pytest.raises(core.ReplacementExecutorError, match="does not bind"):
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE)


def _capture_isolated_hook(monkeypatch, tmp_path):
    """Run _run_pre_push_safety with git/sudo stubbed; return every subprocess call."""
    import hunter.automation.issue_agent_replacement_executor as core

    calls = []

    def fake_git(_repo, *args, **_kwargs):
        return "def enforce_credential_free_candidate_safety(lines):\n    return 0\n" if args[0] == "show" else ""

    class Done:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), dict(kwargs.get("env") or {})))
        return Done()

    monkeypatch.setattr(core, "_git_plumbing", fake_git)
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(core, "ISOLATION_ROOT", tmp_path)
    return core, calls


#: Representative secrets of every class that must never reach candidate code.
_FORBIDDEN_TO_CANDIDATE = {
    "HUNTER_ISSUE_AGENT_WEBHOOK_URL": "https://issuer-secret.example/issue-agent/authorize",
    "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": "verifying-key-secret",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-request-secret",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc-request-secret.example",
    "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN": "push-token-secret",
    "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY": "signing-key-secret",
    "HUNTER_ISSUE_AGENT_PR_TOKEN": "pr-token-secret",
    "HUNTER_AGENT_GITHUB_PUSH_TOKEN": "legacy-push-secret",
    "GROQ_API_KEY": "model-key-secret",
    "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY": "executor-model-secret",
    "UNRELATED_FUTURE_SECRET": "unrelated-secret",
}


def test_candidate_pre_push_receives_only_an_explicit_allowlist_as_the_isolation_user(monkeypatch, tmp_path):
    """Issue #557 / PR #558 review: issuer and every other secret is absent from candidate code."""
    core, calls = _capture_isolated_hook(monkeypatch, tmp_path)
    signed = _signed()
    validated = core.validate_replacement_result(_result(signed), signed_authorization=signed, rehearsal=False)
    for name, value in _FORBIDDEN_TO_CANDIDATE.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/opt/python/bin:/usr/bin")
    for name in ("LANG", "LC_ALL", "TZ"):
        monkeypatch.delenv(name, raising=False)
    core._run_pre_push_safety(
        tmp_path,
        validated=validated,
        head="b" * 40,
        push_url="https://github.com/fafa33/Project-Hunter.git",
        isolation_user="hunter-untrusted",
    )
    hook = [argv for argv, _env in calls if "enforce_credential_free_candidate_safety" in " ".join(argv)]
    assert len(hook) == 1
    argv = hook[0]
    assert argv[:7] == ("sudo", "-n", "-u", "hunter-untrusted", "--", "/usr/bin/env", "-i")
    assignments = dict(part.split("=", 1) for part in argv[7:] if "=" in part and not part.startswith("/"))
    assert set(assignments) == {
        "PATH", "HOME", "PYTHONPATH", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT",
    }  # fmt: skip
    assert assignments["PATH"] == "/opt/python/bin:/usr/bin"
    # No secret value appears in any launched process's argv or environment.
    for every_argv, every_env in calls:
        flattened = " ".join(every_argv) + " " + " ".join(f"{k}={v}" for k, v in every_env.items())
        for value in _FORBIDDEN_TO_CANDIDATE.values():
            assert value not in flattened
        assert set(every_env) <= {"PATH"}
    # Leftover untrusted processes are killed and the untrusted clone removed.
    assert any(argv[:4] == ("sudo", "-n", "pkill", "-KILL") for argv, _ in calls)


def test_candidate_pre_push_refuses_without_a_distinct_isolation_user(monkeypatch, tmp_path):
    import os
    import pwd

    import hunter.automation.issue_agent_replacement_executor as core

    with pytest.raises(core.ReplacementExecutorError):
        core.require_isolation_user("")
    with pytest.raises(core.ReplacementExecutorError):
        core.require_isolation_user("no-such-hunter-user-xyz")
    with pytest.raises(core.ReplacementExecutorError, match="different non-root uid"):
        core.require_isolation_user(pwd.getpwuid(os.getuid()).pw_name)
    with pytest.raises(core.ReplacementExecutorError, match="different non-root uid"):
        core.require_isolation_user("root")


def test_publisher_remote_is_derived_from_signed_repository(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.validation_receipt(doc, signed_authorization=signed, validation_definition="v1")
    seen = []
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    monkeypatch.setattr(core, "build_signed_candidate_commit", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(core, "candidate_tree", lambda *_a, **_k: SAFETY_TREE)

    def candidate_code_must_not_run(*_a, **_k):
        raise AssertionError("the publisher must never execute the candidate's pre-push scripts")

    monkeypatch.setattr(core, "_run_pre_push_safety", candidate_code_must_not_run)

    def fake_git(_repo, *args, **_kwargs):
        seen.append(args)
        return ""

    monkeypatch.setattr(core, "_git_plumbing", fake_git)
    core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE)
    assert seen[0][1] == "https://github.com/fafa33/Project-Hunter.git"
    assert seen[-1][3] == "https://github.com/fafa33/Project-Hunter.git"


# --- Issue #557: candidate safety moved out of the credential-bearing publisher ---


def _git_repo_at_base(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    ident = {
        "GIT_AUTHOR_NAME": "Farhad5778",
        "GIT_AUTHOR_EMAIL": "34549283+fafa33@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Farhad5778",
        "GIT_COMMITTER_EMAIL": "34549283+fafa33@users.noreply.github.com",
    }
    run = lambda *a: subprocess.run(  # noqa: E731
        ("git", *a), cwd=repo, env={**env, **ident}, check=True, capture_output=True, text=True
    ).stdout.strip()
    run("init", "-q")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-q", "-m", "base")
    return repo, run("rev-parse", "HEAD"), ident


def _pinned_detached_repo(tmp_path, hook_text):
    """Shape of actions/checkout at an exact SHA with fetch-depth 0: detached HEAD, main only as a remote-tracking ref.

    Returns ``(repo, base, head, git)``; ``head`` is an unsigned candidate commit adding ``docs/x.md`` to ``base``.
    """
    import subprocess

    repo, _first, ident = _git_repo_at_base(tmp_path)
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        **ident,
    }
    git = lambda *a: subprocess.run(  # noqa: E731
        ("git", *a), cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "scripts").mkdir()
    (repo / "scripts" / "hunter_pre_push.py").write_text(hook_text)
    git("add", "scripts/hunter_pre_push.py")
    git("commit", "-q", "-m", "trusted hook")
    base = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", base)
    git("checkout", "-q", "--detach", base)
    for branch in git("for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        git("branch", "-q", "-D", branch)
    (repo / "docs").mkdir()
    (repo / "docs" / "x.md").write_text("candidate\n")
    git("add", "docs/x.md")
    head = git("commit-tree", git("write-tree"), "-p", base, "-m", "candidate")
    git("reset", "-q", "--hard", base)
    return repo, base, head, git


def _stub_isolation(monkeypatch, core, tmp_path, runner):
    """Replace ``sudo`` and the isolation user with ``runner`` while keeping real git and the real flow."""
    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(core, "isolated_command", runner)
    monkeypatch.setattr(core, "run_privileged", lambda *_a, **_k: None)
    monkeypatch.setattr(core, "ISOLATION_ROOT", tmp_path)


def _signed_at(base_sha):
    s = _signed()
    scope = TaskScopeContract(
        task_id=s.authorization.authorization_id,
        branch_pattern="issue-523-*",
        base_ref="main",
        base_sha=base_sha,
        allowed_paths=("docs/",),
        prohibited_paths=(),
    )
    return SignedIssueAgentAuthorization(s.authorization, scope, "00" * 64)


def test_credential_free_safety_refuses_any_publication_credential(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)

    signed = _signed()
    validated = core.validate_replacement_result(_result(signed), signed_authorization=signed, rehearsal=False)
    monkeypatch.setattr(core, "_git_plumbing", lambda *_a, **_k: pytest.fail("nothing may run"))
    for name in ("HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN", "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "credential")
        with pytest.raises(core.ReplacementExecutorError, match="publication authority"):
            core.run_credential_free_candidate_safety(tmp_path, validated=validated, isolation_user="hunter-untrusted")
        monkeypatch.delenv(name)


def test_credential_free_safety_runs_hook_on_unsigned_exact_tree(monkeypatch, tmp_path):
    import subprocess

    import hunter.automation.issue_agent_replacement_executor as core

    repo, base, ident = _git_repo_at_base(tmp_path)
    for key, value in ident.items():
        monkeypatch.setenv(key, value)
    signed = _signed_at(base)
    validated = core.validate_replacement_result(_result(signed), signed_authorization=signed, rehearsal=False)
    seen = {}

    def fake_safety(root, *, validated, head, push_url, isolation_user):
        seen.update(head=head, push_url=push_url, user=isolation_user)

    monkeypatch.setattr(core, "_run_pre_push_safety", fake_safety)
    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    tree = core.run_credential_free_candidate_safety(repo, validated=validated, isolation_user="hunter-untrusted")
    assert seen["user"] == "hunter-untrusted"
    show = lambda *a: subprocess.run(("git", *a), cwd=repo, capture_output=True, text=True).stdout  # noqa: E731
    assert show("rev-parse", f"{seen['head']}^{{tree}}").strip() == tree
    assert "gpgsig" not in show("cat-file", "commit", seen["head"])
    assert show("show", f"{tree}:docs/x.md") == "safe candidate\n"
    assert seen["push_url"] == "https://github.com/fafa33/Project-Hunter.git"


def test_publisher_refuses_a_tree_the_safety_boundary_did_not_prove(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.validation_receipt(doc, signed_authorization=signed, validation_definition="v1")
    pushes = []
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    monkeypatch.setattr(core, "build_signed_candidate_commit", lambda *_a, **_k: "b" * 40)
    monkeypatch.setattr(core, "candidate_tree", lambda *_a, **_k: "d" * 40)
    monkeypatch.setattr(core, "_git_plumbing", lambda _r, *args, **_k: pushes.append(args) or "")
    with pytest.raises(core.ReplacementExecutorError, match="differs from the credential-free safety tree"):
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree=SAFETY_TREE)
    assert not any(call[0] == "push" for call in pushes)
    with pytest.raises(core.ReplacementExecutorError, match="exact credential-free safety tree"):
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, safety_tree="")


def test_isolation_root_is_unique_private_and_never_writable_by_others(monkeypatch, tmp_path):
    """Sonar python:S5443 -- the untrusted root is a fresh mkdtemp directory, not a shared one."""
    import os
    import stat

    import hunter.automation.issue_agent_replacement_executor as core

    monkeypatch.setattr(core, "ISOLATION_ROOT", tmp_path)
    first, second = core.new_isolation_root("hunter-test-"), core.new_isolation_root("hunter-test-")
    assert first != second and first.parent == tmp_path
    for root in (first, second):
        info = root.stat()
        assert info.st_uid == os.getuid()
        assert stat.S_IMODE(info.st_mode) == 0o711
        assert not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH)


def test_candidate_clone_of_a_pinned_detached_checkout_still_has_the_governed_base(monkeypatch, tmp_path):
    """Codex P2 consequence: an exact-SHA (detached) trusted checkout has no local main."""

    import hunter.automation.issue_agent_replacement_executor as core

    repo, base, head, git = _pinned_detached_repo(
        tmp_path, "def enforce_credential_free_candidate_safety(lines):\n    return 0\n"
    )
    signed = _signed_at(base)
    validated = core.validate_replacement_result(_result(signed), signed_authorization=signed, rehearsal=False)
    report = tmp_path / "report"

    def as_runner(_user, environment, argv):
        script = f'git rev-parse HEAD origin/main > "{report}"'
        return ("/usr/bin/env", "-i", *(f"{k}={v}" for k, v in environment.items()), "/bin/sh", "-c", script)

    _stub_isolation(monkeypatch, core, tmp_path, as_runner)
    core._run_pre_push_safety(
        repo,
        validated=validated,
        head=head,
        push_url="https://github.com/fafa33/Project-Hunter.git",
        isolation_user="u",
    )
    assert report.read_text().split() == [head, base]
    assert git("for-each-ref", "refs/hunter") == ""


def test_publisher_and_validator_reject_the_executor_model_credential_and_any_api_key():
    """PR #558 review: the cutover's own model credential is model authority too."""
    for name in ("HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY", "MISTRAL_API_KEY", "HUNTER_AGENT_NEWPROVIDER_COMMAND"):
        assert not publisher_environment_is_safe({name: "configured"})
    assert publisher_environment_is_safe({"PATH": "/usr/bin", "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN": "t"})
    assert publisher_environment_is_safe({"HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY": "  "})


# --- Issue #682: the isolated candidate clone must carry the authorized repository identity ----------------------
#
# Production run 42021800036 refused a valid canary with PRE_PUSH_SAFETY_FAILED: the Defect Prevention Guard reads the
# repository owner from the repository's own identity (GITHUB_REPOSITORY or a GitHub origin) and the isolated clone's
# origin was a filesystem path while the allowlisted environment carries no GITHUB_REPOSITORY. ``push_url`` was passed
# to ``_run_pre_push_safety`` but never used.

_SCRIPTS = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"
_SECRET = "SECRET-VALUE-123"
#: Stand-in for the trusted base's safety entrypoint. It uses the REAL repository-owner resolver the guard uses, so the
#: outcome depends on the clone's own identity and the (scrubbed) environment, exactly like the production guard.
_OWNER_HOOK = f"""import os, sys
sys.path.insert(0, {str(_SCRIPTS)!r})
import hunter_writer_provenance as provenance


def enforce_credential_free_candidate_safety(lines):
    if provenance.repository_owner(cwd=os.getcwd()) != "fafa33":
        print("  - Defect Prevention Guard        [FAILED ] (0.1s)")
        print("      Details: /home/runner/private {_SECRET} owner unknown")
        sys.stderr.write("Traceback: {_SECRET} /home/runner/private")
        return 1
    return 0
"""


def _owner_guard_world(tmp_path, monkeypatch, *, repository="fafa33/Project-Hunter"):
    """Real git in the production shape (detached checkout, tracked main only); sudo replaced by ``env -i``."""
    import dataclasses
    import sys

    import hunter.automation.issue_agent_replacement_executor as core

    repo, base, head, _git = _pinned_detached_repo(tmp_path, _OWNER_HOOK)
    signed = _signed_at(base)
    validated = core.validate_replacement_result(_result(signed), signed_authorization=signed, rehearsal=False)
    validated = dataclasses.replace(validated, repository=repository)
    launched = []

    def as_runner(_user, environment, argv):
        launched.append(dict(environment))
        return ("/usr/bin/env", "-i", *(f"{k}={v}" for k, v in environment.items()), sys.executable, *argv[1:])

    _stub_isolation(monkeypatch, core, tmp_path, as_runner)

    def run(push_url):
        core._run_pre_push_safety(repo, validated=validated, head=head, push_url=push_url, isolation_user="u")

    return core, run, launched, validated


def test_correct_authorized_remote_passes_the_owner_guard(monkeypatch, tmp_path):
    core, run, launched, _ = _owner_guard_world(tmp_path, monkeypatch)
    # Mirror the hosted runner, which exports GITHUB_REPOSITORY; a different owner proves it is NOT what satisfied the guard.
    monkeypatch.setenv("GITHUB_REPOSITORY", "attacker/other")
    run("https://github.com/fafa33/Project-Hunter.git")  # no exception: owner came from the clone's origin
    assert len(launched) == 1


def test_no_github_repository_is_injected_into_the_isolated_environment(monkeypatch, tmp_path):
    core, run, launched, _ = _owner_guard_world(tmp_path, monkeypatch)
    monkeypatch.setenv("GITHUB_REPOSITORY", "fafa33/Project-Hunter")
    run("https://github.com/fafa33/Project-Hunter.git")
    (environment,) = launched
    assert "GITHUB_REPOSITORY" not in environment
    assert set(environment) <= {
        "PATH", "LANG", "LC_ALL", "TZ", "HOME", "PYTHONPATH", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT",
    }  # fmt: skip


def test_missing_github_remote_is_refused_with_the_failed_gate_name_only(monkeypatch, tmp_path, capsys):
    """The original defect: an origin that is a filesystem path leaves the repository owner unknown."""
    core, run, launched, _ = _owner_guard_world(tmp_path, monkeypatch)
    monkeypatch.setattr(core, "_candidate_origin", lambda _validated, _url: str(tmp_path / "local-origin"))
    with pytest.raises(core.ReplacementExecutorError) as refusal:
        run("https://github.com/fafa33/Project-Hunter.git")
    assert str(refusal.value) == "candidate pre-push safety failed: gates=Defect Prevention Guard"
    printed = capsys.readouterr().out
    assert "candidate pre-push safety failed: gates=Defect Prevention Guard exit=1" in printed
    for leaked in (_SECRET, "/home/runner/private", "Traceback", "Details"):
        assert leaked not in printed and leaked not in str(refusal.value)


def test_wrong_repository_owner_is_refused(monkeypatch, tmp_path):
    core, run, _launched, _ = _owner_guard_world(tmp_path, monkeypatch, repository="someone-else/other")
    with pytest.raises(core.ReplacementExecutorError, match="gates=Defect Prevention Guard"):
        run(
            "https://github.com/someone-else/other.git"
        )  # a self-consistent URL for another owner is still not the owner


@pytest.mark.parametrize(
    "push_url",
    [
        "",
        "https://x-access-token:ghp_credential@github.com/fafa33/Project-Hunter.git",
        "https://fafa33:password@github.com/fafa33/Project-Hunter.git",
        "http://github.com/fafa33/Project-Hunter.git",
        "https://github.com/fafa33/Project-Hunter",
        "https://github.com/fafa33/Project-Hunter.git\n",
        " https://github.com/fafa33/Project-Hunter.git",
        "https://github.com/fafa33/Project-Hunter.git?token=1",
        "https://evil.example/fafa33/Project-Hunter.git",
        "https://github.com.evil.example/fafa33/Project-Hunter.git",
        "git@github.com:fafa33/Project-Hunter.git",
        "file:///etc",
        "/tmp/local-origin",
        "https://github.com/someone-else/other.git",
        "https://GitHub.com/fafa33/Project-Hunter.git",
    ],
)
def test_invalid_or_credential_bearing_push_url_is_refused_before_anything_runs(monkeypatch, tmp_path, push_url):
    core, run, launched, _ = _owner_guard_world(tmp_path, monkeypatch)

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("nothing may be cloned, chowned or launched for an unauthorized remote")

    monkeypatch.setattr(core, "_git_plumbing", must_not_run)
    monkeypatch.setattr(core, "run_privileged", must_not_run)
    monkeypatch.setattr(core.subprocess, "run", must_not_run)
    with pytest.raises(core.ReplacementExecutorError, match="authorized repository"):
        run(push_url)
    assert launched == []


def test_invalid_signed_repository_identity_is_refused(monkeypatch, tmp_path):
    core, run, _launched, _ = _owner_guard_world(tmp_path, monkeypatch, repository="not a/repository")
    with pytest.raises(core.ReplacementExecutorError, match="repository identity"):
        run("https://github.com/not a/repository.git")


def test_failed_gate_diagnostic_is_a_closed_vocabulary():
    import hunter.automation.issue_agent_replacement_executor as core

    summary = "\n".join(
        [
            "  - Architecture Index Guard       [PASSED ] (0.0s)",
            "  - Defect Prevention Guard        [FAILED ] (4.3s)",
            "      Details: [Defect Prevention Guard] FAIL: /home/runner/private SECRET",
            "  - Mypy                           [FAILED ] (19.9s)",
            "  - Mypy                           [FAILED ] (19.9s)",
            "  - Evil Gate (rm -rf /)           [FAILED ] (0.1s)",
            "  - Ruff-ish                       [FAILED ] (0.1s)",
            "Ruff [FAILED ] not a summary row",
            "  - Black                          [PASSED ] (10s)",
        ]
    )
    assert core.failed_safety_gates(summary) == ("Defect Prevention Guard", "Mypy")
    assert core.failed_safety_gates("Traceback ... SECRET") == ()
    assert core.failed_safety_gates("  - Black [FAILED]\n  - Ruff [FAILED ] ") == ("Black", "Ruff")


def test_failed_gate_parsing_is_linear_on_adversarial_output():
    """Sonar python:S5852: candidate-influenced output must not be able to trigger super-linear backtracking."""
    import time

    import hunter.automation.issue_agent_replacement_executor as core

    hostile = "-" + " " * 250_000 + "[FAILED"
    started = time.monotonic()
    assert core.failed_safety_gates(hostile) == ()
    assert core.failed_safety_gates("- " + "A " * 100_000 + "[FAILED ]\n" * 1000) == ()
    assert core.failed_safety_gates(("  - " + "Ruff " * 50 + "[FAILED ]\n") * 1000) == ()
    assert time.monotonic() - started < 2.0


def test_unrecognised_failure_reports_unclassified_without_stderr(monkeypatch, tmp_path, capsys):
    core, run, _launched, _ = _owner_guard_world(tmp_path, monkeypatch)
    hook = (
        "import sys\n"
        f"def enforce_credential_free_candidate_safety(lines):\n    sys.stderr.write('{_SECRET} /home/runner/private')\n    return 7\n"
    )
    monkeypatch.setattr(core, "_git_plumbing", _replace_hook(core._git_plumbing, hook))
    with pytest.raises(core.ReplacementExecutorError) as refusal:
        run("https://github.com/fafa33/Project-Hunter.git")
    assert str(refusal.value) == "candidate pre-push safety failed: gates=UNCLASSIFIED"
    assert "exit=" in capsys.readouterr().out


def _replace_hook(real, hook):
    """The trusted base still advertises the entrypoint; the clone's own script is what fails."""

    def patched(repo, *args, **kwargs):
        out = real(repo, *args, **kwargs)
        if "checkout" in args:  # candidate checked out: swap in a failing script (candidate-controlled code)
            (repo / "scripts" / "hunter_pre_push.py").write_text(hook)
        return out

    return patched


def test_gate_vocabulary_matches_the_trusted_preflight_lane_names():
    import hunter.automation.issue_agent_replacement_executor as core

    lane_source = (_SCRIPTS / "hunter_pr_preflight.py").read_text()
    for gate in core.SAFETY_GATE_VOCABULARY:
        assert f'("{gate}", (' in lane_source, gate
