#!/usr/bin/env python3
"""Shared HTTP transport for the governed Issue-agent edges (Issue #497).

Both the trusted provisioning boundary (``scripts/hunter_issue_agent_provisioner.py``)
and the read-only execution issuer (``scripts/hunter_issue_agent_issuer.py``)
receive exactly one canonical ``hunter-issue-agent-signed-authorization-v1``
body over the same small, hardened wire contract, and the two edges must agree
on it so a document that reaches either edge is rejected or admitted under one
discipline:

-   one bounded request body (256 KiB), validated before any byte is trusted:
    missing or invalid ``Content-Length``, oversized, stale or incomplete
    bodies map to the same fixed status codes on both edges;
-   the canonical signed authorization is parsed here only through the
    canonical parser (bounded size, UTF-8, duplicate-key refusal, object
    shape), so the transport duplicates none of that trust logic;
-   responses are canonical JSON (sorted keys, compact separators), the health
    endpoint shape is identical, and the bounded daemon-worker server answers
    503 at the socket when every worker is busy instead of queueing unbounded
    threads.

The transport carries no authority semantics of its own: it signs nothing and
never decides acceptance. Each edge's own admission function (in its script)
performs the verification and fail-closed disposition of an already-parsed
document.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Final

from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorizationError,
    IssueAgentIssuerError,
    SignedIssueAgentAuthorization,
)

#: Maximum request body size in bytes (256 KiB), matching the trigger envelope cap.
MAX_REQUEST_BYTES: Final[int] = 256 * 1024

#: Small explicit bound on concurrent request workers. A stalled body holds
#: exactly one slot and is released by the read deadline below, so an
#: unauthenticated client can never monopolize the transport with one-thread
#: starvation or unbounded thread churn.
MAX_CONCURRENT_REQUEST_WORKERS: Final[int] = 8

#: Finite socket read deadline applied to every request connection before the
#: body is read, so a client that withholds its declared body terminates within
#: a bounded time (fail-closed 408) instead of occupying a worker indefinitely.
REQUEST_READ_TIMEOUT_SECONDS: Final[float] = 15.0


def canonical_json(value: object) -> str:
    """Canonical JSON: sorted keys, compact separators, no ASCII escaping."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def setup_logging(verbose: bool) -> None:
    """Configure process-wide logging for one issue-agent edge."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


class IssueAgentEdgeRequestHandler(BaseHTTPRequestHandler):
    """One bounded signed-authorization request on a governed Issue-agent edge.

    Subclasses bind the route they serve (``endpoint``), the name they answer
    the health endpoint with (``service_name``), and the response schema they
    stamp on error payloads (``error_schema_version``). They then implement
    only ``handle_authorization(signed)``: the edge's own verification and
    fail-closed disposition of an already-parsed canonical document.
    """

    endpoint: str = ""
    service_name: str = ""
    error_schema_version: str = ""
    max_request_bytes: Final[int] = MAX_REQUEST_BYTES

    def handle_authorization(self, signed: SignedIssueAgentAuthorization) -> None:
        """Admit or refuse one parsed signed authorization; subclasses override."""
        raise NotImplementedError

    def _parse_authorization(self) -> SignedIssueAgentAuthorization | None:
        """Read one bounded body and parse the canonical signed authorization.

        Returns ``None`` after sending the fixed fail-closed response when the
        transport contract is violated (missing/invalid ``Content-Length``,
        oversized body, stale read, incomplete body) or when the bytes are not
        a canonical signed authorization.
        """
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self._send_error(411, "Length Required")
            return None
        try:
            length = int(content_length)
        except ValueError:
            self._send_error(400, "Invalid Content-Length")
            return None
        if length < 0:
            self._send_error(400, "Invalid Content-Length")
            return None
        if length > self.max_request_bytes:
            self._send_error(413, "Payload Too Large")
            return None

        try:
            body = self.rfile.read(length)
        except TimeoutError:
            self._send_error(408, "Request body read timed out")
            return None
        if len(body) != length:
            self._send_error(400, "Incomplete request body")
            return None

        try:
            return SignedIssueAgentAuthorization.from_json(body)
        except IssueAgentIssuerError as error:
            self._send_error(401, str(error))
            return None
        except IssueAgentAuthorizationError as error:
            self._send_error(400, str(error))
            return None

    def do_POST(self) -> None:
        """Handle one POST carrying a signed authorization."""
        if self.path != self.endpoint:
            self._send_error(404, "Not Found")
            return
        signed = self._parse_authorization()
        if signed is None:
            return
        self.handle_authorization(signed)

    def do_GET(self) -> None:
        """Health check endpoint."""
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": self.service_name})
        else:
            self._send_error(404, "Not Found")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body = canonical_json(payload).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body = canonical_json({"error": message, "schema_version": self.error_schema_version}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger(__name__).info("%s - %s", self.address_string(), format % args)


class BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with a small, explicit concurrency bound.

    A request is admitted to a worker only while a slot is free; when every
    worker is busy the transport answers with a deterministic 503 at the socket
    instead of queueing behind an unbounded thread. Workers are daemon threads
    so shutdown is never blocked by a stalled peer, and every admitted request
    runs under the connection's finite read deadline, so a withheld body can
    hold a slot only until that deadline fires.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        max_workers: int,
    ) -> None:
        if max_workers < 1:
            raise ValueError("concurrent worker bound must be a positive integer")
        super().__init__(server_address, RequestHandlerClass)
        self._worker_slots = threading.Semaphore(max_workers)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self._reject_saturated(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def _reject_saturated(self, request: Any) -> None:
        """Fail closed on a bounded 503 without leaking internal detail."""
        try:
            request.settimeout(5.0)
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
        except OSError:
            pass
