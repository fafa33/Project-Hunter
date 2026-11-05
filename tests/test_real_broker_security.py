"""Adversarial checks for the real container broker command boundary."""

import json
import socket
import struct

# The preflight uses the pytest console entrypoint, which does not add the
# checkout root to sys.path. Resolve only this checkout, not a global path.
import sys
from importlib import import_module
from pathlib import Path

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
