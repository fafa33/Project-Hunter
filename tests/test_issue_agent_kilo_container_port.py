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
    assert calls.index(("logs", "--timestamps", isolation.broker)) < calls.index(("rm", "-f", isolation.broker))


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

    fake = _container_fake(home, agent_exit=returncode)

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


def test_session_fifo_is_rejected_without_waiting_for_writer(tmp_path: Path) -> None:
    directory = tmp_path.joinpath(*kilo_port.KILO_SESSION_STORE[:-1])
    directory.mkdir(parents=True)
    __import__("os").mkfifo(directory / kilo_port.KILO_SESSION_STORE[-1])
    assert kilo_port.kilo_session_outcome(tmp_path)["error"] == "UNREADABLE"


def test_untrusted_recursive_sqlite_view_has_bounded_query_budget(tmp_path: Path) -> None:
    store = _session_store(tmp_path)
    connection = sqlite3.connect(store)
    connection.execute("DROP TABLE message")
    connection.execute(
        "CREATE VIEW message AS WITH RECURSIVE infinite(n) AS "
        "(VALUES(1) UNION ALL SELECT n+1 FROM infinite) "
        "SELECT cast(n as text) AS id, 's' AS session_id, n AS time_created, "
        '\'{"role":"assistant"}\' AS data FROM infinite'
    )
    connection.commit()
    connection.close()
    assert kilo_port.kilo_session_outcome(tmp_path)["error"] == "UNREADABLE"


# --- Issue #577 (run 42465838010): the store behind the model uid's private home is read by a reader container ---

import os  # noqa: E402
import sys  # noqa: E402


def _run_reader_script(home: Path) -> subprocess.CompletedProcess[bytes]:
    """The exact reader script, executed locally with its fixed mount point mapped to ``home``."""

    script = kilo_port._SESSION_STAGE_SCRIPT.replace("cur='/h'", f"cur={str(home)!r}")
    assert script != kilo_port._SESSION_STAGE_SCRIPT
    return subprocess.run(
        [sys.executable, "-I", "-c", script, str(kilo_port._SESSION_STORE_LIMIT)], capture_output=True, check=False
    )


def _is_reader(args: tuple[str, ...]) -> bool:
    return args[0] == "run" and kilo_port.PYTHON_IMAGE in args and kilo_port._SESSION_STAGE_SCRIPT in args


def _container_fake(
    readable_home: Path, *, agent_exit: int, calls: list[tuple[str, ...]] | None = None, stderr: bytes = b""
):
    """Docker as the executor sees it: the agent container exits with ``agent_exit``; the reader container (a
    privileged reader of the model uid's home) runs the real script against ``readable_home``."""

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        if calls is not None:
            calls.append(args)
        if _is_reader(args):
            done = _run_reader_script(readable_home)
            return subprocess.CompletedProcess(args, done.returncode, done.stdout, b"")
        if args[0] == "run" and kilo_port.KILO_IMAGE in args:
            return subprocess.CompletedProcess(args, agent_exit, b"", stderr)
        return subprocess.CompletedProcess(args, 0, b"BROKER_GIT_SNAPSHOT: ready\n" if args[0] == "logs" else b"", b"")

    return fake


def _prepared(tmp_path: Path) -> tuple[KiloContainerIsolation, Path, Path]:
    workspace, home = _roots(tmp_path)
    isolation = KiloContainerIsolation(SCRIPTS)
    isolation.workspace, isolation.home, isolation.started = workspace.resolve(), home.resolve(), True
    return isolation, workspace, home


def _run_agent(isolation: KiloContainerIsolation, workspace: Path) -> int:
    return isolation.run(
        ("kilo", "run"), cwd=workspace, public_env={"KILO_CONFIG_CONTENT": POLICY}, secret_env={}, stdin=b"", timeout=1
    )


def test_the_reader_container_is_offline_read_only_and_holds_only_the_read_search_capability(tmp_path: Path) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    _session_store(home, {"role": "assistant", "finish": "stop"})
    calls: list[tuple[str, ...]] = []
    with patch.object(
        KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=0, calls=calls))
    ):
        isolation.session_outcome()
    (reader,) = calls
    assert _is_reader(reader)
    joined = " ".join(reader)
    assert "--network none" in joined and "--read-only" in reader and "--user 0:0" in joined
    assert "--cap-drop ALL" in joined and "no-new-privileges" in joined
    assert [reader[i + 1] for i, a in enumerate(reader) if a == "--cap-add"] == ["DAC_READ_SEARCH"]
    assert [a for a in reader if a.startswith("type=")] == [f"type=bind,src={home.resolve()},dst=/h,readonly"]
    assert "--env-file" not in reader and "/var/run/docker.sock" not in joined
    assert kilo_port.PYTHON_IMAGE.startswith("python@sha256:")  # pinned by digest


def test_a_completed_session_with_readable_evidence_is_reported(tmp_path: Path) -> None:
    isolation, _, home = _prepared(tmp_path)
    _session_store(home, {"role": "assistant", "finish": "stop"})
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=0))):
        assert isolation.session_outcome() == {
            "error": "none",
            "status": "none",
            "retryable": "none",
            "finish": "stop",
            "tool_errors": "0",
        }


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_store_the_trusted_step_cannot_search_is_still_read_through_the_reader(tmp_path: Path) -> None:
    """The hosted shape: the direct read is UNREADABLE (home not searchable), the reader container is not."""

    isolation, _, home = _prepared(tmp_path)
    readable = tmp_path / "as-seen-by-the-reader"
    _session_store(readable, {"role": "assistant", "error": {"name": "APIError", "data": {"statusCode": 502}}})
    _session_store(home, {"role": "assistant"})
    home.chmod(0o000)
    try:
        assert kilo_port.kilo_session_outcome(home)["error"] == "UNREADABLE"  # run 42465838010's line
        with patch.object(KiloContainerIsolation, "_docker", staticmethod(_container_fake(readable, agent_exit=1))):
            outcome = isolation.session_outcome()
    finally:
        home.chmod(0o700)
    assert outcome["error"] == "APIError" and outcome["status"] == "502"


def test_a_genuinely_incomplete_session_stays_a_failed_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    _session_store(home, {"role": "assistant"})  # the turn never finished
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=1))):
        assert _run_agent(isolation, workspace) == 1  # the exit status, not the record, decides
    out = capsys.readouterr().out
    assert "isolated Kilo session outcome: error=none status=none retryable=none finish=none tool_errors=0" in out
    assert "exit=NONZERO" in out


def test_a_finished_record_never_turns_a_non_zero_exit_into_success(tmp_path: Path) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    _session_store(home, {"role": "assistant", "finish": "stop"})
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=1))):
        assert _run_agent(isolation, workspace) == 1


def test_denied_tool_calls_are_counted_and_reported_with_the_session_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    _session_store(
        home,
        {"role": "assistant", "finish": "tool-calls"},
        parts=({"type": "tool", "state": {"status": "error", "error": "denied: secret path"}},) * 2,
    )
    stderr = "\u2192 Read secret\nDENIED\n\u2192 Bash rm\nDENIED\n".encode()
    with patch.object(
        KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=1, stderr=stderr))
    ):
        assert _run_agent(isolation, workspace) == 1
    out = capsys.readouterr().out
    assert "tool_calls=2 shell=0 denied=2" in out
    assert "error=none status=none retryable=none finish=tool-calls tool_errors=2" in out
    assert "secret path" not in out


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (3, b"", "ABSENT"),
        (4, b"", "UNREADABLE"),
        (137, b"", "UNREADABLE"),
        (0, b"", "UNREADABLE"),
        (0, b"\x00" * 8 + b"\x00" * 8, "UNREADABLE"),  # an empty store is not a store
        (0, (5).to_bytes(8, "big") + b"abc", "UNREADABLE"),  # truncated frame
        (0, (3).to_bytes(8, "big") + b"abc" + (0).to_bytes(8, "big") + b"x", "UNREADABLE"),  # trailing bytes
        (0, (2**40).to_bytes(8, "big"), "UNREADABLE"),  # beyond the bound
        (0, (3).to_bytes(8, "big") + b"abc" + (0).to_bytes(8, "big"), "UNREADABLE"),  # not SQLite
    ],
)
def test_missing_corrupt_or_malformed_reader_output_is_never_evidence(
    tmp_path: Path, returncode: int, stdout: bytes, expected: str
) -> None:
    isolation, _, _ = _prepared(tmp_path)

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        return subprocess.CompletedProcess(args, returncode, stdout, b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        assert isolation.session_outcome()["error"] == expected


def test_the_real_reader_script_refuses_links_fifos_and_missing_stores(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    assert _run_reader_script(missing).returncode == 3
    store = _session_store(tmp_path / "real", {"role": "assistant"})
    linked_dir = tmp_path / "linked-dir"
    linked_dir.joinpath(".local", "share").mkdir(parents=True)
    linked_dir.joinpath(".local", "share", "kilo").symlink_to(store.parent)
    assert _run_reader_script(linked_dir).returncode == 4
    linked_file = tmp_path / "linked-file"
    linked_file.joinpath(*kilo_port.KILO_SESSION_STORE[:-1]).mkdir(parents=True)
    linked_file.joinpath(*kilo_port.KILO_SESSION_STORE).symlink_to(store)
    assert _run_reader_script(linked_file).returncode == 4
    fifo = tmp_path / "fifo"
    fifo.joinpath(*kilo_port.KILO_SESSION_STORE[:-1]).mkdir(parents=True)
    os.mkfifo(fifo.joinpath(*kilo_port.KILO_SESSION_STORE))
    assert _run_reader_script(fifo).returncode == 4
    done = _run_reader_script(tmp_path / "real")
    assert done.returncode == 0 and kilo_port._unframe_session_store(done.stdout) is not None


def test_the_reader_runs_once_per_failed_turn_and_never_reruns_the_model(tmp_path: Path) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    calls: list[tuple[str, ...]] = []

    def failing_reader(*args: str, timeout: float = 40, stdin: bytes = b""):
        calls.append(args)
        if _is_reader(args):
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, 1 if kilo_port.KILO_IMAGE in args else 0, b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(failing_reader)):
        assert _run_agent(isolation, workspace) == 1
    assert sum(kilo_port.KILO_IMAGE in call for call in calls) == 1
    assert sum(_is_reader(call) for call in calls) == 1


def test_a_successful_turn_never_starts_the_reader(tmp_path: Path) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    calls: list[tuple[str, ...]] = []
    with patch.object(
        KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=0, calls=calls))
    ):
        assert _run_agent(isolation, workspace) == 0
    assert not any(_is_reader(call) for call in calls)


def test_the_reader_is_stateless_across_restart(tmp_path: Path) -> None:
    isolation, _, home = _prepared(tmp_path)
    _session_store(home, {"role": "assistant", "error": {"name": "APIError", "data": {}}})
    with patch.object(KiloContainerIsolation, "_docker", staticmethod(_container_fake(home, agent_exit=1))):
        first, second = isolation.session_outcome(), isolation.session_outcome()
    assert first == second and first["error"] == "APIError"
    fresh = KiloContainerIsolation(SCRIPTS)  # a restarted step that never prepared a home has nothing to read
    assert fresh.session_outcome()["error"] == "ABSENT"


# --- Issue #577: the trusted step formats the agent's Python before collection ----------------------------------


def _formatter(tmp_path: Path) -> tuple[KiloContainerIsolation, Path, list[tuple[str, ...]]]:
    isolation = KiloContainerIsolation(Path(kilo_port.__file__).resolve().parents[3] / "scripts" / "issue_agent")
    workspace = tmp_path / "workspace"
    (workspace / "pkg").mkdir(parents=True)
    isolation.workspace = workspace
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, timeout: float = 40, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(list(args), 0, b"", b"")

    isolation._docker = fake  # type: ignore[method-assign]
    return isolation, workspace, calls


def test_the_formatter_runs_black_offline_read_only_as_the_agent_with_the_trusted_config(tmp_path: Path) -> None:
    isolation, workspace, calls = _formatter(tmp_path)
    (workspace / "pkg" / "m.py").write_text("x=1\n")
    assert isolation.format_python(["pkg/m.py"]) == "FORMATTED"
    (args,) = calls
    for flag in (("--network", "none"), ("--user", "65534:65532"), ("--cap-drop", "ALL")):
        assert args[args.index(flag[0]) + 1] == flag[1]
    assert "--read-only" in args and kilo_port.BROKER_IMAGE in args
    trusted = isolation.scripts.parents[1] / "pyproject.toml"
    assert f"type=bind,src={trusted},dst=/opt/hunter-format/pyproject.toml,readonly" in args
    tail = args[args.index(kilo_port.BROKER_IMAGE) + 1 :]
    assert tail == (
        "python",
        "-I",
        "-m",
        "black",
        "--config",
        "/opt/hunter-format/pyproject.toml",
        "--quiet",
        "--",
        "pkg/m.py",
    )


def test_only_regular_python_files_inside_the_workspace_are_passed(tmp_path: Path) -> None:
    isolation, workspace, calls = _formatter(tmp_path)
    (workspace / "pkg" / "m.py").write_text("x=1\n")
    (workspace / "pkg" / "link.py").symlink_to("m.py")
    (workspace / "linked").symlink_to(workspace / "pkg", target_is_directory=True)
    (workspace / "README.md").write_text("x\n")
    (tmp_path / "outside.py").write_text("x=1\n")
    paths = ["pkg/m.py", "pkg/link.py", "linked/m.py", "README.md", "../outside.py", "-q.py", "/etc/x.py", "gone.py"]
    assert isolation.format_python(paths) == "FORMATTED"
    assert calls[0][-2:] == ("--", "pkg/m.py")


@pytest.mark.parametrize("paths", [[], ["README.md"], ["pkg/link.py"]])
def test_nothing_to_format_launches_nothing(tmp_path: Path, paths: list[str]) -> None:
    isolation, workspace, calls = _formatter(tmp_path)
    (workspace / "pkg" / "link.py").symlink_to(tmp_path)
    assert isolation.format_python(paths) == "SKIPPED" and calls == []


def test_the_formatter_never_runs_while_isolated_processes_are_up(tmp_path: Path) -> None:
    isolation, workspace, calls = _formatter(tmp_path)
    (workspace / "pkg" / "m.py").write_text("x=1\n")
    isolation.started = True
    assert isolation.format_python(["pkg/m.py"]) == "SKIPPED" and calls == []


def test_a_formatter_failure_is_reported_never_raised(tmp_path: Path) -> None:
    isolation, workspace, _ = _formatter(tmp_path)
    (workspace / "pkg" / "m.py").write_text("def (\n")

    def refuse(*args: str, timeout: float = 40, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(list(args), 123, b"", b"cannot parse")

    isolation._docker = refuse  # type: ignore[method-assign]
    assert isolation.format_python(["pkg/m.py"]) == "FAILED"


def _hanging_formatter(tmp_path: Path, *, still_running: bool = False, rm_error: bool = False):  # type: ignore[no-untyped-def]
    isolation, workspace, _ = _formatter(tmp_path)
    (workspace / "pkg" / "m.py").write_text("x=1\n")
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, timeout: float = 40, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if args[0] == "run":
            raise subprocess.TimeoutExpired(list(args), timeout)
        if args[0] == "rm" and rm_error:
            raise OSError("daemon unavailable")
        exists = args[:2] == ("container", "inspect") and still_running
        return subprocess.CompletedProcess(list(args), 0 if exists else 1, b"", b"")

    isolation._docker = docker  # type: ignore[method-assign]
    return isolation, calls


def test_a_timed_out_formatter_is_removed_and_proven_gone_before_collection(tmp_path: Path) -> None:
    # PR #702 review: killing the docker CLI leaves a --rm container running, which could race collection.
    isolation, calls = _hanging_formatter(tmp_path)
    assert isolation.format_python(["pkg/m.py"]) == "FAILED"
    name = calls[0][calls[0].index("--name") + 1]
    assert name == f"{isolation.agent}-format"
    assert calls[1:] == [("rm", "-f", name), ("container", "inspect", name)]


@pytest.mark.parametrize("kw", [{"still_running": True}, {"rm_error": True}])
def test_a_formatter_that_cannot_be_proven_stopped_refuses_collection(tmp_path: Path, kw: dict) -> None:
    isolation, _ = _hanging_formatter(tmp_path, **kw)
    with pytest.raises(kilo_port.RoleRefused, match="could not be stopped"):
        isolation.format_python(["pkg/m.py"])


# --- Issue #718: a model timeout reports the same closed diagnostics a non-zero exit does ---------------------


def test_a_model_timeout_reports_its_stderr_outline_and_session_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, workspace, home = _prepared(tmp_path)
    _session_store(
        home, {"role": "assistant", "error": {"name": "APIError", "data": {"statusCode": 429, "isRetryable": True}}}
    )
    calls: list[tuple[str, ...]] = []
    reader = _container_fake(home, agent_exit=0, calls=calls)
    partial = "\u2192 bash ls\n\u2190 bash ls\nError: 429 Too Many Requests SECRET-PROMPT-TEXT\n".encode()

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        if args[0] == "run" and kilo_port.KILO_IMAGE in args:
            calls.append(args)
            raise subprocess.TimeoutExpired("docker", timeout, output=b"partial model text", stderr=partial)
        return reader(*args, timeout=timeout, stdin=stdin)

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(subprocess.TimeoutExpired):
            _run_agent(isolation, workspace)
    out = capsys.readouterr().out
    assert "isolated Kilo timeout diagnostic: stdout_bytes=18 stderr_bytes=" in out
    assert "stderr_category=RATE_LIMIT" in out
    assert "isolated Kilo stderr structure: " in out and "status=429" in out
    assert "isolated Kilo session outcome: error=APIError status=429 retryable=true" in out
    assert "SECRET-PROMPT-TEXT" not in out and "partial model text" not in out and "bash ls" not in out
    # The agent is stopped before its store is read, and nothing feeds a fallback or canary decision.
    agent_run = next(i for i, args in enumerate(calls) if args[0] == "run" and kilo_port.KILO_IMAGE in args)
    removal = calls.index(("rm", "-f", isolation.agent))
    assert agent_run < removal < next(i for i, args in enumerate(calls) if _is_reader(args))
    assert isolation.last_stderr_category is None


def test_a_timeout_without_any_output_or_store_is_still_reported_and_still_a_timeout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, workspace, home = _prepared(tmp_path)

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        if args[0] == "run" and kilo_port.KILO_IMAGE in args:
            raise subprocess.TimeoutExpired("docker", timeout)
        if args[0] == "rm":
            raise OSError("docker went away")  # a failed stop never masks the timeout
        return _container_fake(home, agent_exit=0)(*args, timeout=timeout, stdin=stdin)

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        with pytest.raises(subprocess.TimeoutExpired):
            _run_agent(isolation, workspace)
    out = capsys.readouterr().out
    assert "isolated Kilo timeout diagnostic: stdout_bytes=0 stderr_bytes=0" in out
    assert "isolated Kilo session outcome: error=ABSENT" in out


# --- Issue #718: the broker trace carries Docker's timestamps, so turn time and the broker's end are measurable ---

_T = "2026-11-09T15:18:00.000000000Z "


def _at(seconds: int) -> str:
    minute, second = divmod(seconds, 60)
    return f"2026-11-09T15:{18 + minute:02d}:{second:02d}.500000000Z "


def test_a_timestamped_trace_reports_when_each_command_ran_and_for_how_long() -> None:
    tree = "a" * 64
    log = (
        _T
        + "BROKER_GIT_SNAPSHOT: ready\n"
        + _at(40)
        + "BROKER_RECEIVED: 'python -m pytest -q'\n"
        + _at(70)
        + f"BROKER_EXECUTED: ['python', '-m', 'pytest', '-q'] EXIT: 1 TREE: {tree} STDOUT_BYTES: 9\n"
        + _at(100)
        + "BROKER_RECEIVED: 'cat /etc/passwd'\n"
        + _at(100)
        + "BROKER_DENIED: ShellDenied\n"
        + _at(130)
        + f"BROKER_EXECUTED: ['ls'] EXIT: 0 TREE: {tree} STDOUT_BYTES: 3\n"
        + _at(660)
        + "BROKER_DENIED: BudgetExhausted\n"
        + _at(661)
        + "BROKER_DENIED: SomethingInjected\n"
    )
    trace = kilo_port.parse_broker_trace(log)
    assert [(t["seq"], t["at"], t["dur"]) for t in trace] == [(1, 70, 30), (2, 130, None)]
    assert kilo_port.broker_session_summary(log) == {
        "denied": "BudgetExhausted:1,OTHER:1,ShellDenied:1",
        "last_line_at": "+661s",
    }


def test_an_untimestamped_log_keeps_the_former_trace_and_reports_no_timing() -> None:
    line = f"BROKER_EXECUTED: ['ls'] EXIT: 0 TREE: {'b' * 64} STDOUT_BYTES: 3\n"
    [entry] = kilo_port.parse_broker_trace("BROKER_RECEIVED: 'ls'\n" + line)
    assert entry["at"] is None and entry["dur"] is None and entry["cmd"] == "ls"
    assert kilo_port.broker_session_summary(line) == {"denied": "none", "last_line_at": "none"}


def test_a_forged_timestamp_or_mid_line_marker_never_creates_an_entry() -> None:
    tree = "c" * 64
    log = (
        "2026-13-99T99:99:99Z BROKER_EXECUTED: ['ls'] EXIT: 0 TREE: "
        + tree
        + " STDOUT_BYTES: 1\n"
        + _T
        + "noise BROKER_EXECUTED: ['ls'] EXIT: 0 TREE: "
        + tree
        + " STDOUT_BYTES: 1\n"
    )
    assert kilo_port.parse_broker_trace(log) == []


def test_stop_prints_the_timed_trace_and_the_session_outline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, _, _ = _prepared(tmp_path)
    log = (
        _T
        + "BROKER_GIT_SNAPSHOT: ready\n"
        + _at(5)
        + "BROKER_RECEIVED: 'ls'\n"
        + _at(7)
        + f"BROKER_EXECUTED: ['ls'] EXIT: 0 TREE: {'d' * 64} STDOUT_BYTES: 3\n"
    )

    def fake(*args: str, timeout: float = 40, stdin: bytes = b""):
        return subprocess.CompletedProcess(args, 0, log.encode() if args[0] == "logs" else b"", b"")

    with patch.object(KiloContainerIsolation, "_docker", staticmethod(fake)):
        isolation.stop()
    out = capsys.readouterr().out
    assert "isolated broker trace: seq=1 cmd='ls' exit=0 tree=dddddddddddd stdout_bytes=3 at=+7s dur=2s" in out
    assert (
        f"isolated broker session: executed=1 denied=none last_line_at=+7s deadline={kilo_port.BROKER_DEADLINE_SECONDS}s"
        in out
    )
