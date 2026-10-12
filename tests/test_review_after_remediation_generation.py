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

import sys

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
        self.cycle = cycle

    def _dispatch(
        self, _repository, _token, pr_number, head_sha, generation_id=orchestrator.BASE_GENERATION_ID, _election=None
    ):
        self.dispatches.append(generation_id)
        self.runs.append(_run("in_progress", generation_id=generation_id, pr_number=pr_number, head_sha=head_sha))

    def _request_json(self, _repository, _token, _method, path, _payload=None):
        if path == "":
            return {"default_branch": "main"}
        if path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs") and (
            "event=workflow_dispatch" in path
        ):
            return {"workflow_runs": []}  # pre-migration collectors: none draining
        if path.startswith(f"actions/workflows/{orchestrator.COLLECTOR_WORKFLOW}/runs"):
            return {"workflow_runs": self.runs}
        if path.startswith("commits/") and "/statuses" in path:
            # _all_commit_statuses called by _find_dispatch_proof_status
            return []
        raise AssertionError(f"unexpected path: {path}")


def _thread(thread_id, comment_id, resolved=True, created_at="2026-09-18T01:35:24Z"):
    return orchestrator.BlockingThread(thread_id, comment_id, created_at, resolved)


# --- the transition itself ------------------------------------------------


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
    monkeypatch.setattr(collector, "verify_trusted_dispatch_before_work", lambda *a, **k: None)
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


# --- the guard that keeps this repaired -----------------------------------


@pytest.fixture
def guard():
    import hunter_defect_prevention_preflight as preflight

    return preflight


def test_the_guard_passes_on_the_repaired_repository(guard):
    assert guard.validate_review_after_remediation_boundary() == []


def _tmp_root(tmp_path, orchestrator_source):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hunter_review_orchestrator.py").write_text(orchestrator_source, encoding="utf-8")
    return tmp_path


_GEN = "${{ github.event.client_payload.generation_id }}"
_COLLECT = "python scripts/hunter_reviewer_collector.py --pr 1 --head abc"


_REAL = (
    'python scripts/hunter_reviewer_collector.py --pr "$PR_NUMBER" --head "$CANDIDATE_HEAD" '
    '--generation "$REMEDIATION_GENERATION" --output reviewer-results.json'
)
_REAL_ENV = {"PR_NUMBER": "x", "CANDIDATE_HEAD": "y", "REMEDIATION_GENERATION": _GEN}


_BOUND = f'{_COLLECT} --generation "$G"'
_UNBOUND = _COLLECT


# --- the parsed-workflow guard: trigger-key spellings and malformed documents ---------------------------------------


def _real_collector_definition():
    import yaml

    return yaml.safe_load((guard_module_root() / ".github/workflows/hunter-reviewer-collector.yml").read_text("utf-8"))


def guard_module_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


def test_the_real_collector_workflow_passes_the_definition_guard(guard):
    assert guard.validate_collector_workflow_definition(_real_collector_definition()) == []


def test_quoted_and_unquoted_on_keys_are_the_same_trigger(guard):
    definition = _real_collector_definition()
    assert True in definition  # PyYAML spells the unquoted key as a boolean
    quoted = {("on" if key is True else key): value for key, value in definition.items()}
    assert guard.validate_collector_workflow_definition(quoted) == []
    # Both spellings declaring the same trigger are one declaration; conflicting ones are ambiguous.
    both_same = {**definition, "on": definition[True]}
    assert guard.validate_collector_workflow_definition(both_same) == []
    both_conflicting = {**definition, "on": {"push": None}}
    assert guard.validate_collector_workflow_definition(both_conflicting)
    # An extra candidate-controlled event is still rejected under either spelling.
    for key in (True, "on"):
        widened = {k: v for k, v in definition.items() if k is not True}
        widened[key] = {**definition[True], "push": {"branches": ["**"]}}
        assert guard.validate_collector_workflow_definition(widened), key


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        "not a mapping",
        {"jobs": []},
        {"jobs": "x"},
        {True: {"repository_dispatch": {"types": ["hunter-reviewer-collect"]}}, "jobs": []},
        {True: {"repository_dispatch": {"types": ["hunter-reviewer-collect"]}}, "jobs": {"a": []}},
        {True: {"repository_dispatch": {"types": ["hunter-reviewer-collect"]}}, "jobs": {"a": {"steps": "run"}}},
        {True: {"repository_dispatch": {"types": ["hunter-reviewer-collect"]}}, "jobs": {"a": {"steps": [None, 3]}}},
    ],
)
def test_malformed_collector_workflows_produce_guard_errors_not_exceptions(guard, malformed):
    errors = guard.validate_collector_workflow_definition(malformed)
    assert errors and all(isinstance(message, str) for message in errors)


def test_a_non_mapping_step_env_is_treated_as_empty_not_a_crash(guard):
    definition = _real_collector_definition()
    for job in definition["jobs"].values():
        for step in job["steps"]:
            if "hunter_reviewer_collector.py" in str(step.get("run", "")):
                step["env"] = ["G=main"]
    assert guard.validate_collector_workflow_definition(definition)


def _collector_job(definition):
    return next(
        job
        for job in definition["jobs"].values()
        if any("hunter_reviewer_collector" in str(s.get("run")) for s in job["steps"])
    )


def test_an_indirect_collector_invocation_in_another_step_is_held_to_the_contract(guard):
    """Copilot: a second step reaching the script through an env variable was never checked."""
    import copy

    definition = _real_collector_definition()
    job = _collector_job(definition)
    job["steps"].append(
        {
            "name": "indirect",
            "env": {"SCRIPT": "scripts/hunter_reviewer_collector.py"},
            "run": 'python "$SCRIPT" --pr 1 --head abc --generation main',
        }
    )
    assert guard.validate_collector_workflow_definition(definition)
    # the same indirection as the only collector step cannot satisfy the guard either
    sole = copy.deepcopy(_real_collector_definition())
    sole_job = _collector_job(sole)
    for step in sole_job["steps"]:
        if "hunter_reviewer_collector" in str(step.get("run")):
            step["env"] = {**(step.get("env") or {}), "SCRIPT": "scripts/hunter_reviewer_collector.py"}
            step["run"] = (
                'python "$SCRIPT" --pr 1 --head abc --generation "${{ github.event.client_payload.generation_id }}"'
            )
    assert guard.validate_collector_workflow_definition(sole)
