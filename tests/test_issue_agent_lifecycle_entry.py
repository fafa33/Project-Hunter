"""S5 inert state: every lifecycle job refuses ``MISSING_CONFIGURATION`` before any secret, ledger or network."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pr737_replay_fixture():
    from types import SimpleNamespace

    config = SimpleNamespace(repository="fafa33/Project-Hunter", repository_id=1292945327)
    pull = {
        "number": 737,
        "state": "closed",
        "merged_at": "2026-11-12T18:29:55Z",
        "merge_commit_sha": "08f565fd14e9ded796a63c9fc45dce6f8997aeff",
        "head": {"sha": "2bb2bda4e183c89b139b85c349d8a3679a70b4ec", "repo": {"id": 1292945327}},
        "base": {"sha": "080fd56b2653a5ce209d372aad7da1c061794b34", "repo": {"id": 1292945327}},
    }
    paths = {
        4553826778: ".github/workflows/hunter-issue-agent-knowledge.yml",
        4553826901: "scripts/hunter_collect_learning_observations.py",
        4553826979: "scripts/hunter_issue_agent_lifecycle.py",
        4553827043: "scripts/hunter_merge_readiness_v2.py",
        4553827094: "scripts/hunter_merge_readiness_v2.py",
        4553827147: "scripts/hunter_merge_readiness_v2.py",
        4553827202: "src/hunter/automation/issue_agent_knowledge.py",
        4553827261: "src/hunter/automation/issue_agent_knowledge.py",
    }
    observations = [
        {
            "event_id": f"review-comment-{identity}",
            "source": "github-review",
            "source_pr": 737,
            "reviewed_head_sha": "2bb2bda4e183c89b139b85c349d8a3679a70b4ec",
            "reviewed_base_sha": "080fd56b2653a5ce209d372aad7da1c061794b34",
            "source_event_head_sha": "a16037419c0d067319c8edbb4f5ebfcbf3c4ae42",
            "reviewer": "Copilot",
            "path": path,
        }
        for identity, path in paths.items()
    ]
    return config, pull, observations


def test_pr737_replay_selects_only_the_eight_authenticated_inline_findings():
    config, pull, observations = _pr737_replay_fixture()
    # A review summary is not a ninth finding and must never enter the replay batch.
    summary = {"event_id": "review-5878890776", "source": "github-review"}
    selected = lifecycle._pr737_replay_scope(config, pull, [*observations, summary])
    assert [item["event_id"] for item in selected] == [item["event_id"] for item in observations]


@pytest.mark.parametrize("change", ["repository", "fork", "head", "base", "merge", "open", "pr"])
def test_pr737_replay_refuses_a_different_repository_or_historical_identity(change):
    config, pull, observations = _pr737_replay_fixture()
    if change == "repository":
        config.repository_id = 1
    elif change == "fork":
        pull["head"]["repo"]["id"] = 1
    elif change in {"head", "base"}:
        pull[change]["sha"] = "f" * 40
    elif change == "merge":
        pull["merge_commit_sha"] = "f" * 40
    elif change == "pr":
        pull["number"] = 738
    else:
        pull["state"] = "open"
    with pytest.raises(lifecycle.LifecycleRefused, match="INVALID_SCOPE"):
        lifecycle._pr737_replay_scope(config, pull, observations)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "extra", "provider", "path", "source_head", "pr", "head", "base"]
)
def test_pr737_replay_refuses_partial_duplicate_or_foreign_observations(change):
    config, pull, observations = _pr737_replay_fixture()
    if change == "missing":
        observations.pop()
    elif change == "duplicate":
        observations.append(dict(observations[0]))
    elif change == "extra":
        observations.append({**observations[0], "event_id": "review-comment-1"})
    else:
        field, value = {
            "provider": ("reviewer", "untrusted-substitute"),
            "path": ("path", "scripts/unrelated.py"),
            "source_head": ("source_event_head_sha", "f" * 40),
            "pr": ("source_pr", 738),
            "head": ("reviewed_head_sha", "f" * 40),
            "base": ("reviewed_base_sha", "f" * 40),
        }[change]
        observations[0][field] = value
    with pytest.raises(lifecycle.LifecycleRefused, match="INVALID_SCOPE"):
        lifecycle._pr737_replay_scope(config, pull, observations)


def test_pr737_replay_cannot_be_combined_with_an_arbitrary_pr():
    with pytest.raises(SystemExit):
        lifecycle._parser().parse_args(["knowledge-ingest", "--replay-pr737", "--pr", "738"])


@pytest.mark.parametrize("preflight_success", [True, False])
def test_pr737_replay_uses_existing_signed_ledger_and_never_invents_receipts(tmp_path, monkeypatch, preflight_success):
    from types import SimpleNamespace

    import hunter_collect_learning_observations as collector
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hunter.automation import issue_agent_control as control
    from hunter.automation import issue_agent_knowledge as knowledge
    from hunter.automation import issue_agent_state as state

    config, pull, observations = _pr737_replay_fixture()
    key = Ed25519PrivateKey.generate()
    config.trust = state.TrustRoots({state.public_key_id(key.public_key()): key.public_key()}, 1292945327)
    for item in observations:
        item.update(
            provider="github-review", message="Verified historical finding, not completed proof", obligation=True
        )
    run = {
        "id": 43276347921,
        "run_attempt": 1,
        "head_sha": pull["head"]["sha"],
        "event": "push",
        "head_branch": "feat/527-finding-knowledge-receipt-lifecycle",
        "repository": {"id": 1292945327},
        "head_repository": {"id": 1292945327},
        "status": "completed",
        "conclusion": "success" if preflight_success else "failure",
    }

    def get(path):
        if path == "/repos/fafa33/Project-Hunter/pulls/737":
            return control.Read("ok", pull)
        if "/actions/workflows/hunter-pre-pr-preflight.yml/runs?" in path:
            return control.Read("ok", {"workflow_runs": [run]})
        raise AssertionError(f"unexpected GitHub read {path}")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    store = state.GitLedgerStore(str(remote), workdir=tmp_path / "reader")
    by = {
        "workflow_path": control.KNOWLEDGE_WORKFLOW,
        "job": "knowledge-ingest",
        "role": "knowledge-ingest",
        "run_id": 100,
        "run_attempt": 1,
        "head_sha": "c" * 40,
    }

    def provenance(_by, _record):
        return True

    monkeypatch.setattr(lifecycle, "_writer", lambda *_args: SimpleNamespace(recorded_by=lambda: by))
    monkeypatch.setattr(lifecycle, "_github", lambda _cfg: SimpleNamespace(get=get))
    monkeypatch.setattr(lifecycle, "_secret", lambda _name: "unit-test-token")
    monkeypatch.setattr(lifecycle, "_ed25519", lambda _name: key)
    monkeypatch.setattr(lifecycle, "_store", lambda *_args, **_kw: store)
    monkeypatch.setattr(control, "require_anchor", lambda *_args: None)
    monkeypatch.setattr(control, "run_provenance", lambda *_args: provenance)
    monkeypatch.setattr(collector, "_review_threads", lambda *_args: {1: True, 2: False})
    monkeypatch.setattr(collector, "collect", lambda *_args, **_kwargs: observations)
    published = []
    published_kwargs = []
    monkeypatch.setattr(
        lifecycle,
        "_publish_knowledge_receipt_status",
        lambda *args, **kwargs: (published.append(args), published_kwargs.append(kwargs)),
    )
    args = lifecycle._parser().parse_args(["knowledge-ingest", "--replay-pr737"])
    if not preflight_success:
        with pytest.raises(lifecycle.LifecycleRefused, match="MISSING_EVIDENCE"):
            lifecycle.cmd_knowledge_ingest(config, args)
        assert store.ref_head(knowledge.KNOWLEDGE_LEDGER_REF) is None
        assert published == []
        return
    assert lifecycle.cmd_knowledge_ingest(config, args) == 0
    first_head, view = knowledge.read(store, trust=config.trust, provenance=provenance)
    assert len(view.findings) == 8
    assert all(finding.receipt is None and finding.proven is None for finding in view.findings.values())
    assert published[-1][5] == "failure"  # eight genuine obligations remain unsatisfied
    # ADR 0039 L11 hardening (Copilot F1/F3, PR #737; Issue #586): cmd_knowledge_ingest fetches the
    # live review-thread snapshot once and binds the published verdict to its fingerprint.
    assert published_kwargs[-1]["obligation_fingerprint"] == knowledge.review_thread_fingerprint({1: True, 2: False})
    assert lifecycle.cmd_knowledge_ingest(config, args) == 0
    second_head, repeated = knowledge.read(store, trust=config.trust, provenance=provenance)
    assert second_head == first_head and len(repeated.findings) == 8


AUTH = "hunter-issue-agent-authorization:" + "a" * 64
COMMANDS = {
    "source-handling-bootstrap": [],
    "authorize-prepare": ["--event", "event.json", "--out-dir", "out"],
    "remediate": ["--issue", "1"],
    "resolve-finding": ["--issue", "1"],
    "authorize-commit": ["--out-dir", "out", "--artifact-id", "1"],
    "step": ["--role", "reconcile"],
    "resume-bind": ["--issue", "1", "--authorization-id", AUTH, "--stage", "validation", "--nonce", "f" * 64],
    "bound": ["--issue", "1", "--authorization-id", AUTH],
    "execute": [
        "--issue", "1", "--authorization-id", AUTH, "--handoff", "h", "--out", "o", "--workroot", "w",
        "--model-key-name", "GROQ_API_KEY", "--model-argv", "opencode", "run",
    ],  # fmt: skip
    "fallback-decision": ["--event", "event.json"],
    "execute-fallback": ["--issue", "1", "--authorization-id", AUTH, "--handoff", "h", "--out", "o", "--model", "m"],
    "validate": ["--issue", "1", "--authorization-id", AUTH, "--result", "r", "--trusted-repo", ".", "--out", "o"],
    "publish": [
        "--issue", "1", "--authorization-id", AUTH, "--result", "r", "--trusted-repo", ".", "--out", "o",
        "--workroot", "w",
    ],  # fmt: skip
    "candidate-gate": ["--branch", "issue-1-" + "a" * 16, "--head-sha", "b" * 40],
    "knowledge-ingest": ["--pr", "561"],
    "verified-e2e-outcome": ["--issue", "577", "--pr", "703"],
    "historical-coverage": ["--pr", "579"],
    "historical-assess": ["--pr", "579"],
    "knowledge-context-check": ["--path", "scripts/x.py", "--base-sha", "a" * 40],
}
SECRETS = {
    "GITHUB_TOKEN": "ghs_runner_token_value",
    "HUNTER_ISSUE_AGENT_STATE_SIGNING_KEY": "11" * 32,
    "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY": "22" * 32,
    "HUNTER_ISSUE_AGENT_HANDOFF_KEY": "33" * 32,
    "HUNTER_ISSUE_AGENT_RESULT_KEY": "44" * 32,
    "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY": "gsk_model_key_value",
    "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN": "github_pat_push_value",
    "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----",
    "GITHUB_RUN_ID": "100",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_SHA": "c" * 40,
}


def test_every_subcommand_has_a_refusal_case() -> None:
    parser = lifecycle._parser()
    choices = next(a for a in parser._actions if a.dest == "command").choices
    assert set(choices) == set(COMMANDS)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_an_unprovisioned_repository_still_refuses_before_any_secret_network_or_subprocess(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "checkout"
    config = checkout / "config"
    config.mkdir(parents=True)
    (config / "issue_agent_trust_roots.json").write_text(
        json.dumps({"provisioned": False, "schema_version": "hunter-issue-agent-trust-roots-v1"}),
        encoding="utf-8",
    )
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no secret, network or subprocess may happen before the trust roots load")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(lifecycle, "_secret", forbidden)
    assert lifecycle.main(["--checkout", str(checkout), command, *COMMANDS[command]]) == lifecycle.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "MISSING_CONFIGURATION" in err
    for value in SECRETS.values():
        if len(value) > 8:
            assert value not in err


def test_the_pinned_s6_trust_roots_are_provisioned_and_complete() -> None:
    document = json.loads((ROOT / "config" / "issue_agent_trust_roots.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == "hunter-issue-agent-trust-roots-v1"
    assert document["provisioned"] is True
    assert document["repository"] == "fafa33/Project-Hunter"
    assert document["repository_id"] == 1292945327
    assert document["owner_login"] == "fafa33"
    assert document["anchor"] == {
        "ruleset_id": 24526712,
        "updated_at": "2026-10-06T20:52:31.773Z",
    }
    assert len(document["state_keys"]) == 1
    assert len(document["authorization_verifying_key"]) == 64
    assert len(document["prompt_verifying_key"]) == 64
    assert len(document["handoff_recipient"]) == 64
    assert len(document["result_recipient"]) == 64
    assert (
        document["source_handling"]["genesis_rule_sha256"]
        == "41119071db0f5c2a2eacfe2848ab6696355195e1ac9c671ee33c4128793aa70a"
    )


def test_a_workflow_rerun_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "100")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(lifecycle.LifecycleRefused, match="RERUN_REFUSED"):
        lifecycle._run_context()


def test_secrets_are_never_accepted_from_argv() -> None:
    parser = lifecycle._parser()
    flags = {
        flag
        for action in parser._actions
        if action.dest == "command"
        for child in action.choices.values()
        for sub in child._actions
        for flag in sub.option_strings
    }
    # The only key-named flag carries the *name* of the provider's environment variable, never a value.
    assert {flag for flag in flags if any(word in flag for word in ("key", "token", "secret"))} == {"--model-key-name"}


# --- Issue #577: a signed terminal FAILED is visible on the run, never reported as task completion -------------


def test_a_terminal_failure_is_annotated_with_its_closed_code_only() -> None:
    from hunter.automation import issue_agent_state as state

    annotation = lifecycle.terminal_outcome_annotation(577, state.Decision("fail", code="EXECUTION_NOT_COMPLETED"))
    assert annotation is not None
    assert annotation.startswith("::error title=Issue agent FAILED::issue 577: FAILED EXECUTION_NOT_COMPLETED.")
    assert "\n" not in annotation and "%" not in annotation


@pytest.mark.parametrize("code", ["NOT_A_FAILURE_CODE", "EXECUTION_NOT_COMPLETED\n::warning::x", "", None])
def test_a_code_outside_the_failed_vocabulary_is_never_echoed(code: str | None) -> None:
    from hunter.automation import issue_agent_state as state

    annotation = lifecycle.terminal_outcome_annotation(577, state.Decision("fail", code=code))
    assert annotation is not None and "FAILED UNKNOWN." in annotation and "warning" not in annotation


@pytest.mark.parametrize(
    "decision",
    [("noop", None, None), ("transition", "RESULT_BOUND", None), ("transition", "COMPLETED", None)],
)
def test_non_terminal_decisions_carry_no_failure_annotation(decision: tuple[str, str | None, str | None]) -> None:
    from hunter.automation import issue_agent_state as state

    action, target, code = decision
    assert lifecycle.terminal_outcome_annotation(577, state.Decision(action, target_state=target, code=code)) is None


@pytest.mark.parametrize(
    "action,target,code,annotated,bound",
    [
        ("fail", None, "EXECUTION_NOT_COMPLETED", True, "false"),
        ("transition", "RESULT_BOUND", None, False, "true"),
    ],
)
def test_bind_step_annotates_a_terminal_failure_without_failing_the_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
    target: str | None,
    code: str | None,
    annotated: bool,
    bound: str,
) -> None:
    from hunter.automation import issue_agent_control as control
    from hunter.automation import issue_agent_state as state

    outputs: dict[str, str] = {}
    monkeypatch.setattr(lifecycle, "_writer", lambda *_: object())
    monkeypatch.setattr(lifecycle, "_github", lambda *_: object())
    monkeypatch.setattr(lifecycle, "_store", lambda *_, **__: object())
    monkeypatch.setattr(lifecycle, "_ed25519", lambda *_: object())
    monkeypatch.setattr(lifecycle, "_output", lambda **values: outputs.update(values))
    monkeypatch.setattr(control, "step", lambda **_: state.Decision(action, target_state=target, code=code))
    arguments = lifecycle.argparse.Namespace(role="bind", issue=577, advisory="PROVIDER_UNAVAILABLE", job="bind")
    assert lifecycle.cmd_step(object(), arguments) == 0  # orchestration succeeded; the ledger holds the outcome
    printed = capsys.readouterr().out
    assert ("::error title=Issue agent FAILED::" in printed) is annotated
    assert outputs == {"result_bound": bound, "fallback_authorized": "false"}


# --- ADR 0039 L11 (Issue #527): the Knowledge Receipt status transport -------------------------------------


class _FakeGovernance:
    def __init__(self, combined: dict[str, Any], calls: list[tuple[str, str, dict | None]]) -> None:
        self._combined = combined
        self._calls = calls

    def request_json(self, _repository: str, _token: str, method: str, path: str, payload: dict | None = None) -> Any:
        self._calls.append((method, path, payload))
        return self._combined if method == "GET" else {"id": 1}


class _FakeCollector:
    def __init__(self, combined: dict[str, Any], calls: list[tuple[str, str, dict | None]]) -> None:
        self.governance = _FakeGovernance(combined, calls)


def test_knowledge_receipt_status_publish_is_idempotent_when_unchanged() -> None:
    """ADR 0039 L11: an unchanged verdict at this exact head is never republished."""
    calls: list[tuple[str, str, dict | None]] = []
    combined = {
        "statuses": [
            {"context": f"{lifecycle.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}737", "state": "success", "description": "ok"}
        ]
    }
    collector = _FakeCollector(combined, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector, "fafa33/Project-Hunter", "token", "a" * 40, 737, "success", "ok"
    )
    assert calls == [("GET", "commits/" + "a" * 40 + "/status", None)]


def test_knowledge_receipt_status_publish_posts_on_a_changed_verdict() -> None:
    calls: list[tuple[str, str, dict | None]] = []
    combined = {"statuses": []}
    collector = _FakeCollector(combined, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector, "fafa33/Project-Hunter", "token", "a" * 40, 737, "failure", "1 missing"
    )
    assert len(calls) == 2
    method, path, payload = calls[1]
    assert method == "POST"
    assert path == "statuses/" + "a" * 40
    assert payload == {
        "state": "failure",
        "context": f"{lifecycle.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}737",
        "description": "1 missing",
    }


def test_knowledge_receipt_status_publish_truncates_to_the_github_description_limit() -> None:
    calls: list[tuple[str, str, dict | None]] = []
    collector = _FakeCollector({"statuses": []}, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector, "fafa33/Project-Hunter", "token", "a" * 40, 737, "failure", "x" * 200
    )
    _method, _path, payload = calls[1]
    assert payload is not None
    assert len(payload["description"]) == 140


def test_knowledge_receipt_status_publish_embeds_the_obligation_fingerprint() -> None:
    """ADR 0039 L11 hardening (Copilot F1/F3, PR #737; Issue #586): the verdict binds to the exact

    review-thread snapshot it was derived from, so a consumer can detect it has moved on.
    """
    calls: list[tuple[str, str, dict | None]] = []
    collector = _FakeCollector({"statuses": []}, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector,
        "fafa33/Project-Hunter",
        "token",
        "a" * 40,
        737,
        "success",
        "no obligation",
        obligation_fingerprint="a" * 16,
    )
    _method, _path, payload = calls[1]
    assert payload is not None
    assert payload["description"] == f"no obligation [rt:{'a' * 16}]"


def test_knowledge_receipt_status_publish_truncates_leaving_room_for_the_fingerprint_suffix() -> None:
    calls: list[tuple[str, str, dict | None]] = []
    collector = _FakeCollector({"statuses": []}, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector,
        "fafa33/Project-Hunter",
        "token",
        "a" * 40,
        737,
        "failure",
        "x" * 200,
        obligation_fingerprint="a" * 16,
    )
    _method, _path, payload = calls[1]
    assert payload is not None
    assert len(payload["description"]) == 140
    assert payload["description"].endswith(f"[rt:{'a' * 16}]")


def test_knowledge_receipt_status_publish_republishes_when_only_the_fingerprint_changed() -> None:
    """Unlike an unchanged human-readable verdict, a moved review-thread snapshot is never

    idempotent-skipped: the status (and its ``created_at``) must refresh so a consumer sees the
    new snapshot, even though the verdict text alone would have looked unchanged.
    """
    calls: list[tuple[str, str, dict | None]] = []
    combined = {
        "statuses": [
            {
                "context": f"{lifecycle.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}737",
                "state": "success",
                "description": f"no obligation [rt:{'a' * 16}]",
            }
        ]
    }
    collector = _FakeCollector(combined, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector,
        "fafa33/Project-Hunter",
        "token",
        "a" * 40,
        737,
        "success",
        "no obligation",
        obligation_fingerprint="b" * 16,
    )
    assert len(calls) == 2  # a real post, not a skip, despite the identical human-readable text
    _method, _path, payload = calls[1]
    assert payload is not None
    assert payload["description"] == f"no obligation [rt:{'b' * 16}]"


def test_knowledge_receipt_status_publish_without_a_fingerprint_is_unchanged() -> None:
    """Backward compatibility: omitting ``obligation_fingerprint`` behaves exactly as before."""
    calls: list[tuple[str, str, dict | None]] = []
    collector = _FakeCollector({"statuses": []}, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector, "fafa33/Project-Hunter", "token", "a" * 40, 737, "success", "no obligation"
    )
    _method, _path, payload = calls[1]
    assert payload is not None
    assert payload["description"] == "no obligation"


def test_knowledge_receipt_status_publish_is_bound_to_pr_identity_not_only_the_head() -> None:
    """Copilot F4 (PR #737): two PRs sharing one head SHA must never read or overwrite each

    other's verdict. An existing status for a *different* PR's context at the same SHA must
    never be treated as "unchanged" for this PR, and must never be overwritten by this publish.
    """
    calls: list[tuple[str, str, dict | None]] = []
    combined = {
        "statuses": [
            {"context": f"{lifecycle.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}100", "state": "success", "description": "ok"}
        ]
    }
    collector = _FakeCollector(combined, calls)
    lifecycle._publish_knowledge_receipt_status(
        collector, "fafa33/Project-Hunter", "token", "a" * 40, 200, "failure", "1 missing"
    )
    assert len(calls) == 2  # PR #200's context was absent, so this is a real post, not a skip
    _method, _path, payload = calls[1]
    assert payload is not None
    assert payload["context"] == f"{lifecycle.KNOWLEDGE_RECEIPT_CONTEXT_PREFIX}200"
