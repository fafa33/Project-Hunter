"""An authenticated Codex clear of the exact HEAD survives trigger ordering.

Hunter used to discard a Codex review of the current exact HEAD whenever it was
submitted before a later (for example remediation-generation) bot trigger, so an
already-valid review kept the PR in WAITING_FOR_REVIEWER while redundant
``@codex review`` comments were posted for unchanged content.

Adoption is bound to the commit the review names, never to when it was written:

* the reviewer is the trusted Codex identity;
* the review's ``commit_id`` and its named reviewed commit equal the exact HEAD;
* only a clear outcome is adopted regardless of ordering. A review carrying
  findings still needs a response after the trigger, because remediation owes a
  fresh review, and unresolved findings stay blocking in governance;
* a new HEAD has a different ``commit_id``, so a review of the old HEAD adopts
  nothing for it.
"""

from __future__ import annotations

import hunter_governance_review_v2 as core
import hunter_pre_ready_review as pre_ready
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector

HEAD = "a" * 40
NEW_HEAD = "b" * 40
CLAIMS = "c" * 64
CODEX = "chatgpt-codex-connector[bot]"
REVIEW_TIME = "2026-09-30T10:00:00Z"
LATER_TRIGGER_TIME = "2026-09-30T11:00:00Z"
EARLIER_TRIGGER_TIME = "2026-09-30T09:00:00Z"

FINDINGS_BODY = (
    "\n### 💡 Codex Review\n\nHere are some automated review suggestions for this pull request.\n\n"
    f"**Reviewed commit:** `{HEAD[:10]}`\n"
)


def _clear_body(head: str = HEAD) -> str:
    return f"Codex Review: Didn't find any major issues. Nice work!\n\n**Reviewed commit:** `{head[:10]}`"


def _review(
    *, body: str, commit_id: str = HEAD, login: str = CODEX, state: str = "COMMENTED", submitted_at: str = REVIEW_TIME
) -> dict:
    return {
        "id": 8,
        "user": {"login": login},
        "state": state,
        "body": body,
        "commit_id": commit_id,
        "submitted_at": submitted_at,
        "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-8",
    }


def _codex_agent() -> dict:
    pool, error = pre_ready.load_reviewer_pool()
    assert not error and pool is not None
    return next(agent for agent in pool["agents"] if agent["id"] == "codex")


def _collector_backend(monkeypatch, reviews: list[dict], *, head: str = HEAD) -> collector.GitHubBackend:
    backend = collector.GitHubBackend("owner/repo", "token", 544, head, CLAIMS, 123, 1)
    monkeypatch.setattr(
        collector, "_pages", lambda _repo, _token, path, *_a, **_k: reviews if path.endswith("/reviews") else []
    )
    return backend


# --- Collector: the live response state -----------------------------------------


def test_a_codex_clear_of_the_exact_head_survives_a_later_bot_trigger(monkeypatch):
    """A: the review predates the trigger and is still the review of this HEAD."""

    backend = _collector_backend(monkeypatch, [_review(body=_clear_body())])
    trigger = {"id": 7, "created_at": LATER_TRIGGER_TIME}

    assert backend.response_state(_codex_agent(), trigger) == "clear"


def test_a_clear_after_the_trigger_is_still_clear(monkeypatch):
    backend = _collector_backend(monkeypatch, [_review(body=_clear_body())])
    trigger = {"id": 7, "created_at": EARLIER_TRIGGER_TIME}

    assert backend.response_state(_codex_agent(), trigger) == "clear"


def test_b_head_mutation_invalidates_the_old_exact_head_clear(monkeypatch):
    """B: the same review authorizes nothing once HEAD has moved."""

    backend = _collector_backend(monkeypatch, [_review(body=_clear_body())], head=NEW_HEAD)
    trigger = {"id": 7, "created_at": LATER_TRIGGER_TIME}

    assert backend.response_state(_codex_agent(), trigger) == "waiting"


def test_c_an_untrusted_reviewer_clear_is_not_adopted(monkeypatch):
    """C: only the trusted Codex identity can supply the clear."""

    for login in ("alternate[bot]", "fafa33", "github-actions[bot]", CODEX + "-lookalike"):
        backend = _collector_backend(monkeypatch, [_review(body=_clear_body(), login=login)])
        assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "waiting", login


def test_c_a_mismatched_commit_is_not_adopted(monkeypatch):
    """C: neither the review's commit_id nor its named commit may differ from HEAD."""

    wrong_commit_id = _collector_backend(monkeypatch, [_review(body=_clear_body(), commit_id=NEW_HEAD)])
    assert wrong_commit_id.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "waiting"

    wrong_named_commit = _collector_backend(monkeypatch, [_review(body=_clear_body(NEW_HEAD))])
    assert wrong_named_commit.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_c_a_clear_that_only_reads_like_one_is_not_adopted(monkeypatch):
    body = _clear_body() + "\n\nBlocking finding: unsafe bypass"
    backend = _collector_backend(monkeypatch, [_review(body=body)])

    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_d_a_review_with_findings_still_needs_a_response_after_the_trigger(monkeypatch):
    """D: ordering is waived for a clear only; findings keep their fresh-review requirement."""

    backend = _collector_backend(monkeypatch, [_review(body=FINDINGS_BODY)])

    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "waiting"
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": EARLIER_TRIGGER_TIME}) == "blocking"


def test_a_changes_requested_review_is_never_adopted_as_a_clear(monkeypatch):
    backend = _collector_backend(monkeypatch, [_review(body=_clear_body(), state="CHANGES_REQUESTED")])

    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "waiting"


# --- Governance: adoption of the review for the committed request -----------------


def _pool() -> dict:
    pool, error = pre_ready.load_reviewer_pool()
    assert not error and pool is not None
    return pool


def _read_observations(monkeypatch, reviews: list[dict], *, trigger_created_at: str | None, head: str = HEAD):
    comments: list[dict] = []
    if trigger_created_at is not None:
        comments.append(
            {
                "id": 456,
                "user": {"login": "github-actions[bot]"},
                "body": collector.trigger_body(head, CLAIMS, _codex_agent(), 123, 1, 1),
                "created_at": trigger_created_at,
                "html_url": "trigger",
            }
        )

    def request(_repository, _token, _method, path, *_args):
        if path.startswith("pulls/"):
            return reviews
        if path.startswith("issues/"):
            return comments
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", request)
    monkeypatch.setattr(core, "trusted_collector_run", lambda *_a, **_k: True)
    return core.read_pr_pool_review_comments("owner/repo", "token", 544, _pool(), head)


def test_a_governance_adopts_a_clear_that_predates_a_later_trigger(monkeypatch):
    observations, error = _read_observations(
        monkeypatch, [_review(body=_clear_body())], trigger_created_at=LATER_TRIGGER_TIME
    )

    assert error is None
    assert len(observations) == 1
    assert observations[0]["trigger_claims_id"] == CLAIMS
    adopted = core.review_adoption_acknowledgement(observations[0], HEAD, CLAIMS)
    assert adopted is not None
    assert adopted["head_sha"] == HEAD and adopted["claims_id"] == CLAIMS and adopted["verdict"] == "clear"


def test_governance_still_requires_a_trusted_trigger_bound_to_the_claims(monkeypatch):
    """A Codex clear with no trusted trigger for the head stays fail-closed.

    Ordering is waived, the trigger's existence is not: an out-of-band clear is
    not authority until a trusted trigger binds it to these claims.
    """

    observations, error = _read_observations(monkeypatch, [_review(body=_clear_body())], trigger_created_at=None)

    assert error is None
    assert "trigger_claims_id" not in observations[0]
    assert core.review_adoption_acknowledgement(observations[0], HEAD, CLAIMS) is None


def test_a_governance_still_rejects_a_clear_bound_to_other_claims(monkeypatch):
    observations, _ = _read_observations(
        monkeypatch, [_review(body=_clear_body())], trigger_created_at=LATER_TRIGGER_TIME
    )

    assert core.review_adoption_acknowledgement(observations[0], HEAD, "d" * 64) is None


def test_b_governance_does_not_adopt_the_old_heads_clear_for_a_new_head(monkeypatch):
    observations, _ = _read_observations(
        monkeypatch, [_review(body=_clear_body())], trigger_created_at=LATER_TRIGGER_TIME, head=NEW_HEAD
    )

    for observation in observations:
        assert core.review_adoption_acknowledgement(observation, NEW_HEAD, CLAIMS) is None


def test_c_governance_does_not_adopt_an_untrusted_reviewers_clear(monkeypatch):
    observations, error = _read_observations(
        monkeypatch, [_review(body=_clear_body(), login="alternate[bot]")], trigger_created_at=LATER_TRIGGER_TIME
    )

    assert error is None
    assert observations == []


def test_c_governance_does_not_adopt_a_clear_with_a_mismatched_commit(monkeypatch):
    observations, _ = _read_observations(
        monkeypatch, [_review(body=_clear_body(), commit_id=NEW_HEAD)], trigger_created_at=LATER_TRIGGER_TIME
    )

    for observation in observations:
        assert core.review_adoption_acknowledgement(observation, HEAD, CLAIMS) is None


def test_d_governance_never_adopts_a_review_that_carries_findings(monkeypatch):
    observations, _ = _read_observations(
        monkeypatch, [_review(body=FINDINGS_BODY)], trigger_created_at=LATER_TRIGGER_TIME
    )

    for observation in observations:
        assert core.review_adoption_acknowledgement(observation, HEAD, CLAIMS) is None


# --- Orchestrator: no redundant bot request for unchanged content ------------------


def _orchestrator_adopts(monkeypatch, reviews, *, head: str = HEAD, trigger: bool = True) -> bool:
    """Run the orchestrator's skip decision over governance's own readers."""

    comments = []
    if trigger:
        comments.append(
            {
                "id": 456,
                "user": {"login": "github-actions[bot]"},
                "body": collector.trigger_body(head, CLAIMS, _codex_agent(), 123, 1, 1),
                "created_at": LATER_TRIGGER_TIME,
                "html_url": "trigger",
            }
        )

    def request(_repository, _token, _method, path, *_args):
        if path.startswith("pulls/"):
            return reviews
        if path.startswith("issues/"):
            return comments
        raise AssertionError(path)

    monkeypatch.setattr(core, "request_json", request)
    monkeypatch.setattr(core, "trusted_collector_run", lambda *_a, **_k: True)
    return orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, head, CLAIMS)


def test_the_orchestrator_detects_an_exact_head_codex_clear(monkeypatch):
    rows = [_review(body=_clear_body())]

    assert _orchestrator_adopts(monkeypatch, rows) is True
    assert _orchestrator_adopts(monkeypatch, rows, head=NEW_HEAD) is False


def test_the_orchestrator_does_not_count_untrusted_mismatched_or_findings_reviews(monkeypatch):
    for row in (
        _review(body=_clear_body(), login="alternate[bot]"),
        _review(body=_clear_body(), commit_id=NEW_HEAD),
        _review(body=FINDINGS_BODY),
        _review(body=_clear_body(), state="CHANGES_REQUESTED"),
    ):
        assert _orchestrator_adopts(monkeypatch, [row]) is False


def test_the_orchestrator_does_not_skip_dispatch_for_a_clear_without_a_trusted_trigger(monkeypatch):
    """Skipping would strand the head: governance refuses an unbound clear, and nothing would dispatch."""

    assert _orchestrator_adopts(monkeypatch, [_review(body=_clear_body())], trigger=False) is False


def test_unreadable_review_evidence_adopts_nothing(monkeypatch):
    def request(*_a):
        return {"unexpected": "shape"}

    monkeypatch.setattr(core, "request_json", request)

    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False


# --- The latest Codex review decides, exactly as governance selects it ------------

NEWER = "2026-09-30T10:30:00Z"


def _clear_review(rid: int, submitted_at: str, commit_id: str = HEAD) -> dict:
    return {**_review(body=_clear_body(commit_id), commit_id=commit_id, submitted_at=submitted_at), "id": rid}


def _findings_review(rid: int, submitted_at: str, commit_id: str = HEAD) -> dict:
    return {**_review(body=FINDINGS_BODY, commit_id=commit_id, submitted_at=submitted_at), "id": rid}


def test_an_older_clear_never_overrides_a_newer_review_with_findings(monkeypatch):
    """An older exact-head clear must not mask a newer findings review.

    Otherwise resolving the newer review's threads would leave the older clear as
    authority with no fresh review, which is the resolved-finding bypass.
    """

    rows = [_clear_review(8, REVIEW_TIME), _findings_review(9, NEWER)]

    assert _orchestrator_adopts(monkeypatch, rows) is False
    backend = _collector_backend(monkeypatch, rows)
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_an_older_clear_never_overrides_a_newer_review_of_another_commit(monkeypatch):
    rows = [_clear_review(8, REVIEW_TIME), _findings_review(9, NEWER, commit_id=NEW_HEAD)]

    assert _orchestrator_adopts(monkeypatch, rows) is False
    backend = _collector_backend(monkeypatch, rows)
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_a_newer_clear_after_older_findings_is_the_review_of_record(monkeypatch):
    """Codex itself re-reviewed the unchanged HEAD and cleared it; that is its latest word."""

    rows = [_findings_review(8, REVIEW_TIME), _clear_review(9, NEWER)]

    assert _orchestrator_adopts(monkeypatch, rows) is True
    backend = _collector_backend(monkeypatch, rows)
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "clear"


# --- One predicate everywhere: a clear that hides findings is adopted nowhere ------

HIDDEN_FINDING_BODY = (
    _clear_body()
    + "\n\n<details><summary>Details</summary>\n\n- **P1** unsafe bypass in scripts/hunter_x.py:12\n</details>"
)


def test_a_clear_whose_trailing_content_hides_findings_is_adopted_nowhere(monkeypatch):
    """Dispatch is skipped only for a review governance will actually adopt.

    The orchestrator and collector use governance's own adoption predicate. A
    lenient clear match here would suppress the collector for a review that
    governance then refuses, stranding the head with no review opportunity.
    """

    observation = {
        "agent_id": "codex",
        "source_kind": "review",
        "state": "COMMENTED",
        "commit_id": HEAD,
        "body": HIDDEN_FINDING_BODY,
    }
    assert core.review_adoption_acknowledgement(observation, HEAD, CLAIMS) is None

    assert _orchestrator_adopts(monkeypatch, [_review(body=HIDDEN_FINDING_BODY)]) is False

    backend = _collector_backend(monkeypatch, [_review(body=HIDDEN_FINDING_BODY)])
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_a_standard_codex_clear_with_collapsed_about_section_is_adopted(monkeypatch):
    """The ordinary Codex footer (a collapsed, finding-free section) still adopts."""

    body = (
        _clear_body()
        + "\n\n<details><summary>ℹ️ About Codex in GitHub</summary>\nGitHub integration details\n</details>"
    )
    assert _orchestrator_adopts(monkeypatch, [_review(body=body)]) is True
    backend = _collector_backend(monkeypatch, [_review(body=body)])
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "clear"
