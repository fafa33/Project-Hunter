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
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, push_url="unused")


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
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, push_url="origin")
    assert calls and calls[0][0] == "ls-remote"


def test_publisher_uses_data_only_git_plumbing_and_create_only_lease(monkeypatch, tmp_path):
    import hunter.automation.issue_agent_replacement_executor as core

    signed = _signed()
    doc = _result(signed)
    validated = core.validate_replacement_result(doc, signed_authorization=signed, rehearsal=False)
    receipt = core.validation_receipt(doc, signed_authorization=signed, validation_definition="v1")
    calls = []
    monkeypatch.setattr(core, "publisher_environment_is_safe", lambda _env: True)
    monkeypatch.setattr(core, "build_signed_candidate_commit", lambda *_a, **_k: "b" * 40)

    def fake_git(_repo, *args, **_kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(core, "_git_plumbing", fake_git)
    publication = core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, push_url="origin")
    assert publication.head_sha == "b" * 40
    push = calls[-1]
    assert push[:3] == ("push", "--no-verify", f"--force-with-lease=refs/heads/{validated.branch}:")
    assert not any(x in {"checkout", "commit", "merge"} for call in calls for x in call)


def test_rehearsal_workflow_has_no_publication_or_model_secret():
    from pathlib import Path

    text = Path(".github/workflows/hunter-issue-agent-replacement-rehearsal.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text
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
    assert "requirements/ci-constraints.txt" in text
    assert '"cryptography>=50.0.0,<51"' in text


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
        core.publish_create_only(tmp_path, validated=validated, verified_receipt=receipt, push_url="unused")
