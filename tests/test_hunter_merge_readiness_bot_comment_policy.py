"""Trusted-bot and owner-comment policy for Hunter Merge Readiness.

The policy is fail-closed by default: external top-level PR Conversation
comments block readiness until the repository owner acknowledges the comment
with a current 👍 reaction. Trusted status/advisory automation comments are
structurally exempt. A demonstrably non-blocking owner status note does not
require self-acknowledgement, while explicit owner blocking feedback remains in
the canonical feedback state.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import hunter_merge_readiness as core
import pytest
from hunter_readiness_harness import FakeGitHub, install, ready_pull_request


@pytest.fixture
def gh(monkeypatch):
    server = FakeGitHub()
    install(monkeypatch, server)
    return server


def _comment(login: str, body: str) -> dict:
    return {"id": 1, "user": {"login": login}, "body": body, "created_at": "2026-08-13T00:00:00Z"}


def test_dependency_review_status_comment_is_exempt():
    comment = _comment(
        "github-actions[bot]",
        "<!-- dependency-review-pr-comment-marker -->\nNo vulnerable dependencies found.",
    )
    assert core.is_exempt_status_comment(comment) is True


def test_draft_promotion_signal_comment_is_exempt():
    comment = _comment("github-actions[bot]", "<!-- hunter-draft-promotion:501 -->\nPromoted.")
    assert core.is_exempt_status_comment(comment) is True


def test_trusted_bot_without_a_structural_marker_is_not_exempt():
    comment = _comment("github-actions[bot]", "I think this looks wrong, please change it.")
    assert core.is_exempt_status_comment(comment) is False


def test_unknown_bot_carrying_a_known_marker_is_not_exempt():
    comment = _comment("impersonator[bot]", "<!-- dependency-review-pr-comment-marker -->\nlooks fine")
    assert core.is_exempt_status_comment(comment) is False


def test_human_comment_is_never_exempt():
    comment = _comment("fafa33", "<!-- dependency-review-pr-comment-marker -->")
    assert core.is_exempt_status_comment(comment) is False


def test_comment_without_a_user_is_not_exempt():
    assert core.is_exempt_status_comment({"id": 1, "body": "<!-- hunter-draft-promotion:1 -->"}) is False


def test_exempt_comment_does_not_block_readiness(gh):
    ready_pull_request(gh)
    gh.add_comment(
        501,
        900,
        login="github-actions[bot]",
        body="<!-- dependency-review-pr-comment-marker -->\nNo vulnerabilities.",
    )
    assert core.reconcile_pr(501).state == "success"


def test_exempt_comment_does_not_change_the_semantic_revision(gh):
    ready_pull_request(gh)
    before = core.read_current_state(501).semantic_revision()
    gh.add_comment(501, 900, login="github-actions[bot]", body="<!-- hunter-draft-promotion:501 -->")
    assert core.read_current_state(501).semantic_revision() == before


def test_owner_authored_comment_does_not_require_self_acknowledgement(gh):
    ready_pull_request(gh)
    gh.add_comment(
        501,
        907,
        login="fafa33",
        body=(
            "<!-- hunter-owner-status:nonblocking -->\nStatus: nonblocking\n"
            "Detail: reference=https://github.com/fafa33/Project-Hunter/pull/501"
        ),
    )

    decision = core.reconcile_pr(501)

    assert decision.state == "success"
    assert core.unacknowledged_top_level_comments(501) == ()
    assert not gh.reactions.get(907)


@pytest.mark.parametrize(
    "body",
    [
        "ACCEPTED AS BLOCKING. Do not resolve until the root cause is fixed.",
        "Do not merge; this blocker remains unresolved.",
        "CHANGES REQUIRED: must fix the identity bypass.",
        "I reject this change; it cannot proceed until the identity defect is corrected.",
    ],
)
def test_explicit_owner_blocking_comment_remains_a_feedback_blocker(gh, body: str):
    ready_pull_request(gh)
    gh.add_comment(501, 908, login="fafa33", body=body)

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert core.unacknowledged_top_level_comments(501) == (908,)
    gh.acknowledge(908)
    assert core.reconcile_pr(501).state == "failure"


def test_non_owner_cannot_claim_owner_nonblocking_status_exemption(gh):
    ready_pull_request(gh)
    gh.add_comment(
        501,
        909,
        login="a-reviewer",
        body="<!-- hunter-owner-status:nonblocking -->\nlooks fine",
    )

    assert core.reconcile_pr(501).state == "failure"
    assert core.unacknowledged_top_level_comments(501) == (909,)


def test_owner_cannot_quote_nonblocking_marker_to_exempt_blocking_feedback(gh):
    ready_pull_request(gh)
    gh.add_comment(
        501,
        910,
        login="fafa33",
        body="BLOCKING: do not merge. Quoted marker: <!-- hunter-owner-status:nonblocking -->",
    )

    assert core.reconcile_pr(501).state == "failure"
    assert core.unacknowledged_top_level_comments(501) == (910,)


@pytest.mark.parametrize(
    "detail",
    [
        "Reject this change; cannot proceed.",
        "BLOCKING: do not merge.",
        "Approval withheld until fixed.",
    ],
)
def test_owner_nonblocking_schema_rejects_free_form_contradictory_detail(gh, detail: str):
    ready_pull_request(gh)
    gh.add_comment(
        501,
        911,
        login="fafa33",
        body=f"<!-- hunter-owner-status:nonblocking -->\nStatus: nonblocking\nDetail: {detail}",
    )

    assert core.reconcile_pr(501).state == "failure"
    assert core.unacknowledged_top_level_comments(501) == (911,)


def test_non_exempt_bot_comment_blocks_until_acknowledged(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 901, login="github-actions[bot]", body="a free-form remark with no marker")
    assert core.reconcile_pr(501).state == "failure"
    gh.acknowledge(901)
    assert core.reconcile_pr(501).state == "success"


def test_human_comment_blocks_until_acknowledged(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 902, login="a-reviewer", body="please add replay evidence")
    decision = core.reconcile_pr(501)
    assert decision.state == "failure"
    assert "902" in decision.description


def test_acknowledgement_by_a_non_owner_does_not_count(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 903)
    gh.reactions[903] = [
        {"user": {"login": "someone-else"}, "content": "+1", "created_at": "2026-08-13T00:00:00Z"}
    ]
    assert core.reconcile_pr(501).state == "failure"


def test_owner_acknowledgement_survives_later_comment_timestamp(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 904)
    gh.acknowledge(904, at="2026-08-12T00:00:00Z")
    gh.comments[501][0]["updated_at"] = "2026-08-13T00:00:00Z"
    assert core.reconcile_pr(501).state == "success"


def test_owner_acknowledgement_does_not_require_reaction_timestamp(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 905)
    gh.reactions[905] = [{"user": {"login": "fafa33"}, "content": "+1"}]
    assert core.reconcile_pr(501).state == "success"


def test_a_non_thumbsup_reaction_is_not_an_acknowledgement(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 906)
    gh.reactions[906] = [{"user": {"login": "fafa33"}, "content": "eyes", "created_at": "2026-08-13T00:00:00Z"}]
    assert core.reconcile_pr(501).state == "failure"


def test_trusted_preflight_failure_prevents_canonical_success(gh, monkeypatch):
    ready_pull_request(gh)
    monkeypatch.setattr(core, "trusted_governance_preflight_error", lambda _pr: "synthetic preflight blocker")

    decision = core.reconcile_pr(501)

    assert decision.state == "failure"
    assert "Governance preflight blocked readiness" in decision.description
    assert gh.readiness_status(gh.head_sha(501))[0] == "failure"


def test_issue_276_bootstrap_is_only_missing_file_exception(monkeypatch):
    monkeypatch.setenv(core.PREFLIGHT_ENFORCEMENT_ENV, "1")
    monkeypatch.setattr(core, "GOVERNANCE_PREFLIGHT_PATH", Path("/definitely/missing/hunter_governance_preflight.py"))

    assert core.trusted_governance_preflight_error(277) is None
    assert "not installed" in (core.trusted_governance_preflight_error(278) or "")


def test_trusted_preflight_failure_logs_diagnostics_but_returns_status_safe_error(monkeypatch, tmp_path: Path, capsys):
    script = tmp_path / "hunter_governance_preflight.py"
    script.write_text("# test preflight\n", encoding="utf-8")
    monkeypatch.setenv(core.PREFLIGHT_ENFORCEMENT_ENV, "1")
    monkeypatch.setattr(core, "GOVERNANCE_PREFLIGHT_PATH", script)
    core.repo = "fafa33/Project-Hunter"
    core.token = "controller-token"
    observed: dict[str, object] = {}

    def failed_run(*_args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=2,
            stdout="API diagnostic ghs_super-secret-token",
            stderr="Authorization: Bearer sensitive-header",
        )

    monkeypatch.setattr(core.subprocess, "run", failed_run)

    error = core.trusted_governance_preflight_error(278)

    assert error == core.GOVERNANCE_PREFLIGHT_FAILURE_DESCRIPTION
    logs = capsys.readouterr().out
    assert "ghs_super-secret-token" not in logs
    assert "super-secret-token" not in logs
    assert "sensitive-header" not in logs
    assert "[REDACTED" in logs or "REDACTED" in logs


def _assert_credential_redacted(logs: str, secret: str) -> None:
    prefix = secret.split("_", 1)[0] + "_"
    assert secret not in logs
    assert prefix not in logs
    assert secret[:-10] not in logs
    assert secret[-8:] not in logs
    assert logs.count("[REDACTED_TOKEN]") == 1


def _assert_authorization_header_canonical(logs: str) -> None:
    assert logs == "Trusted governance preflight stdout:\nAuthorization: [REDACTED]\n"
    assert "Bearer" not in logs
    assert logs.count("[REDACTED]") == 1


@pytest.mark.parametrize("prefix", ["ghp_", "ghs_", "gho_", "ghu_", "ghr_", "github_pat_"])
def test_governance_preflight_logs_redact_every_supported_credential_prefix(prefix, capsys):
    secret = f"{prefix}{'a' * 40}"
    core._log_governance_preflight_output(f"API diagnostic {secret} inline", None)
    logs = capsys.readouterr().out
    _assert_credential_redacted(logs, secret)
    assert "API diagnostic" in logs
    assert "inline" in logs


def test_governance_preflight_logs_redact_authorization_header_to_exact_canonical_shape(capsys):
    secret = "ghp_1234567890ABCDEFabcdef"
    core._log_governance_preflight_output(f"Authorization: Bearer {secret}", None)
    _assert_authorization_header_canonical(capsys.readouterr().out)

    core._log_governance_preflight_output(f"Authorization: Bearer {secret} extra", None)
    logs = capsys.readouterr().out
    assert logs == "Trusted governance preflight stdout:\nAuthorization: [REDACTED] extra\n"
    assert secret not in logs
    assert logs.count("[REDACTED]") == 1

    core._log_governance_preflight_output(f"Bearer {secret} extra", None)
    logs = capsys.readouterr().out
    assert logs == "Trusted governance preflight stdout:\nBearer [REDACTED] extra\n"
    assert secret not in logs
    assert logs.count("[REDACTED]") == 1


def _sabotaged_re(should_sabotage: Callable[[str], bool]) -> object:
    real_sub = re.sub

    class _LocalRe:
        def __getattr__(self, name: str) -> object:
            return getattr(re, name)

        def sub(self, pattern: str, repl: str, string: str, count: int = 0, flags: int = 0) -> str:
            if should_sabotage(pattern):
                return string
            return real_sub(pattern, repl, string, count=count, flags=flags)

    return _LocalRe()


def test_governance_preflight_redaction_contract_fails_when_sanitizer_stops_redacting(monkeypatch, capsys):
    secret = "ghp_1234567890ABCDEFabcdef"
    real_stdlib_sub = re.sub

    monkeypatch.setattr(core, "re", _sabotaged_re(lambda pattern: True))
    core._log_governance_preflight_output(f"API diagnostic {secret} inline", None)
    logs = capsys.readouterr().out
    assert secret in logs
    assert re.sub is real_stdlib_sub
    with pytest.raises(AssertionError):
        _assert_credential_redacted(logs, secret)

    core._log_governance_preflight_output(f"Authorization: Bearer {secret}", None)
    logs = capsys.readouterr().out
    assert "Bearer" in logs
    with pytest.raises(AssertionError):
        _assert_authorization_header_canonical(logs)


def test_governance_preflight_redaction_contract_fails_on_partial_token_redaction(monkeypatch, capsys):
    secret = "ghp_1234567890ABCDEFabcdef"

    monkeypatch.setattr(core, "re", _sabotaged_re(lambda pattern: "gh[psour]_" in pattern))
    core._log_governance_preflight_output(f"API diagnostic {secret} inline", None)
    logs = capsys.readouterr().out
    assert secret in logs
    with pytest.raises(AssertionError):
        _assert_credential_redacted(logs, secret)


def test_governance_preflight_redaction_contract_fails_on_partial_authorization_redaction(monkeypatch, capsys):
    secret = "ghp_1234567890ABCDEFabcdef"

    monkeypatch.setattr(core, "re", _sabotaged_re(lambda pattern: "Authorization" in pattern))
    core._log_governance_preflight_output(f"Authorization: Bearer {secret}", None)
    logs = capsys.readouterr().out
    assert "Bearer" in logs
    with pytest.raises(AssertionError):
        _assert_authorization_header_canonical(logs)


def test_trusted_preflight_timeout_is_bounded_and_status_safe(monkeypatch, tmp_path: Path, capsys):
    script = tmp_path / "hunter_governance_preflight.py"
    script.write_text("# test preflight\n", encoding="utf-8")
    monkeypatch.setenv(core.PREFLIGHT_ENFORCEMENT_ENV, "1")
    monkeypatch.setattr(core, "GOVERNANCE_PREFLIGHT_PATH", script)
    core.repo = "fafa33/Project-Hunter"
    core.token = "controller-token"
    observed: dict[str, object] = {}

    def timed_out_run(*_args, **kwargs):
        observed.update(kwargs)
        raise subprocess.TimeoutExpired(
            cmd=("python", "hunter_governance_preflight.py"),
            timeout=core.GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS,
            output="partial stdout",
            stderr="partial stderr",
        )

    monkeypatch.setattr(core.subprocess, "run", timed_out_run)

    error = core.trusted_governance_preflight_error(278)

    assert error == core.GOVERNANCE_PREFLIGHT_TIMEOUT_DESCRIPTION
    assert observed["timeout"] == core.GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS
    logs = capsys.readouterr().out
    assert "partial stdout" in logs
    assert "partial stderr" in logs
    assert "timed out after" in logs
