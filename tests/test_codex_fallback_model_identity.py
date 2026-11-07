"""ADR 0041 F5a, owner decisions in Issue #390 (comments 6706666538 and 6706679138): model provenance, not lock-in.

``gpt-5.6`` is the preferred model the fallback requests, pinned at ``control_sha``; workflow inputs cannot choose
another. A provider reroute inside the authorized trust boundary (the owner-approved ChatGPT-plan Codex session, the
Seatbelt sandbox, the allowlisted OpenAI gateway) is accepted, never rejected, and never hidden. The requested and
observed models are recorded, and ``observed`` names a model only when the pinned CLI itself reported one; model text
can neither assert nor forge it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_fallback_control as fc
import test_issue_agent_roles as base
from test_issue_agent_control import ledger_state, step
from test_issue_agent_mac_fallback import attested_port, launcher, provisioned_checkout

from hunter.automation import issue_agent_mac_fallback as mac
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_roles import RoleRefused

repos = base.repos
_prompt_keys = base._prompt_keys
ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-5.6"


def stream(*events: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(event).encode() + b"\n" for event in events)


STARTED = {"type": "thread.started", "thread_id": "01a1b5e3-93e0-7581-ab08-d0e36b4b1a1f"}
TURN = {"type": "turn.started"}
DONE = {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}


def message(text: str) -> dict[str, Any]:
    return {"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": text}}


def cli_error(text: str, *, top_level: bool = False) -> dict[str, Any]:
    if top_level:
        return {"type": "error", "message": text}
    return {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": text}}


# Verbatim from the pinned Codex 0.167.0 binary and its observed output.
REROUTE_TEXT = (
    "Your account was flagged for potentially high-risk cyber activity and this request was routed to gpt-5.2 "
    "as a fallback. To regain access to gpt-5.3-codex, apply for trusted access"
)
UNKNOWN_TEXT = "Model metadata for `gpt-5.6` not found. Defaulting to fallback metadata; this can degrade performance."


# --- exact model match ----------------------------------------------------------------------------------------


def test_a_cli_reported_preferred_model_is_recorded_as_reported() -> None:
    events = stream(STARTED, {"type": "turn.started", "model": MODEL}, DONE)
    assert launcher.model_provenance(events, MODEL) == ("REPORTED", MODEL)


def test_without_cli_evidence_the_requested_model_is_never_asserted() -> None:
    assert launcher.model_provenance(stream(STARTED, TURN, message("done"), DONE), MODEL) == ("NOT_OBSERVED", None)
    assert launcher.model_provenance(b"", MODEL) == ("NOT_OBSERVED", None)


def test_the_exec_argv_requests_the_preferred_model_and_the_event_stream() -> None:
    argv = launcher.codex_exec_argv(MODEL, Path("/w"))
    assert "--json" in argv and argv[argv.index("--model") + 1] == MODEL


# --- silent rerouting: recorded, not rejected -----------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        cli_error(REROUTE_TEXT),
        cli_error(REROUTE_TEXT, top_level=True),
        {"type": "model_reroute", "from_model": MODEL, "to_model": "gpt-5.2"},
        {"type": "item.completed", "item": {"id": "i", "type": "model_reroute", "to_model": "gpt-5.2"}},
        {"type": "turn.started", "model": "gpt-5.2"},
    ],
    ids=["cli-notice-item", "cli-notice-event", "reroute-event", "reroute-item", "reported-other-model"],
)
def test_a_reroute_is_recorded_with_the_model_the_cli_named(event: dict[str, Any]) -> None:
    assert launcher.model_provenance(stream(STARTED, TURN, event, DONE), MODEL) == ("REROUTED", "gpt-5.2")


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "model_reroute", "to_model": "not a slug!"}],
        [{"type": "model_reroute"}],
        [cli_error("routed to gpt-5.2"), {"type": "model_reroute", "to_model": "gpt-5.5"}],  # two different targets
        [{"type": "turn.started", "model": "gpt-5.2"}, {"type": "turn.started", "model": "gpt-5.5"}],
    ],
)
def test_an_unidentifiable_reroute_names_no_model(events: list[dict[str, Any]]) -> None:
    assert launcher.model_provenance(stream(STARTED, *events, DONE), MODEL) == ("REROUTED_UNIDENTIFIED", None)


# --- missing or ambiguous model identity ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "events",
    [
        stream(STARTED, TURN, DONE) + b"not json\n",
        stream(STARTED, TURN, DONE) + b"[1, 2]\n",
        stream(STARTED, {"type": 7}, DONE),
        stream(STARTED, {"type": "turn.started", "model": 5}, DONE),
        stream(STARTED, TURN, DONE) + b" " * launcher.MAX_EVENT_STREAM,
    ],
)
def test_a_malformed_stream_is_never_used_as_evidence(events: bytes) -> None:
    assert launcher.model_provenance(events, MODEL) == ("STREAM_INVALID", None)


def test_an_unknown_model_warning_or_unrelated_error_asserts_nothing() -> None:
    for noise in (cli_error(UNKNOWN_TEXT), cli_error("Reconnecting... 2/5", top_level=True)):
        assert launcher.model_provenance(stream(STARTED, noise, TURN, DONE), MODEL) == ("NOT_OBSERVED", None)


# --- forged or untrusted metadata -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"served by {MODEL}",
        "this request was routed to gpt-5.2",
        json.dumps({"type": "model_reroute", "to_model": "gpt-4o"}),
        json.dumps({"type": "turn.started", "model": MODEL}),
    ],
)
def test_model_text_can_neither_assert_nor_forge_provenance(text: str) -> None:
    for item in (
        message(text),
        {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "aggregated_output": text}},
        {"type": "item.completed", "item": {"id": "c", "type": "agent_message", "text": text, "model": "gpt-4o"}},
    ):
        assert launcher.model_provenance(stream(STARTED, TURN, item, DONE), MODEL) == ("NOT_OBSERVED", None)


@pytest.mark.parametrize(
    "reported",
    [
        None,
        "REPORTED",
        {"requested": MODEL, "requested_listed": True, "observed": "gpt-5.2", "provenance": "NOT_OBSERVED"},
        {"requested": MODEL, "requested_listed": True, "observed": None, "provenance": "REROUTED"},
        {"requested": "gpt-5.2", "requested_listed": True, "observed": MODEL, "provenance": "REPORTED"},
        {"requested": MODEL, "requested_listed": "yes", "observed": None, "provenance": "NOT_OBSERVED"},
        {"requested": MODEL, "requested_listed": True, "observed": None, "provenance": "VERIFIED"},
        {"requested": MODEL, "requested_listed": True, "observed": None, "provenance": "NOT_OBSERVED", "extra": 1},
    ],
)
def test_a_forged_or_malformed_report_is_recorded_as_observing_nothing(reported: Any) -> None:
    assert mac.model_record(reported, MODEL) == {
        "requested": MODEL,
        "requested_listed": False,
        "observed": None,
        "provenance": "STREAM_INVALID",
    }


# --- the launcher: catalog as evidence, the run is never gated on the model ------------------------------------


class Launch:
    def __init__(self, catalog: tuple[int, bytes, bytes], run: tuple[int, bytes, bytes]) -> None:
        self.catalog, self.exec = catalog, run
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], env: dict[str, str], *, stdin: bytes = b"", timeout: float) -> Any:
        self.calls.append(argv)
        return self.catalog if "debug" in argv else self.exec


def signed_in_run(monkeypatch: pytest.MonkeyPatch, catalog: Any, run: Any) -> tuple[dict[str, Any], Launch]:
    launch = Launch(catalog, run)
    monkeypatch.setattr(launcher, "_run", launch)
    request = {"kind": "run", "workroot_id": "a" * 32, "model": MODEL}
    return launcher._signed_in_run(request, b"prompt", Path("/w"), Path("/h")), launch


LISTED = (0, json.dumps({"models": [{"slug": MODEL}]}).encode(), b"")
UNLISTED = (0, json.dumps({"models": [{"slug": "gpt-5.6-sol"}, {"slug": "gpt-5.5"}]}).encode(), b"")


def test_a_permitted_reroute_completes_and_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    response, launch = signed_in_run(
        monkeypatch, LISTED, (0, stream(STARTED, TURN, cli_error(REROUTE_TEXT), DONE), b"")
    )
    assert response == {
        "status": "COMPLETED",
        "exit": "ZERO",
        "stderr_category": "UNCLASSIFIED",
        "model": {"requested": MODEL, "requested_listed": True, "observed": "gpt-5.2", "provenance": "REROUTED"},
    }
    catalog_call, exec_call = launch.calls
    assert catalog_call[0] == exec_call[0] == "/usr/bin/sandbox-exec" and "--json" in exec_call


def test_an_unlisted_preferred_model_still_runs_and_is_recorded_unlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    response, launch = signed_in_run(monkeypatch, UNLISTED, (0, stream(STARTED, TURN, DONE), b""))
    assert response["status"] == "COMPLETED" and len(launch.calls) == 2
    assert response["model"] == {
        "requested": MODEL,
        "requested_listed": False,
        "observed": None,
        "provenance": "NOT_OBSERVED",
    }


@pytest.mark.parametrize(
    ("catalog", "run", "expected"),
    [
        ((71, b"", b""), (0, b"", b""), {"status": "SANDBOX_SETUP_FAILED"}),  # a sandbox defect is never a run
        (LISTED, (71, b"", b""), {"status": "SANDBOX_SETUP_FAILED"}),
    ],
)
def test_sandbox_setup_failure_never_runs(
    monkeypatch: pytest.MonkeyPatch, catalog: Any, run: Any, expected: Any
) -> None:
    assert signed_in_run(monkeypatch, catalog, run)[0] == expected


@pytest.mark.parametrize(
    ("stderr", "category"),
    [(b"You've hit your usage limit", "QUOTA_EXCEEDED"), (b"401 Unauthorized", "AUTHENTICATION")],
)
def test_authentication_or_provider_failure_is_a_classified_failure(
    monkeypatch: pytest.MonkeyPatch, stderr: bytes, category: str
) -> None:
    failed = stream(STARTED, TURN, {"type": "turn.failed", "error": {"message": "x"}})
    response, _ = signed_in_run(monkeypatch, (1, b"", stderr), (1, failed, stderr))
    assert response["exit"] == "NONZERO" and response["stderr_category"] == category
    assert response["model"]["requested_listed"] is False and response["model"]["observed"] is None


# --- the runner: preferred model pinned, outcome decided by exit and validation only ---------------------------


def test_the_repository_pins_the_owner_preferred_model() -> None:
    assert mac.load_host_config(ROOT).codex_model == "gpt-5.6"


@pytest.mark.parametrize("pinned", [None, "", "gpt 5.6", 7, "-c"])
def test_an_unpinned_or_malformed_preference_keeps_the_fallback_inert(tmp_path: Path, pinned: Any) -> None:
    with pytest.raises(RoleRefused, match="model is not pinned"):
        mac.load_host_config(provisioned_checkout(tmp_path, codex_model=pinned))


def _run(port: Any, workroot: Path, model: str = "gpt-5.1-codex") -> int:
    return int(
        port.run(
            ("codex", "exec", "--model", model),
            cwd=workroot / "workspace",
            public_env={},
            secret_env={},
            stdin=b"x",
            timeout=1,
        )
    )


def test_workflow_configuration_never_chooses_another_model(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, sent = attested_port(tmp_path, monkeypatch, {"status": "COMPLETED", "exit": "ZERO"})
    before = len(sent)
    with pytest.raises(RoleRefused, match="not the pinned model") as refused:
        _run(port, workroot, "gpt-5.2")
    assert refused.value.code == "EXECUTION_NOT_STARTED" and len(sent) == before


@pytest.mark.parametrize("exit_class", ["ZERO", "NONZERO"])
def test_a_reroute_never_changes_the_outcome(tmp_path: Path, monkeypatch: Any, exit_class: str) -> None:
    report = {"requested": "gpt-5.1-codex", "requested_listed": True, "observed": "gpt-5.2", "provenance": "REROUTED"}
    port, workroot, _ = attested_port(
        tmp_path, monkeypatch, {"status": "COMPLETED", "exit": exit_class, "model": report}
    )
    assert _run(port, workroot) == (0 if exit_class == "ZERO" else 1) and port.model == report


def test_a_rerouted_fallback_result_binds_with_its_provenance_on_the_signed_ledger(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    sealed = fc.fallback_result(repos, tmp_path, monkeypatch)
    report = {"requested": MODEL, "requested_listed": False, "observed": "gpt-5.2", "provenance": "REROUTED"}
    decision = step(
        repos,
        fc.fallback_world(sealed),
        configuration=fc.FALLBACK_CONFIG,
        fallback_attestation_sha256="ab" * 32,
        fallback_model=report,
    )
    assert decision.target_state == state.RESULT_BOUND
    assert ledger_state(repos).evidence[state.RESULT_BOUND]["fallback"]["model"] == report


@pytest.mark.parametrize(
    "report",
    [
        {"requested": MODEL, "requested_listed": True, "observed": MODEL, "provenance": "NOT_OBSERVED"},
        {"requested": MODEL, "requested_listed": True, "observed": "gpt-5.2", "provenance": "REPORTED"},
        {"requested": MODEL, "observed": None, "provenance": "NOT_OBSERVED"},
    ],
)
def test_the_ledger_never_records_a_misrepresented_provenance(report: dict[str, Any]) -> None:
    assert not state.fallback_model_conforms(report)
