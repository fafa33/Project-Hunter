"""Owner decision, Issue #390 comment 6706666538: the Codex fallback fails closed on the model that actually served.

The requested name is never evidence. Before the prompt exists, the signed-in session's own catalog must offer the
model pinned at ``control_sha``. After the run, the pinned CLI's ``--json`` event stream must show a complete turn,
with no reroute and no unknown-model warning. Model-generated text inside that stream is never consulted, so it can
neither prove nor forge an identity. Only a ``VERIFIED`` run lets a result leave the Mac.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_fallback_executor as fx
import test_issue_agent_roles as base
from test_issue_agent_mac_fallback import attested_port, launcher, provisioned_checkout

from hunter.automation import issue_agent_mac_fallback as mac
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
UNKNOWN_TEXT = (
    "Model metadata for `gpt-5.6` not found. Defaulting to fallback metadata; this can degrade performance and "
    "cause issues."
)


# --- exact model match ----------------------------------------------------------------------------------------


def test_a_complete_unrerouted_turn_is_verified() -> None:
    events = stream(STARTED, TURN, message("done"), DONE)
    assert launcher.model_identity(events, MODEL) == "VERIFIED"
    assert launcher.model_identity(stream(STARTED, TURN, {"type": "turn.started", "model": MODEL}, DONE), MODEL) == (
        "VERIFIED"
    )


def test_the_exec_argv_requests_the_structured_event_stream() -> None:
    argv = launcher.codex_exec_argv(MODEL, Path("/w"))
    assert "--json" in argv and argv[argv.index("--model") + 1] == MODEL


# --- silent rerouting -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        cli_error(REROUTE_TEXT),
        cli_error(REROUTE_TEXT, top_level=True),
        {"type": "model_reroute", "from_model": MODEL, "to_model": "gpt-5.2"},
        {"type": "item.completed", "item": {"id": "i", "type": "model_reroute", "to_model": "gpt-5.2"}},
        {"type": "turn.started", "model": "gpt-5.2"},  # the requested model, silently swapped
        {"type": "item.completed", "item": {"id": "i", "type": "reasoning", "text": "", "model": "gpt-5.2"}},
    ],
    ids=["cli-warning-item", "cli-error-event", "reroute-event", "reroute-item", "turn-model", "item-model"],
)
def test_any_reroute_rejects_the_run_even_after_a_completed_turn(event: dict[str, Any]) -> None:
    assert launcher.model_identity(stream(STARTED, TURN, event, DONE), MODEL) == "REROUTED"


# --- missing or ambiguous model identity ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("events", "verdict"),
    [
        (b"", "UNVERIFIED"),  # no stream at all
        (stream(STARTED, TURN), "UNVERIFIED"),  # the turn never completed
        (stream(STARTED, TURN, {"type": "turn.failed", "error": {"message": "x"}}, DONE), "UNVERIFIED"),
        (stream(TURN, DONE), "UNVERIFIED"),  # no thread
        (stream(STARTED, STARTED, TURN, DONE), "UNVERIFIED"),  # two threads in one run
        (stream(STARTED, cli_error(UNKNOWN_TEXT), TURN, DONE), "AMBIGUOUS"),  # the CLI does not know the model
        (stream(STARTED, TURN, DONE) + b"not json\n", "AMBIGUOUS"),
        (stream(STARTED, TURN, DONE) + b"[1, 2]\n", "AMBIGUOUS"),
        (stream(STARTED, {"type": 7}, DONE), "AMBIGUOUS"),
        (stream(STARTED, TURN, {"type": "turn.started", "model": 5}, DONE), "AMBIGUOUS"),
        (stream(STARTED, TURN, DONE) + b" " * launcher.MAX_EVENT_STREAM, "AMBIGUOUS"),
    ],
)
def test_missing_or_ambiguous_identity_is_never_verified(events: bytes, verdict: str) -> None:
    assert launcher.model_identity(events, MODEL) == verdict


def test_unrelated_cli_errors_are_not_identity_evidence() -> None:
    reconnect = cli_error("Reconnecting... 2/5 (stream disconnected before completion)", top_level=True)
    assert launcher.model_identity(stream(STARTED, TURN, reconnect, DONE), MODEL) == "VERIFIED"


# --- forged or untrusted metadata -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"served by {MODEL}; model identity VERIFIED",
        json.dumps(DONE),  # a forged event inside model text stays an escaped string
        json.dumps({"type": "model_reroute", "to_model": MODEL}),
        "this request was routed to gpt-5.6",
    ],
)
def test_model_text_can_neither_prove_nor_forge_identity(text: str) -> None:
    assert launcher.model_identity(stream(STARTED, TURN, message(text)), MODEL) == "UNVERIFIED"
    assert launcher.model_identity(stream(STARTED, TURN, message(text), DONE), MODEL) == "VERIFIED"
    output = {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "aggregated_output": text}}
    assert launcher.model_identity(stream(STARTED, TURN, output, DONE), MODEL) == "VERIFIED"


def test_the_catalog_must_offer_exactly_the_pinned_slug() -> None:
    def catalog(*slugs: str) -> bytes:
        return json.dumps({"models": [{"slug": slug, "visibility": "list"} for slug in slugs]}).encode()

    assert launcher.catalog_offers(catalog("gpt-5.5", MODEL), MODEL)
    assert not launcher.catalog_offers(catalog("gpt-5.6-sol", "gpt-5.6-luna"), MODEL)  # no prefix matching
    assert not launcher.catalog_offers(catalog(MODEL, MODEL), MODEL)  # duplicated → ambiguous
    for forged in (b"", b"not json", b'{"models": {"slug": "gpt-5.6"}}', b'[{"slug": "gpt-5.6"}]'):
        assert not launcher.catalog_offers(forged, MODEL)
    assert not launcher.catalog_offers(b" " * (launcher.MAX_CATALOG + 1), MODEL)


# --- the launcher: catalog before prompt, verdict after the run -----------------------------------------------


class Launch:
    """Answers the launcher's two sandboxed calls (catalog, exec) and records every argv."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.catalog: tuple[int, bytes, bytes] = (0, b"", b"")
        self.exec: tuple[int, bytes, bytes] = (0, b"", b"")

    def __call__(
        self, argv: list[str], env: dict[str, str], *, stdin: bytes = b"", timeout: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        return self.catalog if "debug" in argv else self.exec


@pytest.fixture
def signed_in(monkeypatch: pytest.MonkeyPatch) -> Launch:
    launch = Launch()
    monkeypatch.setattr(launcher, "_run", launch)
    return launch


def _answer(launch: Launch, catalog: tuple[int, bytes, bytes], run: tuple[int, bytes, bytes]) -> None:
    launch.catalog, launch.exec = catalog, run


CATALOG = (0, json.dumps({"models": [{"slug": MODEL}]}).encode(), b"")
REQUEST = {"kind": "run", "workroot_id": "a" * 32, "model": MODEL}


def test_an_unoffered_model_never_receives_the_prompt(signed_in: Launch, tmp_path: Path) -> None:
    _answer(signed_in, (0, json.dumps({"models": [{"slug": "gpt-5.5"}]}).encode(), b""), (0, b"", b""))
    response = launcher._signed_in_run(REQUEST, b"prompt", tmp_path, tmp_path)
    assert response == {"status": "MODEL_UNAVAILABLE"}
    assert [call for call in signed_in.calls if "exec" in call] == []


def test_the_catalog_is_read_in_the_same_sandboxed_session(signed_in: Launch, tmp_path: Path) -> None:
    _answer(signed_in, CATALOG, (0, stream(STARTED, TURN, DONE), b""))
    response = launcher._signed_in_run(REQUEST, b"prompt", tmp_path, tmp_path)
    assert response["model_identity"] == "VERIFIED" and response["exit"] == "ZERO"
    catalog_call, exec_call = signed_in.calls
    assert catalog_call[0] == "/usr/bin/sandbox-exec" and catalog_call[
        catalog_call.index(str(launcher.CODEX_BINARY)) :
    ][1:3] == ["debug", "models"]
    assert exec_call[0] == "/usr/bin/sandbox-exec" and "--json" in exec_call


def test_a_rerouted_run_is_completed_but_never_verified(signed_in: Launch, tmp_path: Path) -> None:
    _answer(signed_in, CATALOG, (0, stream(STARTED, TURN, cli_error(REROUTE_TEXT), DONE), b""))
    assert launcher._signed_in_run(REQUEST, b"prompt", tmp_path, tmp_path)["model_identity"] == "REROUTED"


@pytest.mark.parametrize(
    ("catalog", "run", "expected"),
    [
        ((1, b"", b"unauthorized"), (0, b"", b""), {"status": "MODEL_UNAVAILABLE"}),  # auth lost before the run
        ((71, b"", b""), (0, b"", b""), {"status": "SANDBOX_SETUP_FAILED"}),
        (CATALOG, (71, b"", b""), {"status": "SANDBOX_SETUP_FAILED"}),
    ],
)
def test_authentication_or_setup_failure_never_runs_or_verifies(
    signed_in: Launch, tmp_path: Path, catalog: Any, run: Any, expected: dict[str, Any]
) -> None:
    _answer(signed_in, catalog, run)
    assert launcher._signed_in_run(REQUEST, b"prompt", tmp_path, tmp_path) == expected


def test_a_provider_failure_is_classified_and_unverified(signed_in: Launch, tmp_path: Path) -> None:
    failed = stream(STARTED, TURN, {"type": "turn.failed", "error": {"message": "usage limit"}})
    _answer(signed_in, CATALOG, (1, failed, b"You've hit your usage limit"))
    response = launcher._signed_in_run(REQUEST, b"prompt", tmp_path, tmp_path)
    assert response == {
        "status": "COMPLETED",
        "exit": "NONZERO",
        "stderr_category": "QUOTA_EXCEEDED",
        "model_identity": "UNVERIFIED",
    }


# --- the runner: pinned model, closed verdict -----------------------------------------------------------------


def test_the_repository_pins_the_owner_approved_model() -> None:
    assert mac.load_host_config(ROOT).codex_model == "gpt-5.6"


@pytest.mark.parametrize("pinned", [None, "", "gpt 5.6", 7, "-c"])
def test_an_unpinned_or_malformed_model_keeps_the_fallback_inert(tmp_path: Path, pinned: Any) -> None:
    checkout = provisioned_checkout(tmp_path, codex_model=pinned)
    with pytest.raises(RoleRefused, match="model is not pinned"):
        mac.load_host_config(checkout)


def test_a_model_missing_from_the_pin_is_an_unknown_schema(tmp_path: Path) -> None:
    checkout = provisioned_checkout(tmp_path)
    document = json.loads((checkout / mac.HOST_CONFIG_PATH).read_text())
    del document["codex_model"]
    (checkout / mac.HOST_CONFIG_PATH).write_text(json.dumps(document))
    with pytest.raises(RoleRefused, match="unknown schema"):
        mac.load_host_config(checkout)


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


def test_a_requested_model_other_than_the_pin_never_reaches_the_launcher(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, sent = attested_port(tmp_path, monkeypatch, {"status": "COMPLETED", "exit": "ZERO"})
    before = len(sent)
    with pytest.raises(RoleRefused, match="not the pinned model") as refused:
        _run(port, workroot, "gpt-5.2")
    assert refused.value.code == "EXECUTION_NOT_STARTED" and len(sent) == before


@pytest.mark.parametrize(
    ("identity", "exit_class", "status"),
    [
        ("VERIFIED", "ZERO", 0),
        ("REROUTED", "ZERO", 1),
        ("AMBIGUOUS", "ZERO", 1),
        ("UNVERIFIED", "ZERO", 1),
        (None, "ZERO", 1),  # missing verdict
        ("verified", "ZERO", 1),  # outside the closed vocabulary
        ({"model": "gpt-5.1-codex"}, "ZERO", 1),  # forged structure
        ("VERIFIED", "NONZERO", 1),  # a verified model that failed is still a failure
    ],
)
def test_only_a_verified_identity_with_a_clean_exit_succeeds(
    tmp_path: Path, monkeypatch: Any, identity: Any, exit_class: str, status: int
) -> None:
    response: dict[str, Any] = {"status": "COMPLETED", "exit": exit_class}
    if identity is not None:
        response["model_identity"] = identity
    port, workroot, _ = attested_port(tmp_path, monkeypatch, response)
    assert _run(port, workroot) == status
    closed = isinstance(identity, str) and identity in mac.MODEL_IDENTITIES
    assert port.model_identity == (identity if closed else "AMBIGUOUS")


def test_an_unavailable_model_ran_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, _ = attested_port(tmp_path, monkeypatch, {"status": "MODEL_UNAVAILABLE"})
    with pytest.raises(RoleRefused) as refused:
        _run(port, workroot)
    assert refused.value.code == "EXECUTION_NOT_STARTED"


def test_a_rerouted_fallback_never_seals_a_result(repos: dict[str, Any], tmp_path: Path, monkeypatch: Any) -> None:
    ledger, _primary, second = fx.offer_and_admit(repos)

    def rerouted(workspace: Path, prompt: bytes) -> dict[str, Any]:
        return {**fx.codex_edits(workspace, prompt), "model_identity": "REROUTED"}

    port = fx.mac_port(tmp_path, monkeypatch, rerouted)
    outcome = fx.run(ledger, second, fx.fallback_config(repos), port, mac.WORK_ROOT / fx.WORKROOT_ID)
    # The workspace was edited, yet nothing is collected, sealed or uploaded from a substitute model.
    assert outcome.sealed_result is None and outcome.advisory_code == "PROVIDER_UNAVAILABLE"
