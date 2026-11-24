"""Private, zero-subscription workspace authority HTTP boundary (Issue #575).

Bind only to loopback; a separately governed private tunnel can forward traffic.
Writer credentials must never be shared with candidates or read-only agents.
"""

from __future__ import annotations

import hmac
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch, WorkspaceGrant, vars_grant
from hunter.automation.workspace_sqlite_backend import WorkspaceSQLiteBackend


class WorkspaceAuthorityHTTPServer(ThreadingHTTPServer):
    """Trusted local transport; the SQLite backend remains sole mutation authority."""

    def __init__(
        self, address: tuple[str, int], *, backend: WorkspaceSQLiteBackend, reader_token: str, writer_token: str
    ) -> None:
        if address[0] not in ("127.0.0.1", "::1"):
            raise WorkspaceAuthorityMismatch("authority HTTP must bind loopback")
        if min(len(reader_token), len(writer_token)) < 32 or reader_token == writer_token:
            raise WorkspaceAuthorityMismatch("distinct strong transport tokens required")
        self.backend = backend
        self.reader_token = reader_token
        self.writer_token = writer_token
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: WorkspaceAuthorityHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        # Never log Authorization or lease token request bodies.
        pass

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, *, write: bool) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = self.server.writer_token if write else self.server.reader_token
        return hmac.compare_digest(supplied, "Bearer " + expected)

    def do_GET(self) -> None:
        if not self._authorized(write=False):
            self._reply(403, {"error": "forbidden"})
            return
        if not self.path.startswith("/v1/grants/"):
            self._reply(404, {"error": "not_found"})
            return
        try:
            issue = int(self.path.removeprefix("/v1/grants/"))
            if issue <= 0:
                raise ValueError
            grant = self.server.backend.read_grant(issue)
            self._reply(200, {"grant": vars_grant(grant)})
        except (ValueError, WorkspaceAuthorityMismatch):
            self._reply(404, {"error": "unavailable"})

    def do_POST(self) -> None:
        if not self._authorized(write=True):
            self._reply(403, {"error": "forbidden"})
            return
        if self.path not in ("/v1/grants", "/v1/leases/renew"):
            self._reply(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
            if self.path == "/v1/grants":
                self.server.backend.issue_exclusive(WorkspaceGrant(**payload))
            else:
                self.server.backend.renew_owned_lease(
                    issue=payload["issue"],
                    owner=payload["owner"],
                    lease_token=payload["lease_token"],
                    now=datetime.fromisoformat(payload["now"]),
                )
            self._reply(200, {"ok": True})
        except (ValueError, TypeError, KeyError, WorkspaceAuthorityMismatch):
            self._reply(409, {"error": "authority_mismatch"})
