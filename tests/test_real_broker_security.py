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
