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
AUTH = "hunter-issue-agent-authorization:" + "a" * 64
COMMANDS = {
    "authorize-prepare": ["--event", "event.json", "--out-dir", "out"],
    "authorize-commit": ["--out-dir", "out", "--artifact-id", "1"],
    "step": ["--role", "reconcile"],
    "resume-bind": ["--issue", "1", "--authorization-id", AUTH, "--stage", "validation", "--nonce", "f" * 64],
    "bound": ["--issue", "1", "--authorization-id", AUTH],
    "execute": [
        "--issue", "1", "--authorization-id", AUTH, "--handoff", "h", "--out", "o", "--workroot", "w",
        "--model-key-name", "GROQ_API_KEY", "--model-argv", "opencode", "run",
    ],  # fmt: skip
    "validate": ["--issue", "1", "--authorization-id", AUTH, "--result", "r", "--trusted-repo", ".", "--out", "o"],
    "publish": [
        "--issue", "1", "--authorization-id", AUTH, "--result", "r", "--trusted-repo", ".", "--out", "o",
        "--workroot", "w",
    ],  # fmt: skip
    "candidate-gate": ["--branch", "issue-1-" + "a" * 16, "--head-sha", "b" * 40],
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
def test_an_unprovisioned_repository_refuses_before_any_secret_network_or_subprocess(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no network or subprocess may happen before the trust roots load")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(lifecycle, "_secret", forbidden)
    assert lifecycle.main(["--checkout", str(ROOT), command, *COMMANDS[command]]) == lifecycle.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "MISSING_CONFIGURATION" in err
    for value in SECRETS.values():
        if len(value) > 8:
            assert value not in err


def test_the_pinned_trust_roots_are_the_unprovisioned_document() -> None:
    document = json.loads((ROOT / "config" / "issue_agent_trust_roots.json").read_text(encoding="utf-8"))
    assert document == {"provisioned": False, "schema_version": "hunter-issue-agent-trust-roots-v1"}


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
