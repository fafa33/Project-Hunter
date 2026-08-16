"""Trusted-bot and owner-comment policy for Hunter Merge Readiness.

The policy is fail-closed by default: external top-level PR Conversation
comments block readiness until the repository owner acknowledges that exact
comment version with a 👍 reaction. Trusted status/advisory automation comments
are structurally exempt. A demonstrably non-blocking owner status note does not
require self-acknowledgement, while explicit owner blocking feedback remains in
the canonical feedback state.
"""

from __future__ import annotations

import subprocess
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
    gh.add_comment(501, 907, login="fafa33", body="owner implementation note")

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
    gh.acknowledge(903, owner="someone-else")
    assert core.reconcile_pr(501).state == "failure"


def test_acknowledgement_predating_the_current_comment_version_does_not_count(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 904)
    gh.acknowledge(904, at="2026-08-12T00:00:00Z")
    gh.comments[501][0]["updated_at"] = "2026-08-13T00:00:00Z"
    assert core.reconcile_pr(501).state == "failure"


def test_acknowledgement_without_a_reaction_time_fails_closed(gh):
    ready_pull_request(gh)
    gh.add_comment(501, 905)
    gh.reactions[905] = [{"user": {"login": "fafa33"}, "content": "+1"}]
    assert core.reconcile_pr(501).state == "failure"


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
            stdout="API diagnostic super-secret-token",
            stderr="Authorization: Bearer sensitive-header",
        )

    monkeypatch.setattr(core.subprocess, "run", failed_run)

    error = core.trusted_governance_preflight_error(278)

    assert error == core.GOVERNANCE_PREFLIGHT_FAILURE_DESCRIPTION
    assert "secret" not in error
    assert "sensitive" not in error
    assert observed["timeout"] == core.GOVERNANCE_PREFLIGHT_TIMEOUT_SECONDS
    logs = capsys.readouterr().out
    assert "super-secret-token" in logs
    assert "sensitive-header" in logs


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
