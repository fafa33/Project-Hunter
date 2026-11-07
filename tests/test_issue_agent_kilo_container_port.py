"""Runner's Kilo Isolation port must fail closed on wrong runtime or workspace."""

from pathlib import Path

import pytest

from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
from hunter.automation.issue_agent_roles import RoleRefused, require_governed_kilo_isolation


def test_kilo_requires_exact_container_port(tmp_path: Path) -> None:
    isolation = KiloContainerIsolation(Path(__file__).resolve().parents[1] / "scripts/issue_agent")
    require_governed_kilo_isolation(("kilo", "run", "--model", "kilo/kilo-auto/free"), isolation)
    with pytest.raises(RoleRefused, match="not prepared"):
        isolation.run(("kilo", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)


def test_wrong_isolation_refused() -> None:
    with pytest.raises(RoleRefused, match="governed container"):
        require_governed_kilo_isolation(("kilo", "run", "--model", "free"), object())


def test_unrelated_provider_keeps_existing_port() -> None:
    require_governed_kilo_isolation(("opencode", "run"), object())


# --- Issue #682: container boundary invariants (no Docker daemon required) -------------------------------
import json  # noqa: E402
import subprocess  # noqa: E402
from unittest.mock import patch  # noqa: E402

from hunter.automation import issue_agent_kilo_container as kilo_port  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/issue_agent"
POLICY = json.dumps({"agent": {"build": {}}, "instructions": ["x"]})


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace, home = tmp_path / "workspace", tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    return workspace, home


def _recorder(calls: list[tuple[str, ...]]):
    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"BROKER_GIT_SNAPSHOT: ready\n" if args[0] == "logs" else b"", b"")

    return fake


def test_missing_container_runtime_fails_closed_without_host_fallback(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    with patch.object(kilo_port.subprocess, "run", side_effect=FileNotFoundError("docker")) as run:
        with pytest.raises(RoleRefused, match="runtime unavailable") as caught:
            isolation.prepare(workspace, home)
    assert caught.value.code == "EXECUTION_NOT_STARTED"
    assert all(call.args[0][0] == "docker" for call in run.call_args_list)  # nothing else was ever launched
    assert not isolation.started


def test_failed_setup_tears_down_everything(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[:2] == ("volume", "create") else 0, b"", b"boom")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(RoleRefused):
            isolation.prepare(workspace, home)
    removed = {c[-1] for c in calls if c[0] in {"rm", "network", "volume"} and "rm" in c[:2]}
    assert {isolation.agent, isolation.broker, isolation.gateway, isolation.network}.issubset(removed)


def test_prepared_containers_have_no_docker_socket_host_home_or_privileges(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_recorder(calls))):
        isolation.prepare(workspace, home)
    flat = " ".join(" ".join(c) for c in calls)
    assert "docker.sock" not in flat and "--privileged" not in flat and "--network host" not in flat
    assert "--internal" in " ".join(next(c for c in calls if c[:2] == ("network", "create")))
    broker_run = next(c for c in calls if isolation.broker in c and c[0] == "run")
    assert broker_run[broker_run.index("--network") + 1] == "none"
    assert f"type=bind,src={workspace},dst=/workspace,readonly" in broker_run  # broker cannot write the workspace
    assert f"HUNTER_BROKER_MAX_COMMANDS={kilo_port.BROKER_MAX_COMMANDS}" in broker_run
    assert f"HUNTER_BROKER_DEADLINE_SECONDS={kilo_port.BROKER_DEADLINE_SECONDS}" in broker_run
    assert kilo_port.BROKER_DEADLINE_SECONDS < 12 * 60  # strictly inside the model wall clock
    sources = {part.split("src=")[1].split(",")[0] for part in broker_run if part.startswith("type=bind")}
    assert sources == {
        str(workspace),
        str(isolation.scripts / "real_broker_server.py"),
        str(isolation.policy),
    }  # no home directory, Docker socket or any other host path reaches the broker
    for run_call in (c for c in calls if c[0] == "run" and "--rm" not in c):
        assert "--cap-drop" in run_call and "no-new-privileges" in run_call and "--read-only" in run_call


def test_agent_container_is_bounded_and_secrets_stay_out_of_argv(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_recorder(calls))):
        isolation.run(
            ("kilo", "run", "--model", "kilo/x:free"),
            cwd=workspace,
            public_env={"KILO_CONFIG_CONTENT": POLICY},
            secret_env={"KILO_API_KEY": "secret-sentinel-123"},
            stdin=b"prompt",
            timeout=5,
        )
    (agent,) = calls
    joined = " ".join(agent)
    assert "secret-sentinel-123" not in joined and "KILO_API_KEY" not in joined
    assert "docker.sock" not in joined and "--privileged" not in joined
    assert agent[agent.index("--network") + 1] == isolation.network
    for flag in ("--read-only", "--cap-drop", "--pids-limit", "--memory", "--cpus", "--env-file"):
        assert flag in agent, flag
    assert agent[agent.index("--user") + 1] == "65534:65532"  # never root
    assert f"type=volume,src={isolation.socket_volume},dst=/run/hunter,readonly" in agent
    mounts = [agent[i + 1] for i, part in enumerate(agent) if part == "--mount"]
    assert {m.split("dst=")[1].split(",")[0] for m in mounts} == {"/run/hunter", "/workspace", "/hunter-home"}


def test_only_governed_kilo_run_argv_is_accepted(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    for argv in (("bash", "-c", "id"), ("kilo", "auth"), ("sh",), ("kilo",)):
        with pytest.raises(RoleRefused, match="only Kilo"):
            isolation.run(argv, cwd=workspace, public_env={}, secret_env={}, stdin=b"", timeout=1)
    with pytest.raises(RoleRefused, match="not prepared"):  # cwd outside the bound workspace
        isolation.run(("kilo", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)


def test_model_timeout_propagates_and_stop_removes_every_container(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        if args[0] == "run":
            raise subprocess.TimeoutExpired("docker", timeout)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(subprocess.TimeoutExpired):
            isolation.run(
                ("kilo", "run"),
                cwd=workspace,
                public_env={"KILO_CONFIG_CONTENT": POLICY},
                secret_env={},
                stdin=b"",
                timeout=1,
            )
        isolation.stop()
    assert ("rm", "-f", isolation.agent) in calls and ("rm", "-f", isolation.broker) in calls
    assert not isolation.started
    isolation.stop()  # idempotent


def test_host_isolation_is_refused_for_kilo() -> None:
    class SudoIsolation:  # same name as the legacy host launcher, different module
        pass

    with pytest.raises(RoleRefused, match="governed container"):
        require_governed_kilo_isolation(("kilo", "run", "--model", "kilo/x:free"), SudoIsolation())


def test_broker_trace_is_parsed_from_log_without_command_output() -> None:
    tree_a, tree_b = "a" * 64, "b" * 64
    log = (
        "BROKER_RECEIVED: 'python -m pytest -q test_sample.py'\n"
        f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q', 'test_sample.py'] EXIT: 1 TREE: {tree_a} STDOUT_BYTES: 321\n"
        "BROKER_DENIED: ShellDenied\n"
        f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q', 'test_sample.py'] EXIT: 0 TREE: {tree_b} STDOUT_BYTES: 40\n"
        "BROKER_EXECUTED: [__import__('os').system('id')] EXIT: 0 TREE: " + tree_a + " STDOUT_BYTES: 1\n"
        "forged BROKER_EXECUTED: nonsense\n"
    )
    trace = kilo_port.parse_broker_trace(log)
    assert [(t["seq"], t["exit"], t["tree"]) for t in trace] == [(1, 1, tree_a), (2, 0, tree_b)]
    assert trace[0]["cmd"] == "python -m pytest -q test_sample.py" and trace[0]["stdout_bytes"] == 321


def test_stop_collects_broker_trace_before_removal(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    calls: list[tuple[str, ...]] = []
    line = f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q'] EXIT: 0 TREE: {'c' * 64} STDOUT_BYTES: 5\n"

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, line.encode() if args[0] == "logs" else b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        isolation.stop()
    assert len(isolation.trace) == 1
    assert calls.index(("logs", isolation.broker)) < calls.index(("rm", "-f", isolation.broker))


def test_agent_is_not_started_until_the_broker_snapshot_is_ready(tmp_path: Path) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    calls: list[tuple[str, ...]] = []

    def never_ready(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with (
        patch.object(KiloContainerIsolation, "_docker", staticmethod(never_ready)),
        patch.object(kilo_port.time, "sleep"),
    ):
        with pytest.raises(RoleRefused, match="did not become ready"):
            isolation.prepare(workspace, home)
    assert not isolation.started  # torn down: the agent was never launched
    assert sum(1 for c in calls if c[:2] == ("logs", isolation.broker)) >= 60


# --- Issue #577: a non-zero Kilo exit is attributable from a content-free stderr outline -------------------------

_ESC = "\x1b"
_LOCAL_SUCCESS_SHAPE = (
    "(node:1) [UNDICI-EHPA] Warning: EnvHttpProxyAgent is experimental\n"
    f"{_ESC}[0m\n> code · nvidia/nemotron-3-ultra-550b-a55b:free\n{_ESC}[0m\n"
    f"{_ESC}[0m→ {_ESC}[0mRead src/hunter/automation/issue_agent_control.py\n"
    f"{_ESC}[0m← {_ESC}[0mEdit tests/test_issue_agent_control.py\n"
    "+    assert run['repository']['id'] == 503\n"  # diff text: numbers outside an Error line are never reported
    "$ python -m pytest tests/test_issue_agent_control.py\n\nDENIED\n"
).encode()


def test_stderr_structure_counts_the_cli_line_shapes_only() -> None:
    assert kilo_port.stderr_structure(_LOCAL_SUCCESS_SHAPE) == {
        "tool_calls": "2",
        "shell": "1",
        "denied": "1",
        "error_lines": "0",
        "stack_frames": "0",
        "http_status": "none",
    }


@pytest.mark.parametrize(
    "error,status",
    [
        (f"{_ESC}[91mError: {_ESC}[0mAI_APICallError: Too Many Requests (429)", "429"),
        ("Error: upstream returned 503 for request 4040404", "503"),
        ("Error: request 404040 failed with code 418", "none"),  # unlisted status and embedded digits stay unreported
        ("Error: something unexpected", "none"),
    ],
)
def test_stderr_structure_reports_only_an_allowlisted_status_from_an_error_line(error: str, status: str) -> None:
    sample = (f"> code · m\n{error}\n    at fetch (/app/node_modules/x.js:1:2)\n").encode()
    outline = kilo_port.stderr_structure(sample)
    assert outline["error_lines"] == "1" and outline["stack_frames"] == "1" and outline["http_status"] == status


def test_stderr_structure_never_echoes_stderr_content() -> None:
    sample = b"Error: key sk-live-SECRET123 rejected for /workspace/private.py 429\n$ cat ~/.netrc\n"
    rendered = " ".join(f"{k}={v}" for k, v in kilo_port.stderr_structure(sample).items())
    for fragment in ("SECRET", "sk-live", "private.py", "netrc", "rejected", "cat"):
        assert fragment not in rendered
    assert set(rendered.replace("=", " ").split()) <= {
        "tool_calls",
        "shell",
        "denied",
        "error_lines",
        "stack_frames",
        "http_status",
        "none",
        "0",
        "1",
        "429",
    }


@pytest.mark.parametrize("returncode,printed", [(1, True), (0, False)])
def test_only_a_non_zero_exit_prints_the_stderr_outline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], returncode: int, printed: bool
) -> None:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        stderr = b"> code \xc2\xb7 m\nError: provider said no (key sk-SECRET) 502\n" if args[0] == "run" else b""
        return subprocess.CompletedProcess(args, returncode if args[0] == "run" else 0, b"", stderr)

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        status = isolation.run(
            ("kilo", "run"),
            cwd=workspace,
            public_env={"KILO_CONFIG_CONTENT": POLICY},
            secret_env={},
            stdin=b"",
            timeout=1,
        )
    out = capsys.readouterr().out
    assert status == returncode and "sk-SECRET" not in out and "provider said no" not in out
    assert (
        "isolated Kilo stderr structure: tool_calls=0 shell=0 denied=0 error_lines=1 stack_frames=0 "
        "http_status=502" in out
    ) is printed
    assert "stderr_category=UNCLASSIFIED" in out  # the closed category and its fallback consequence are unchanged


# --- Issue #577: the CLI's own closed record of why the last turn ended -------------------------------------------

import sqlite3  # noqa: E402


def _session_store(home: Path, *messages: dict, parts: tuple[dict, ...] = (), wal: bool = False) -> Path:
    store_dir = home.joinpath(*kilo_port.KILO_SESSION_STORE[:-1])
    store_dir.mkdir(parents=True, exist_ok=True)
    store = store_dir / kilo_port.KILO_SESSION_STORE[-1]
    connection = sqlite3.connect(store)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE message (id text, session_id text, time_created integer, data text)")
    connection.execute("CREATE TABLE part (id text, message_id text, session_id text, time_created integer, data text)")
    for index, message in enumerate(messages):
        connection.execute("INSERT INTO message VALUES (?, 's', ?, ?)", (f"m{index}", index, json.dumps(message)))
    for index, part in enumerate(parts):
        connection.execute("INSERT INTO part VALUES (?, 'm0', 's', ?, ?)", (f"p{index}", index, json.dumps(part)))
    connection.commit()
    if not wal:
        connection.close()
    return store


def test_the_last_assistant_turn_reports_its_closed_error_status_and_retryability(tmp_path: Path) -> None:
    _session_store(
        tmp_path,
        {"role": "user"},
        {"role": "assistant", "finish": "tool-calls"},
        {
            "role": "assistant",
            "error": {
                "name": "APIError",
                "data": {"message": "upstream said sk-SECRET", "statusCode": 503, "isRetryable": True},
            },
        },
        parts=(
            {"type": "tool", "state": {"status": "error", "error": "oldString not found"}},
            {"type": "tool", "state": {"status": "completed"}},
        ),
    )
    outcome = kilo_port.kilo_session_outcome(tmp_path)
    assert outcome == {"error": "APIError", "status": "503", "retryable": "true", "finish": "none", "tool_errors": "1"}
    assert "SECRET" not in json.dumps(outcome) and "oldString" not in json.dumps(outcome)


@pytest.mark.parametrize(
    "message,expected",
    [
        ({"role": "assistant", "finish": "stop"}, {"error": "none", "finish": "stop"}),
        ({"role": "assistant", "finish": "length"}, {"error": "none", "finish": "length"}),
        (
            {"role": "assistant", "error": {"name": "Injected sk-SECRET text", "data": {"statusCode": 418}}},
            {"error": "OTHER", "status": "none"},
        ),
        (
            {
                "role": "assistant",
                "error": {"name": "UnknownError", "data": {"statusCode": "503"}},
                "finish": "free text",
            },
            {"error": "UnknownError", "status": "none", "finish": "OTHER"},
        ),
        (
            {"role": "assistant", "error": {"name": "MessageAbortedError", "data": {"isRetryable": "yes"}}},
            {"error": "MessageAbortedError", "retryable": "none"},
        ),
    ],
)
def test_anything_outside_the_closed_vocabulary_is_never_echoed(tmp_path: Path, message: dict, expected: dict) -> None:
    _session_store(tmp_path, message)
    outcome = kilo_port.kilo_session_outcome(tmp_path)
    assert {key: outcome[key] for key in expected} == expected
    assert "SECRET" not in json.dumps(outcome) and "free text" not in json.dumps(outcome)


def test_an_uncheckpointed_write_ahead_log_is_still_read_from_the_private_copy(tmp_path: Path) -> None:
    store = _session_store(tmp_path, {"role": "assistant", "error": {"name": "APIError", "data": {}}}, wal=True)
    assert store.with_name(store.name + "-wal").stat().st_size > 0
    assert kilo_port.kilo_session_outcome(tmp_path)["error"] == "APIError"


def test_a_missing_store_is_absent(tmp_path: Path) -> None:
    assert kilo_port.kilo_session_outcome(tmp_path)["error"] == "ABSENT"


def test_a_linked_store_or_directory_is_never_followed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    _session_store(elsewhere, {"role": "assistant", "error": {"name": "APIError", "data": {}}})
    linked_file = tmp_path / "file-home"
    linked_file.joinpath(*kilo_port.KILO_SESSION_STORE[:-1]).mkdir(parents=True)
    linked_file.joinpath(*kilo_port.KILO_SESSION_STORE).symlink_to(elsewhere.joinpath(*kilo_port.KILO_SESSION_STORE))
    assert kilo_port.kilo_session_outcome(linked_file)["error"] == "UNREADABLE"
    linked_dir = tmp_path / "dir-home"
    linked_dir.joinpath(".local", "share").mkdir(parents=True)
    linked_dir.joinpath(".local", "share", "kilo").symlink_to(elsewhere.joinpath(".local", "share", "kilo"))
    assert kilo_port.kilo_session_outcome(linked_dir)["error"] == "UNREADABLE"


def test_a_corrupt_or_oversized_store_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path.joinpath(*kilo_port.KILO_SESSION_STORE)
    store.parent.mkdir(parents=True)
    store.write_bytes(b"not a database")
    assert kilo_port.kilo_session_outcome(tmp_path)["error"] == "UNREADABLE"
    _session_store(tmp_path / "big", {"role": "assistant"})
    monkeypatch.setattr(kilo_port, "_SESSION_STORE_LIMIT", 16)
    assert kilo_port.kilo_session_outcome(tmp_path / "big")["error"] == "UNREADABLE"


@pytest.mark.parametrize("returncode,printed", [(1, True), (0, False)])
def test_only_a_non_zero_exit_prints_the_session_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], returncode: int, printed: bool
) -> None:
    workspace, home = _roots(tmp_path)
    _session_store(home, {"role": "assistant", "error": {"name": "APIError", "data": {"statusCode": 502}}})
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        return subprocess.CompletedProcess(args, returncode if args[0] == "run" else 0, b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        isolation.run(
            ("kilo", "run"),
            cwd=workspace,
            public_env={"KILO_CONFIG_CONTENT": POLICY},
            secret_env={},
            stdin=b"",
            timeout=1,
        )
    out = capsys.readouterr().out
    line = "isolated Kilo session outcome: error=APIError status=502 retryable=none finish=none tool_errors=0"
    assert (line in out) is printed
    assert "stderr_category=UNCLASSIFIED" in out  # the closed category and the F2 admission it drives are unchanged
