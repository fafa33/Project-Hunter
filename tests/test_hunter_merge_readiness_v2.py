from __future__ import annotations

from pathlib import Path
from typing import Any

import hunter_merge_readiness_v2 as core
import pytest


def _pr(*, draft: bool = False, mergeable: bool | None = True, body: str = "anything") -> dict:
    return {
        "state": "open",
        "draft": draft,
        "mergeable": mergeable,
        "body": body,
        "title": "metadata is not authority",
        "head": {"sha": "a" * 40},
    }


def _green_check(name: str, ident: int) -> dict:
    return {"id": ident, "name": name, "status": "completed", "conclusion": "success"}


def _install_green(monkeypatch, pr: dict | None = None, *, mock_knowledge_receipt: bool = True) -> None:
    pr_payload = pr or _pr()
    monkeypatch.setattr(core, "request_json", lambda method, path, payload=None: pr_payload)
    monkeypatch.setattr(core, "unresolved_review_threads", lambda _number: ())
    monkeypatch.setattr(core, "changes_requested_reviewers", lambda _number: ())
    monkeypatch.setattr(
        core,
        "all_check_runs",
        lambda _sha: [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)],
    )
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "success"})
    if mock_knowledge_receipt:
        # Callers exercising the real knowledge_receipt_state (its paged()/authentication/
        # freshness mechanics) pass mock_knowledge_receipt=False to keep this fixture from
        # shadowing the exact function under test.
        monkeypatch.setattr(core, "knowledge_receipt_state", lambda _sha, _number: ("success", "verified"))
    monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (501,))
    monkeypatch.setattr(core, "review_authority_state", lambda _sha, _number: ("success", "reviewed"))
    monkeypatch.setattr(core, "candidate_admission_state", lambda _sha, _number: ("success", "admitted"))


def test_green_current_state_is_merge_ready(monkeypatch):
    _install_green(monkeypatch)

    sha, decision = core.decide(501)

    assert sha == "a" * 40
    assert decision.state == "success"


def test_pr_body_and_title_do_not_change_merge_decision(monkeypatch):
    _install_green(monkeypatch, _pr(body="no canonical matrix, no issue identity, no readiness declaration"))

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_draft_blocks(monkeypatch):
    _install_green(monkeypatch, _pr(draft=True))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "Draft" in decision.description


def test_merge_conflict_blocks(monkeypatch):
    _install_green(monkeypatch, _pr(mergeable=False))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "conflict" in decision.description.lower()


def test_unresolved_mergeability_waits(monkeypatch):
    _install_green(monkeypatch, _pr(mergeable=None))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"


def test_unresolved_review_thread_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "unresolved_review_threads", lambda _number: ("thread-1",))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "Unresolved review threads" in decision.description


def test_no_codex_review_on_current_head_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "no exact-head hostile review exists on the current HEAD"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_codex_review_of_an_older_head_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "the candidate was mutated after it was reviewed"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_current_head_codex_review_with_unresolved_finding_is_non_blocking(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "substantive review findings remain unresolved: F-1"),
    )

    _sha, decision = core.decide(501)

    # A non-terminal authority failure remains fail-closed; timeout/unavailability
    # are represented separately as successful terminal opportunity states.
    assert decision.state == "success"


def test_resolved_finding_without_structured_evidence_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "resolved finding F-2 lacks structured resolution evidence"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_current_head_codex_review_with_structured_evidence_allows(monkeypatch):
    _install_green(monkeypatch)

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_new_commit_after_codex_review_stales_readiness_again():
    """Issue #467: a review bound to content cannot survive a head mutation."""
    reviewed = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        governance_status={"id": 99, "state": "success"},
        review_authority=("success", "complete base->HEAD hostile review"),
        knowledge_receipt=("success", ""),
    )
    assert core.evaluate(reviewed).state == "success"

    mutated = core.StaticReadinessObservation(
        check_runs=reviewed.check_runs,
        governance_status=reviewed.governance_status,
        review_authority=("failure", "the candidate was mutated after it was reviewed"),
        knowledge_receipt=("success", ""),
    )

    decision = core.evaluate(mutated)

    assert decision.state == "success"


def test_a_fallback_review_of_the_exact_head_is_a_valid_review_authority(monkeypatch):
    """Requirement 2: Codex is preferred, not the only admissible reviewer."""
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("success", "complete base->HEAD hostile review (fallback: Codex unavailable)"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_missing_fallback_review_still_blocks_readiness(monkeypatch):
    """Requirement 10: Codex unavailable and fallback evidence missing still blocks."""
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "review_authority_state",
        lambda _sha, _number: ("failure", "no exact-head hostile review exists on the current HEAD"),
    )

    _sha, decision = core.decide(501)

    assert decision.state == "success"


def test_a_new_commit_after_a_valid_fallback_review_stales_readiness_again():
    """Requirement 6: HEAD mutation invalidates a fallback review exactly as a Codex one."""
    reviewed = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        governance_status={"id": 99, "state": "success"},
        review_authority=("success", "fallback hostile review verified"),
        knowledge_receipt=("success", ""),
    )
    assert core.evaluate(reviewed).state == "success"

    mutated = core.StaticReadinessObservation(
        check_runs=reviewed.check_runs,
        governance_status=reviewed.governance_status,
        review_authority=("failure", "the candidate was mutated after it was reviewed"),
        knowledge_receipt=("success", ""),
    )

    assert core.evaluate(mutated).state == "success"


def test_changes_requested_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "changes_requested_reviewers", lambda _number: ("reviewer",))

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "reviewer" in decision.description


def test_failed_required_check_blocks(monkeypatch):
    _install_green(monkeypatch)
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    runs[0] = {**runs[0], "conclusion": "failure"}
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: runs)

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "Quality Gates=failure" in decision.description


def test_failed_codeql_blocks(monkeypatch):
    _install_green(monkeypatch)
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    codeql_index = core.REQUIRED_CHECKS.index("CodeQL")
    runs[codeql_index] = {**runs[codeql_index], "conclusion": "failure"}
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: runs)

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "CodeQL=failure" in decision.description


def test_missing_required_check_waits(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "all_check_runs", lambda _sha: [])

    _sha, decision = core.decide(501)

    assert decision.state == "pending"


def test_legacy_governance_status_is_not_a_second_merge_gate(monkeypatch):
    # knowledge_receipt_state no longer shares latest_status with governance_status (Issue #527),
    # so overriding it here exercises only the legacy GOVERNANCE_CONTEXT status, as intended.
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "failure"})
    _sha, decision = core.decide(501)
    assert decision.state == "success"
    assert core.GOVERNANCE_CONTEXT not in decision.description


def test_missing_legacy_governance_status_does_not_duplicate_current_evidence(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: None)
    _sha, decision = core.decide(501)
    assert decision.state == "success"


# --- ADR 0039 L11 (Issue #527): the fast-path Knowledge Receipt obligation --------------------------------

TRUSTED_CREATOR = {"login": core.KNOWLEDGE_RECEIPT_TRUSTED_CREATOR}


def _receipt_status(pr_number: int = 501, *, state: str = "success", description: str = "ok", **overrides: Any) -> dict:
    status = {
        "id": 99,
        "context": f"{core.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}{pr_number}",
        "state": state,
        "description": description,
        "creator": TRUSTED_CREATOR,
        "created_at": _now_iso(),
    }
    status.update(overrides)
    return status


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def test_missing_knowledge_receipt_fails_closed(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "knowledge_receipt_state", lambda _sha, _n: ("failure", "1 missing"))
    _sha, decision = core.decide(501)
    assert decision.state == "failure"
    assert "Knowledge Receipt" in decision.description


def test_absent_knowledge_receipt_status_is_pending_not_success(monkeypatch):
    """No status yet at this exact head is a genuine unknown, never a silently satisfied obligation."""
    _install_green(monkeypatch, mock_knowledge_receipt=False)
    monkeypatch.setattr(core, "paged", lambda _path: [])
    _sha, decision = core.decide(501)
    assert decision.state == "pending"
    assert "Knowledge Receipt" in decision.description


def test_knowledge_receipt_producer_failure_is_pending_never_success(monkeypatch):
    """A failed read of the transport status must never be treated as a satisfied (zero) obligation."""
    _install_green(monkeypatch, mock_knowledge_receipt=False)

    def raising(_path: str) -> list:
        raise RuntimeError("transport failure")

    monkeypatch.setattr(core, "paged", raising)
    _sha, decision = core.decide(501)
    assert decision.state == "pending"


def test_valid_pending_adjudication_receipt_permits_progression(monkeypatch):
    """AC3/AC7: a receipt the producer verified as satisfied -- even with a pending family link --
    is success, never a block, once the trusted producer has published that verdict."""
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "knowledge_receipt_state", lambda _sha, _n: ("success", "1 receipt(s) verified"))
    _sha, decision = core.decide(501)
    assert decision.state == "success"


def test_legacy_pr_with_no_verified_obligation_is_not_retroactively_blocked(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core, "knowledge_receipt_state", lambda _sha, _n: ("success", "no verified applicable receipt obligation")
    )
    _sha, decision = core.decide(501)
    assert decision.state == "success"


def test_knowledge_receipt_state_reads_the_exact_head_never_a_stale_or_foreign_one():
    seen: list[str] = []

    def spy(path: str) -> list[dict]:
        seen.append(path)
        return [_receipt_status(737)]

    original = core.paged
    try:
        core.paged = spy  # type: ignore[assignment]
        state, _detail = core.knowledge_receipt_state("f" * 40, 737)
    finally:
        core.paged = original
    assert seen == [f"commits/{'f' * 40}/statuses"]
    assert state == "success"


# --- F4: PR-bound, never a different PR's status ------------------------------------------------------


def test_a_different_prs_status_at_the_same_sha_is_never_consumed(monkeypatch):
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(100, state="success")])
    state, detail = core.knowledge_receipt_state("a" * 40, 200)
    assert state == "pending"
    assert "200" in detail


def test_each_pr_reads_only_its_own_context_at_a_shared_sha(monkeypatch):
    monkeypatch.setattr(
        core,
        "paged",
        lambda _path: [
            _receipt_status(100, state="failure", description="100 missing"),
            _receipt_status(200, state="success"),
        ],
    )
    assert core.knowledge_receipt_state("a" * 40, 100)[0] == "failure"
    assert core.knowledge_receipt_state("a" * 40, 200)[0] == "success"


# --- F5: unauthenticated publisher ---------------------------------------------------------------------


def test_an_untrusted_publisher_is_never_consumed(monkeypatch):
    monkeypatch.setattr(
        core, "paged", lambda _path: [_receipt_status(501, state="success", creator={"login": "random-actor"})]
    )
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"  # invisible, not merely distrusted -- never a forged success either


def test_a_missing_creator_is_never_consumed(monkeypatch):
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success", creator=None)])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"


def test_a_trusted_publisher_with_valid_evidence_is_accepted(monkeypatch):
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success")])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "success"


def test_a_forged_status_cannot_bypass_a_genuinely_missing_receipt(monkeypatch):
    """An untrusted actor posting a forged success alongside the real (failing) trusted verdict
    must never win: only the trusted creator's entry is ever consulted."""
    monkeypatch.setattr(
        core,
        "paged",
        lambda _path: [
            _receipt_status(501, state="failure", description="1 missing"),
            _receipt_status(501, state="success", creator={"login": "random-actor"}, id=100),
        ],
    )
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "failure"


# --- F1/F3: bounded freshness --------------------------------------------------------------------------


def test_a_fresh_trusted_success_is_accepted(monkeypatch):
    monkeypatch.setattr(core, "_status_age_seconds", lambda _status: 60.0)
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success")])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "success"


def test_a_stale_success_past_the_freshness_bound_is_pending_not_success(monkeypatch):
    """F1/F3: a success published before a thread resolved into a new obligation, or before a
    status-publish retry recovered from a transient failure, must not be trusted forever."""
    monkeypatch.setattr(core, "_status_age_seconds", lambda _status: core.KNOWLEDGE_RECEIPT_MAX_AGE_SECONDS + 1)
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success")])
    state, detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"
    assert "stale" in detail


def test_a_stale_failure_is_also_downgraded_to_pending_never_fabricated_as_a_block(monkeypatch):
    """Staleness is symmetric: an old status is simply unknown, never upgraded into a confident
    failure either -- only a fresh verdict, in either direction, is trusted."""
    monkeypatch.setattr(core, "_status_age_seconds", lambda _status: core.KNOWLEDGE_RECEIPT_MAX_AGE_SECONDS + 1)
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="failure")])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"


def test_an_unparseable_created_at_is_treated_as_stale(monkeypatch):
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success", created_at="not-a-date")])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"


# --- Issue #586 / ADR 0039 L11 hardening (Copilot F1/F3, PR #737): obligation-fingerprint binding


def _with_fingerprint(description: str, fingerprint: str) -> str:
    return f"{description} [rt:{fingerprint}]"


def test_a_live_obligation_fingerprint_mismatch_downgrades_a_fresh_success_to_pending(monkeypatch):
    """F1: a thread resolving into a new obligation after publish must invalidate the cached

    success immediately, not merely after the bare age bound expires.
    """
    status = _receipt_status(501, state="success", description=_with_fingerprint("no obligation", "a" * 16))
    monkeypatch.setattr(core, "paged", lambda _path: [status])
    monkeypatch.setattr(core, "knowledge_receipt_obligation_fingerprint", lambda _pr: "b" * 16)
    state, detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"
    assert "review-thread change" in detail


def test_a_live_obligation_fingerprint_mismatch_downgrades_a_fresh_failure_to_pending_too(monkeypatch):
    """Staleness under obligation-fingerprint mismatch is symmetric with the age bound: a

    now-outdated verdict is simply unknown in either direction, never a fabricated block.
    """
    status = _receipt_status(501, state="failure", description=_with_fingerprint("1 missing", "a" * 16))
    monkeypatch.setattr(core, "paged", lambda _path: [status])
    monkeypatch.setattr(core, "knowledge_receipt_obligation_fingerprint", lambda _pr: "b" * 16)
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"


def test_a_matching_obligation_fingerprint_keeps_a_fresh_success(monkeypatch):
    status = _receipt_status(501, state="success", description=_with_fingerprint("1 receipt(s) verified", "a" * 16))
    monkeypatch.setattr(core, "paged", lambda _path: [status])
    monkeypatch.setattr(core, "knowledge_receipt_obligation_fingerprint", lambda _pr: "a" * 16)
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "success"


def test_an_unreadable_live_obligation_snapshot_is_pending_never_a_silent_passthrough(monkeypatch):
    """F3: a transient failure re-reading live thread state must never fall back to trusting the

    cached (possibly now-stale) verdict.
    """
    status = _receipt_status(501, state="success", description=_with_fingerprint("no obligation", "a" * 16))
    monkeypatch.setattr(core, "paged", lambda _path: [status])
    monkeypatch.setattr(core, "knowledge_receipt_obligation_fingerprint", lambda _pr: None)
    state, detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"
    assert "re-validated" in detail


def test_a_legacy_status_with_no_embedded_fingerprint_falls_back_to_the_age_bound_only(monkeypatch):
    """A status published before this hardening carries no ``[rt:...]`` suffix; it must not be

    newly blocked on a format it never had the chance to carry, and must never trigger a live
    network read it cannot need.
    """
    called: list[int] = []
    monkeypatch.setattr(core, "knowledge_receipt_obligation_fingerprint", lambda pr: called.append(pr) or "x" * 16)
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, state="success", description="ok")])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "success"
    assert called == []


def test_knowledge_receipt_obligation_fingerprint_returns_none_on_read_failure(monkeypatch):
    def raising(_pr: int) -> dict:
        raise RuntimeError("transport failure")

    monkeypatch.setattr(core, "_live_review_thread_resolutions", raising)
    assert core.knowledge_receipt_obligation_fingerprint(501) is None


def test_knowledge_receipt_obligation_fingerprint_matches_the_shared_pure_function(monkeypatch):
    from hunter.automation.issue_agent_knowledge import review_thread_fingerprint

    monkeypatch.setattr(core, "_live_review_thread_resolutions", lambda _pr: {11: True, 12: False})
    assert core.knowledge_receipt_obligation_fingerprint(501) == review_thread_fingerprint({11: True, 12: False})


def test_fingerprint_in_dependency_free_hosted_controller(monkeypatch):
    """Regression for Codex P1: hosted bootstrap has scripts, not src/hunter."""
    import json
    import os
    import subprocess
    import sys

    scripts = str(Path(core.__file__).resolve().parent)
    snippet = (
        "import hunter_merge_readiness_v2 as readiness; "
        "readiness._live_review_thread_resolutions = lambda _pr: {11: True, 12: False}; "
        "print(readiness.knowledge_receipt_obligation_fingerprint(501))"
    )
    env = {**os.environ, "PYTHONPATH": scripts}
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, " + repr(scripts) + "); " + snippet],
        cwd=Path(core.__file__).resolve().parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = (
        __import__("hashlib")
        .sha256(json.dumps({"11": True, "12": False}, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()[:16]
    )
    assert result.stdout.strip() == expected


def test_live_review_thread_resolutions_paginates(monkeypatch):
    pages = [
        {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"isResolved": True, "comments": {"nodes": [{"databaseId": 1}]}}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    }
                }
            }
        },
        {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [{"isResolved": False, "comments": {"nodes": [{"databaseId": 2}]}}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        },
    ]
    calls: list[Any] = []

    def fake_graphql(_query, variables):
        calls.append(variables["after"])
        return pages.pop(0)

    monkeypatch.setattr(core, "REPO", "fafa33/Project-Hunter")
    monkeypatch.setattr(core, "graphql_json", fake_graphql)
    result = core._live_review_thread_resolutions(501)
    assert result == {1: True, 2: False}
    assert calls == [None, "c1"]


def test_live_review_thread_resolutions_rejects_a_malformed_payload(monkeypatch):
    monkeypatch.setattr(core, "REPO", "fafa33/Project-Hunter")
    monkeypatch.setattr(
        core,
        "graphql_json",
        lambda _q, _v: {
            "repository": {"pullRequest": {"reviewThreads": {"nodes": [{"isResolved": True}], "pageInfo": {}}}}
        },
    )
    with pytest.raises(ValueError):
        core._live_review_thread_resolutions(501)


@pytest.mark.parametrize(
    ("overrides", "expected_state"),
    [
        ({"state": "success", "description": "ok"}, "success"),
        ({"state": "failure", "description": "missing"}, "failure"),
        ({"state": "error", "description": "producer crashed"}, "failure"),
        ({"state": "pending", "description": "still computing"}, "pending"),
    ],
)
def test_knowledge_receipt_state_maps_every_github_status_value(monkeypatch, overrides, expected_state):
    monkeypatch.setattr(core, "paged", lambda _path: [_receipt_status(501, **overrides)])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == expected_state


def test_knowledge_receipt_state_with_no_status_at_all_is_pending(monkeypatch):
    monkeypatch.setattr(core, "paged", lambda _path: [])
    state, _detail = core.knowledge_receipt_state("a" * 40, 501)
    assert state == "pending"


def test_shared_head_waits_for_unique_attribution(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (501, 502))

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "#502" in decision.description


def test_required_checks_match_repository_jobs():
    assert core.REQUIRED_CHECKS == ("Quality Gates", "dependency-review", "CodeQL")


def test_an_early_blocker_reads_no_review_or_check_state(monkeypatch):
    """The decision short-circuits, so a Draft sweep costs one API call, not six."""

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("decided state was read after an earlier blocker already decided")

    _install_green(monkeypatch, _pr(draft=True))
    for name in (
        "review_authority_state",
        "unresolved_review_threads",
        "changes_requested_reviewers",
        "all_check_runs",
        "latest_status",
        "open_prs_for_head",
    ):
        monkeypatch.setattr(core, name, _must_not_be_called)

    _sha, decision = core.decide(501)

    assert decision.state == "pending"
    assert "Draft" in decision.description


def test_a_supplied_observation_decides_identically_to_the_live_one(monkeypatch):
    """Callers reusing this definition must not need a second implementation."""

    _install_green(monkeypatch)
    _sha, live = core.decide(501)

    supplied = core.evaluate(
        core.StaticReadinessObservation(
            draft=False,
            mergeable=True,
            check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
            governance_status={"id": 99, "state": "success"},
            knowledge_receipt=("success", ""),
        )
    )

    assert supplied == live


def test_sweep_isolates_failure_to_one_pull_request(monkeypatch):
    published = []
    monkeypatch.setattr(core, "candidate_prs", lambda: (501, 502))

    def fake_decide(number: int):
        if number == 501:
            raise RuntimeError("boom")
        return "d" * 40, core.Decision("success", "ready")

    monkeypatch.setattr(core, "decide", fake_decide)
    monkeypatch.setattr(core, "publish", lambda sha, decision: published.append((sha, decision.state)))

    assert core.main() == 1
    assert published == [("d" * 40, "success")]


def test_governance_reconcile_completion_sweeps_open_pull_requests(monkeypatch):
    """The reconcile run publishes PR statuses from a default-branch run."""
    monkeypatch.setattr(
        core,
        "event_payload",
        lambda: {
            "workflow_run": {
                "name": "Hunter Governance Review Reconcile",
                "head_sha": "m" * 40,
                "pull_requests": [],
            }
        },
    )
    monkeypatch.setattr(core, "open_pull_requests", lambda: (466, 467))
    monkeypatch.setattr(
        core,
        "open_prs_for_head",
        lambda _sha: (_ for _ in ()).throw(AssertionError("default-branch SHA is not a candidate head")),
    )

    assert core.candidate_prs() == (466, 467)


def test_ordinary_workflow_completion_without_association_uses_exact_head(monkeypatch):
    monkeypatch.setattr(
        core,
        "event_payload",
        lambda: {
            "workflow_run": {
                "name": "CI",
                "head_sha": "h" * 40,
                "pull_requests": [],
            }
        },
    )
    monkeypatch.setattr(core, "open_prs_for_head", lambda sha: (501,) if sha == "h" * 40 else ())
    monkeypatch.setattr(
        core,
        "open_pull_requests",
        lambda: (_ for _ in ()).throw(AssertionError("ordinary workflow completion must remain head-scoped")),
    )

    assert core.candidate_prs() == (501,)


@pytest.mark.parametrize("kind", ["pull_request_review", "pull_request_review_comment"])
def test_review_wakeup_targets_only_affected_pr(monkeypatch, kind):
    monkeypatch.setenv("GITHUB_EVENT_NAME", kind)
    monkeypatch.setattr(core, "event_payload", lambda: {"pull_request": {"number": 588}, "action": "created"})
    monkeypatch.setattr(
        core,
        "open_pull_requests",
        lambda: (_ for _ in ()).throw(AssertionError("review wakeup must not sweep all PRs")),
    )
    assert core.candidate_prs() == (588,)


@pytest.mark.parametrize("invalid_number", [0, -1, "not-a-number"])
def test_review_wakeup_rejects_invalid_pr_identity(monkeypatch, invalid_number):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_review_comment")
    monkeypatch.setattr(core, "event_payload", lambda: {"pull_request": {"number": invalid_number}})
    monkeypatch.setattr(core, "open_pull_requests", lambda: (_ for _ in ()).throw(AssertionError("no sweep")))
    with pytest.raises(ValueError):
        core.candidate_prs()


def test_review_wakeup_with_missing_pr_identity_fails_closed_without_sweep(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_review_comment")
    monkeypatch.setattr(core, "event_payload", lambda: {"action": "created", "comment": {"id": 123}})
    monkeypatch.setattr(
        core,
        "open_pull_requests",
        lambda: (_ for _ in ()).throw(AssertionError("unattributed review must not trigger sweep")),
    )
    with pytest.raises(ValueError, match="review event has no pull request identity"):
        core.candidate_prs()


# --- Completion authority: a worker's self-report is never the source of truth ---
#
# These prove the "premature agent completion" failure mode is already
# structurally prevented by this controller: `evaluate_completion_claim`
# re-derives its verdict from current hosted state every time, so nothing an
# agent asserts (a final report, a pushed commit, a passed local preflight, an
# opened PR) can make a candidate COMPLETION_ACCEPTED on its own.


def _green_observation() -> core.StaticReadinessObservation:
    return core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        knowledge_receipt=("success", ""),
    )


def test_worker_says_done_while_required_checks_are_red_is_rejected():
    runs = [_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)]
    runs[0] = {**runs[0], "conclusion": "failure"}
    observation = core.StaticReadinessObservation(check_runs=tuple(runs), knowledge_receipt=("success", ""))

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "failed" in verdict.reason.lower()


def test_worker_says_done_while_pr_is_still_draft_is_rejected():
    """A Draft PR is the concrete, current-state form of "the review/readiness
    opportunity is still pending" this controller already gates on: it is not
    merge-ready no matter what any agent claims about it in the meantime."""

    observation = core.StaticReadinessObservation(
        draft=True,
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
    )

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "Draft" in verdict.reason


def test_worker_says_done_with_unresolved_validated_finding_is_rejected(tmp_path, monkeypatch):
    dispositions = tmp_path / "REVIEWER_FINDING_DISPOSITIONS.json"
    dispositions.write_text(
        '{"findings": [{"id": "RFD-1", "validation_state": "validated", "resolution_state": "unresolved"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(core, "REVIEWER_DISPOSITIONS_PATH", dispositions)

    verdict = core.evaluate_completion_claim(_green_observation())

    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"
    assert "RFD-1" in verdict.reason


def test_exact_head_with_all_required_evidence_satisfied_is_accepted():
    verdict = core.evaluate_completion_claim(_green_observation())

    assert verdict.accepted is True
    assert verdict.state == "COMPLETION_ACCEPTED"


def test_completion_verdict_does_not_depend_on_a_self_reported_claim():
    """There is no `claimed_done` input at all: calling the same observation
    twice, as any real caller would whether or not a worker claims completion,
    must produce the identical verdict -- self-report cannot move this."""

    observation = _green_observation()

    first = core.evaluate_completion_claim(observation)
    second = core.evaluate_completion_claim(observation)

    assert first == second == core.CompletionVerdict(True, "COMPLETION_ACCEPTED", first.reason)


def test_live_independent_review_opportunity_is_non_blocking():
    observation = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        review_authority=("pending", "WAITING_FOR_REVIEWER: exact HEAD dispatched"),
        knowledge_receipt=("success", ""),
    )
    assert core.evaluate(observation).state == "success"
    assert core.evaluate_completion_claim(observation).accepted is True


def test_broken_independent_review_orchestration_is_non_blocking():
    observation = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        review_authority=("failure", "MISSING_REVIEW_AUTHORITY: collector unavailable"),
        knowledge_receipt=("success", ""),
    )
    assert core.evaluate(observation).state == "success"


def test_worker_says_done_after_independent_review_reaches_a_terminal_state_is_accepted():
    """The paired positive: a terminal review-authority outcome (success,
    failure/exhausted-and-guarded, or anything other than the generic
    "pending" review_wait_state projects) does not block completion once
    every other current-state signal is green."""

    observation = core.StaticReadinessObservation(
        check_runs=tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1)),
        review_authority=("success", "VALID_LAST_RESORT_GUARD: pool exhausted, guard reviewed"),
        knowledge_receipt=("success", ""),
    )

    verdict = core.evaluate_completion_claim(observation)

    assert verdict.accepted is True
    assert verdict.state == "COMPLETION_ACCEPTED"


def test_decide_completion_is_a_real_production_caller_against_a_live_pr(monkeypatch):
    """Codex P1 (PR #530 review): a repo-wide search found no production
    caller of `evaluate_completion_claim` -- only the unit tests. This proves
    a real one exists: `decide_completion` builds the same live GitHub
    observation `decide()` uses and asks the completion question against it,
    so `python scripts/hunter_merge_readiness_v2.py completion <pr>` (see
    `main()`) is a genuine, invokable production path, not test-only code."""

    _install_green(monkeypatch)

    verdict = core.decide_completion(501)

    assert verdict == core.CompletionVerdict(True, "COMPLETION_ACCEPTED", verdict.reason)


def test_decide_completion_rejects_against_the_same_live_pr_when_checks_are_red(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(
        core,
        "all_check_runs",
        lambda _sha: [
            {"id": 1, "name": name, "status": "completed", "conclusion": "failure" if index == 1 else "success"}
            for index, name in enumerate(core.REQUIRED_CHECKS, start=1)
        ],
    )

    verdict = core.decide_completion(501)

    assert verdict is not None
    assert verdict.accepted is False
    assert verdict.state == "COMPLETION_REJECTED"


def test_decide_completion_returns_none_for_a_pr_that_is_not_open(monkeypatch):
    monkeypatch.setattr(core, "request_json", lambda method, path, payload=None: {**_pr(), "state": "closed"})

    assert core.decide_completion(501) is None


def test_normal_decide_path_enforces_completion_gate(monkeypatch):
    pr = {"state": "open", "head": {"sha": "a" * 40}}
    monkeypatch.setattr(core, "request_json", lambda *args, **kwargs: pr)
    monkeypatch.setattr(core, "LiveReadinessObservation", lambda *args: object())
    monkeypatch.setattr(
        core,
        "evaluate_completion_claim",
        lambda observation: core.CompletionVerdict(False, "COMPLETION_REJECTED", "worker completion rejected"),
    )
    monkeypatch.setattr(core, "evaluate", lambda observation: core.Decision("pending", "waiting"))
    head, decision = core.decide(530)
    assert head == "a" * 40
    assert decision.state == "pending"
    assert decision.description == "worker completion rejected"


def test_owner_merge_guard_rejects_dirty_or_unpushed_intended_head(monkeypatch):
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    outputs = iter(
        [
            "",
            "b" * 40 + "\n",
            "topic\n",
            "a" * 40 + "\trefs/heads/topic\n",
            '{"state":"open","head":{"sha":"' + "a" * 40 + '"}}',
        ]
    )
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: Result(next(outputs)))
    monkeypatch.setattr(core, "request_json", lambda *_args, **_kwargs: {"state": "open", "head": {"sha": "a" * 40}})
    verdict = core.owner_merge_guard(562)
    assert not verdict.accepted
    assert verdict.state == "HEAD_DIVERGENCE"


def test_owner_merge_guard_accepts_only_clean_identical_merge_ready_head(monkeypatch):
    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    head = "a" * 40
    outputs = iter(
        ["", head + "\n", "topic\n", head + "\trefs/heads/topic\n", '{"state":"open","head":{"sha":"' + head + '"}}']
    )
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: Result(next(outputs)))
    monkeypatch.setattr(core, "request_json", lambda *_args, **_kwargs: {"state": "open", "head": {"sha": head}})
    monkeypatch.setattr(core.transport, "request_rest_json", lambda **_kwargs: {"state": "open", "head": {"sha": head}})
    monkeypatch.setattr(
        core, "decide_completion", lambda _number: core.CompletionVerdict(True, "COMPLETION_ACCEPTED", "ready")
    )
    import hunter_github_contract as hgcc

    monkeypatch.setattr(core, "REPO", "fafa33/Project-Hunter")
    monkeypatch.setattr(core, "TOKEN", "authenticated-test-token")
    monkeypatch.setattr(
        hgcc,
        "observe_contract",
        lambda *args, **kwargs: {
            "rulesets": [
                {
                    "target": "branch",
                    "bypass_actors": [],
                    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                    "rules": [
                        {
                            "type": "required_status_checks",
                            "parameters": {"required_status_checks": [{"context": "Quality Gates"}]},
                        }
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        core,
        "all_check_runs",
        lambda _head, **kwargs: [{"id": 1, "name": "Quality Gates", "status": "completed", "conclusion": "success"}],
    )
    monkeypatch.setattr(core, "paged", lambda path, **kwargs: [])
    verdict = core.owner_merge_guard(562)
    assert verdict.accepted
    assert verdict.state == "OWNER_MERGE_GUARD_ACCEPTED"


def _success_description(authority: tuple[str, str]) -> str:
    runs = tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1))
    decision = core.evaluate(
        core.StaticReadinessObservation(check_runs=runs, review_authority=authority, knowledge_receipt=("success", ""))
    )
    assert decision.state == "success"
    assert len(decision.description) <= 140
    return decision.description


def test_success_description_names_a_completed_independent_review():
    description = _success_description(("success", "VALID_AGENT_REVIEW: codex cleared the exact head"))
    assert "independent review completed (VALID_AGENT_REVIEW)" in description
    assert "terminal" not in description


def test_success_description_says_terminal_only_for_policy_exhaustion():
    for state in sorted(core.TERMINAL_NONBLOCKING_REVIEW_STATES):
        description = _success_description(("success", f"{state}: independent-review opportunity ended"))
        assert f"exhausted under policy ({state})" in description
        assert "none completed" in description


def test_success_description_does_not_claim_a_review_that_was_never_requested():
    for authority in (
        ("pending", "MISSING_REVIEW_AUTHORITY: no usable pre-ready review request"),
        ("failure", "MALFORMED_REVIEW: verifier did not establish authority"),
        ("success", ""),
    ):
        description = _success_description(authority)
        assert "not requested or not established" in description
        assert "completed" not in description
        assert "terminal" not in description


def test_success_description_reports_a_pending_independent_review_as_pending():
    description = _success_description(("pending", "WAITING_FOR_REVIEWER: no trusted exact-head cycle published"))
    assert "independent review pending (WAITING_FOR_REVIEWER)" in description
    assert "completed" not in description


def test_review_disposition_wording_does_not_change_readiness_predicates():
    runs = tuple(_green_check(name, index) for index, name in enumerate(core.REQUIRED_CHECKS, start=1))
    states = {
        core.evaluate(
            core.StaticReadinessObservation(
                check_runs=runs, review_authority=authority, knowledge_receipt=("success", "")
            )
        ).state
        for authority in (
            ("success", "VALID_AGENT_REVIEW: x"),
            ("success", "POOL_EXHAUSTED: x"),
            ("pending", "WAITING_FOR_REVIEWER: x"),
            ("pending", "MISSING_REVIEW_AUTHORITY: x"),
            ("failure", "MALFORMED_REVIEW: x"),
        )
    }
    assert states == {"success"}


def test_merge_readiness_reconciles_both_exact_head_preflight_completions(monkeypatch):
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/hunter-merge-readiness.yml").read_text()
    )
    triggers = workflow.get("on", workflow.get(True))
    completed = triggers["workflow_run"]["workflows"]
    assert "Hunter / Pre-PR Preflight" in completed
    assert "Hunter / Trusted Preflight Upgrade" in completed
    assert "Hunter / Issue Agent Knowledge" in completed
    monkeypatch.setattr(
        core, "event_payload", lambda: {"workflow_run": {"name": "Hunter / Issue Agent Knowledge", "pull_requests": []}}
    )
    monkeypatch.setattr(core, "open_pull_requests", lambda: (746,))
    assert core.candidate_prs() == (746,)
    assert "|| 'sweep'" in workflow["concurrency"]["group"]
    for name in ("Hunter / Pre-PR Preflight", "Hunter / Trusted Preflight Upgrade"):
        monkeypatch.setattr(
            core,
            "event_payload",
            lambda name=name: {"workflow_run": {"name": name, "head_sha": "e" * 40, "pull_requests": []}},
        )
        monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (582,))
        assert core.candidate_prs() == (582,)


@pytest.mark.parametrize("payload", [None, [], "invalid", {}, {"check_runs": "bad"}, {"check_runs": [None]}])
def test_malformed_check_run_response_fails_closed(monkeypatch, payload):
    monkeypatch.setattr(core, "request_json", lambda *_args, **_kwargs: payload)
    with pytest.raises(ValueError, match="Malformed GitHub check"):
        core.all_check_runs("a" * 40)


def test_controller_replaces_stale_success_after_malformed_evidence(monkeypatch):
    import sys

    head = "a" * 40
    monkeypatch.setattr(core, "candidate_prs", lambda: (605,))
    monkeypatch.setattr(core, "decide", lambda _pr: (_ for _ in ()).throw(ValueError("malformed check runs")))
    monkeypatch.setattr(core, "request_json", lambda *_args, **_kwargs: {"state": "open", "head": {"sha": head}})
    published = []
    monkeypatch.setattr(core, "publish", lambda sha, decision: published.append((sha, decision)))
    monkeypatch.setattr(sys, "argv", ["hunter_merge_readiness_v2.py"])
    assert core.main() == 1
    assert len(published) == 1
    assert published[0][0] == head
    assert published[0][1].state == "pending"


def test_unchanged_readiness_status_does_not_exhaust_github_quota(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {
            "statuses": [{"context": core.CONTEXT, "state": "pending", "description": "Waiting for Ready for Review"}]
        }

    monkeypatch.setattr(core, "request_json", fake_request)
    core.publish("a" * 40, core.Decision("pending", "Waiting for Ready for Review"))
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/status")


def test_changed_readiness_status_must_publish_even_after_previous_success(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"statuses": [{"context": core.CONTEXT, "state": "success", "description": "Ready"}]}

    monkeypatch.setattr(core, "request_json", fake_request)
    core.publish("a" * 40, core.Decision("failure", "New blocking finding"))
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2]["state"] == "failure"


def test_malformed_combined_status_response_fails_closed(monkeypatch):
    monkeypatch.setattr(core, "request_json", lambda *_args: {"statuses": None})
    import pytest

    with pytest.raises(ValueError, match="Malformed combined"):
        core.publish("a" * 40, core.Decision("success", "Ready"))
