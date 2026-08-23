from __future__ import annotations

import hunter_merge_readiness_v2 as core


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


def _install_green(monkeypatch, pr: dict | None = None) -> None:
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
    monkeypatch.setattr(core, "open_prs_for_head", lambda _sha: (501,))


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


def test_failed_governance_status_blocks(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "failure"})

    _sha, decision = core.decide(501)

    assert decision.state == "failure"
    assert "Hunter Governance Review=failure" in decision.description


def test_stale_governance_pending_does_not_lock_resolved_mergeability(monkeypatch):
    _install_green(monkeypatch)
    monkeypatch.setattr(core, "latest_status", lambda _sha, _context: {"id": 99, "state": "pending"})

    _sha, decision = core.decide(501)

    assert decision.state == "success"


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
