"""Private authority transport integration regressions."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant, vars_grant
from hunter.automation.workspace_authority_http import WorkspaceAuthorityHTTPServer
from hunter.automation.workspace_sqlite_backend import WorkspaceSQLiteBackend


def test_private_http_separates_reader_writer_and_survives_restart(tmp_path: Path) -> None:
    reader, writer = "r" * 40, "w" * 40
    backend = WorkspaceSQLiteBackend(tmp_path / "authority.sqlite", authority_key=b"k" * 32)
    with pytest.raises(WorkspaceAuthorityMismatch, match="loopback"):
        WorkspaceAuthorityHTTPServer(("0.0.0.0", 0), backend=backend, reader_token=reader, writer_token=writer)
    server = WorkspaceAuthorityHTTPServer(("127.0.0.1", 0), backend=backend, reader_token=reader, writer_token=writer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    now = datetime(2026, 11, 22, tzinfo=UTC)
    grant = WorkspaceGrant(
        repository="fafa33/Project-Hunter",
        issue=575,
        task_scope_digest="a" * 64,
        workspace=str(tmp_path / "work"),
        branch="issue-575-aaaa",
        base_sha="b" * 40,
        head_sha="b" * 40,
        checkpoint="checkpoint",
        lease_owner="issuer",
        lease_token_digest=hashlib.sha256(b"lease").hexdigest(),
        lease_expiry=(now + timedelta(minutes=30)).isoformat(),
        fencing=1,
        next_action="dispatch",
    )
    url = f"http://127.0.0.1:{server.server_port}"

    def call(method: str, path: str, token: str, body: dict[str, object] | None = None) -> int:
        req = urllib.request.Request(
            url + path,
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    try:
        assert call("POST", "/v1/grants", reader, vars_grant(grant)) == 403
        assert call("POST", "/v1/grants", writer, vars_grant(grant)) == 200
        assert call("POST", "/v1/grants", writer, vars_grant(grant)) == 409
        assert call("GET", "/v1/grants/575", writer) == 403
        assert call("GET", "/v1/grants/575", reader) == 200
        assert (
            call(
                "POST",
                "/v1/leases/renew",
                writer,
                {"issue": 575, "owner": "foreign", "lease_token": "lease", "now": now.isoformat()},
            )
            == 409
        )
        assert (
            call(
                "POST",
                "/v1/leases/renew",
                writer,
                {"issue": 575, "owner": "issuer", "lease_token": "lease", "now": now.isoformat()},
            )
            == 200
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert WorkspaceSQLiteBackend(tmp_path / "authority.sqlite", authority_key=b"k" * 32).read_grant(575).issue == 575
