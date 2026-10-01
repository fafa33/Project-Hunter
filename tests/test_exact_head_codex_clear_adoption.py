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
    *,
    body: str,
    commit_id: str = HEAD,
    login: str = CODEX,
    state: str = "COMMENTED",
    submitted_at: str = REVIEW_TIME,
    review_id: int = 8,
) -> dict:
    return {
        "id": review_id,
        "user": {"login": login},
        "state": state,
        "body": body,
        "commit_id": commit_id,
        "submitted_at": submitted_at,
        "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-8",
        "trigger_claims_id": CLAIMS,
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


def test_governance_rejects_a_clear_when_no_trusted_trigger_exists_for_the_head(monkeypatch):
    """Exact-head identity alone does not replace the current claims provenance binding."""

    observations, error = _read_observations(monkeypatch, [_review(body=_clear_body())], trigger_created_at=None)

    assert error is None
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


def test_the_orchestrator_detects_an_exact_head_codex_clear(monkeypatch):
    rows = [_review(body=_clear_body())]
    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: rows)

    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is True
    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, NEW_HEAD, CLAIMS) is False


def test_the_orchestrator_does_not_count_untrusted_mismatched_or_findings_reviews(monkeypatch):
    for row in (
        _review(body=_clear_body(), login="alternate[bot]"),
        _review(body=_clear_body(), commit_id=NEW_HEAD),
        _review(body=FINDINGS_BODY),
        _review(body=_clear_body(), state="CHANGES_REQUESTED"),
    ):
        monkeypatch.setattr(orchestrator, "request_json", lambda *_a, row=row: [row])
        assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False


def test_unreadable_review_evidence_adopts_nothing(monkeypatch):
    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: {"unexpected": "shape"})

    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False


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

    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: [_review(body=HIDDEN_FINDING_BODY)])
    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False

    backend = _collector_backend(monkeypatch, [_review(body=HIDDEN_FINDING_BODY)])
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) != "clear"


def test_a_standard_codex_clear_with_collapsed_about_section_is_adopted(monkeypatch):
    """The ordinary Codex footer (a collapsed, finding-free section) still adopts."""

    body = (
        _clear_body()
        + "\n\n<details><summary>ℹ️ About Codex in GitHub</summary>\nGitHub integration details\n</details>"
    )
    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: [_review(body=body)])

    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is True
    backend = _collector_backend(monkeypatch, [_review(body=body)])
    assert backend.response_state(_codex_agent(), {"id": 7, "created_at": LATER_TRIGGER_TIME}) == "clear"


# --- Only the latest Codex review establishes clear authority ----------------------

OLD = "2026-09-30T09:30:00Z"
NEWER = "2026-09-30T10:30:00Z"


def _old_clear() -> dict:
    return _review(body=_clear_body(), submitted_at=OLD, review_id=1)


def _adopted_everywhere(monkeypatch, reviews: list) -> set[bool]:
    """The verdict of every consumer of the shared latest-review predicate."""

    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: reviews)
    backend = _collector_backend(monkeypatch, reviews)
    return {
        core.latest_codex_review_is_exact_head_clear(reviews, HEAD, CLAIMS),
        orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS),
        backend._exact_head_native_clear(),
    }


def test_old_clear_then_newer_clear_the_newest_valid_clear_governs(monkeypatch):
    reviews = [_old_clear(), _review(body=_clear_body(), submitted_at=NEWER, review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {True}


def test_old_clear_then_newer_finding_is_not_clear(monkeypatch):
    reviews = [_old_clear(), _review(body=FINDINGS_BODY, submitted_at=NEWER, review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {False}
    # Order of arrival in the API payload is irrelevant; submission order governs.
    assert _adopted_everywhere(monkeypatch, list(reversed(reviews))) == {False}


def test_old_clear_then_newer_changes_requested_is_not_clear(monkeypatch):
    reviews = [_old_clear(), _review(body="blocking", state="CHANGES_REQUESTED", submitted_at=NEWER, review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {False}


def test_old_clear_then_newer_dismissed_review_is_not_clear(monkeypatch):
    reviews = [_old_clear(), _review(body="", state="DISMISSED", submitted_at=NEWER, review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {False}


def test_old_clear_then_newer_review_of_another_commit_is_not_clear(monkeypatch):
    reviews = [_old_clear(), _review(body=_clear_body(NEW_HEAD), commit_id=NEW_HEAD, submitted_at=NEWER, review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {False}


def test_the_latest_clear_is_adopted_and_an_older_finding_does_not_block_it(monkeypatch):
    reviews = [_review(body=FINDINGS_BODY, submitted_at=OLD, review_id=1), _review(body=_clear_body(), review_id=2)]

    assert _adopted_everywhere(monkeypatch, reviews) == {True}


def test_shared_predicate_rejects_native_clear_without_current_claims_binding():
    review = _review(body=_clear_body())
    review.pop("trigger_claims_id")
    assert core.latest_codex_review_is_exact_head_clear([review], HEAD, CLAIMS) is False


def test_live_consumers_enrich_raw_exact_head_review_after_trusted_request(monkeypatch):
    review = _review(body=_clear_body())
    review.pop("trigger_claims_id")
    reviews = [review]
    monkeypatch.setattr(orchestrator, "request_json", lambda *_a: reviews)
    backend = _collector_backend(monkeypatch, reviews)
    assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is True
    assert backend._exact_head_native_clear() is True


def test_malformed_review_ids_fail_closed_everywhere(monkeypatch):
    for bad_id in (None, 0, "not-a-number", [], {}, True):
        reviews = [_review(body=_clear_body(), review_id=bad_id)]
        monkeypatch.setattr(orchestrator, "request_json", lambda *_a, r=reviews: r)
        assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False
        try:
            core.latest_codex_review_is_exact_head_clear(reviews, HEAD, CLAIMS)
        except ValueError:
            pass
        else:
            raise AssertionError(f"shared predicate accepted malformed review id {bad_id!r}")


def test_a_malformed_review_record_fails_closed_everywhere(monkeypatch):
    """Governance rejects the whole collection, so no consumer may adopt from it."""

    for bad in ("not-an-object", {"id": 9, "state": "COMMENTED"}, {"id": 9, "user": "x"}, {"id": 9, "user": None}):
        reviews = [_review(body=_clear_body()), bad]
        _, error = _read_observations_error(monkeypatch, reviews)
        assert error == "malformed review record", bad
        monkeypatch.setattr(orchestrator, "request_json", lambda *_a, r=reviews: r)
        assert orchestrator.exact_head_codex_clear_exists("owner/repo", "token", 544, HEAD, CLAIMS) is False
        try:
            core.latest_codex_review_is_exact_head_clear(reviews, HEAD, CLAIMS)
        except ValueError:
            pass
        else:
            raise AssertionError(f"shared predicate accepted malformed record {bad!r}")
        backend = _collector_backend(monkeypatch, reviews)
        try:
            backend._exact_head_native_clear()
        except ValueError:
            pass
        else:
            raise AssertionError(f"collector adopted from malformed collection {bad!r}")


def _read_observations_error(monkeypatch, reviews: list):
    monkeypatch.setattr(
        core, "request_json", lambda _r, _t, _m, path, *_a: reviews if path.startswith("pulls/") else []
    )
    return core.read_pr_pool_review_comments("owner/repo", "token", 544, _pool(), HEAD)


def test_exact_head_mismatch_and_hidden_findings_are_not_adopted_by_the_shared_predicate(monkeypatch):
    assert _adopted_everywhere(monkeypatch, [_review(body=_clear_body(), commit_id=NEW_HEAD)]) == {False}
    assert _adopted_everywhere(monkeypatch, [_review(body=HIDDEN_FINDING_BODY)]) == {False}
    assert _adopted_everywhere(monkeypatch, [_review(body=_clear_body())]) == {True}
