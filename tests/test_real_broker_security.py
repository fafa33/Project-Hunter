"""Adversarial checks for the real container broker command boundary."""

import json
import socket
import struct
from importlib import import_module
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
