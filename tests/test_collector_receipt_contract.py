"""Copilot rP8tr: a trusted completion must never be published for an unusable receipt.

One canonical contract (``collector_receipt_problem``) is applied by the collector
before writing its receipt, by the trusted completion path before any success is
written, by the exhausted-pool finalizer and by the governance exhaustion loader.
Rejected or temporarily unfinalizable evidence never removes a run from recovery.
"""

import json

import fake_github_ownership as fake_gh
import hunter_github_transport as transport
import hunter_review_orchestrator as orchestrator
import pytest
from test_collector_recovery_queue import head_for, stage, sweep

REPO = fake_gh.REPO
PR = 472
GEN = orchestrator.BASE_GENERATION_ID


@pytest.fixture
def env(monkeypatch):
    github = fake_gh.FakeGitHub()
    local = fake_gh.install(monkeypatch, github)
    return github, local


def whole(run, **overrides):
    receipt = {
        "schema": orchestrator.COLLECTOR_RESULTS_SCHEMA,
        "repository": REPO,
        "pr_number": PR,
        "head_sha": head_for(PR),
        "run_id": run,
        "run_attempt": 1,
        "claims_id": "c" * 64,
        "remediation_generation_id": GEN,
        "configuration_digest": "d" * 64,
        "attempts": [],
    }
    receipt.update(overrides)
    return receipt


def completions(github):
    return [s for s in github.statuses if s["context"].startswith(orchestrator.COLLECTOR_CONTEXT_PREFIX)]


def records(github):
    return [ref for ref in github.refs if ref.startswith("tags/hunter-collector-completed/")]


def complete(tmp_path, run, proof, body):
    path = tmp_path / "reviewer-results.json"
    if body is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(body)
    return list(orchestrator.complete_trusted_collector_run(REPO, "token", run, path, proof))


def _without(key):
    return lambda run: json.dumps({k: v for k, v in whole(run).items() if k != key})


UNUSABLE = {
    "only run_attempt": lambda run: '{"run_attempt": 1}',
    "missing repository": _without("repository"),
    "missing pr": _without("pr_number"),
    "missing head": _without("head_sha"),
    "wrong run id": lambda run: json.dumps(whole(run, run_id=run + 1)),
    "wrong attempt": lambda run: json.dumps(whole(run, run_attempt=2)),
    "string attempt": lambda run: json.dumps(whole(run, run_attempt="1")),
    "wrong generation": lambda run: json.dumps(whole(run, remediation_generation_id="gen-other")),
    "missing generation": _without("remediation_generation_id"),
    "missing claims digest": _without("claims_id"),
    "malformed claims digest": lambda run: json.dumps(whole(run, claims_id="c" * 63)),
    "malformed configuration digest": lambda run: json.dumps(whole(run, configuration_digest="")),
    "wrong schema": lambda run: json.dumps(whole(run, schema="hunter.reviewer-collection.v0")),
    "contradictory attempts": lambda run: json.dumps(whole(run, attempts={"codex": "clear"})),
    "non-object attempt records": lambda run: json.dumps(whole(run, attempts=["clear"])),
    "wrong head": lambda run: json.dumps(whole(run, head_sha="f" * 40)),
    "wrong repository": lambda run: json.dumps(whole(run, repository="other/repo")),
    "truncated json": lambda run: json.dumps(whole(run))[:-7],
    "not an object": lambda run: "[1, 2]",
    "unreadable (absent)": lambda run: None,
}


@pytest.mark.parametrize("name", sorted(UNUSABLE))
def test_no_trusted_completion_for_an_unusable_receipt_and_recovery_continues(env, tmp_path, name, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    finalized = []
    monkeypatch.setattr(orchestrator, "finalize_exhausted_pool", lambda *a, **k: finalized.append(a) or "x")
    lines = complete(tmp_path, run, proof, UNUSABLE[name](run))
    assert len(lines) == 1 and "receipt unusable" in lines[0], lines
    assert not completions(github) and not records(github) and not finalized
    assert sweep() == [run]  # still outstanding: invalid evidence never removes it from recovery
    lines = complete(tmp_path, run, proof, json.dumps(whole(run)))  # recovery continues with valid evidence
    assert lines[0].startswith("PUBLISHED")
    assert len(completions(github)) == 1 and len(records(github)) == 1


def test_a_receipt_replayed_from_another_run_is_rejected(env, tmp_path):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    other_run, _other_proof = stage(github, local, 473, orchestrator_run=102)
    replayed = json.dumps(whole(other_run, pr_number=473, head_sha=head_for(473)))
    assert "receipt unusable" in complete(tmp_path, run, proof, replayed)[0]
    assert not completions(github)


def test_the_valid_receipt_publishes_and_finalizes_once(env, tmp_path, monkeypatch):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)
    calls = []
    monkeypatch.setattr(orchestrator, "finalize_exhausted_pool", lambda *a, **k: calls.append(a) or "SKIPPED: done")
    lines = complete(tmp_path, run, proof, json.dumps(whole(run)))
    assert lines == [lines[0], "SKIPPED: done"] and lines[0].startswith("PUBLISHED")
    assert sweep() == []
    # A replay of the whole path neither republishes nor refinalizes.
    assert complete(tmp_path, run, proof, json.dumps(whole(run))) == ["SKIPPED: collector completion already published"]
    assert len(calls) == 1 and len(completions(github)) == 1


@pytest.mark.parametrize(
    "failure",
    [
        transport.GitHubRequestError("down", category="transient", status_code=502),
        "SKIPPED: review request not usable (not committed yet)",
        "SKIPPED: reviewer pool unavailable (policy unreadable)",
        "SKIPPED: collector completion evidence unavailable",
    ],
)
def test_unfinished_finalization_stays_retryable(env, tmp_path, monkeypatch, failure):
    github, local = env
    run, proof = stage(github, local, PR, orchestrator_run=101)

    def finalize(*_a, **_k):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(orchestrator, "finalize_exhausted_pool", finalize)
    try:
        complete(tmp_path, run, proof, json.dumps(whole(run)))
    except transport.GitHubRequestError:
        pass
    assert len(completions(github)) == 1
    assert sweep() == [run], "published but unfinalized work stays queued"
    monkeypatch.setattr(orchestrator, "finalize_exhausted_pool", lambda *_a, **_k: "PUBLISHED: terminal")
    assert complete(tmp_path, run, proof, json.dumps(whole(run)))[-1] == "PUBLISHED: terminal"
    assert sweep() == [] and len(completions(github)) == 1


def test_every_consumer_applies_the_same_contract():
    receipt = {"run_attempt": 1}
    common = {"repository": REPO, "pr_number": PR, "head_sha": "a" * 40, "run_id": 7}
    reason = orchestrator.collector_receipt_problem(receipt, run_attempt=1, **common)
    assert reason
    verdict = orchestrator.pool_exhaustion(
        receipt, claims_id="c" * 64, generation_id=GEN, pool={"agents": ()}, **common
    )
    assert not verdict.exhausted and reason in verdict.reason
