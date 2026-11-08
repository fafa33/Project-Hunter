"""Adversarial checks for the real container broker command boundary."""

import json
import socket
import struct

# The preflight uses the pytest console entrypoint, which does not add the
# checkout root to sys.path. Resolve only this checkout, not a global path.
import sys
from importlib import import_module
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import Mock, patch

handle = import_module("scripts.issue_agent.real_broker_server").handle

socket.SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)  # type: ignore[misc]


def fake_conn(uid, payload):
    conn = Mock(spec=socket.socket)
    conn.getsockopt.return_value = struct.pack("3i", 100, uid, 100)
    conn.recv.return_value = payload
    return conn


def reply(conn):
    return json.loads(conn.sendall.call_args.args[0])


def test_wrong_peer_is_denied():
    conn = fake_conn(65531, b'{"command":"python -m pytest -q"}')
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        handle(conn)
        run.assert_not_called()
    assert reply(conn)["exit"] == 126


def test_forbidden_command_is_denied():
    conn = fake_conn(65534, b'{"command":"bash -c id"}')
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        handle(conn)
        run.assert_not_called()
    assert reply(conn)["exit"] == 126


def test_oversize_request_is_denied():
    conn = fake_conn(65534, b"x" * 4097)
    handle(conn)
    assert reply(conn)["exit"] == 126


def test_unknown_fields_are_denied():
    conn = fake_conn(65534, b'{"command":"python -m pytest -q","uid":65534}')
    handle(conn)
    assert reply(conn)["exit"] == 126


def test_adversarial_command_payloads_are_denied():
    forbidden = [
        "python -m pytest -q; id",
        "python -m pytest -q && id",
        "python -m pytest -q $(id)",
        "python -m pytest -q ../test_sample.py",
        "python -m pytest -q /etc/passwd",
        'python -c "import os; os.system("id")"',
        "env",
        "curl https://example.com",
        "git push origin main",
        "python -m pip install pytest",
        "python -m pytest -q test_sample.py --override-ini=python_files=evil.py",
    ]
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        for command in forbidden:
            conn = fake_conn(65534, json.dumps({"command": command}).encode())
            handle(conn)
            assert reply(conn)["exit"] == 126, command
        run.assert_not_called()


@pytest.mark.parametrize("command", ["ls -la", "cat tests/test_sample.py", "rg -n error src"])
def test_read_only_discovery_uses_broker_workspace_and_clean_environment(command):
    conn = fake_conn(65534, json.dumps({"command": command}).encode())
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""
        handle(conn)
        assert reply(conn)["exit"] == 0
        assert run.call_args.kwargs["cwd"] == "/workspace"
        assert "GITHUB_TOKEN" not in run.call_args.kwargs["env"]


def test_broker_subprocess_receives_sanitized_environment():
    conn = fake_conn(65534, b'{"command":"python -m pytest -q test_sample.py"}')
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "1 passed"
        run.return_value.stderr = ""
        handle(conn)
        kwargs = run.call_args.kwargs
        assert kwargs["cwd"] == "/workspace"
        assert kwargs["timeout"] == 30
        assert kwargs["env"].get("HOME") is None
        assert kwargs["env"].get("GITHUB_TOKEN") is None
        assert kwargs["env"]["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


# --- Issue #682: shared policy, explicit repair budgets, workspace re-checks -------------------------------
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

broker = import_module("scripts.issue_agent.real_broker_server")
RUN = "scripts.issue_agent.real_broker_server.subprocess.run"


def _send(command, budget=None, uid=65534):
    conn = fake_conn(uid, json.dumps({"command": command}).encode())
    broker.handle(conn, budget)
    return reply(conn)


@pytest.mark.parametrize("command", ["python -m ruff check .", "python -m black --check .", "python -m pytest -q"])
def test_task_scoped_checks_are_accepted_with_limits(command, monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 0, "ok", ""
        assert _send(command)["exit"] == 0
        assert run.call_args.kwargs["preexec_fn"] is broker._limits
        assert run.call_args.kwargs["timeout"] == broker.COMMAND_TIMEOUT


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",  # allowed by policy but no git is provisioned in the broker
        "git diff --stat",
        "python -m ruff check --config /etc/passwd .",
        "python -m ruff check --fix .",
        "python -m black --check -c print(1)",
        "python -m pytest -q -p evil_plugin",
        "python -m pytest -q --pdb",
        "python -m pytest -q --rootdir=/",
        "python -m pytest -q conftest.py;id",
        "python -m pytest -q\nid",
        "python3 -c 'import socket'",
        "python -m http.server",
        "python -m pip install requests",
        "sh -c id",
        "/bin/sh",
        "python -m pytest -q -- ../../etc",
        "python -m pytest -q ~/x",
        "python -m pytest -q $HOME",
        "python -m pytest -q `id`",
    ],
)
def test_interpreter_escape_and_chaining_denied(command):
    with patch(RUN) as run:
        assert _send(command)["exit"] == 126, command
        run.assert_not_called()


def test_symlink_in_workspace_is_denied_before_execution(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("x = 1\n")
    broker.assert_no_symlinks(str(tmp_path))
    (tmp_path / "escape").symlink_to("/etc")
    with pytest.raises(broker.ShellDenied, match="symlink"):
        broker.assert_no_symlinks(str(tmp_path))
    real = broker.assert_no_symlinks
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: real(str(tmp_path)))
    with patch(RUN) as run:
        assert _send("python -m pytest -q")["exit"] == 126
        run.assert_not_called()


def test_nested_symlink_to_file_is_denied(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "secret").write_text("s")
    (tmp_path / "pkg" / "link.py").symlink_to(tmp_path / "secret")
    with pytest.raises(broker.ShellDenied):
        broker.assert_no_symlinks(str(tmp_path))


def test_repair_attempt_budget_exhaustion_fails_closed(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    budget = broker.Budget(commands=3, requests=50)
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 1, "", ""
        results = [_send("python -m pytest -q", budget)["exit"] for _ in range(5)]
        assert run.call_count == 3
    assert results == [1, 1, 1, 126, 126]


def test_denied_probes_consume_request_budget(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    budget = broker.Budget(commands=10, requests=3)
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 0, "", ""
        for _ in range(3):
            assert _send("curl https://example.com", budget)["exit"] == 126
        # A legitimate command after the probe flood is refused: the session is spent.
        assert _send("python -m pytest -q", budget)["exit"] == 126
        run.assert_not_called()


def test_wall_clock_deadline_fails_closed(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    budget = broker.Budget(deadline=time.monotonic() - 1)
    with patch(RUN) as run:
        assert _send("python -m pytest -q", budget)["exit"] == 126
        run.assert_not_called()


def test_command_timeout_is_denied_and_still_consumes_attempt(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    budget = broker.Budget(commands=2, requests=10)
    with patch(RUN, side_effect=subprocess.TimeoutExpired("python", 30)):
        assert _send("python -m pytest -q", budget)["exit"] == 126
    assert budget.commands == 1


def test_wrong_peer_does_not_spend_budget():
    budget = broker.Budget(commands=1, requests=1)
    assert _send("python -m pytest -q", budget, uid=0)["exit"] == 126
    assert (budget.commands, budget.requests) == (1, 1)


def test_host_secrets_never_reach_command_environment(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    for name in ("GITHUB_TOKEN", "KILO_API_KEY", "HUNTER_SIGNING_KEY", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setitem(os.environ, name, "leak-sentinel")
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 0, "", ""
        _send("python -m pytest -q")
        env = run.call_args.kwargs["env"]
    assert "leak-sentinel" not in env.values()
    assert set(env) == {"PATH", "PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS", "RUFF_NO_CACHE", "BLACK_CACHE_DIR"}
    assert env["BLACK_CACHE_DIR"] != "/tmp/black-cache"


def test_budget_constants_are_bounded():
    assert 1 <= broker.MAX_COMMANDS <= 32
    assert broker.MAX_REQUESTS >= broker.MAX_COMMANDS
    assert broker.DEADLINE_SECONDS <= 900


def test_black_cache_is_private_unique_and_removed(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    seen = []

    def fake_run(*_args, **kwargs):
        cache = kwargs["env"]["BLACK_CACHE_DIR"]
        seen.append((cache, oct(os.stat(cache).st_mode & 0o777), os.stat(cache).st_uid))
        return subprocess.CompletedProcess(_args, 0, "", "")

    with patch(RUN, side_effect=fake_run):
        _send("python -m black --check .")
        _send("python -m black --check .")
    assert len({cache for cache, _, _ in seen}) == 2  # unpredictable and not shared between commands
    for cache, mode, uid in seen:
        assert mode == "0o700" and uid == os.geteuid()
        assert not cache.startswith("/tmp/black") and cache != "/tmp/black-cache"
        assert not os.path.exists(cache)  # cleaned up after the command


def test_deadline_is_rechecked_after_receive_and_caps_command_timeout(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    # The session expires while the payload is being received: nothing may execute.
    budget = broker.Budget(deadline=time.monotonic() + 100)
    conn = fake_conn(65534, b'{"command":"python -m pytest -q"}')

    def slow_recv(_size):
        budget.deadline = time.monotonic() - 1
        return b'{"command":"python -m pytest -q"}'

    conn.recv.side_effect = slow_recv
    with patch(RUN) as run:
        broker.handle(conn, budget)
        run.assert_not_called()
    assert reply(conn)["exit"] == 126
    # A nearly-spent session bounds the subprocess to the remaining wall clock.
    budget = broker.Budget(deadline=time.monotonic() + 5)
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 0, "", ""
        assert _send("python -m pytest -q", budget)["exit"] == 0
        assert run.call_args.kwargs["timeout"] <= 5


def test_every_broker_launcher_provisions_the_shared_policy():
    root = Path(__file__).resolve().parents[1]
    launchers = [
        path
        for path in [
            *(root / "scripts/issue_agent").glob("*.py"),
            root / "src/hunter/automation/issue_agent_kilo_container.py",
        ]
        if "real_broker_server.py" in path.read_text() and path.name != "real_broker_server.py"
    ]
    assert len(launchers) >= 4
    for path in launchers:
        text = path.read_text()
        assert "issue_agent_governed_shell.py" in text, path.name
        assert "dst=/opt/policy/issue_agent_governed_shell.py,readonly" in text, path.name
        assert "PYTHONPATH=/opt/policy" in text, path.name


def test_executed_commands_log_a_workspace_fingerprint_that_changes_with_edits(tmp_path, monkeypatch, capsys):
    (tmp_path / "test_sample.py").write_text("assert 2 + 2 == 5\n")
    before = broker.workspace_fingerprint(str(tmp_path))
    assert before == broker.workspace_fingerprint(str(tmp_path))  # deterministic
    (tmp_path / "test_sample.py").write_text("assert 2 + 2 == 4\n")
    assert broker.workspace_fingerprint(str(tmp_path)) != before  # an edit is visible
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    monkeypatch.setattr(broker, "workspace_fingerprint", lambda: "f" * 64)
    with patch(RUN) as run:
        run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 1, "failed", ""
        _send("python -m pytest -q test_sample.py")
    assert f"EXIT: 1 TREE: {'f' * 64} STDOUT_BYTES: 6" in capsys.readouterr().out


# --- Issue #682: read-only task-scoped git and resource enforcement (real git / real subprocesses) --------
import shutil  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


@pytest.fixture
def git_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("VALUE = 1\n")
    (workspace / "test_app.py").write_text("from app import VALUE\n\ndef test_v():\n    assert VALUE == 1\n")
    monkeypatch.setattr(broker, "WORKSPACE", str(workspace))
    git_dir = broker.init_git_snapshot(str(workspace))
    assert git_dir is not None
    monkeypatch.setattr(broker, "GIT_DIR", git_dir)
    yield workspace, Path(git_dir)
    shutil.rmtree(git_dir, ignore_errors=True)


@needs_git
def test_git_status_and_diff_show_agent_edits_without_writing_the_workspace(git_workspace):
    workspace, git_dir = git_workspace
    assert not git_dir.is_relative_to(workspace) and oct(git_dir.stat().st_mode & 0o777) == "0o700"
    (workspace / "app.py").write_text("VALUE = 42\n")
    (workspace / "new.py").write_text("x = 1\n")
    status = _send("git status --short")
    assert status["exit"] == 0 and " M app.py" in status["stdout"] and "?? new.py" in status["stdout"]
    stat = _send("git diff --stat")
    assert stat["exit"] == 0 and "app.py" in stat["stdout"]
    diff = _send("git diff")
    assert "-VALUE = 1" in diff["stdout"] and "+VALUE = 42" in diff["stdout"]
    assert not (workspace / ".git").exists()  # nothing was created in the (agent-visible) workspace


@needs_git
def test_workspace_controlled_git_config_hooks_and_drivers_never_execute(git_workspace, tmp_path):
    workspace, _ = git_workspace
    sentinel = tmp_path / "executed"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    script.chmod(0o755)
    # An agent-planted repository inside the workspace: config, hooks, attributes, external diff / textconv.
    planted = workspace / ".git"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True, env=broker.GIT_ENV, capture_output=True)
    (planted / "hooks").mkdir(parents=True, exist_ok=True)
    planted.joinpath("config").write_text(
        f"[core]\n\tfsmonitor = {script}\n\tpager = {script}\n\thooksPath = {planted / 'hooks'}\n"
        f'[diff "evil"]\n\tcommand = {script}\n\ttextconv = {script}\n[filter "evil"]\n\tclean = {script}\n'
    )
    (planted / "hooks" / "post-index-change").write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    (planted / "hooks" / "post-index-change").chmod(0o755)
    (workspace / ".gitattributes").write_text("* diff=evil filter=evil\n")
    (workspace / "app.py").write_text("VALUE = 7\n")
    for command in ("git status --short", "git diff", "git diff --stat"):
        _send(command)
    assert not sentinel.exists(), "workspace-controlled configuration executed code"


@needs_git
@pytest.mark.parametrize(
    "command",
    [
        "git log",
        "git show HEAD",
        "git push",
        "git commit -m x",
        "git config --list",
        "git checkout .",
        "git diff --no-index app.py test_app.py",
        "git diff --output=out.txt",
        "git status --ignored",
        "git -c core.fsmonitor=evil status",
        "git --git-dir=/etc status",
        "git clean -fd",
        "git stash",
        "git remote -v",
        "git status; id",
        "git status && id",
        "git diff /etc/passwd",
        "git diff ../outside",
        "git status $(id)",
    ],
)
def test_git_cannot_escape_the_allowlist(git_workspace, command):
    with patch(RUN) as run:
        assert _send(command)["exit"] == 126, command
        run.assert_not_called()


def test_git_fails_closed_when_the_snapshot_is_unavailable(monkeypatch):
    monkeypatch.setattr(broker, "GIT_DIR", None)
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    with patch(RUN) as run:
        assert _send("git status --short")["exit"] == 126
        run.assert_not_called()


def test_snapshot_failure_returns_none_and_removes_the_private_directory(tmp_path):
    created = []
    real = broker.tempfile.mkdtemp

    def tracking(*args, **kwargs):
        created.append(real(*args, **kwargs))
        return created[-1]

    with patch.object(broker.tempfile, "mkdtemp", tracking), patch(RUN, side_effect=OSError("no git")):
        assert broker.init_git_snapshot(str(tmp_path)) is None
    assert created and not os.path.exists(created[0])


def test_git_argv_is_hardened_and_only_status_or_diff():
    argv = broker.git_argv(["git", "diff", "--stat"], "/g", "/w")
    assert argv[:2] == ["git", "--no-pager"] and "core.hooksPath=/dev/null" in argv and "core.fsmonitor=false" in argv
    assert argv[argv.index("diff") :] == ["diff", "--no-ext-diff", "--no-textconv", "--stat"]
    assert "--git-dir=/g" in argv and "--work-tree=/w" in argv
    assert broker.git_argv(["git", "status"], "/g", "/w")[-1] == "status"
    for sub in ("log", "show", "config", "push", "checkout"):
        with pytest.raises(broker.ShellDenied):
            broker.git_argv(["git", sub], "/g", "/w")
    assert broker.GIT_ENV["GIT_CONFIG_GLOBAL"] == os.devnull and broker.GIT_ENV["GIT_CONFIG_NOSYSTEM"] == "1"


def _run_limited(code, monkeypatch, cpu=None):
    if cpu is not None:
        monkeypatch.setattr(broker, "COMMAND_TIMEOUT", cpu)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, preexec_fn=broker._limits, check=False
    )


def test_cpu_limit_kills_a_busy_loop(monkeypatch):
    monkeypatch.setattr(broker, "CPU_GRACE_SECONDS", 0)
    started = time.monotonic()
    result = _run_limited("while True: pass", monkeypatch, cpu=1)
    assert result.returncode in (-signal.SIGXCPU, -signal.SIGKILL) and time.monotonic() - started < 15


def test_cpu_backstop_sits_above_the_wall_timeout_so_the_timeout_wins_deterministically():
    assert broker.CPU_GRACE_SECONDS > 0
    limits = {}
    with patch.object(broker.resource, "setrlimit", lambda kind, value: limits.setdefault(kind, value)):
        broker._limits()
    assert limits[broker.resource.RLIMIT_CPU][0] == broker.COMMAND_TIMEOUT + broker.CPU_GRACE_SECONDS


def test_memory_limit_stops_a_large_allocation(monkeypatch):
    monkeypatch.setattr(broker, "MEMORY_LIMIT_BYTES", 512 * 1024**2)
    result = _run_limited("x = bytearray(2 * 1024**3)", monkeypatch)
    assert result.returncode != 0 and "MemoryError" in result.stderr


def test_file_size_limit_stops_unbounded_writes(monkeypatch, tmp_path):
    target = tmp_path / "big"
    result = _run_limited(f"open({str(target)!r}, 'wb').write(b'x' * (11 * 1024 * 1024))", monkeypatch)
    assert result.returncode == -signal.SIGXFSZ or result.returncode != 0
    assert not target.exists() or target.stat().st_size <= 10 * 1024 * 1024


@pytest.mark.skipif(os.geteuid() == 0, reason="RLIMIT_NPROC is not enforced for root; proven in the hosted run")
def test_process_limit_stops_a_fork_flood(monkeypatch):
    code = "import subprocess,sys\nn=0\nfor _ in range(300):\n  try: subprocess.Popen(['sleep','5']); n+=1\n  except OSError: break\nprint(n)"
    result = _run_limited(code, monkeypatch)
    assert int(result.stdout.strip() or 0) < 100


def test_real_command_timeout_is_denied_and_the_broker_keeps_serving(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "test_busy.py").write_text("def test_busy():\n    while True:\n        pass\n")
    (workspace / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    monkeypatch.setattr(broker, "WORKSPACE", str(workspace))
    monkeypatch.setattr(broker, "COMMAND_TIMEOUT", 2)
    # Hosted runners keep their interpreter (with pytest) outside the container PATH; the command still goes through
    # the real allowlist, sanitized environment and limits.
    monkeypatch.setattr(broker, "TOOL_PATH", f"{os.path.dirname(sys.executable)}:{broker.TOOL_PATH}")
    budget = broker.Budget(commands=4, requests=10)
    started = time.monotonic()
    assert _send("python -m pytest -q test_busy.py", budget)["exit"] == 126
    assert time.monotonic() - started < 20
    assert _send("python -m pytest -q test_ok.py", budget)["exit"] == 0  # fail closed per command, not per session


def test_cache_directory_is_removed_even_when_the_command_times_out(monkeypatch):
    monkeypatch.setattr(broker, "assert_no_symlinks", lambda: None)
    seen = []

    def boom(*_args, **kwargs):
        seen.append(kwargs["env"]["BLACK_CACHE_DIR"])
        raise subprocess.TimeoutExpired("python", 1)

    with patch(RUN, side_effect=boom):
        assert _send("python -m black --check .")["exit"] == 126
    assert seen and not os.path.exists(seen[0])


# --- Issue #577: an idle broker keeps serving until its session deadline ---------------------------------------


class _Server:
    """Accept times out twice (an agent still reading), then one request arrives, then the deadline passes."""

    def __init__(self, clock: list[float]) -> None:
        self.clock, self.accepts, self.timeouts, self.closed = clock, 0, [], False

    def bind(self, path: str) -> None:
        pass

    def listen(self, backlog: int) -> None:
        pass

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def accept(self):  # type: ignore[no-untyped-def]
        self.accepts += 1
        if self.accepts <= 2:
            self.clock[0] += 120
            raise TimeoutError
        if self.accepts == 3:
            return _Conn(), None
        self.clock[0] += 10_000
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


class _Conn:
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def test_the_broker_survives_idle_accept_timeouts_until_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    server = _Server(clock)
    handled: list[object] = []
    monkeypatch.setattr(broker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(broker.socket, "socket", lambda *a: server)
    monkeypatch.setattr(broker.os, "geteuid", lambda: broker.BROKER_UID)
    monkeypatch.setattr(broker.os, "chmod", lambda *a: None)
    monkeypatch.setattr(broker, "init_git_snapshot", lambda: None)
    monkeypatch.setattr(broker, "handle", lambda conn, budget: handled.append(conn))
    broker.main()
    # Two idle windows (240 s with no command) did not end the session: the late request was still served.
    assert server.accepts == 4 and len(handled) == 1 and server.closed
    assert all(timeout <= 120 for timeout in server.timeouts)


# --- Issue #577: a refusal tells the agent why, in Hunter's own fixed words -------------------------------------


@pytest.mark.parametrize(
    "command,reason",
    [
        ("cd x && python -m pytest -q", "compound or redirected shell command"),
        ("python -m pytest -x tests", "unapproved flag"),
        ("pwd", "command not allowlisted"),
        ("cat /etc/passwd", "path outside task workspace"),
    ],
)
def test_a_policy_refusal_names_its_fixed_reason_and_the_allowed_forms(command: str, reason: str) -> None:
    conn = fake_conn(65534, json.dumps({"command": command}).encode())
    with patch("scripts.issue_agent.real_broker_server.subprocess.run") as run:
        handle(conn)
        run.assert_not_called()
    response = reply(conn)
    lines = response["stderr"].splitlines()
    assert response["exit"] == 126 and lines[0] == "DENIED"
    assert lines[1] == f"reason: {reason}" and lines[2] == broker.ALLOWED_FORMS


def test_nothing_the_agent_sent_is_echoed_in_a_refusal() -> None:
    conn = fake_conn(65534, b'{"command":"python -m pytest SECRET-TOKEN-123 | head"}')
    handle(conn)
    assert "SECRET-TOKEN-123" not in json.dumps(reply(conn))


@pytest.mark.parametrize(
    "payload,uid",
    [
        (b'{"command":"python -m pytest -q"}', 65531),  # wrong peer: PermissionError
        (b"x" * 4097, 65534),  # oversized: ValueError
        (b'{"command":"python -m pytest -q","uid":1}', 65534),  # invalid request: ValueError
    ],
)
def test_non_policy_refusals_stay_a_bare_denied(payload: bytes, uid: int) -> None:
    conn = fake_conn(uid, payload)
    handle(conn)
    assert reply(conn)["stderr"] == "DENIED"


def test_an_unlisted_reason_is_never_returned() -> None:
    conn = fake_conn(65534, b'{"command":"python -m pytest -q"}')
    with patch.object(broker, "parse_command", side_effect=broker.ShellDenied("unexpected text")):
        handle(conn)
    assert reply(conn)["stderr"] == "DENIED"


def test_every_reason_the_broker_path_raises_is_a_listed_constant() -> None:
    import ast

    policy = Path(__file__).resolve().parents[1] / "src/hunter/automation/issue_agent_governed_shell.py"
    sources = {
        policy: {"parse_command"},
        Path(broker.__file__): None,  # every function in the broker
    }
    raised: set[str] = set()
    for path, functions in sources.items():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and (functions is None or node.name in functions):
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Raise)
                        and isinstance(inner.exc, ast.Call)
                        and getattr(inner.exc.func, "id", None) == "ShellDenied"
                    ):
                        (argument,) = inner.exc.args
                        assert isinstance(argument, ast.Constant), "a refusal reason must be a fixed string"
                        raised.add(argument.value)
    assert raised and raised <= broker.DENIAL_REASONS


def test_the_trusted_diagnostics_still_count_a_reasoned_refusal() -> None:
    from hunter.automation.issue_agent_kilo_container import stderr_structure

    conn = fake_conn(65534, b'{"command":"pwd"}')
    handle(conn)
    assert stderr_structure(reply(conn)["stderr"].encode())["denied"] == "1"


def test_the_broker_image_installs_every_declared_runtime_dependency_pinned_by_constraints() -> None:
    # Issue #577: without them the agent's own focused tests could not even be imported inside the broker.
    import re
    import tomllib

    root = Path(__file__).resolve().parents[1]
    declared = {
        re.split(r"[<>=!~\[ ;]", requirement, maxsplit=1)[0].lower()
        for requirement in tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    }
    dockerfile = (root / "scripts/issue_agent/Dockerfile.broker-canary").read_text()
    install = next(block for block in dockerfile.split("RUN ") if block.startswith("pip install"))
    assert "-c /opt/hunter/ci-constraints.txt" in install and "--only-binary=:all:" in install
    words = {word.lower().split("==")[0] for word in install.replace("\\", " ").split()}
    assert declared and declared <= words
    constraints = (root / "requirements/ci-constraints.txt").read_text().lower()
    assert all(re.search(rf"^{re.escape(name)}==", constraints, re.M) for name in declared)
