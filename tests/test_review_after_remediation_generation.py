"""Review-after-remediation generations (Issue #461, confirmed on PR #473).

An exact-head cycle whose reviewers were all exhausted used to be terminal.
Remediating the blocking findings that cycle produced could never start another
review of the same immutable head, so the candidate stayed permanently
``MISSING_REVIEW_AUTHORITY``: the collector run for that head had already
completed, and every reviewer invocation key was already spent.

These cases pin the bounded transition that repairs it -- one more generation,
derived from trusted GitHub review-thread state, never from the candidate.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys

import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import hunter_reviewer_collector as collector
import pytest

HEAD = "a" * 40
CLAIMS = "d" * 64
GENERATION = "0123456789abcdef"
OTHER_GENERATION = "fedcba9876543210"


def make_cycle(**overrides):
    values = {
        "pr_number": 472,
        "head_sha": HEAD,
        "state": "WAITING_FOR_REVIEWER",
        "provider_id": "",
        "trigger_id": 123,
        "started_at": "2020-01-01T00:00:00Z",
        "config_digest": "d" * 64,
        "generation_id": orchestrator.BASE_GENERATION_ID,
    }
    values.update(overrides)
    return orchestrator.ReviewCycle(**values)


def _run(status, conclusion=None, generation_id=orchestrator.BASE_GENERATION_ID, pr_number=472, head_sha=HEAD):
    return {
        "id": 1,
        "display_title": orchestrator.collector_run_name(pr_number, head_sha, generation_id),
        "path": orchestrator.COLLECTOR_WORKFLOW_PATH,
        "status": status,
        "conclusion": conclusion,
    }


class FakeOrchestration:
    """A trusted-state double that records what the orchestrator actually did.

    It advances the same way GitHub does: publishing a cycle replaces the status
    for the head, and a dispatch makes a live collector run for that exact
    generation appear in the run listing. Re-entering ``ensure_collector`` then
    sees the world its own previous pass created, which is what makes the
    idempotency assertions meaningful rather than assumed.
    """

    def __init__(self, cycle, runs=()):
        self.cycle = cycle
        self.runs = list(runs)
        self.dispatches: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "reviewer_pool_config_digest", lambda: "d" * 64)
        monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999)
        monkeypatch.setattr(orchestrator, "read_cycle", self._read_cycle)
        monkeypatch.setattr(orchestrator, "publish_cycle", self._publish_cycle)
        monkeypatch.setattr(orchestrator, "dispatch_collector", self._dispatch)
        monkeypatch.setattr(orchestrator, "request_json", self._request_json)
        return self

    def _read_cycle(self, *_args):
        return ("present", self.cycle, None) if self.cycle is not None else ("absent", None, None)

    def _publish_cycle(self, *_args, cycle):
        self.cycle = dataclasses.replace(cycle, started_at="2020-01-01T00:00:00Z")

    def _dispatch(self, _repository, _token, pr_number, head_sha, generation_id=orchestrator.BASE_GENERATION_ID):
        self.dispatches.append(generation_id)
        self.runs.append(_run("in_progress", generation_id=generation_id, pr_number=pr_number, head_sha=head_sha))

    def _request_json(self, _repository, _token, _method, path, _payload=None):
        assert path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs")
        return {"workflow_runs": self.runs}


def _thread(thread_id, comment_id, resolved=True, created_at="2026-09-18T01:35:24Z"):
    return orchestrator.BlockingThread(thread_id, comment_id, created_at, resolved)


# --- the transition itself ------------------------------------------------


def test_exhausted_cycle_redispatches_exactly_once_after_blockers_are_resolved(monkeypatch):
    """PR #473 verbatim: a completed collector, then remediation, then one review."""

    fake = FakeOrchestration(make_cycle(), [_run("completed", "success")]).install(monkeypatch)

    first = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)
    second = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)

    assert fake.dispatches == [GENERATION]
    assert first.generation_id == GENERATION
    assert second.generation_id == GENERATION
    assert second.state == "WAITING_FOR_REVIEWER"


def test_reconciling_the_same_generation_again_never_redispatches(monkeypatch):
    """The generation, not the pass count, is what spends a reviewer invocation."""

    fake = FakeOrchestration(
        make_cycle(generation_id=GENERATION), [_run("completed", "success", generation_id=GENERATION)]
    ).install(monkeypatch)

    for _ in range(4):
        orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)

    assert fake.dispatches == []


def test_no_material_transition_means_no_redispatch(monkeypatch):
    """An unchanged generation is the ordinary exhausted cycle: still pending."""

    fake = FakeOrchestration(make_cycle(), [_run("completed", "success")]).install(monkeypatch)

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, orchestrator.BASE_GENERATION_ID)

    assert fake.dispatches == []
    assert result.generation_id == orchestrator.BASE_GENERATION_ID


def test_a_live_prior_generation_is_never_overlapped(monkeypatch):
    """Two reviewers must not be run against one immutable head at the same time."""

    fake = FakeOrchestration(make_cycle(), [_run("in_progress")]).install(monkeypatch)

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)

    assert fake.dispatches == []


def test_an_already_dispatched_generation_is_not_dispatched_twice(monkeypatch):
    """A cycle status that failed to advance is not a second dispatch permit."""

    fake = FakeOrchestration(
        make_cycle(), [_run("completed", "success"), _run("completed", "failure", generation_id=GENERATION)]
    ).install(monkeypatch)

    orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)

    assert fake.dispatches == []


def test_remediation_stays_inside_a_bounded_generation_budget(monkeypatch):
    spent = [orchestrator.BASE_GENERATION_ID] + [
        f"{index:016x}" for index in range(orchestrator.MAX_REMEDIATION_GENERATIONS - 1)
    ]
    fake = FakeOrchestration(
        make_cycle(generation_id=spent[-1]),
        [_run("completed", "success", generation_id=item) for item in spent],
    ).install(monkeypatch)

    result = orchestrator.ensure_collector("owner/repo", "token", 472, HEAD, GENERATION)

    assert len(spent) == orchestrator.MAX_REMEDIATION_GENERATIONS
    assert fake.dispatches == []
    assert result.generation_id == spent[-1]


def test_a_changed_head_starts_an_ordinary_new_exact_head_cycle(monkeypatch):
    """A new head is a new candidate: its own cycle, dispatched once, no budget carried."""

    new_head = "b" * 40
    fake = FakeOrchestration(
        None, [_run("completed", "success", generation_id=item) for item in ("", "1" * 16, "2" * 16)]
    ).install(monkeypatch)

    first = orchestrator.ensure_collector("owner/repo", "token", 472, new_head, GENERATION)
    second = orchestrator.ensure_collector("owner/repo", "token", 472, new_head, GENERATION)

    assert fake.dispatches == [GENERATION]
    assert first.head_sha == new_head
    assert second.trigger_id == 999


def test_unreadable_thread_or_run_evidence_never_authorises_a_generation(monkeypatch):
    cycle = make_cycle()

    def unavailable(*_args, **_kwargs):
        raise transport.GitHubRequestError("rate limited", category="transient", status_code=429)

    monkeypatch.setattr(orchestrator, "request_json", unavailable)

    assert orchestrator.remediation_generation_admissible("owner/repo", "token", cycle, GENERATION) is False


# --- the identity the transition is derived from --------------------------


def test_generation_identity_binds_head_claims_and_the_resolved_thread_set():
    threads = (_thread("PRRT_1", 4042982311), _thread("PRRT_2", 4042982318))
    identity = orchestrator.remediation_generation_id(HEAD, CLAIMS, threads)

    assert orchestrator.GENERATION_ID_PATTERN.fullmatch(identity)
    # Order of the same evidence is not new evidence.
    assert orchestrator.remediation_generation_id(HEAD, CLAIMS, tuple(reversed(threads))) == identity
    # Every binding actually changes the identity.
    assert orchestrator.remediation_generation_id("b" * 40, CLAIMS, threads) != identity
    assert orchestrator.remediation_generation_id(HEAD, "e" * 64, threads) != identity
    assert orchestrator.remediation_generation_id(HEAD, CLAIMS, threads[:1]) != identity
    assert orchestrator.remediation_generation_id(HEAD, CLAIMS, ()) != identity


def _graphql(monkeypatch, nodes, author="fafa33"):
    def request_graphql_json(**kwargs):
        assert kwargs["query"] == orchestrator._BLOCKING_THREADS_QUERY
        return {
            "repository": {
                "pullRequest": {
                    "author": {"login": author},
                    "reviewThreads": {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}},
                }
            }
        }

    monkeypatch.setattr(orchestrator.transport, "request_graphql_json", request_graphql_json)


def _node(thread_id, login, typename="Bot", resolved=True, comment_id=1):
    return {
        "id": thread_id,
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "databaseId": comment_id,
                    "createdAt": "2026-09-18T01:35:24Z",
                    "author": {"login": login, "__typename": typename},
                }
            ]
        },
    }


def test_only_authenticated_reviewer_apps_can_open_a_blocking_thread(monkeypatch):
    _graphql(
        monkeypatch,
        [
            _node("PRRT_reviewer", "chatgpt-codex-connector"),
            _node("PRRT_human", "some-maintainer", typename="User"),
            _node("PRRT_author", "fafa33", typename="User"),
            _node("PRRT_automation", "github-actions"),
        ],
    )

    threads = orchestrator.blocking_reviewer_threads("owner/repo", "token", 472)

    assert [thread.thread_id for thread in threads] == ["PRRT_reviewer"]


def test_candidate_authored_evidence_cannot_mint_a_remediation_generation(monkeypatch):
    """A candidate resolving threads it opened itself buys exactly nothing."""

    _graphql(
        monkeypatch,
        [
            _node("PRRT_self", "fafa33", typename="User"),
            _node("PRRT_self_bot", "fafa33", typename="Bot"),
            _node("PRRT_automation", "github-actions"),
        ],
    )

    assert (
        orchestrator.current_remediation_generation("owner/repo", "token", 472, HEAD, CLAIMS)
        == orchestrator.BASE_GENERATION_ID
    )


def test_an_unresolved_blocking_thread_does_not_advance_the_generation(monkeypatch):
    _graphql(monkeypatch, [_node("PRRT_open", "chatgpt-codex-connector", resolved=False)])

    assert (
        orchestrator.current_remediation_generation("owner/repo", "token", 472, HEAD, CLAIMS)
        == orchestrator.BASE_GENERATION_ID
    )


def test_resolving_an_authenticated_blocking_thread_advances_the_generation(monkeypatch):
    _graphql(
        monkeypatch,
        [
            _node("PRRT_fixed", "chatgpt-codex-connector", comment_id=4042982311),
            _node("PRRT_open", "chatgpt-codex-connector", resolved=False, comment_id=4042982400),
        ],
    )

    generation = orchestrator.current_remediation_generation("owner/repo", "token", 472, HEAD, CLAIMS)

    assert generation != orchestrator.BASE_GENERATION_ID
    assert generation == orchestrator.remediation_generation_id(HEAD, CLAIMS, (_thread("PRRT_fixed", 4042982311),))


@pytest.mark.parametrize(
    "nodes",
    [
        [{"id": "PRRT_1", "isResolved": True, "comments": {"nodes": []}}],
        [{"id": "PRRT_1", "isResolved": "yes", "comments": {"nodes": [{"databaseId": 1}]}}],
        [_node("PRRT_1", "chatgpt-codex-connector") | {"id": ""}],
        [
            {
                "id": "PRRT_1",
                "isResolved": True,
                "comments": {"nodes": [{"databaseId": 0, "createdAt": "x", "author": {"login": "a"}}]},
            }
        ],
    ],
)
def test_malformed_thread_evidence_fails_closed(monkeypatch, nodes):
    _graphql(monkeypatch, nodes)

    with pytest.raises(ValueError):
        orchestrator.blocking_reviewer_threads("owner/repo", "token", 472)


def test_spent_generations_are_counted_from_the_trusted_run_listing(monkeypatch):
    """Correlation is the rendered run name, so an unrelated run cannot pay the budget."""

    runs = [
        _run("completed", "success"),
        _run("completed", "failure", generation_id=GENERATION),
        _run("in_progress", generation_id=GENERATION),
        _run("completed", "success", pr_number=471, generation_id=OTHER_GENERATION),
        _run("completed", "success", head_sha="b" * 40, generation_id=OTHER_GENERATION),
        {**_run("completed", "success", generation_id=OTHER_GENERATION), "path": ".github/workflows/ci.yml"},
        {**_run("completed", "success"), "display_title": orchestrator.collector_run_name(472, HEAD) + " GEN nope"},
    ]
    monkeypatch.setattr(orchestrator, "request_json", lambda *_a, **_k: {"workflow_runs": runs})

    assert orchestrator.remediation_generations_used("owner/repo", "token", 472, HEAD) == {
        orchestrator.BASE_GENERATION_ID,
        GENERATION,
    }
    assert orchestrator.collector_liveness("owner/repo", "token", 472, HEAD, GENERATION) == ("active", 2)
    assert orchestrator.collector_liveness("owner/repo", "token", 472, HEAD) == ("completed", 1)


def test_a_cycle_status_round_trips_its_generation(monkeypatch):
    published = []
    monkeypatch.setattr(orchestrator, "current_run_id", lambda: 999)
    monkeypatch.setattr(
        orchestrator,
        "request_json",
        lambda _repository, _token, _method, _path, payload=None: published.append(payload),
    )
    orchestrator.publish_cycle("owner/repo", "token", HEAD, cycle=make_cycle(generation_id=GENERATION))
    description = published[0]["description"]

    status = {
        "creator": {"login": orchestrator.TRUSTED_STATUS_CREATOR},
        "context": "Hunter Review Orchestration / PR #472",
        "description": description,
        "created_at": "2026-09-18T01:35:24Z",
    }
    assert len(description) <= 140
    assert orchestrator._parse_cycle(status, 472, HEAD).generation_id == GENERATION
    # A status written before remediation generations existed is the first cycle.
    legacy = {**status, "description": description.rsplit("|", 1)[0]}
    assert orchestrator._parse_cycle(legacy, 472, HEAD).generation_id == orchestrator.BASE_GENERATION_ID
    # A generation that is not a canonical identity is not a cycle at all.
    forged = {**status, "description": description.rsplit("|", 1)[0] + "|not-a-generation"}
    assert orchestrator._parse_cycle(forged, 472, HEAD) is None


# --- one reviewer invocation per reviewer per generation ------------------


def test_one_invocation_identity_per_reviewer_per_generation():
    base = collector.invocation_key(HEAD, CLAIMS, "codex", 1)
    same = collector.invocation_key(HEAD, CLAIMS, "codex", 1, orchestrator.BASE_GENERATION_ID)
    remediation = collector.invocation_key(HEAD, CLAIMS, "codex", 1, GENERATION)

    # Unchanged generation: the collector adopts its own earlier invocation.
    assert base == same
    assert collector.invocation_key(HEAD, CLAIMS, "codex", 1, GENERATION) == remediation
    # New generation: the same reviewer is owed exactly one new invocation.
    assert remediation != base
    assert collector.invocation_key(HEAD, CLAIMS, "codex", 1, OTHER_GENERATION) != remediation


def test_native_trigger_carries_and_verifies_its_generation():
    agent = {
        "id": "codex",
        "trigger_method": "github-pr-comment:@codex review",
        "github_login": "chatgpt-codex-connector[bot]",
    }
    body = collector.trigger_body(HEAD, CLAIMS, agent, 123, 1, 1, GENERATION)
    parsed = collector.parse_native_trigger(body)

    assert parsed is not None
    assert parsed["remediation_generation_id"] == GENERATION
    # The base generation stays byte-identical to the pre-remediation trigger.
    assert "Remediation generation" not in collector.trigger_body(HEAD, CLAIMS, agent, 123, 1, 1)
    # A generation swapped into an otherwise valid body does not verify.
    assert collector.parse_native_trigger(body.replace(GENERATION, OTHER_GENERATION)) is None


def test_api_trigger_carries_and_verifies_its_generation():
    agent = {"id": "gemini", "trigger_method": "api:gemini"}
    body = collector.api_trigger_body(HEAD, CLAIMS, agent, 123, 1, 1, GENERATION)
    parsed = collector.parse_api_trigger(body)

    assert parsed is not None
    assert parsed["remediation_generation_id"] == GENERATION
    assert "remediation_generation_id" not in collector.api_trigger_payload(HEAD, CLAIMS, agent, 123, 1, 1)
    assert collector.parse_api_trigger(body.replace(GENERATION, OTHER_GENERATION)) is None


def test_a_base_generation_may_not_be_spelled_as_an_explicit_field():
    """One generation, one encoding: two spellings would be two identities."""

    agent = {"id": "gemini", "trigger_method": "api:gemini"}
    body = collector.api_trigger_body(HEAD, CLAIMS, agent, 123, 1, 1)
    forged = body.replace('"reviewer_agent"', '"remediation_generation_id": "", "reviewer_agent"', 1)

    assert collector.parse_api_trigger(forged) is None


# --- the collector re-derives what it was told ----------------------------


def _collector_main(monkeypatch, tmp_path, dispatched, derived):
    document = {"claims": {}, "review_request": {"schema": "hunter.review-request.v1", "claims_id": CLAIMS}}
    monkeypatch.setattr(collector.review, "load_reviewer_pool", lambda *_a, **_k: ({"agents": []}, ""))
    monkeypatch.setattr(collector.review, "review_id", lambda _claims: CLAIMS)
    monkeypatch.setattr(collector.governance, "read_head_pre_ready_review", lambda *_a: ("present", document, None))
    monkeypatch.setattr(collector.orchestration, "current_remediation_generation", lambda *_a: derived)
    monkeypatch.setattr(collector, "collect_attempts", lambda *_a: [])
    monkeypatch.setattr(collector.GitHubBackend, "head", lambda _self: HEAD)
    monkeypatch.setattr(collector, "configuration_digest", lambda _pool: "c" * 64)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    output = tmp_path / "reviewer-results.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hunter_reviewer_collector.py",
            "--pr",
            "472",
            "--head",
            HEAD,
            "--generation",
            dispatched,
            "--output",
            str(output),
        ],
    )
    return output


def test_collector_records_the_generation_it_re_derived(monkeypatch, tmp_path):
    import json

    output = _collector_main(monkeypatch, tmp_path, GENERATION, GENERATION)

    assert collector.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["remediation_generation_id"] == GENERATION


def test_a_dispatched_generation_trusted_state_does_not_derive_fails_closed(monkeypatch, tmp_path):
    """The dispatch input correlates a run; it never authorises one."""

    _collector_main(monkeypatch, tmp_path, GENERATION, orchestrator.BASE_GENERATION_ID)

    with pytest.raises(ValueError, match="trusted review-thread state derives"):
        collector.main()


def test_a_malformed_generation_input_is_refused_before_any_reviewer_is_invoked(monkeypatch, tmp_path):
    _collector_main(monkeypatch, tmp_path, "not-a-generation", GENERATION)

    with pytest.raises(ValueError, match="malformed"):
        collector.main()


# --- the guard that keeps this repaired -----------------------------------


@pytest.fixture
def guard():
    import hunter_defect_prevention_preflight as preflight

    return preflight


def test_the_guard_passes_on_the_repaired_repository(guard):
    assert guard.validate_review_after_remediation_boundary() == []


@pytest.mark.parametrize(
    ("failure", "regress"),
    [
        (
            "reviewer invocation identity must separate",
            lambda m: m.setattr(
                collector, "invocation_key", lambda head, claims, agent, number, generation="": f"{head}{number}"
            ),
        ),
        (
            "collector run name must correlate",
            lambda m: m.setattr(
                orchestrator, "collector_run_name", lambda pr, head, generation="": f"PR {pr} HEAD {head}"
            ),
        ),
        (
            "trigger adoption must separate",
            lambda m: m.setattr(
                collector.GitHubBackend,
                "invocation_marker",
                lambda self, agent, number: f"Invocation key: {self.expected_head}.",
            ),
        ),
        (
            "must carry the remediation generation",
            lambda m: m.setattr(
                collector,
                "trigger_body",
                lambda head, claims, agent, run_id, attempt, number, generation="": "@codex review",
            ),
        ),
        (
            "must bind the exact head, the claims digest and the resolved set",
            lambda m: m.setattr(orchestrator, "remediation_generation_id", lambda head, claims, resolved: "f" * 16),
        ),
        (
            "must be bounded by a positive budget",
            lambda m: m.setattr(orchestrator, "MAX_REMEDIATION_GENERATIONS", 0),
        ),
        (
            "unchanged remediation generation must never authorise",
            lambda m: m.setattr(orchestrator, "remediation_generation_admissible", lambda *_a: True),
        ),
    ],
)
def test_the_guard_fails_when_the_generation_binding_is_removed(guard, monkeypatch, failure, regress):
    """Paired negative fixtures: each is a way the repair could be undone."""

    regress(monkeypatch)
    errors = guard.validate_review_after_remediation_boundary()

    assert any(failure in message for message in errors), errors


def _tmp_root(tmp_path, orchestrator_source):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hunter_review_orchestrator.py").write_text(orchestrator_source, encoding="utf-8")
    return tmp_path


def test_the_guard_fails_when_the_workflow_stops_forwarding_the_generation(guard, monkeypatch, tmp_path):
    root = _tmp_root(tmp_path, pathlib.Path(orchestrator.__file__).read_text(encoding="utf-8"))
    workflow = root / "hunter-reviewer-collector.yml"
    workflow.write_text(
        "name: Hunter Reviewer Collector\n"
        "on:\n  workflow_dispatch:\n    inputs:\n      pr_number:\n        type: string\n"
        "jobs:\n  collect:\n    steps:\n      - run: python scripts/hunter_reviewer_collector.py --pr 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "ROOT", root)
    monkeypatch.setattr(guard, "REVIEWER_COLLECTOR_WORKFLOW", workflow.name)

    errors = guard.validate_review_after_remediation_boundary()

    assert any("must accept the remediation generation" in message for message in errors)
    assert any("must forward the remediation generation" in message for message in errors)


def test_the_guard_fails_when_the_orchestrator_stops_deriving_the_generation(guard, monkeypatch, tmp_path):
    root = _tmp_root(
        tmp_path,
        "def ensure_current(repository, token, pr_number):\n"
        "    return ensure_collector(repository, token, pr_number, 'head')\n",
    )
    monkeypatch.setattr(guard, "ROOT", root)

    errors = guard.validate_review_after_remediation_boundary()

    assert any("must derive the remediation generation" in message for message in errors)
